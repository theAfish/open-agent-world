"""Bounded per-role model checkpoints for recovery after transport loss."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from backend.agents.context import replayable_contents


MAX_SNAPSHOT_BYTES = 256 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunModelCheckpoints:
    def __init__(self, database: Any, run_id: str, *, api_key: str | None = None) -> None:
        self.database = database
        self.run_id = run_id
        self.api_key = api_key

    def start(self, role: str, request_id: int, run_attempt: int, request: Any) -> None:
        contents = [item.model_dump(mode="json", exclude_none=True)
                    for item in getattr(request, "contents", ()) or ()]
        serialized = json.dumps(replayable_contents(contents), ensure_ascii=False,
                                separators=(",", ":"), default=str)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        contains_key = bool(self.api_key and self.api_key in serialized)
        replayable = not contains_key and len(serialized.encode("utf-8")) <= MAX_SNAPSHOT_BYTES
        now = _now()
        with self.database.transaction(immediate=True) as db:
            db.execute("""INSERT INTO run_model_checkpoints (
                run_id, role, request_id, run_attempt, state, contents_json,
                input_sha256, replayable, started_at, updated_at
            ) VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, role) DO UPDATE SET
                request_id=excluded.request_id, run_attempt=excluded.run_attempt,
                state='started', contents_json=excluded.contents_json,
                input_sha256=excluded.input_sha256, replayable=excluded.replayable,
                started_at=excluded.started_at, updated_at=excluded.updated_at""", (
                self.run_id, role, request_id, run_attempt,
                serialized if replayable else None, digest, int(replayable), now, now,
            ))

    def settle(self, role: str, request_id: int, state: str) -> None:
        if state not in {"finished", "interrupted"}:
            raise ValueError("model checkpoint can only finish or interrupt")
        with self.database.transaction(immediate=True) as db:
            db.execute("""UPDATE run_model_checkpoints SET state=?, updated_at=?
                WHERE run_id=? AND role=? AND request_id=? AND state='started'""",
                (state, _now(), self.run_id, role, request_id))

    def interrupt_run(self) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute("""UPDATE run_model_checkpoints SET state='interrupted', updated_at=?
                WHERE run_id=? AND state='started'""", (_now(), self.run_id))

    def latest(self, role: str) -> dict[str, Any] | None:
        with self.database.locked() as db:
            row = db.execute("""SELECT * FROM run_model_checkpoints
                WHERE run_id=? AND role=?""", (self.run_id, role)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["replayable"] = bool(result["replayable"])
        if result["contents_json"] is not None:
            result["contents"] = json.loads(result.pop("contents_json"))
        return result
