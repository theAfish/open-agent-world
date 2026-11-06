from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

from backend.errors import NotFoundError
from backend.persistence.database import Database

from .models import RunRecord, RunStatus, TERMINAL_RUN_STATUSES
from .submissions import RunSubmission


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunStore:
    """Durable Run metadata; provider events are never the source of truth."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        agent_id: str,
        run_id: str | None = None,
        runtime_provider_id: str,
        caller_kind: str,
        caller_id: str | None = None,
        parent_run_id: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> RunRecord:
        run_id = run_id or str(uuid4())
        root_run_id = run_id
        if parent_run_id is not None:
            parent = self.get(parent_run_id)
            root_run_id = parent.root_run_id
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, agent_id, parent_run_id, root_run_id, task_id,
                    caller_kind, caller_id, context_id, runtime_provider_id,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, agent_id, parent_run_id, root_run_id, task_id,
                    caller_kind, caller_id, context_id, runtime_provider_id,
                    RunStatus.CREATED.value, now, now,
                ),
            )
        return self.get(run_id)

    def get(self, run_id: str) -> RunRecord:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"run {run_id!r} does not exist")
        return self._record(row)

    def enqueue(self, run_id: str, submission: RunSubmission) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO run_submissions (run_id, state, input_json) VALUES (?, 'pending', ?)",
                (run_id, submission.to_json()),
            )

    def submission(self, run_id: str) -> RunSubmission | None:
        with self.database.locked() as connection:
            row = connection.execute("SELECT input_json FROM run_submissions WHERE run_id=?", (run_id,)).fetchone()
        return RunSubmission.from_json(row["input_json"]) if row is not None else None

    def pending_submissions(self) -> list[str]:
        with self.database.locked() as connection:
            return [row["run_id"] for row in connection.execute(
                "SELECT run_id FROM run_submissions WHERE state='pending' ORDER BY rowid"
            )]

    def pending_count(self) -> int:
        with self.database.locked() as connection:
            return connection.execute("SELECT COUNT(*) FROM run_submissions WHERE state='pending'").fetchone()[0]

    def is_pending(self, run_id: str) -> bool:
        with self.database.locked() as connection:
            return connection.execute(
                "SELECT 1 FROM run_submissions WHERE run_id=? AND state='pending'", (run_id,)
            ).fetchone() is not None

    def has_pending_for_agent(self, agent_id: str) -> bool:
        with self.database.locked() as connection:
            return connection.execute(
                """SELECT 1 FROM run_submissions JOIN runs USING (run_id)
                WHERE run_submissions.state='pending' AND runs.agent_id=? LIMIT 1""", (agent_id,),
            ).fetchone() is not None

    def claim_submission(self, run_id: str) -> bool:
        # Once claimed, this attempt is never eligible for automatic dispatch again.
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                """UPDATE run_submissions SET state='claimed' WHERE run_id=? AND state='pending'
                AND EXISTS (SELECT 1 FROM runs WHERE runs.run_id=run_submissions.run_id AND status='created')""",
                (run_id,),
            )
            return result.rowcount == 1

    def finish_pending(
        self, run_id: str, status: RunStatus, *, error: str | None = None,
    ) -> RunRecord | None:
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                """UPDATE run_submissions SET state='closed' WHERE run_id=? AND state='pending'
                AND EXISTS (SELECT 1 FROM runs WHERE runs.run_id=run_submissions.run_id AND status='created')""",
                (run_id,),
            )
            if result.rowcount != 1:
                return None
            self.update_status(run_id, status, error=error)
            return self.update_lifecycle(run_id, execution='finished', holds_capacity=False, cleanup='none',
                                         cancellation_requested=status is RunStatus.CANCELLED,
                                         cancellation_origin='user' if status is RunStatus.CANCELLED else None)

    def list(
        self, *, agent_id: str | None = None, task_id: str | None = None
    ) -> list[RunRecord]:
        clauses: list[str] = []
        values: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            values.append(agent_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            values.append(task_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.locked() as connection:
            # Creation timestamps can tie; UUID ordering must never reverse
            # sends or select an older Run as the latest execution.
            rows = connection.execute(
                f"SELECT * FROM runs{where} ORDER BY created_at, rowid", values
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_children(self, parent_run_id: str) -> list[RunRecord]:
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs WHERE parent_run_id = ?
                ORDER BY created_at, rowid
                """,
                (parent_run_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def update_status(
        self, run_id: str, status: RunStatus, *, error: str | None = None
    ) -> RunRecord:
        current = self.get(run_id)
        now = _now()
        started_at = current.started_at
        if status is RunStatus.RUNNING and started_at is None:
            started_at = datetime.fromisoformat(now)
        finished_at = datetime.fromisoformat(now) if status in TERMINAL_RUN_STATUSES else None
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, started_at = ?, updated_at = ?, finished_at = ?, error = ?
                WHERE run_id = ?
                """,
                (
                    status.value,
                    started_at.isoformat() if started_at is not None else None,
                    now,
                    finished_at.isoformat() if finished_at is not None else None,
                    error,
                    run_id,
                ),
            )
        return self.get(run_id)

    def interrupt_incomplete(self) -> list[RunRecord]:
        """Mark attempts left active by an abnormal process stop as interrupted."""

        now = _now()
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT run_id FROM runs
                WHERE status IN ('created', 'running', 'waiting') AND NOT (
                    status='created' AND EXISTS (
                        SELECT 1 FROM run_submissions WHERE run_submissions.run_id=runs.run_id AND state='pending'
                    )
                )
                """
            ).fetchall()
            connection.execute(
                """
                UPDATE runs
                SET status = 'interrupted', updated_at = ?, finished_at = ?,
                    error = COALESCE(error, 'backend stopped before the Run reached a terminal state')
                WHERE status IN ('created', 'running', 'waiting') AND NOT (
                    status='created' AND EXISTS (
                        SELECT 1 FROM run_submissions WHERE run_submissions.run_id=runs.run_id AND state='pending'
                    )
                )
                """,
                (now, now),
            )
        return [self.get(str(row["run_id"])) for row in rows]

    def update_lifecycle(self, run_id: str, **changes) -> RunRecord:
        with self.database.transaction(immediate=True) as connection:
            current = self.get(run_id)
            value = {**current.lifecycle, **changes}
            connection.execute('UPDATE runs SET lifecycle_json = ?, updated_at = ? WHERE run_id = ?',
                               (json.dumps(value), _now(), run_id))
        return self.get(run_id)

    @staticmethod
    def _record(row: object) -> RunRecord:
        return RunRecord(
            run_id=str(row["run_id"]),
            agent_id=str(row["agent_id"]),
            parent_run_id=row["parent_run_id"],
            root_run_id=str(row["root_run_id"]),
            task_id=row["task_id"],
            caller_kind=str(row["caller_kind"]),
            caller_id=row["caller_id"],
            context_id=row["context_id"],
            runtime_provider_id=str(row["runtime_provider_id"]),
            status=RunStatus(str(row["status"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            started_at=(
                datetime.fromisoformat(str(row["started_at"]))
                if row["started_at"] is not None else None
            ),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None else None
            ),
            error=row["error"],
            lifecycle=json.loads(row['lifecycle_json']),
        )
