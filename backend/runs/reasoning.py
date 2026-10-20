"""Provider-exposed thinking owned by the existing OAW Run StateStore."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from backend.errors import NotFoundError

PREVIEW_CHARS = 2000


class RunReasoning:
    def __init__(self, state, run_id: str):
        self.state = state
        self.run_id = run_id
        self._last_write: dict[str, float] = {}
        self._pending: dict[str, dict] = {}
        self._settled: set[str] = set()

    def records(self) -> dict[str, dict]:
        try:
            return self.state.get(self.state.get_scope("run", self.run_id), "model_reasoning")
        except NotFoundError:
            return {}

    def save(self, payload: dict[str, Any], *, force: bool = False) -> dict:
        identity = payload.get("provider_message_id")
        text = payload.get("text")
        if not isinstance(identity, str) or not identity or not isinstance(text, str):
            return {}
        if identity in self._settled:
            return {}
        self._pending[identity] = dict(payload)
        now = time.monotonic()
        if (not force and payload.get("streaming") is True
                and now - self._last_write.get(identity, -float("inf")) < 1):
            return {}
        current = self.records().get(identity, {})
        # A delayed live snapshot cannot reopen an already terminated attempt.
        if current.get("streaming") is False:
            self._pending.pop(identity, None)
            self._settled.add(identity)
            return current
        fields = {key: payload[key] for key in ("role", "model_request", "model_attempt", "run_attempt",
            "provider_message_id", "text", "streaming", "interrupted", "truncated", "suffix_withheld") if key in payload}
        if current and all(current.get(key) == value for key, value in fields.items()):
            self._pending.pop(identity, None)
            return current
        stamp = datetime.now(UTC).isoformat()
        row = {**fields, "run_id": self.run_id, "kind": "model_reasoning",
               "created_at": current.get("created_at", stamp), "updated_at": stamp,
               "revision": current.get("revision", 0) + 1}
        scope = self.state.get_scope("run", self.run_id)
        with self.state.database.transaction(immediate=True):
            self.state.patch(scope, "model_reasoning", {identity: row}, run_id=self.run_id)
            from .history import RunActivityHistory
            RunActivityHistory(self.state.database, self.run_id).thinking(row)
        self._last_write[identity] = now
        self._pending.pop(identity, None)
        if row.get("streaming") is False:
            self._settled.add(identity)
        return row

    def interrupt(self):
        stored = self.records()
        rows = {**stored, **self._pending}
        for identity, row in rows.items():
            if stored.get(identity, {}).get("streaming") is False:
                continue
            if row.get("streaming") is True:
                self.save({**row, "streaming": False, "interrupted": True}, force=True)

    def page(self, offset: int, limit: int) -> dict:
        rows = sorted(self.records().values(), key=lambda row: (row["created_at"], row["provider_message_id"]))
        items = [{**row, "text": row["text"][:PREVIEW_CHARS], "text_length": len(row["text"]),
                  "preview_truncated": len(row["text"]) > PREVIEW_CHARS} for row in rows[offset:offset + limit]]
        return {"run_id": self.run_id, "items": items, "total": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    def get(self, identity: str) -> dict:
        from .history import stored_record
        row = stored_record(self.state.database, self.run_id, 'model_reasoning', identity)
        if row is None:
            raise NotFoundError("No saved thinking for this model request")
        return {**row, "text_length": len(row["text"]), "preview_truncated": False}
