"""One cached ``KnowledgeBase`` client per card.

The MKB client owns a daemon thread that runs durable jobs, so it has to outlive a
single resource action: a client opened and closed inside one handler would kill the
markdown conversion it just submitted. Clients are therefore cached by node id and
closed only on shutdown or deletion.
"""
from __future__ import annotations

import sqlite3
import threading

from .errors import KnowledgeError

FILE_NAME = "knowledge.db"
DEFAULT_COLLECTION = "Knowledge base"

_clients: dict[str, object] = {}
_lock = threading.Lock()

INSTALL_HINT = (
    "This card needs mat-know-base in the backend environment. Install it with "
    "`backend/.venv/bin/pip install \"mat-know-base @ /path/to/mat_know_base\"` and reopen the card."
)


def database_path(storage_path):
    return storage_path / FILE_NAME


def _prepare_file(path):
    """WAL + a generous busy timeout: the job thread and resource actions both write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        db.commit()
    finally:
        db.close()


def _build(path):
    try:
        from mkb.sdk import KnowledgeBase
    except ImportError as error:  # pragma: no cover - depends on the deployment env
        raise KnowledgeError(INSTALL_HINT) from error
    from sqlalchemy import create_engine

    from .graph_store import SqlGraphStore
    from .pipelines import register_pipelines

    _prepare_file(path)
    url = f"sqlite:///{path}"
    # A separate engine for the graph tables keeps graph writes out of MKB transactions.
    engine = create_engine(url, connect_args={"timeout": 30})
    graph_store = SqlGraphStore(engine)
    kb = KnowledgeBase.from_url(
        database_url=url,
        object_store_url="sql:",  # object bytes live in the same file; no MinIO
        graph_store=graph_store,
    )
    kb.initialize()
    kb.oaw_graph_store = graph_store
    kb.oaw_engine = engine
    register_pipelines(kb)
    kb.jobs.recover_interrupted()
    return kb


def collection(kb, name=None):
    """The single collection this card writes into, created on first use."""
    wanted = (name or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    for existing in kb.collections.list(limit=100):
        if existing.name == wanted:
            return existing
    return kb.collections.create(name=wanted)


def open_client(node_id, storage_path):
    path = database_path(storage_path)
    with _lock:
        cached = _clients.get(node_id)
        if cached is not None:
            return cached
    # Build outside the lock: initialize() touches the disk and can be slow.
    kb = _build(path)
    with _lock:
        cached = _clients.get(node_id)
        if cached is not None:
            _close(kb)
            return cached
        _clients[node_id] = kb
        return kb


def _close(kb):
    try:
        kb.close()
    except Exception:  # pragma: no cover - close must never mask the caller's error
        pass
    engine = getattr(kb, "oaw_engine", None)
    if engine is not None:
        try:
            engine.dispose()
        except Exception:  # pragma: no cover
            pass


def close_client(node_id):
    with _lock:
        kb = _clients.pop(node_id, None)
    if kb is not None:
        _close(kb)


def close_all():
    with _lock:
        clients = list(_clients.values())
        _clients.clear()
    for kb in clients:
        _close(kb)
