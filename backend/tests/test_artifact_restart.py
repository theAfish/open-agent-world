"""Kill a separate backend process, then reconcile the same persistent store."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from backend.tests.artifact_crash_worker import services_for


@pytest.mark.parametrize('phase', ['publication', 'publication_rename', 'ready', 'deletion', 'reclamation'])
def test_backend_process_restart(tmp_path, phase, monkeypatch):
    root = tmp_path / 'persistent'
    process = subprocess.Popen([sys.executable, '-m', 'backend.tests.artifact_crash_worker', str(root), phase],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=Path(__file__).resolve().parents[2],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    marker = root / 'checkpoint.json'
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not marker.exists():
            process.kill()
            output, error = process.communicate(timeout=10)
            pytest.fail((output + error).decode(errors='replace'))
        saved = json.loads(marker.read_text(encoding='utf-8'))
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    async def recover():
        services = services_for(root)
        try:
            store = services.resources.artifacts
            if phase == 'ready':
                # A large canonical file must not be opened or hashed on startup.
                target = store.path(saved['versions'][0]['version_id']) / 'output.bin'
                with target.open('r+b') as output:
                    output.truncate(2 * 1024 ** 3)
            def unexpected_content_io(*args, **kwargs):
                raise AssertionError('Startup must not inspect ready artifact bytes')
            with monkeypatch.context() as startup_patch:
                startup_patch.setattr(store, 'validate', unexpected_content_io)
                startup_patch.setattr('backend.resources.artifacts.pinned', unexpected_content_io)
                startup_patch.setattr('backend.resources.artifacts.hashlib', SimpleNamespace(sha256=unexpected_content_io))
                await services.startup()
            version = store.get(saved['versions'][0]['version_id'])
            assert len(store.all()) == 1
            if phase.startswith('publication'):
                assert version['state'] == 'failed' and version['cleanup'] == 'complete'
                assert not store.path(version['version_id']).exists()
                assert not store.path(version['version_id'], staging=True).exists()
            elif phase == 'deletion':
                assert version['state'] == 'deleted'
                assert not store.path(version['version_id']).exists()
            else:
                assert version['state'] == 'ready'
                if phase == 'ready':
                    with target.open('r+b') as output:
                        output.truncate(len(b'\0durable\xff' * 10000))
                content = b''.join([chunk async for chunk in store.read(services, saved['collection'], version['version_id'], 'output.bin')])
                assert content == b'\0durable\xff' * 10000
            if phase == 'reclamation':
                assert services.summoning.records()[0]['reclaimed']
                assert services.world.maybe_get_card(saved['sandbox']) is not None
                with services.database.locked() as db:
                    assert db.execute('SELECT count(*) FROM pending_node_deletions').fetchone()[0] == 0
        finally:
            services.close()
    asyncio.run(recover())
