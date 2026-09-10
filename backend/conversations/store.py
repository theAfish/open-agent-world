from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import ConflictError, ConversationValidationError, NotFoundError
from backend.persistence.database import Database

from .models import ConversationMessage, ConversationMessagePage, ConversationSession, ConversationSessionCreate


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    """Durable room/session/message state; graph authorization stays in services."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(
        self, conversation_id: str, request: ConversationSessionCreate, *, is_default: bool = False
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
            group_id = request.group_id or session_id
            if request.group_id:
                group = connection.execute('SELECT id FROM conversation_groups WHERE id = ? AND conversation_id = ?',
                                           (group_id, conversation_id)).fetchone()
                if group is None:
                    raise NotFoundError("conversation group does not exist in this conversation")
            else:
                group_title = (request.group_title or title).strip()
                if not group_title:
                    raise ConversationValidationError("group title must not be empty")
                connection.execute('INSERT INTO conversation_groups (id, conversation_id, title) VALUES (?, ?, ?)',
                                   (group_id, conversation_id, group_title))
            connection.execute(
                """
                INSERT INTO conversation_sessions (
                    id, conversation_id, title, created_at, updated_at, group_id, auto_title, is_default
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, conversation_id, title, now, now, group_id, title == "New session", is_default),
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
                SELECT s.*, c.name AS conversation_name, g.title AS group_title
                FROM conversation_sessions s
                JOIN cards c ON c.id = s.conversation_id
                JOIN conversation_groups g ON g.id = s.group_id
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
        self.admit_default_participants(conversation_id)
        with self.database.locked() as connection:
            rows = connection.execute(
                """
                SELECT s.*, c.name AS conversation_name, g.title AS group_title
                FROM conversation_sessions s
                JOIN cards c ON c.id = s.conversation_id
                JOIN conversation_groups g ON g.id = s.group_id
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

    def admit_default_participants(self, conversation_id: str) -> None:
        # Remember each connection admission so an explicit kick remains effective.
        # Existing databases are backfilled through the same path as new edges.
        with self.database.transaction(immediate=True) as db:
            session = db.execute('SELECT id FROM conversation_sessions WHERE conversation_id=? AND is_default=1', (conversation_id,)).fetchone()
            if session is None:
                return
            edges = db.execute('''SELECT id, source_id FROM edges WHERE target_id=? AND relationship='participate'
                AND id NOT IN (SELECT edge_id FROM conversation_default_admissions)''', (conversation_id,)).fetchall()
            for edge in edges:
                db.execute('INSERT OR IGNORE INTO conversation_participants VALUES (?,?,?)', (session['id'], edge['source_id'], _now()))
                db.execute('INSERT INTO conversation_default_admissions VALUES (?)', (edge['id'],))
            if edges:
                db.execute('UPDATE conversation_sessions SET revision=revision+1 WHERE id=?', (session['id'],))

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

    def add_participants(
        self, conversation_id: str, session_id: str, participant_ids: list[str]
    ) -> ConversationSession:
        self.get_session(conversation_id, session_id)
        now = _now()
        participants = list(dict.fromkeys(participant_ids))
        with self.database.transaction(immediate=True) as connection:
            for agent_id in participants:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO conversation_participants (
                        session_id, agent_id, joined_at
                    ) VALUES (?, ?, ?)
                    """,
                    (session_id, agent_id, now),
                )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?, revision = revision + 1 WHERE id = ?
                """,
                (now, session_id),
            )
        return self.get_session(conversation_id, session_id)

    def remove_participant(
        self, conversation_id: str, session_id: str, agent_id: str
    ) -> ConversationSession:
        session = self.get_session(conversation_id, session_id)
        if agent_id not in session.participant_ids:
            raise ConversationValidationError(
                f"agent {agent_id!r} is not a participant in session {session_id!r}"
            )
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                DELETE FROM conversation_participants
                WHERE session_id = ? AND agent_id = ?
                """,
                (session_id, agent_id),
            )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?, revision = revision + 1 WHERE id = ?
                """,
                (now, session_id),
            )
        return self.get_session(conversation_id, session_id)

    def delete_session(self, conversation_id: str, session_id: str) -> None:
        self.get_session(conversation_id, session_id)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM conversation_sessions WHERE id = ? AND conversation_id = ?",
                (session_id, conversation_id),
            )

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
        kind: str = "text",
        message_id: str | None = None,
        is_final: bool = True,
        attachments: list | None = None,
    ) -> ConversationMessage:
        value = content.strip()
        if not value and not attachments:
            raise ConversationValidationError("conversation message must not be empty")
        message_id = message_id or str(uuid4())
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
            if connection.execute("SELECT 1 FROM conversation_messages WHERE id = ?", (message_id,)).fetchone():
                raise ConflictError("message ID is already in use")
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    id, conversation_id, session_id, sender_kind, sender_id,
                    sender_name, content, mention_ids_json, run_id, created_at, sequence, kind, is_final, attachments_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    (SELECT COALESCE(MAX(sequence), 0) + 1 FROM conversation_messages WHERE session_id = ?), ?, ?, ?)
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
                    session_id, kind, is_final,
                    json.dumps([item.model_dump() for item in attachments or []]),
                ),
            )
            connection.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?, revision = revision + 1 WHERE id = ?
                """,
                (now, session_id),
            )
            if sender_kind == "user":
                connection.execute("""UPDATE conversation_sessions SET title = ?, auto_title = 0
                    WHERE id = ? AND auto_title = 1""", (" ".join(value.split())[:60] or attachments[0].name[:60], session_id))
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
                    WHERE conversation_id = ? AND session_id = ? AND is_final = 1
                    ORDER BY sequence DESC LIMIT ?
                ) ORDER BY sequence
                """,
                (conversation_id, session_id, max(1, min(limit, 500))),
            ).fetchall()
        return [self._message(row) for row in rows]

    def rename_session(self, conversation_id: str, session_id: str, title: str) -> ConversationSession:
        self.get_session(conversation_id, session_id)
        if not title.strip():
            raise ConversationValidationError("session title must not be empty")
        with self.database.transaction(immediate=True) as connection:
            connection.execute("""UPDATE conversation_sessions SET title = ?, auto_title = 0,
                revision = revision + 1 WHERE id = ?""", (title.strip(), session_id))
        return self.get_session(conversation_id, session_id)

    def finalize_message(self, message_id: str, run_id: str, session_id: str) -> ConversationMessage:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT id FROM conversation_messages WHERE id = ? AND run_id = ? AND session_id = ? AND kind = 'text'",
                                     (message_id, run_id, session_id)).fetchone()
            if row is None:
                raise NotFoundError("run output message does not exist in this session")
            connection.execute('UPDATE conversation_messages SET is_final = 1 WHERE id = ?', (message_id,))
        return self.get_message(message_id)

    def page_messages(self, conversation_id: str, session_id: str, *, before: int | None = None,
                      after: int | None = None, limit: int = 50) -> ConversationMessagePage:
        self.get_session(conversation_id, session_id)
        if before is not None and after is not None:
            raise ConversationValidationError("use either before or after")
        clauses = 'conversation_id = ? AND session_id = ?'
        values: list = [conversation_id, session_id]
        if before is not None:
            clauses += ' AND sequence < ?'
            values.append(before)
        if after is not None:
            clauses += ' AND sequence > ?'
            values.append(after)
        order = 'ASC' if after is not None else 'DESC'
        with self.database.locked() as connection:
            rows = connection.execute(f'SELECT * FROM conversation_messages WHERE {clauses} ORDER BY sequence {order} LIMIT ?',
                                      (*values, max(1, min(limit, 100)))).fetchall()
            if order == 'DESC':
                rows = list(reversed(rows))
            low = rows[0]['sequence'] if rows else (after or before or 0)
            high = rows[-1]['sequence'] if rows else (after or before or 0)
            has_before = connection.execute('SELECT 1 FROM conversation_messages WHERE session_id = ? AND sequence < ? LIMIT 1', (session_id, low)).fetchone() is not None
            has_after = connection.execute('SELECT 1 FROM conversation_messages WHERE session_id = ? AND sequence > ? LIMIT 1', (session_id, high)).fetchone() is not None
            active = connection.execute("""SELECT DISTINCT agent_id FROM runs WHERE
                caller_kind = 'conversation' AND caller_id = ? AND context_id = ?
                AND status IN ('created', 'running', 'waiting')""", (conversation_id, session_id)).fetchall()
        return ConversationMessagePage(items=[self._message(row) for row in rows], has_before=has_before, has_after=has_after,
                                       active_agent_ids=[str(row['agent_id']) for row in active])

    @staticmethod
    def _session(row: object, participants: list[str]) -> ConversationSession:
        return ConversationSession(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            conversation_name=str(row["conversation_name"]),
            title=str(row["title"]),
            group_id=str(row["group_id"]),
            group_title=str(row["group_title"]),
            auto_title=bool(row["auto_title"]),
            is_default=bool(row["is_default"]),
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
            attachments=json.loads(row["attachments_json"]),
            sequence=int(row["sequence"]),
            kind=str(row["kind"]),
            is_final=bool(row["is_final"]),
            mention_agent_ids=list(json.loads(str(row["mention_ids_json"]))),
            run_id=None if row["run_id"] is None else str(row["run_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
