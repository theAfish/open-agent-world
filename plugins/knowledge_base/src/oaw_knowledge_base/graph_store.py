"""SQL-backed GraphStore so an approved knowledge graph survives a restart.

MKB's portable client defaults to ``InMemoryGraphStore``, which loses every entity
and relation when the client closes. This adapter satisfies ``mkb.ports.GraphStore``
over two plugin-owned tables inside the same SQLite file as the rest of the card, so
the whole knowledge base stays a single SQL database with no external service.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Index, MetaData, String, Table, Text,
    delete, func, insert, select, update,
)

METADATA = MetaData()

ENTITIES = Table(
    "oaw_kg_entity", METADATA,
    Column("id", String(36), primary_key=True),
    Column("type", String(255), nullable=False),
    Column("name", String(1024), nullable=False),
    Column("properties", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

RELATIONS = Table(
    "oaw_kg_relation", METADATA,
    Column("id", String(36), primary_key=True),
    Column("source_id", String(36), nullable=False),
    Column("target_id", String(36), nullable=False),
    Column("type", String(255), nullable=False),
    Column("properties", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_oaw_kg_relation_source", "source_id"),
    Index("ix_oaw_kg_relation_target", "target_id"),
)


def _aware(value):
    # SQLite returns naive datetimes; MKB's models expect timezone-aware values.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class SqlGraphStore:
    """Durable entity/relation storage mirroring ``InMemoryGraphStore`` semantics."""

    def __init__(self, engine):
        from mkb.ports import Capabilities

        self.capabilities = frozenset({Capabilities.GRAPH_TRAVERSAL, Capabilities.BULK_UPSERT})
        self._engine = engine
        METADATA.create_all(engine, checkfirst=True)

    # Rows are written through the plugin's own engine rather than MKB's session so
    # that a graph write never joins an unrelated MKB transaction.
    def _row(self, table, identifier):
        with self._engine.connect() as connection:
            return connection.execute(select(table).where(table.c.id == identifier)).mappings().first()

    def _entity(self, row):
        from mkb.models import Entity

        return Entity(id=row["id"], type=row["type"], name=row["name"],
            properties=json.loads(row["properties"]),
            created_at=_aware(row["created_at"]), updated_at=_aware(row["updated_at"]))

    def _relation(self, row):
        from mkb.models import Relation

        return Relation(id=row["id"], source_id=row["source_id"], target_id=row["target_id"],
            type=row["type"], properties=json.loads(row["properties"]),
            created_at=_aware(row["created_at"]), updated_at=_aware(row["updated_at"]))

    @staticmethod
    def _values(element):
        return {"type": element.type, "properties": json.dumps(element.properties, sort_keys=True),
                "created_at": element.created_at, "updated_at": element.updated_at}

    def upsert_entity(self, entity):
        values = {"id": str(entity.id), "name": entity.name, **self._values(entity)}
        with self._engine.begin() as connection:
            existing = connection.execute(
                select(ENTITIES.c.id).where(ENTITIES.c.id == values["id"])).first()
            if existing is None:
                connection.execute(insert(ENTITIES).values(**values))
            else:
                connection.execute(update(ENTITIES).where(ENTITIES.c.id == values["id"]).values(
                    **{key: value for key, value in values.items() if key != "id"}))
        return entity

    def get_entity(self, entity_id):
        row = self._row(ENTITIES, str(entity_id))
        return self._entity(row) if row is not None else None

    def list_entities(self, *, type=None):
        query = select(ENTITIES).order_by(ENTITIES.c.id)
        if type is not None:
            query = query.where(ENTITIES.c.type == type)
        with self._engine.connect() as connection:
            return [self._entity(row) for row in connection.execute(query).mappings()]

    def upsert_relation(self, relation):
        from mkb.exceptions import ConflictError

        source, target = str(relation.source_id), str(relation.target_id)
        values = {"id": str(relation.id), "source_id": source, "target_id": target,
                  **self._values(relation)}
        with self._engine.begin() as connection:
            endpoints = connection.execute(
                select(ENTITIES.c.id).where(ENTITIES.c.id.in_({source, target}))).scalars().all()
            if set(endpoints) != {source, target}:
                raise ConflictError("Both relation endpoints must exist")
            existing = connection.execute(
                select(RELATIONS.c.id).where(RELATIONS.c.id == values["id"])).first()
            if existing is None:
                connection.execute(insert(RELATIONS).values(**values))
            else:
                connection.execute(update(RELATIONS).where(RELATIONS.c.id == values["id"]).values(
                    **{key: value for key, value in values.items() if key != "id"}))
        return relation

    def get_relation(self, relation_id):
        row = self._row(RELATIONS, str(relation_id))
        return self._relation(row) if row is not None else None

    def list_relations(self, *, entity_id=None, type=None):
        query = select(RELATIONS).order_by(RELATIONS.c.id)
        if type is not None:
            query = query.where(RELATIONS.c.type == type)
        if entity_id is not None:
            identifier = str(entity_id)
            query = query.where(
                (RELATIONS.c.source_id == identifier) | (RELATIONS.c.target_id == identifier))
        with self._engine.connect() as connection:
            return [self._relation(row) for row in connection.execute(query).mappings()]

    def clear(self):
        """Drop every graph element; used when rebuilding from approved facts."""
        with self._engine.begin() as connection:
            connection.execute(delete(RELATIONS))
            connection.execute(delete(ENTITIES))

    def counts(self):
        with self._engine.connect() as connection:
            return {
                "entities": connection.execute(
                    select(func.count()).select_from(ENTITIES)).scalar_one(),
                "relations": connection.execute(
                    select(func.count()).select_from(RELATIONS)).scalar_one(),
            }

    def close(self):
        # The engine is owned by the plugin client, which disposes it on card close.
        return None
