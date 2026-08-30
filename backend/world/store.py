from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import uuid4

from pydantic import ValidationError

from backend.errors import ConflictError, GraphValidationError, NotFoundError
from backend.persistence.database import Database
from backend.world.models import (
    AgentConfig,
    Card,
    CardCreate,
    CardPatch,
    CardType,
    Edge,
    EdgeCreate,
    EdgePatch,
    ImageConfig,
    Relationship,
    SandboxConfig,
    Size,
    TextConfig,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")

_RELATIONSHIPS: dict[tuple[CardType, CardType], frozenset[Relationship]] = {
    (CardType.AGENT, CardType.TEXT): frozenset(
        {Relationship.READ, Relationship.READ_EDIT}
    ),
    (CardType.AGENT, CardType.IMAGE): frozenset({Relationship.VIEW}),
    (CardType.AGENT, CardType.SANDBOX): frozenset({Relationship.EXECUTE}),
    (CardType.TEXT, CardType.SANDBOX): frozenset(
        {Relationship.MOUNT_READ_ONLY, Relationship.MOUNT_READ_WRITE}
    ),
    (CardType.IMAGE, CardType.SANDBOX): frozenset(
        {Relationship.MOUNT_READ_ONLY}
    ),
}

_DEFAULT_NAMES = {
    CardType.AGENT: "New Agent",
    CardType.TEXT: "Untitled Text",
    CardType.IMAGE: "Untitled Image",
    CardType.SANDBOX: "New Sandbox",
}

_DEFAULT_SIZES = {
    CardType.AGENT: Size(width=300, height=190),
    CardType.TEXT: Size(width=300, height=220),
    CardType.IMAGE: Size(width=280, height=240),
    CardType.SANDBOX: Size(width=340, height=220),
}

_CONFIG_MODELS = {
    CardType.AGENT: AgentConfig,
    CardType.TEXT: TextConfig,
    CardType.IMAGE: ImageConfig,
    CardType.SANDBOX: SandboxConfig,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _id_or_new(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    if not _SAFE_ID.fullmatch(value):
        raise GraphValidationError(
            "ids must be 1-100 URL-safe letters, numbers, underscores, or hyphens"
        )
    return value


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class WorldStore:
    def __init__(self, database: Database, *, chunk_size: int = 2048) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.database = database
        self.chunk_size = chunk_size

    def _chunk(self, coordinate: float) -> int:
        return math.floor(coordinate / self.chunk_size)

    def _validate_config(
        self, card_type: CardType, value: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            model = _CONFIG_MODELS[card_type].model_validate(value)
        except ValidationError as exc:
            raise GraphValidationError(f"invalid {card_type.value} configuration: {exc}") from exc
        return model.model_dump(mode="json")

    def create_card(self, request: CardCreate) -> Card:
        card_id = _id_or_new(request.id)
        now = utc_now().isoformat()
        size = request.size or _DEFAULT_SIZES[request.type]
        raw_config = dict(request.config)
        if request.status is not None:
            raw_config["status"] = request.status
        config = self._validate_config(request.type, raw_config)
        values = (
            card_id,
            request.type.value,
            request.name or _DEFAULT_NAMES[request.type],
            request.position.x,
            request.position.y,
            size.width,
            size.height,
            int(request.expanded),
            _json(config),
            self._chunk(request.position.x),
            self._chunk(request.position.y),
            now,
            now,
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO cards (
                        id, type, name, x, y, width, height, expanded,
                        config_json, chunk_x, chunk_y, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"card {card_id!r} already exists") from exc
        return self.get_card(card_id)

    def get_card(self, card_id: str) -> Card:
        with self.database.locked() as connection:
            row = connection.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"card {card_id!r} does not exist")
        return self._card_from_row(row)

    def maybe_get_card(self, card_id: str) -> Card | None:
        with self.database.locked() as connection:
            row = connection.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return None if row is None else self._card_from_row(row)

    def list_cards(
        self, chunks: Iterable[tuple[int, int]] | None = None
    ) -> list[Card]:
        chunk_list = list(dict.fromkeys(chunks)) if chunks is not None else None
        if chunk_list == []:
            return []
        sql = "SELECT * FROM cards"
        parameters: list[int] = []
        if chunk_list is not None:
            clauses = []
            for x, y in chunk_list:
                clauses.append("(chunk_x = ? AND chunk_y = ?)")
                parameters.extend((x, y))
            sql += " WHERE " + " OR ".join(clauses)
        sql += " ORDER BY created_at, id"
        with self.database.locked() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._card_from_row(row) for row in rows]

    def update_card(self, card_id: str, request: CardPatch) -> Card:
        current = self.get_card(card_id)
        changes = request.model_dump(exclude_unset=True)
        if not changes:
            return current

        name = changes.get("name") or current.name
        position = request.position or current.position
        size = request.size or current.size
        expanded = current.expanded if request.expanded is None else request.expanded
        config = current.config
        if request.config is not None:
            config = {**config, **request.config}
        if request.status is not None:
            self._assert_valid_status(current.type, request.status)
            config = {**config, "status": request.status}
        config = self._validate_config(current.type, config)
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE cards
                SET name = ?, x = ?, y = ?, width = ?, height = ?, expanded = ?,
                    config_json = ?, chunk_x = ?, chunk_y = ?, updated_at = ?,
                    revision = revision + 1
                WHERE id = ?
                """,
                (
                    name,
                    position.x,
                    position.y,
                    size.width,
                    size.height,
                    int(expanded),
                    _json(config),
                    self._chunk(position.x),
                    self._chunk(position.y),
                    now,
                    card_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"card {card_id!r} does not exist")
        return self.get_card(card_id)

    def delete_card(self, card_id: str) -> Card:
        card = self.get_card(card_id)
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            if cursor.rowcount != 1:
                raise NotFoundError(f"card {card_id!r} does not exist")
        return card

    def create_edge(self, request: EdgeCreate) -> Edge:
        edge_id = _id_or_new(request.id)
        now = utc_now().isoformat()
        try:
            with self.database.transaction(immediate=True) as connection:
                source_row = connection.execute(
                    "SELECT type FROM cards WHERE id = ?", (request.source,)
                ).fetchone()
                target_row = connection.execute(
                    "SELECT type FROM cards WHERE id = ?", (request.target,)
                ).fetchone()
                if source_row is None:
                    raise NotFoundError(f"source card {request.source!r} does not exist")
                if target_row is None:
                    raise NotFoundError(f"target card {request.target!r} does not exist")
                self._assert_valid_relationship(
                    CardType(source_row["type"]),
                    CardType(target_row["type"]),
                    request.relationship,
                )
                connection.execute(
                    """
                    INSERT INTO edges (
                        id, source_id, target_id, relationship, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        request.source,
                        request.target,
                        request.relationship.value,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "an edge with that id or source/target pair already exists"
            ) from exc
        return self.get_edge(edge_id)

    def get_edge(self, edge_id: str) -> Edge:
        with self.database.locked() as connection:
            row = connection.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"edge {edge_id!r} does not exist")
        return self._edge_from_row(row)

    def find_edge(self, source_id: str, target_id: str) -> Edge | None:
        with self.database.locked() as connection:
            row = connection.execute(
                "SELECT * FROM edges WHERE source_id = ? AND target_id = ?",
                (source_id, target_id),
            ).fetchone()
        return None if row is None else self._edge_from_row(row)

    def list_edges(self, card_ids: Iterable[str] | None = None) -> list[Edge]:
        ids = list(dict.fromkeys(card_ids)) if card_ids is not None else None
        if ids == []:
            return []
        sql = "SELECT * FROM edges"
        parameters: list[str] = []
        if ids is not None:
            placeholders = ",".join("?" for _ in ids)
            # Only complete edges are returned; the frontend never receives an
            # edge pointing to an unloaded React Flow node.
            sql += (
                f" WHERE source_id IN ({placeholders})"
                f" AND target_id IN ({placeholders})"
            )
            parameters.extend(ids)
            parameters.extend(ids)
        sql += " ORDER BY created_at, id"
        with self.database.locked() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def list_edges_from(self, source_id: str) -> list[Edge]:
        with self.database.locked() as connection:
            rows = connection.execute(
                "SELECT * FROM edges WHERE source_id = ? ORDER BY created_at, id",
                (source_id,),
            ).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def list_edges_to(self, target_id: str) -> list[Edge]:
        with self.database.locked() as connection:
            rows = connection.execute(
                "SELECT * FROM edges WHERE target_id = ? ORDER BY created_at, id",
                (target_id,),
            ).fetchall()
        return [self._edge_from_row(row) for row in rows]

    def update_edge(self, edge_id: str, request: EdgePatch) -> Edge:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT e.*, source.type AS source_type, target.type AS target_type
                FROM edges e
                JOIN cards source ON source.id = e.source_id
                JOIN cards target ON target.id = e.target_id
                WHERE e.id = ?
                """,
                (edge_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"edge {edge_id!r} does not exist")
            self._assert_valid_relationship(
                CardType(row["source_type"]),
                CardType(row["target_type"]),
                request.relationship,
            )
            connection.execute(
                """
                UPDATE edges
                SET relationship = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (request.relationship.value, now, edge_id),
            )
        return self.get_edge(edge_id)

    def delete_edge(self, edge_id: str) -> Edge:
        edge = self.get_edge(edge_id)
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
            if cursor.rowcount != 1:
                raise NotFoundError(f"edge {edge_id!r} does not exist")
        return edge

    @staticmethod
    def _assert_valid_status(card_type: CardType, status: str) -> None:
        allowed = {
            CardType.AGENT: {"idle", "running", "waiting", "error"},
            CardType.SANDBOX: {"stopped", "ready", "running", "error"},
            CardType.TEXT: {"available", "modified"},
            CardType.IMAGE: {"available", "modified"},
        }[card_type]
        if status not in allowed:
            raise GraphValidationError(
                f"status {status!r} is not valid for {card_type.value}"
            )

    @staticmethod
    def _assert_valid_relationship(
        source_type: CardType,
        target_type: CardType,
        relationship: Relationship,
    ) -> None:
        allowed = _RELATIONSHIPS.get((source_type, target_type), frozenset())
        if relationship not in allowed:
            allowed_text = ", ".join(sorted(item.value for item in allowed)) or "none"
            raise GraphValidationError(
                f"{source_type.value} -> {target_type.value} does not allow "
                f"{relationship.value!r}; allowed relationships: {allowed_text}"
            )

    @staticmethod
    def _card_from_row(row: sqlite3.Row) -> Card:
        card_type = CardType(row["type"])
        config = json.loads(row["config_json"])
        default_status = (
            "idle"
            if card_type is CardType.AGENT
            else "stopped"
            if card_type is CardType.SANDBOX
            else "available"
        )
        return Card(
            id=row["id"],
            type=card_type,
            name=row["name"],
            position={"x": row["x"], "y": row["y"]},
            size={"width": row["width"], "height": row["height"]},
            expanded=bool(row["expanded"]),
            status=str(config.get("status", default_status)),
            config=config,
            chunk=(row["chunk_x"], row["chunk_y"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
        )

    @staticmethod
    def _edge_from_row(row: sqlite3.Row) -> Edge:
        return Edge(
            id=row["id"],
            source=row["source_id"],
            target=row["target_id"],
            relationship=Relationship(row["relationship"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
        )
