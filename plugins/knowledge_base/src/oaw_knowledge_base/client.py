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
    "Knowledge Base dependencies are not prepared. Restore or reinstall this Pack in Pack & Card Library, "
    "then restart OAW. For a source checkout, run the project setup command to prepare its declared Pack dependencies."
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


def _backfill_search_index(kb, engine):
    """Index any record converted before the search feature existed.

    Chunks are otherwise only (re)written by the conversion pipeline going forward;
    this catches up a database created before ``search_store`` did, once, cheaply
    skipping whatever a record already has indexed.
    """
    from .search_store import has_chunks, index_record

    offset = 0
    while True:
        batch = kb.records.list(limit=200, offset=offset)
        if not batch:
            return
        for record in batch:
            data = record.data or {}
            text, source_id = data.get("markdown"), data.get("source_id")
            if isinstance(text, str) and source_id and not has_chunks(engine, record.id):
                index_record(engine, record_id=record.id, source_id=source_id,
                            group_id=record.collection_id, text=text)
        offset += len(batch)


def _build(path, resolve_secret=None, resolve_model=None):
    try:
        from mkb.sdk import KnowledgeBase
    except ImportError as error:  # pragma: no cover - depends on the deployment env
        raise KnowledgeError(INSTALL_HINT) from error
    from sqlalchemy import create_engine

    from .experiment_store import ExperimentRecordStore
    from .graph_store import SqlGraphStore
    from .insight_store import InsightStore
    from .knowledge_migration import migrate_knowledge_model
    from .knowledge_store import KnowledgeDraftStore
    from .pipelines import register_pipelines
    from .search_store import ensure_schema as ensure_search_schema

    _prepare_file(path)
    url = f"sqlite:///{path}"
    # A separate engine for the plugin's own tables keeps their writes out of MKB
    # transactions: the graph, the search index and experiment records all live here.
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
    # Only callables live in memory; no secret or connection enters the durable
    # job database — the conversion job resolves both when it actually runs.
    kb.oaw_resolve_secret = resolve_secret
    kb.oaw_resolve_model = resolve_model
    ensure_search_schema(engine)
    kb.oaw_experiments = ExperimentRecordStore(engine)
    kb.oaw_insights = InsightStore(engine)
    kb.oaw_knowledge = KnowledgeDraftStore(engine)
    migrate_knowledge_model(engine)
    register_pipelines(kb)
    kb.jobs.recover_interrupted()
    _backfill_search_index(kb, engine)
    return kb


def collection(kb, name=None):
    """The single collection this card writes into, created on first use."""
    wanted = (name or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    for existing in kb.collections.list(limit=100):
        if existing.name == wanted:
            return existing
    return kb.collections.create(name=wanted)


def open_client(node_id, storage_path, *, resolve_secret=None, resolve_model=None):
    path = database_path(storage_path)
    with _lock:
        cached = _clients.get(node_id)
        if cached is not None:
            if resolve_secret is not None:
                cached.oaw_resolve_secret = resolve_secret
            if resolve_model is not None:
                cached.oaw_resolve_model = resolve_model
            return cached
    # Build outside the lock: initialize() touches the disk and can be slow.
    kb = _build(path, resolve_secret, resolve_model)
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
