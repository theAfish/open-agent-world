from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import ConversationValidationError, NotFoundError
from backend.persistence.database import Database

from .models import ConversationMessage, ConversationSession, ConversationSessionCreate


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    """Durable room/session/message state; graph authorization stays in services."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(
        self, conversation_id: str, request: ConversationSessionCreate
    ) -> ConversationSession:
        session_id = str(uuid4())
        now = _now()
        participants = list(dict.fromkeys(request.participant_ids))
        title = request.title.strip()
        if not title:
            raise ConversationValidationError("conversation session title must not be empty")
        with self.database.transaction(immediate=True) as connection:
            card = connection.execute(
                "SELECT type FROM cards WHERE id = ?", (conversation_id,)
            ).fetchone()
            if card is None or str(card["type"]) != "conversation":
                raise NotFoundError(
                    f"conversation card {conversation_id!r} does not exist"
                )
            connection.execute(
                """
                INSERT INTO conversation_sessions (
                    id, conversation_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, conversation_id, title, now, now),
            )
            for agent_id in participants:
                connection.execute(
                    """
                    INSERT INTO conversation_participants (session_id, agent_id, joined_at)
                    VALUES (?, ?, ?)
                    """,
                    (session_id, agent_id, now),
                )
        return self.get_session(conversation_id, session_id)

    def get_session(self, conversation_id: str, session_id: str) -> ConversationSession:
        with self.database.locked() as connection:
            row = connection.execute(
                """
                SELECT s.*, c.name AS conversation_name
                FROM conversation_sessions s
                JOIN cards c ON c.id = s.conversation_id
                WHERE s.id = ? AND s.conversation_id = ?
                """,
                (session_id, conversation_id),
            ).fetchone()
            participants = connection.execute(
                """
                SELECT agent_id FROM conversation_participants
                WHERE session_id = ? ORDER BY joined_at, agent_id
                """,
                (session_id,),
            ).fetchall()
        if row is None:
            raise NotFoundError(
                f"session {session_id!r} does not exist in conversation {conversation_id!r}"
            )
        return self._session(row, [str(item["agent_id"]) for item in participants])

    def list_sessions(self, conversation_id: str) -> list[ConversationSession]:
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT s.*, c.name AS conversation_name
                FROM conversation_sessions s
                JOIN cards c ON c.id = s.conversation_id
                WHERE s.conversation_id = ? ORDER BY s.updated_at DESC, s.id
                """,
                (conversation_id,),
            ).fetchall()
            participant_rows = connection.execute(
                """
                SELECT p.session_id, p.agent_id
                FROM conversation_participants p
                JOIN conversation_sessions s ON s.id = p.session_id
                WHERE s.conversation_id = ?
                ORDER BY p.joined_at, p.agent_id
                """,
                (conversation_id,),
            ).fetchall()
        by_session: dict[str, list[str]] = {}
        for item in participant_rows:
            by_session.setdefault(str(item["session_id"]), []).append(str(item["agent_id"]))
        return [self._session(row, by_session.get(str(row["id"]), [])) for row in rows]

    def list_agent_sessions(self, agent_id: str) -> list[ConversationSession]:
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM conversation_sessions s
                JOIN conversation_participants p ON p.session_id = s.id
                WHERE p.agent_id = ? ORDER BY s.updated_at DESC, s.id
                """,
                (agent_id,),
            ).fetchall()
        return [self.get_session(str(row["conversation_id"]), str(row["id"])) for row in rows]

    def add_message(
        self,
        conversation_id: str,
        session_id: str,
        *,
        sender_kind: str,
        sender_id: str | None,
        sender_name: str,
        content: str,
        mention_agent_ids: list[str] | None = None,
        run_id: str | None = None,
    ) -> ConversationMessage:
        value = content.strip()
        if not value:
            raise ConversationValidationError("conversation message must not be empty")
        message_id = str(uuid4())
        now = _now()
        mentions = list(dict.fromkeys(mention_agent_ids or []))
        with self.database.transaction(immediate=True) as connection:
            session = connection.execute(
                """
                SELECT id FROM conversation_sessions
                WHERE id = ? AND conversation_id = ?
                """,
                (session_id, conversation_id),
            ).fetchone()
            if session is None:
                raise NotFoundError(
                    f"session {session_id!r} does not exist in conversation {conversation_id!r}"
                )
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    id, conversation_id, session_id, sender_kind, sender_id,
                    sender_name, content, mention_ids_json, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    conversation_id,
                    session_id,
                    sender_kind,
                    sender_id,
                    sender_name,
                    value,
                    json.dumps(mentions, separators=(",", ":")),
                    run_id,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?, revision = revision + 1 WHERE id = ?
                """,
                (now, session_id),
            )
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> ConversationMessage:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_messages WHERE id = ?", (message_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"conversation message {message_id!r} does not exist")
        return self._message(row)

    def list_messages(
        self, conversation_id: str, session_id: str, *, limit: int = 200
    ) -> list[ConversationMessage]:
        self.get_session(conversation_id, session_id)
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM conversation_messages
                    WHERE conversation_id = ? AND session_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                ) ORDER BY created_at, id
                """,
                (conversation_id, session_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._message(row) for row in rows]

    @staticmethod
    def _session(row: object, participants: list[str]) -> ConversationSession:
        return ConversationSession(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            conversation_name=str(row["conversation_name"]),
            title=str(row["title"]),
            participant_ids=participants,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _message(row: object) -> ConversationMessage:
        return ConversationMessage(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            session_id=str(row["session_id"]),
            sender_kind=str(row["sender_kind"]),
            sender_id=None if row["sender_id"] is None else str(row["sender_id"]),
            sender_name=str(row["sender_name"]),
            content=str(row["content"]),
            mention_agent_ids=list(json.loads(str(row["mention_ids_json"]))),
            run_id=None if row["run_id"] is None else str(row["run_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
