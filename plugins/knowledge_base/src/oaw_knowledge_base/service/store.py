"""One store directory, opened in this process, shared by every front door.

A store is a folder holding ``knowledge.db`` (projects, records, projections and the
graph, all in SQLite) and one ``settings.json`` file.
It is the standalone equivalent of an OAW card's storage directory, and
it is deliberately the same shape: the service calls the very same handlers the card
does, with a :class:`~oaw_knowledge_base.context.KnowledgeContext` in place of OAW's
``NodeResourceContext``.

Two rules live here because they are about the file, not about any one transport:

* **One writer.** MKB runs a job thread inside each open client. Two processes on one
  ``knowledge.db`` means two job threads racing the same job rows, so ``kb serve``
  writes ``.kb-service.lock`` and in-process callers refuse a store a live server
  holds.
* **One call at a time.** OAW serializes resource actions behind a global node
  mutation lock; :meth:`Store.run` does the same so handlers see the concurrency they
  were written for.
"""
from __future__ import annotations

import errno
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from ..client import close_client, open_client
from ..context import JsonSettings, KnowledgeContext
from ..operations import BY_NAME

LOCK_NAME = ".kb-service.lock"
STORE_ENV = "KB_SERVICE_STORE"


def default_store() -> Path:
    configured = os.environ.get(STORE_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "oaw-knowledge"


def resolve_store(value=None) -> Path:
    return Path(value).expanduser() if value else default_store()


class StoreBusy(RuntimeError):
    """The store belongs to a running server; talk to it over HTTP instead."""


def _alive(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # Someone else's process, but it exists.
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def read_lock(store):
    """The live owner of this store, or ``None`` when it is free or the lock is stale."""
    path = Path(store) / LOCK_NAME
    try:
        holder = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    pid = holder.get("pid")
    if not isinstance(pid, int) or pid == os.getpid() or not _alive(pid):
        return None
    return holder


def require_free(store):
    holder = read_lock(store)
    if holder is None:
        return
    address = holder.get("url") or "the running service"
    raise StoreBusy(
        f"A knowledge base service (pid {holder.get('pid')}) already has {store} open. "
        f"Point this command at it with --service {address}, or stop the service first.")


@contextmanager
def hold_lock(store, url=None):
    store = Path(store)
    require_free(store)
    store.mkdir(parents=True, exist_ok=True)
    path = store / LOCK_NAME
    path.write_text(json.dumps(
        {"pid": os.getpid(), "url": url, "started_at": time.time()}), "utf-8")
    try:
        yield path
    finally:
        try:
            holder = json.loads(path.read_text("utf-8"))
            if holder.get("pid") == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, ValueError):  # pragma: no cover - best effort on shutdown
            pass


class Store:
    """An open knowledge base: projects share one client and settings authority."""

    def __init__(self, path, *, key="service"):
        self.path = Path(path).expanduser()
        self.key = key
        self._settings = JsonSettings(self.path / "settings.json")
        # Mirrors OAW's global node mutation lock, for the same reason.
        self._mutation = threading.Lock()

    def open(self):
        """Materialize the database now so a caller sees a bad store immediately."""
        return open_client(self.key, self.path)

    def context(self, *, actor=None, confirmed=False):
        return KnowledgeContext(node_id=self.key, storage_path=self.path, state=self._settings,
                                actor_id=actor, confirmed=confirmed)

    def run(self, operation, arguments, *, actor=None, confirmed=False):
        """Run one named operation, one at a time, against this knowledge base."""
        handler = BY_NAME[operation].handler
        context = self.context(actor=actor, confirmed=confirmed)
        with self._mutation:
            return handler(context, arguments or {})

    def close(self):
        close_client(self.key)
