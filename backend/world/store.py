from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import uuid4

from backend.errors import (
    ConflictError,
    GraphValidationError,
    NotFoundError,
    PluginUnavailableError,
)
from backend.plugins.registry import PluginRegistry
from backend.persistence.database import Database
from backend.world.models import (
    Card,
    CardBatchPatch,
    CardCreate,
    CardPatch,
    Edge,
    EdgeCreate,
    EdgeDirection,
    EdgePatch,
    Size,
)


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")

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
    def __init__(
        self, database: Database, registry: PluginRegistry, *, chunk_size: int = 2048
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.database = database
        self.registry = registry
        self.chunk_size = chunk_size

    def assert_plugin_availability(self) -> None:
        """Fail startup with ownership-aware diagnostics for persisted objects."""

        with self.database.locked() as connection:
            cards = connection.execute(
                "SELECT id, type, plugin_id FROM cards ORDER BY id"
            ).fetchall()
            edges = connection.execute(
                "SELECT id, relationship, plugin_id FROM edges ORDER BY id"
            ).fetchall()
        for row in cards:
            plugin_id = str(row["plugin_id"])
            if not self.registry.has_plugin(plugin_id):
                raise PluginUnavailableError(
                    f"node {row['id']!r} requires unavailable plugin {plugin_id!r} "
                    f"for node type {row['type']!r}"
                )
            try:
                actual = self.registry.node_type_owner_id(str(row["type"]))
            except ValueError as exc:
                raise PluginUnavailableError(
                    f"plugin {plugin_id!r} does not provide persisted node type "
                    f"{row['type']!r} required by node {row['id']!r}"
                ) from exc
            if actual != plugin_id:
                raise PluginUnavailableError(
                    f"node {row['id']!r} records plugin {plugin_id!r}, but node type "
                    f"{row['type']!r} is owned by {actual!r}"
                )
        for row in edges:
            plugin_id = str(row["plugin_id"])
            if not self.registry.has_plugin(plugin_id):
                raise PluginUnavailableError(
                    f"edge {row['id']!r} requires unavailable plugin {plugin_id!r} "
                    f"for relationship {row['relationship']!r}"
                )
            try:
                actual = self.registry.relationship_owner_id(str(row["relationship"]))
            except ValueError as exc:
                raise PluginUnavailableError(
                    f"plugin {plugin_id!r} does not provide persisted relationship "
                    f"{row['relationship']!r} required by edge {row['id']!r}"
                ) from exc
            if actual != plugin_id:
                raise PluginUnavailableError(
                    f"edge {row['id']!r} records plugin {plugin_id!r}, but relationship "
                    f"{row['relationship']!r} is owned by {actual!r}"
                )

    def _chunk(self, coordinate: float) -> int:
        return math.floor(coordinate / self.chunk_size)

    def _validate_config(
        self, card_type: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        return self.registry.validate_config(card_type, value)

    def preview_card(self, request: CardCreate, *, card_id: str | None = None) -> Card:
        """Validate a create request and materialize its node without persistence."""

        resolved_id = _id_or_new(card_id if card_id is not None else request.id)
        now = utc_now()
        definition = self.registry.node_type(request.type)
        self.registry.validate_creation_fields(
            request.type, content=request.content, data_base64=request.data_base64
        )
        if request.status is not None:
            self.registry.validate_status(request.type, request.status)
        size = request.size or Size(
            width=definition.default_size[0], height=definition.default_size[1]
        )
        raw_config = dict(request.config)
        if request.status is not None:
            raw_config["status"] = request.status
        config = self._validate_config(request.type, raw_config)
        return Card(
            id=resolved_id,
            type=request.type,
            name=request.name or definition.default_name,
            position=request.position,
            size=size,
            expanded=request.expanded,
            status=str(config.get("status", definition.default_status)),
            config=config,
            chunk=(self._chunk(request.position.x), self._chunk(request.position.y)),
            created_at=now,
            updated_at=now,
            revision=1,
        )

    def create_card(self, request: CardCreate, *, card_id: str | None = None) -> Card:
        card = self.preview_card(request, card_id=card_id)
        values = (
            card.id,
            card.type,
            self.registry.node_type_owner_id(card.type),
            card.name,
            card.position.x,
            card.position.y,
            card.size.width,
            card.size.height,
            int(card.expanded),
            _json(card.config),
            card.chunk[0],
            card.chunk[1],
            card.created_at.isoformat(),
            card.updated_at.isoformat(),
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """
                    INSERT INTO cards (
                        id, type, plugin_id, name, x, y, width, height, expanded,
                        config_json, chunk_x, chunk_y, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"card {card.id!r} already exists") from exc
        return self.get_card(card.id)

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

    def update_cards(self, updates: Iterable[CardBatchPatch]) -> list[Card]:
        """Validate every patch, then persist the full batch in one transaction."""

        items = list(updates)
        if not items:
            return []
        previews = [
            self.preview_update_card(item.node_id, item.patch) for item in items
        ]
        changed = [
            (item, preview)
            for item, preview in zip(items, previews, strict=True)
            if item.patch.model_dump(exclude_unset=True)
        ]
        if changed:
            with self.database.transaction(immediate=True) as connection:
                for item, preview in changed:
                    cursor = connection.execute(
                        """
                        UPDATE cards
                        SET name = ?, x = ?, y = ?, width = ?, height = ?, expanded = ?,
                            config_json = ?, chunk_x = ?, chunk_y = ?, updated_at = ?,
                            revision = revision + 1
                        WHERE id = ?
                        """,
                        (
                            preview.name,
                            preview.position.x,
                            preview.position.y,
                            preview.size.width,
                            preview.size.height,
                            int(preview.expanded),
                            _json(preview.config),
                            preview.chunk[0],
                            preview.chunk[1],
                            preview.updated_at.isoformat(),
                            item.node_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError(
                            f"card {item.node_id!r} no longer exists"
                        )
        return [self.get_card(item.node_id) for item in items]

    def preview_update_card(self, card_id: str, request: CardPatch) -> Card:
        """Validate an update and return its resulting node without persisting it."""

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
        return current.model_copy(update={
            "name": name,
            "position": position,
            "size": size,
            "expanded": expanded,
            "status": str(config.get("status", current.status)),
            "config": config,
            "chunk": (self._chunk(position.x), self._chunk(position.y)),
            "updated_at": utc_now(),
            "revision": current.revision + 1,
        })

    def delete_card(self, card_id: str) -> Card:
        return self.delete_cards([card_id])[0]

    def delete_cards(self, card_ids: Iterable[str]) -> list[Card]:
        ids = list(dict.fromkeys(card_ids))
        if not ids:
            return []
        cards = [self.get_card(card_id) for card_id in ids]
        placeholders = ",".join("?" for _ in ids)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                f"""
                DELETE FROM state_scopes
                WHERE scope_kind = 'session'
                  AND owner_id IN (
                    SELECT id FROM conversation_sessions
                    WHERE conversation_id IN ({placeholders})
                  )
                """,
                ids,
            )
            cursor = connection.execute(
                f"DELETE FROM cards WHERE id IN ({placeholders})", ids
            )
            if cursor.rowcount != len(ids):
                raise NotFoundError("one or more cards no longer exist")
        return cards

    def create_edge(self, request: EdgeCreate) -> Edge:
        request = self.normalize_edge_request(request)
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
                    str(source_row["type"]), str(target_row["type"]), request.relationship
                )
                self._assert_valid_direction(
                    str(source_row["type"]),
                    str(target_row["type"]),
                    request.relationship,
                    request.direction,
                )
                connection.execute(
                    """
                    INSERT INTO edges (
                        id, source_id, target_id, relationship, plugin_id, direction,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        request.source,
                        request.target,
                        request.relationship,
                        self.registry.relationship_owner_id(request.relationship),
                        request.direction.value,
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

    def list_incident_edges(self, card_ids: Iterable[str]) -> list[Edge]:
        """Return edges touching at least one requested card.

        Chunk snapshots use this form so a cross-chunk edge is retained when
        its endpoints arrive in separate incremental loads.
        """

        ids = list(dict.fromkeys(card_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.database.locked() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM edges
                WHERE source_id IN ({placeholders})
                   OR target_id IN ({placeholders})
                ORDER BY created_at, id
                """,
                [*ids, *ids],
            ).fetchall()
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
            relationship = request.relationship or str(row["relationship"])
            direction = request.direction or EdgeDirection(row["direction"])
            self._assert_valid_relationship(
                str(row["source_type"]),
                str(row["target_type"]),
                relationship,
            )
            self._assert_valid_direction(
                str(row["source_type"]),
                str(row["target_type"]),
                relationship,
                direction,
            )
            connection.execute(
                """
                UPDATE edges
                SET relationship = ?, plugin_id = ?, direction = ?, updated_at = ?,
                    revision = revision + 1
                WHERE id = ?
                """,
                (
                    relationship,
                    self.registry.relationship_owner_id(relationship),
                    direction.value,
                    now,
                    edge_id,
                ),
            )
        return self.get_edge(edge_id)

    def delete_edge(self, edge_id: str) -> Edge:
        edge = self.get_edge(edge_id)
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
            if cursor.rowcount != 1:
                raise NotFoundError(f"edge {edge_id!r} does not exist")
        return edge

    def normalize_edge_request(self, request: EdgeCreate) -> EdgeCreate:
        source = self.get_card(request.source)
        target = self.get_card(request.target)
        _definition, reversed_endpoints = self.registry.resolve_relationship(
            source.type, target.type, request.relationship
        )
        self.registry.validate_direction(request.relationship, request.direction.value)
        if not reversed_endpoints:
            return request
        return request.model_copy(update={"source": request.target, "target": request.source})

    def _assert_valid_status(self, card_type: str, status: str) -> None:
        self.registry.validate_status(card_type, status)

    def _assert_valid_relationship(
        self,
        source_type: str,
        target_type: str,
        relationship: str,
    ) -> None:
        self.registry.validate_relationship_order(source_type, target_type, relationship)

    def _assert_valid_direction(
        self,
        source_type: str,
        target_type: str,
        relationship: str,
        direction: EdgeDirection,
    ) -> None:
        self.registry.validate_relationship_order(source_type, target_type, relationship)
        self.registry.validate_direction(relationship, direction.value)

    def _card_from_row(self, row: sqlite3.Row) -> Card:
        card_type = str(row["type"])
        plugin_id = str(row["plugin_id"])
        if not self.registry.has_plugin(plugin_id):
            raise PluginUnavailableError(
                f"node {row['id']!r} requires unavailable plugin {plugin_id!r} "
                f"for node type {card_type!r}"
            )
        definition = self.registry.node_type(card_type)
        owner_id = self.registry.node_type_owner_id(card_type)
        if owner_id != plugin_id:
            raise PluginUnavailableError(
                f"node {row['id']!r} records plugin {plugin_id!r}, but node type "
                f"{card_type!r} is owned by {owner_id!r}"
            )
        config = json.loads(row["config_json"])
        return Card(
            id=row["id"],
            type=card_type,
            name=row["name"],
            position={"x": row["x"], "y": row["y"]},
            size={"width": row["width"], "height": row["height"]},
            expanded=bool(row["expanded"]),
            status=str(config.get("status", definition.default_status)),
            config=config,
            chunk=(row["chunk_x"], row["chunk_y"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
        )

    def _edge_from_row(self, row: sqlite3.Row) -> Edge:
        relationship = str(row["relationship"])
        plugin_id = str(row["plugin_id"])
        if not self.registry.has_plugin(plugin_id):
            raise PluginUnavailableError(
                f"edge {row['id']!r} requires unavailable plugin {plugin_id!r} "
                f"for relationship {relationship!r}"
            )
        owner_id = self.registry.relationship_owner_id(relationship)
        if owner_id != plugin_id:
            raise PluginUnavailableError(
                f"edge {row['id']!r} records plugin {plugin_id!r}, but relationship "
                f"{relationship!r} is owned by {owner_id!r}"
            )
        return Edge(
            id=row["id"],
            source=row["source_id"],
            target=row["target_id"],
            relationship=relationship,
            direction=EdgeDirection(row["direction"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            revision=row["revision"],
        )
