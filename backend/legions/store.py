from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import ConflictError, NotFoundError, ResourceValidationError
from backend.legions.models import LegionBlueprint, LegionRecord
from backend.persistence.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LegionStore:
    MAX_BLUEPRINT_BYTES = 64 * 1024 * 1024

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self, name: str, description: str, blueprint: LegionBlueprint
    ) -> LegionRecord:
        legion_id = str(uuid4())
        now = _now()
        serialized = blueprint.model_dump_json()
        if len(serialized.encode("utf-8")) > self.MAX_BLUEPRINT_BYTES:
            raise ResourceValidationError(
                "Legion blueprint exceeds the 64 MiB portable-state limit"
            )
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO legions (
                        id, name, description, blueprint_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        legion_id,
                        name,
                        description,
                        serialized,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - UUID collision
            raise ConflictError(f"legion {legion_id!r} already exists") from exc
        return self.get(legion_id)

    def list(self) -> list[LegionRecord]:
        with self.database.locked() as connection:
            rows = connection.execute(
                "SELECT * FROM legions ORDER BY created_at, id"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, legion_id: str) -> LegionRecord:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM legions WHERE id = ?", (legion_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"legion {legion_id!r} does not exist")
        return self._from_row(row)

    def delete(self, legion_id: str) -> LegionRecord:
        record = self.get(legion_id)
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM legions WHERE id = ?", (legion_id,))
            if cursor.rowcount != 1:
                raise NotFoundError(f"legion {legion_id!r} does not exist")
        return record

    @staticmethod
    def _from_row(row: sqlite3.Row) -> LegionRecord:
        return LegionRecord(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            blueprint=LegionBlueprint.model_validate_json(row["blueprint_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
        )
