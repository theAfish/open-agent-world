"""Durable plugin discovery and nonblocking, idempotent environment preparation."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time

from backend.sandbox.python_runtime import validate_requirements

logger = logging.getLogger(__name__)


class PluginEnvironmentBootstrap:
    def __init__(self, root: Path, registry, sandbox_manager):
        self.path = root / "runtime" / "plugins.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.manager = sandbox_manager
        self.task = None
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS plugins (
                id TEXT PRIMARY KEY, declaration TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, initialized_at REAL NOT NULL,
                completed TEXT NOT NULL DEFAULT '{}', error TEXT, updated_at REAL NOT NULL)""")
        self.discover()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def discover(self):
        plugins = self.registry.plugins()
        with self.connect() as db:
            ids = {p.id for p in plugins}
            for row in db.execute("SELECT id FROM plugins"):
                if row['id'] not in ids:
                    db.execute("DELETE FROM plugins WHERE id=?", (row['id'],))
            for plugin in plugins:
                requirements = validate_requirements(self.registry.runtime_requirements.get(plugin.id, plugin.python_requirements))
                declaration = json.dumps(sorted(set(requirements)))
                digest = hashlib.sha256(declaration.encode()).hexdigest()
                now = time.time()
                db.execute("""INSERT INTO plugins(id,declaration,digest,state,initialized_at,updated_at)
                    VALUES(?,?,?,'discovered',?,?) ON CONFLICT(id) DO UPDATE SET
                    declaration=excluded.declaration, digest=excluded.digest,
                    state=CASE WHEN plugins.digest != excluded.digest THEN 'discovered' ELSE plugins.state END,
                    error=CASE WHEN plugins.digest != excluded.digest THEN NULL ELSE plugins.error END,
                    updated_at=excluded.updated_at""", (plugin.id, declaration, digest, now, now))

    def records(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM plugins ORDER BY id")]

    def enqueue(self):
        self.discover()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="plugin-environment-bootstrap")

    def update(self, plugin_id, state, *, error=None, completed=None):
        with self.connect() as db:
            db.execute("UPDATE plugins SET state=?,error=?,updated_at=? WHERE id=?",
                (state, error, time.time(), plugin_id))
            if completed is not None:
                db.execute("UPDATE plugins SET completed=? WHERE id=?", (json.dumps(completed), plugin_id))

    async def run(self):
        for row in self.records():
            if not self.registry.is_enabled(row['id']):
                continue
            requirements = json.loads(row['declaration'])
            completed = json.loads(row['completed'])
            if not requirements:
                self.update(row['id'], 'environment_ready')
                continue
            try:
                if self.manager is None:
                    raise RuntimeError("Managed sandbox execution is not configured")
                targets = [r.id for r in await self.manager.registry.catalog() if r.available]
                if not targets:
                    raise RuntimeError("No sandbox execution platform is available for Python bootstrap")
                for target in targets:
                    self.update(row['id'], 'environment_pending')
                    await self.manager.prepare_python(target, requirements, bootstrap_key=row['id'])
                    completed[target] = row['digest']
                    self.update(row['id'], 'environment_pending', completed=completed)
                self.update(row['id'], 'environment_ready')
            except Exception as exc:
                self.update(row['id'], 'environment_failed', error=str(exc))
                logger.exception("Plugin %s environment bootstrap failed", row['id'])

    async def shutdown(self):
        if self.task is not None:
            # Drain installers rather than abandoning an active mutation.
            await self.task
