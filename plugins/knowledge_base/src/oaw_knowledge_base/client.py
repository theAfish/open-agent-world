"""One cached ``KnowledgeBase`` client per card.

The MKB client owns a daemon thread that runs durable jobs, so it has to outlive a
single resource action: a client opened and closed inside one handler would kill the
markdown conversion it just submitted. Clients are therefore cached by node id and
closed only on shutdown or deletion.

Where the original bytes live is a deployment decision, not a card setting: MKB's
``sql:`` store keeps them in ``knowledge.db`` (the default), ``file:///root`` puts
them on a filesystem, ``s3://bucket?endpoint=…`` on any S3-compatible service. The
choice is recorded in the database the first time it is opened and must not change
afterwards — every source row points into exactly one store, and silently opening
it against another would make all retained evidence unreadable.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from urllib.parse import urlparse

from .errors import KnowledgeError

FILE_NAME = "knowledge.db"
DEFAULT_COLLECTION = "Knowledge base"
DEFAULT_OBJECT_STORE = "sql:"
OBJECT_STORE_ENV = "OAW_KB_OBJECT_STORE_URL"
OBJECT_STORE_ACCESS_KEY_ENV = "OAW_KB_OBJECT_STORE_ACCESS_KEY"
OBJECT_STORE_SECRET_KEY_ENV = "OAW_KB_OBJECT_STORE_SECRET_KEY"

_clients: dict[str, object] = {}
_lock = threading.Lock()

INSTALL_HINT = (
    "Knowledge Base dependencies are not prepared. Restore or reinstall this Pack in Pack & Card Library, "
    "then restart OAW. For a source checkout, run the project setup command to prepare its declared Pack dependencies."
)


def database_path(storage_path):
    return storage_path / FILE_NAME


def configured_object_store(object_store_url=None):
    """The evidence store this process is deployed with: an explicit URL, else the
    environment, else MKB's in-database store."""
    value = (object_store_url or os.environ.get(OBJECT_STORE_ENV) or "").strip()
    return value or DEFAULT_OBJECT_STORE


def _object_store_credentials(url, resolve_secret=None):
    if urlparse(url).scheme != "s3":
        return None, None

    def secret(name):
        return (resolve_secret(name) if resolve_secret else None) or os.environ.get(name) or None

    return secret(OBJECT_STORE_ACCESS_KEY_ENV), secret(OBJECT_STORE_SECRET_KEY_ENV)


def _check_object_store(kb, engine, url):
    """Refuse to open retained evidence against a store other than the one it was
    written to. A database from before this record existed can only have used
    ``sql:``, so one with sources and no record is treated as such."""
    from sqlalchemy import Column, MetaData, String, Table, Text, insert, select

    metadata = MetaData()
    table = Table("oaw_kb_evidence_store", metadata,
                  Column("key", String(64), primary_key=True),
                  Column("value", Text, nullable=False))
    metadata.create_all(engine, checkfirst=True)
    with engine.begin() as connection:
        recorded = connection.execute(
            select(table.c.value).where(table.c.key == "object_store_url")).scalar_one_or_none()
        if recorded is None:
            if kb.sources.list(limit=1):
                recorded = DEFAULT_OBJECT_STORE
            else:
                connection.execute(insert(table).values(key="object_store_url", value=url))
                return
    if recorded != url:
        raise KnowledgeError(
            f"This knowledge base keeps its original files in {recorded!r} but the deployment "
            f"is configured for {url!r} ({OBJECT_STORE_ENV}). Restore the original setting; "
            "moving retained evidence between stores is not done automatically.")


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


def _build(path, resolve_secret=None, resolve_model=None, object_store_url=None):
    try:
        from mkb.sdk import KnowledgeBase
    except ImportError as error:  # pragma: no cover - depends on the deployment env
        raise KnowledgeError(INSTALL_HINT) from error
    from sqlalchemy import create_engine

    from .experiment_store import ExperimentRecordStore
    from .graph_store import SqlGraphStore
    from .insight_store import InsightStore
    from .projection_store import ProjectionStore
    from .pipelines import register_pipelines
    from .search_store import ensure_schema as ensure_search_schema
    from .spaces import ensure_builtin_spaces

    _prepare_file(path)
    url = f"sqlite:///{path}"
    # A separate engine for the plugin's own tables keeps their writes out of MKB
    # transactions: the graph, the search index and experiment records all live here.
    engine = create_engine(url, connect_args={"timeout": 30})
    graph_store = SqlGraphStore(engine)
    store_url = configured_object_store(object_store_url)
    access_key, secret_key = _object_store_credentials(store_url, resolve_secret)
    kb = KnowledgeBase.from_url(
        database_url=url,
        object_store_url=store_url,
        object_store_access_key=access_key,
        object_store_secret_key=secret_key,
        graph_store=graph_store,
    )
    kb.initialize()
    try:
        _check_object_store(kb, engine, store_url)
    except Exception:
        kb.close()
        engine.dispose()
        raise
    kb.oaw_graph_store = graph_store
    kb.oaw_engine = engine
    kb.oaw_object_store_url = store_url
    # Only callables live in memory; no secret or connection enters the durable
    # job database — the conversion job resolves both when it actually runs.
    kb.oaw_resolve_secret = resolve_secret
    kb.oaw_resolve_model = resolve_model
    ensure_search_schema(engine)
    kb.oaw_experiments = ExperimentRecordStore(engine)
    kb.oaw_insights = InsightStore(engine)
    kb.oaw_projections = ProjectionStore(engine)
    register_pipelines(kb)
    kb.jobs.recover_interrupted()
    _backfill_search_index(kb, engine)
    ensure_builtin_spaces(kb)
    return kb


def collection(kb, name=None):
    """The single collection this card writes into, created on first use."""
    wanted = (name or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION
    for existing in kb.collections.list(limit=100):
        if existing.name == wanted:
            return existing
    return kb.collections.create(name=wanted)


def open_client(node_id, storage_path, *, resolve_secret=None, resolve_model=None,
                object_store_url=None):
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
    kb = _build(path, resolve_secret, resolve_model, object_store_url)
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
