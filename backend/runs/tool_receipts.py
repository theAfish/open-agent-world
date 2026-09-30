"""Durable, privacy-bounded receipts for Agent capability invocations.

A receipt records what OAW knows, not a claim of exactly-once execution. A
process loss or exception between dispatch and confirmation remains unknown.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


_HINT_KEYS = frozenset({
    "operation_id", "command_id", "revision", "document_revision", "sha256",
    "written", "path", "file_name", "sandbox_id", "structure_digest",
    "exit_code", "status", "ok",
})
_current_receipt: ContextVar[str | None] = ContextVar("current_tool_receipt", default=None)


def current_tool_receipt_id() -> str | None:
    return _current_receipt.get()


@contextmanager
def bind_tool_receipt(receipt_id: str | None):
    token = _current_receipt.set(receipt_id)
    try:
        yield
    finally:
        _current_receipt.reset(token)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: Any) -> str:
    try:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = f"<{type(value).__name__}>"
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _result_hints(result: Any) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    hints: dict[str, Any] = {}
    for key in _HINT_KEYS:
        value = result.get(key)
        if isinstance(value, bool) or isinstance(value, int):
            hints[key] = value
        elif isinstance(value, str) and len(value) <= 256:
            hints[key] = value
    return hints


class RunToolReceipts:
    def __init__(self, database: Any) -> None:
        self.database = database

    def begin(self, *, run_id: str, agent_id: str, capability_kind: str,
              target_id: str, arguments: Mapping[str, Any], read_only: bool) -> str:
        receipt_id = uuid4().hex
        with self.database.transaction(immediate=True) as db:
            db.execute("""INSERT INTO run_tool_receipts (
                receipt_id, run_id, agent_id, capability_kind, target_id,
                argument_sha256, read_only, state, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)""", (
                receipt_id, run_id, agent_id, capability_kind, target_id,
                _digest(arguments), int(read_only), _now(),
            ))
        return receipt_id

    def finish(self, receipt_id: str, result: Any) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute("""UPDATE run_tool_receipts SET state='finished',
                result_sha256=?, result_hints_json=?, finished_at=?
                WHERE receipt_id=? AND state='running'""", (
                _digest(result), json.dumps(_result_hints(result)), _now(), receipt_id,
            ))

    def uncertain(self, receipt_id: str, error: BaseException) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute("""UPDATE run_tool_receipts SET state='outcome_unknown',
                error_type=?, finished_at=? WHERE receipt_id=? AND state='running'""", (
                type(error).__name__, _now(), receipt_id,
            ))

    def interrupt_run(self, run_id: str) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute("""UPDATE run_tool_receipts SET state='outcome_unknown',
                error_type='BackendRestart', finished_at=?
                WHERE run_id=? AND state='running'""", (_now(), run_id))

    def list_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.locked() as db:
            rows = db.execute("""SELECT * FROM run_tool_receipts WHERE run_id=?
                ORDER BY started_at, receipt_id""", (run_id,)).fetchall()
        return [{**dict(row), "read_only": bool(row["read_only"]),
                 "result_hints": json.loads(row["result_hints_json"] or "{}")}
                for row in rows]

    def recovery_classification(self, run_id: str) -> str:
        """Only a fully read-only history is safe to replay without inspection.

        A returned write result does not prove the current external state;
        recovery.py must independently confirm each effect before callers may
        report completed_effects.
        """
        receipts = self.list_run(run_id)
        return "reconcile_required" if any(not item["read_only"] for item in receipts) else "read_only"
