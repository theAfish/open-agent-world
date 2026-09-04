from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    plugin_id TEXT NOT NULL,
    name TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    width REAL NOT NULL CHECK (width > 0),
    height REAL NOT NULL CHECK (height > 0),
    expanded INTEGER NOT NULL DEFAULT 0 CHECK (expanded IN (0, 1)),
    config_json TEXT NOT NULL,
    chunk_x INTEGER NOT NULL,
    chunk_y INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS cards_chunk_idx ON cards (chunk_x, chunk_y);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL,
    plugin_id TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'forward' CHECK (direction IN ('forward', 'bidirectional')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT no_self_edge CHECK (source_id <> target_id),
    CONSTRAINT one_edge_per_pair UNIQUE (source_id, target_id)
);

CREATE INDEX IF NOT EXISTS edges_source_idx ON edges (source_id);
CREATE INDEX IF NOT EXISTS edges_target_idx ON edges (target_id);

CREATE TABLE IF NOT EXISTS legions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    blueprint_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pending_node_deletions (
    node_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    commit_state TEXT NOT NULL DEFAULT 'prepared'
        CHECK (commit_state IN ('prepared', 'started', 'committed')),
    plugin_id TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    plugin_api_version TEXT NOT NULL,
    requires_finalize INTEGER NOT NULL CHECK (requires_finalize IN (0, 1)),
    cleanup_json TEXT NOT NULL DEFAULT '{}',
    card_json TEXT NOT NULL,
    edges_json TEXT NOT NULL,
    resource_json TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS pending_node_deletions_batch_idx
    ON pending_node_deletions (batch_id, sequence);

CREATE TABLE IF NOT EXISTS conversation_sessions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS conversation_sessions_card_idx
    ON conversation_sessions (conversation_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_participants (
    session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (session_id, agent_id)
);

CREATE INDEX IF NOT EXISTS conversation_participants_agent_idx
    ON conversation_participants (agent_id, joined_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES conversation_sessions(id) ON DELETE CASCADE,
    sender_kind TEXT NOT NULL CHECK (sender_kind IN ('user', 'agent', 'system')),
    sender_id TEXT,
    sender_name TEXT NOT NULL,
    content TEXT NOT NULL,
    mention_ids_json TEXT NOT NULL DEFAULT '[]',
    run_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS conversation_messages_session_idx
    ON conversation_messages (session_id, created_at, id);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    parent_run_id TEXT REFERENCES runs(run_id),
    root_run_id TEXT NOT NULL REFERENCES runs(run_id),
    task_id TEXT,
    caller_kind TEXT NOT NULL,
    caller_id TEXT,
    context_id TEXT,
    runtime_provider_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('created', 'running', 'waiting', 'succeeded', 'failed',
                   'cancelled', 'interrupted')
    ),
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS runs_agent_idx ON runs (agent_id, created_at);
CREATE INDEX IF NOT EXISTS runs_parent_idx ON runs (parent_run_id, created_at);
CREATE INDEX IF NOT EXISTS runs_task_idx ON runs (task_id, created_at);

CREATE TABLE IF NOT EXISTS state_scopes (
    scope_id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    schema_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT one_state_scope_per_owner UNIQUE (scope_kind, owner_id)
);

CREATE INDEX IF NOT EXISTS state_scopes_owner_idx
    ON state_scopes (scope_kind, owner_id);

CREATE TABLE IF NOT EXISTS state_values (
    scope_id TEXT NOT NULL REFERENCES state_scopes(scope_id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope_id, key)
);

CREATE INDEX IF NOT EXISTS state_values_scope_idx ON state_values (scope_id);

CREATE TABLE IF NOT EXISTS resources (
    card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('text', 'image')),
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    width INTEGER,
    height INTEGER,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    operation TEXT NOT NULL,
    old_sha256 TEXT,
    new_sha256 TEXT NOT NULL,
    actor_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (card_id, revision)
);

CREATE INDEX IF NOT EXISTS resource_history_card_idx
    ON resource_history (card_id, revision DESC);
"""


class Database:
    """A small, serialized SQLite boundary suitable for the local POC.

    A single connection is intentionally retained so `:memory:` databases work
    in tests and multi-statement operations have one clear transaction lock.
    """

    def __init__(self, path: str | Path) -> None:
        raw_path = str(path)
        if raw_path != ":memory:":
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        self.path = raw_path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            raw_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=10,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        if raw_path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._migrate_open_card_types()
            card_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(cards)")
            }
            if "plugin_id" not in card_columns:
                self._connection.execute(
                    "ALTER TABLE cards ADD COLUMN plugin_id TEXT NOT NULL "
                    "DEFAULT 'open-agent-world.core'"
                )
            edge_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(edges)")
            }
            if "direction" not in edge_columns:
                self._connection.execute(
                    "ALTER TABLE edges ADD COLUMN direction TEXT NOT NULL DEFAULT 'forward'"
                )
            if "plugin_id" not in edge_columns:
                self._connection.execute(
                    "ALTER TABLE edges ADD COLUMN plugin_id TEXT NOT NULL "
                    "DEFAULT 'open-agent-world.core'"
                )
            state_value_columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(state_values)")
            }
            if "deleted" not in state_value_columns:
                self._connection.execute(
                    "ALTER TABLE state_values ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
                )
            pending_delete_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(pending_node_deletions)"
                )
            }
            if "plugin_version" not in pending_delete_columns:
                self._connection.execute(
                    "ALTER TABLE pending_node_deletions ADD COLUMN "
                    "plugin_version TEXT NOT NULL DEFAULT ''"
                )
            if "requires_finalize" not in pending_delete_columns:
                self._connection.execute(
                    "ALTER TABLE pending_node_deletions ADD COLUMN "
                    "requires_finalize INTEGER NOT NULL DEFAULT 1"
                )
            if "cleanup_json" not in pending_delete_columns:
                self._connection.execute(
                    "ALTER TABLE pending_node_deletions ADD COLUMN "
                    "cleanup_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "commit_state" not in pending_delete_columns:
                # Rows written by the earlier journal implementation were
                # persisted only after plugin commit had completed.
                self._connection.execute(
                    "ALTER TABLE pending_node_deletions ADD COLUMN "
                    "commit_state TEXT NOT NULL DEFAULT 'committed'"
                )
            if "plugin_api_version" not in pending_delete_columns:
                self._connection.execute(
                    "ALTER TABLE pending_node_deletions ADD COLUMN "
                    "plugin_api_version TEXT NOT NULL DEFAULT '1.0'"
                )

    def _migrate_open_card_types(self) -> None:
        row = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'cards'"
        ).fetchone()
        schema = "" if row is None else str(row["sql"] or "")
        if "CHECK (type IN" not in schema:
            return

        # Plugin node identifiers are registry-validated strings. Older POC
        # databases constrained this column to the four built-in node types.
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE cards_open_types (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    plugin_id TEXT NOT NULL DEFAULT 'open-agent-world.core',
                    name TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    width REAL NOT NULL CHECK (width > 0),
                    height REAL NOT NULL CHECK (height > 0),
                    expanded INTEGER NOT NULL DEFAULT 0 CHECK (expanded IN (0, 1)),
                    config_json TEXT NOT NULL,
                    chunk_x INTEGER NOT NULL,
                    chunk_y INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO cards_open_types (
                    id, type, name, x, y, width, height, expanded, config_json,
                    chunk_x, chunk_y, created_at, updated_at, revision
                )
                SELECT id, type, name, x, y, width, height, expanded, config_json,
                       chunk_x, chunk_y, created_at, updated_at, revision
                FROM cards;
                DROP TABLE cards;
                ALTER TABLE cards_open_types RENAME TO cards;
                CREATE INDEX cards_chunk_idx ON cards (chunk_x, chunk_y);
                COMMIT;
                """
            )
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                try:
                    self._connection.commit()
                except BaseException:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    raise

    @contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()
