from __future__ import annotations

from datetime import UTC, datetime
from backend.persistence.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationDeliveryStore:
    """Durable per-Agent delivery ledger for Conversation user messages."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def next_batch(self, agent_id: str) -> tuple[str, str, list[str], int] | None:
        """Return the oldest queued Agent+session burst without mutating it."""
        with self.database.locked() as db:
            first = db.execute(
                """SELECT conversation_id, session_id FROM conversation_deliveries
                WHERE agent_id=? AND status='queued' ORDER BY id LIMIT 1""",
                (agent_id,),
            ).fetchone()
            if first is None:
                return None
            rows = db.execute(
                """SELECT d.message_id, m.sequence FROM conversation_deliveries d
                JOIN conversation_messages m ON m.id=d.message_id
                WHERE d.agent_id=? AND d.conversation_id=? AND d.session_id=?
                AND d.status='queued' ORDER BY d.id""",
                (agent_id, first["conversation_id"], first["session_id"]),
            ).fetchall()
        if not rows:
            return None
        return (
            str(first["conversation_id"]),
            str(first["session_id"]),
            [str(row["message_id"]) for row in rows],
            max(int(row["sequence"]) for row in rows),
        )

    def all_queued_agents(self) -> list[str]:
        with self.database.locked() as db:
            rows = db.execute(
                """SELECT DISTINCT agent_id FROM conversation_deliveries
                WHERE status='queued' ORDER BY agent_id"""
            ).fetchall()
        return [str(row["agent_id"]) for row in rows]

    def claim_batch(
        self,
        conversation_id: str,
        session_id: str,
        agent_id: str,
        run_id: str,
        message_ids: list[str],
    ) -> list[str]:
        """Atomically claim exactly the burst selected for the next Run."""
        if not message_ids:
            return []
        message_placeholders = ",".join("?" for _ in message_ids)
        with self.database.transaction(immediate=True) as db:
            rows = db.execute(
                f"""SELECT id, message_id FROM conversation_deliveries
                WHERE conversation_id=? AND session_id=? AND agent_id=? AND status='queued'
                AND message_id IN ({message_placeholders}) ORDER BY id""",
                (conversation_id, session_id, agent_id, *message_ids),
            ).fetchall()
            if not rows:
                return []
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            db.execute(
                f"""UPDATE conversation_deliveries
                SET status='claimed', claimed_run_id=?, claimed_at=?
                WHERE id IN ({placeholders}) AND status='queued'""",
                (run_id, _now(), *ids),
            )
            claimed = db.execute(
                f"""SELECT message_id FROM conversation_deliveries
                WHERE id IN ({placeholders}) AND status='claimed' AND claimed_run_id=?
                ORDER BY id""",
                (*ids, run_id),
            ).fetchall()
        return [str(row["message_id"]) for row in claimed]

    def mark_run_done(self, run_id: str) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """UPDATE conversation_deliveries
                SET status='done', completed_at=?
                WHERE status='claimed' AND claimed_run_id=?""",
                (_now(), run_id),
            )

    def requeue_run(self, run_id: str) -> None:
        with self.database.transaction(immediate=True) as db:
            db.execute(
                """UPDATE conversation_deliveries
                SET status='queued', claimed_run_id=NULL, claimed_at=NULL
                WHERE status='claimed' AND claimed_run_id=?""",
                (run_id,),
            )

    def recover_after_restart(self) -> None:
        """Reconcile claims after RunManager has interrupted incomplete Runs."""
        with self.database.transaction(immediate=True) as db:
            # No Run row means the process died after claim but before
            # RunStore.create(); interrupted work is safe to retry.
            db.execute(
                """UPDATE conversation_deliveries
                SET status='queued', claimed_run_id=NULL, claimed_at=NULL
                WHERE status='claimed' AND (
                    claimed_run_id IS NULL
                    OR NOT EXISTS (
                        SELECT 1 FROM runs WHERE runs.run_id=conversation_deliveries.claimed_run_id
                    )
                    OR claimed_run_id IN (
                        SELECT run_id FROM runs WHERE status='interrupted'
                    )
                )"""
            )
            # A terminal attempt other than restart interruption already
            # consumed its input. Do not replay failed/cancelled/succeeded work.
            db.execute(
                """UPDATE conversation_deliveries
                SET status='done', completed_at=?
                WHERE status='claimed' AND claimed_run_id IN (
                    SELECT run_id FROM runs
                    WHERE status IN ('succeeded','failed','cancelled')
                )""",
                (_now(),),
            )

