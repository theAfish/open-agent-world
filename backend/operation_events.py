"""Host-local committed operation journal; EventHub is only a notification bus.

Writers call record inside their existing SQLite mutation transaction. The same
Database's nested transactions keep the event and mutation on one commit. No
subscriber, browser or provider coroutine is required to retain an event.
"""
from __future__ import annotations

from collections.abc import Callable

from backend.events.models import RuntimeEvent
from backend.errors import ConflictError


class OperationEventJournal:
    def __init__(self, database):
        self.database = database
        self.wake: Callable[[], None] | None = None
        with database.transaction(immediate=True) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS operation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_json TEXT NOT NULL
            )""")

    def record(self, event: RuntimeEvent) -> int:
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute("SELECT event_json FROM operation_events WHERE event_id=?", (event.id,)).fetchone()
            if existing:
                retained = RuntimeEvent.model_validate_json(existing[0])
                ignored = {"timestamp", "sequence", "stream_id"}
                if retained.model_dump(exclude=ignored) != event.model_dump(exclude=ignored):
                    raise ConflictError("A durable event ID cannot be reused with different operation data")
            connection.execute("INSERT OR IGNORE INTO operation_events(event_id,event_json) VALUES (?,?)",
                               (event.id, event.model_dump_json()))
            sequence = connection.execute("SELECT sequence FROM operation_events WHERE event_id=?",
                                          (event.id,)).fetchone()[0]
        if self.wake:
            self.wake()
        return int(sequence)

    def max_sequence(self) -> int:
        with self.database.locked() as connection:
            return int(connection.execute("SELECT COALESCE(MAX(sequence),0) FROM operation_events").fetchone()[0])

    def after(self, sequence: int, *, limit: int = 100):
        with self.database.locked() as connection:
            rows = connection.execute("SELECT sequence,event_json FROM operation_events WHERE sequence>? ORDER BY sequence LIMIT ?",
                                      (sequence, limit)).fetchall()
        return [(int(row["sequence"]), RuntimeEvent.model_validate_json(row["event_json"])) for row in rows]
