from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('agent', 'text', 'image', 'sandbox')),
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
    direction TEXT NOT NULL DEFAULT 'forward' CHECK (direction IN ('forward', 'bidirectional')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT no_self_edge CHECK (source_id <> target_id),
    CONSTRAINT one_edge_per_pair UNIQUE (source_id, target_id)
);

CREATE INDEX IF NOT EXISTS edges_source_idx ON edges (source_id);
CREATE INDEX IF NOT EXISTS edges_target_idx ON edges (target_id);

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
            edge_columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(edges)")
            }
            if "direction" not in edge_columns:
                self._connection.execute(
                    "ALTER TABLE edges ADD COLUMN direction TEXT NOT NULL DEFAULT 'forward'"
                )

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
                self._connection.commit()

    @contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()
