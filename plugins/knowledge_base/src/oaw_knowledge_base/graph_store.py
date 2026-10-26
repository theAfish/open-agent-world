"""SQL-backed GraphStore so an approved knowledge graph survives a restart.

MKB's portable client defaults to ``InMemoryGraphStore``, which loses every entity
and relation when the client closes. This adapter satisfies ``mkb.ports.GraphStore``
over two plugin-owned tables inside the same SQLite file as the rest of the card, so
the whole knowledge base stays a single SQL database with no external service.

Every element carries a ``layer``: ``literature`` for what a published knowledge
draft wrote (see ``knowledge.py``), ``experiment`` for a published insight and the
record and field nodes it cites (see ``insights.py``). One graph, two provenances —
MKB's own query and traversal walk both; the plugin's filters narrow to either.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Index, MetaData, String, Table, Text,
    delete, func, insert, select, update,
)

METADATA = MetaData()

LITERATURE_LAYER = "literature"
EXPERIMENT_LAYER = "experiment"
LAYERS = (LITERATURE_LAYER, EXPERIMENT_LAYER)

ENTITIES = Table(
    "oaw_kg_entity", METADATA,
    Column("id", String(36), primary_key=True),
    Column("type", String(255), nullable=False),
    Column("name", String(1024), nullable=False),
    Column("properties", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # The group (MKB collection) whose approval last introduced or touched this
    # entity. Null for entities published before groups existed. Not a hard
    # ownership boundary — an entity that several groups' facts resolve to the
    # same alias for keeps only the most recent group here.
    Column("group_id", String(36), nullable=True),
    # Which pipeline published this element: ``literature`` for an approved draft's
    # facts, ``experiment`` for a published insight and the record/field nodes it
    # cites. One graph, two layers — a query filters by it, traversal crosses it.
    Column("layer", String(32), nullable=False, server_default=LITERATURE_LAYER),
    Index("ix_oaw_kg_entity_group", "group_id"),
    Index("ix_oaw_kg_entity_layer", "layer"),
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
    Column("group_id", String(36), nullable=True),
    Column("layer", String(32), nullable=False, server_default=LITERATURE_LAYER),
    Index("ix_oaw_kg_relation_source", "source_id"),
    Index("ix_oaw_kg_relation_target", "target_id"),
    Index("ix_oaw_kg_relation_group", "group_id"),
    Index("ix_oaw_kg_relation_layer", "layer"),
)


def _aware(value):
    # SQLite returns naive datetimes; MKB's models expect timezone-aware values.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _add_column(engine, table_name, column, ddl):
    # ``create_all(checkfirst=True)`` only creates missing tables, so a card's
    # database from before groups (or layers) existed needs the column added by hand.
    with engine.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql(
            f"PRAGMA table_info({table_name})").fetchall()}
        if column not in columns:
            connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}")


def _add_group_column(engine, table_name):
    _add_column(engine, table_name, "group_id", "VARCHAR(36)")


def _add_layer_column(engine, table_name):
    # Everything published before layers existed came from an approved literature draft.
    _add_column(engine, table_name, "layer", f"VARCHAR(32) NOT NULL DEFAULT '{LITERATURE_LAYER}'")


def _layered(query, table, layer):
    if layer is not None:
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}")
        query = query.where(table.c.layer == layer)
    return query


class SqlGraphStore:
    """Durable entity/relation storage mirroring ``InMemoryGraphStore`` semantics."""

    def __init__(self, engine):
        from mkb.ports import Capabilities

        self.capabilities = frozenset({Capabilities.GRAPH_TRAVERSAL, Capabilities.BULK_UPSERT})
        self._engine = engine
        METADATA.create_all(engine, checkfirst=True)
        _add_group_column(engine, "oaw_kg_entity")
        _add_group_column(engine, "oaw_kg_relation")
        _add_layer_column(engine, "oaw_kg_entity")
        _add_layer_column(engine, "oaw_kg_relation")

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

    def dataset(self, *, limit, entity_type=None, relation_type=None, layer=None):
        """Bound the SQL result before loading a graph for visualization."""
        query = select(ENTITIES.c.id, ENTITIES.c.name, ENTITIES.c.type).order_by(ENTITIES.c.id).limit(limit + 1)
        if entity_type:
            query = query.where(ENTITIES.c.type == entity_type)
        query = _layered(query, ENTITIES, layer)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
            nodes = [dict(row) for row in rows[:limit]]
            identifiers = [node["id"] for node in nodes]
            links = select(RELATIONS.c.id, RELATIONS.c.source_id.label("source"),
                           RELATIONS.c.target_id.label("target"), RELATIONS.c.type).where(
                RELATIONS.c.source_id.in_(identifiers), RELATIONS.c.target_id.in_(identifiers)
            ).order_by(RELATIONS.c.id).limit(limit + 1)
            if relation_type:
                links = links.where(RELATIONS.c.type == relation_type)
            edges = connection.execute(links).mappings().all() if nodes else []
        return {"kind": "graph", "nodes": nodes, "edges": [dict(row) for row in edges[:limit]],
                "truncated": len(rows) > limit or len(edges) > limit, "scope": "published"}

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

    def list_entities(self, *, type=None, group_id=None, layer=None):
        query = select(ENTITIES).order_by(ENTITIES.c.id)
        if type is not None:
            query = query.where(ENTITIES.c.type == type)
        if group_id is not None:
            query = query.where(ENTITIES.c.group_id == str(group_id))
        query = _layered(query, ENTITIES, layer)
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

    def list_relations(self, *, entity_id=None, type=None, group_id=None, layer=None):
        query = select(RELATIONS).order_by(RELATIONS.c.id)
        if type is not None:
            query = query.where(RELATIONS.c.type == type)
        if entity_id is not None:
            identifier = str(entity_id)
            query = query.where(
                (RELATIONS.c.source_id == identifier) | (RELATIONS.c.target_id == identifier))
        if group_id is not None:
            query = query.where(RELATIONS.c.group_id == str(group_id))
        query = _layered(query, RELATIONS, layer)
        with self._engine.connect() as connection:
            return [self._relation(row) for row in connection.execute(query).mappings()]

    def tag(self, entity_ids, relation_ids, *, group_id=None, layer=None):
        """Stamp group and/or layer onto exactly the elements a publication touched.

        ``kb.graph.extract`` builds and upserts :class:`~mkb.models.Entity` /
        ``Relation`` objects itself, so there is no seam to hand it either value
        directly. This runs right after, over the exact ids that call returned.
        """
        values = {}
        if group_id is not None:
            values["group_id"] = str(group_id)
        if layer is not None:
            if layer not in LAYERS:
                raise ValueError(f"layer must be one of {LAYERS}")
            values["layer"] = layer
        if not values:
            return
        with self._engine.begin() as connection:
            if entity_ids:
                connection.execute(update(ENTITIES).where(
                    ENTITIES.c.id.in_([str(value) for value in entity_ids])).values(**values))
            if relation_ids:
                connection.execute(update(RELATIONS).where(
                    RELATIONS.c.id.in_([str(value) for value in relation_ids])).values(**values))

    def tag_group(self, entity_ids, relation_ids, group_id):
        """Record which group's approval introduced or last touched these elements."""
        self.tag(entity_ids, relation_ids, group_id=group_id)

    def remove(self, entity_ids):
        """Delete these entities and every relation touching them (an unpublish)."""
        identifiers = [str(value) for value in entity_ids]
        if not identifiers:
            return
        with self._engine.begin() as connection:
            connection.execute(delete(RELATIONS).where(
                RELATIONS.c.source_id.in_(identifiers) | RELATIONS.c.target_id.in_(identifiers)))
            connection.execute(delete(ENTITIES).where(ENTITIES.c.id.in_(identifiers)))

    def remove_relations(self, relation_ids):
        """Delete exactly these relations, leaving their endpoints in place."""
        identifiers = [str(value) for value in relation_ids]
        if not identifiers:
            return
        with self._engine.begin() as connection:
            connection.execute(delete(RELATIONS).where(RELATIONS.c.id.in_(identifiers)))

    def orphans(self, *, layer, types):
        """Ids of this layer's entities of the given types that no relation touches.

        Reference nodes (a record, a field) exist only to be cited; once the last
        insight citing one is unpublished, nothing should keep it on the map.
        """
        if not types:
            return []
        linked = select(RELATIONS.c.source_id).union(select(RELATIONS.c.target_id))
        query = select(ENTITIES.c.id).where(
            ENTITIES.c.type.in_(list(types)), ENTITIES.c.id.not_in(linked))
        query = _layered(query, ENTITIES, layer)
        with self._engine.connect() as connection:
            return [row[0] for row in connection.execute(query).all()]

    def clear_layer(self, layer):
        """Drop one layer's elements, before rebuilding it from its published drafts."""
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {LAYERS}")
        with self._engine.begin() as connection:
            connection.execute(delete(RELATIONS).where(RELATIONS.c.layer == layer))
            connection.execute(delete(ENTITIES).where(ENTITIES.c.layer == layer))

    def clear(self):
        """Drop every graph element; used when rebuilding from approved facts."""
        with self._engine.begin() as connection:
            connection.execute(delete(RELATIONS))
            connection.execute(delete(ENTITIES))

    def predicate_counts(self, *, layer=None, limit=50):
        """Relation types in use with their frequencies, most common first."""
        query = select(RELATIONS.c.type, func.count().label("count")).group_by(RELATIONS.c.type)
        query = _layered(query, RELATIONS, layer).order_by(func.count().desc(), RELATIONS.c.type)
        with self._engine.connect() as connection:
            rows = connection.execute(query.limit(max(1, min(int(limit), 500)))).all()
        return [{"predicate": row[0], "count": row[1]} for row in rows]

    def find_names(self, terms, *, layer=None, limit=20):
        """Entities whose name or aliases contain any of ``terms`` (case-insensitive),
        with the predicates used around them — what an extraction consults before it
        names a concept the published graph already holds."""
        needles = [str(term).strip().casefold() for term in terms if str(term).strip()]
        if not needles:
            return []
        query = select(ENTITIES.c.id, ENTITIES.c.name, ENTITIES.c.properties)
        query = _layered(query, ENTITIES, layer)
        condition = None
        for needle in needles:
            clause = func.lower(ENTITIES.c.name).contains(needle) | func.lower(ENTITIES.c.properties).contains(needle)
            condition = clause if condition is None else condition | clause
        query = query.where(condition).order_by(ENTITIES.c.name)
        with self._engine.connect() as connection:
            rows = connection.execute(query.limit(max(1, min(int(limit), 200)) * 3)).mappings().all()
            matches = []
            for row in rows:
                properties = json.loads(row["properties"] or "{}")
                aliases = [alias for alias in properties.get("aliases") or [] if isinstance(alias, str)]
                haystack = [row["name"].casefold()] + [alias.casefold() for alias in aliases]
                hits = [needle for needle in needles if any(needle in text for text in haystack)]
                if hits:
                    matches.append({"id": row["id"], "name": row["name"], "aliases": aliases, "matched": hits})
            matches = matches[:max(1, min(int(limit), 200))]
            if matches:
                identifiers = [item["id"] for item in matches]
                edges = connection.execute(select(RELATIONS.c.source_id, RELATIONS.c.target_id, RELATIONS.c.type)
                                           .where(RELATIONS.c.source_id.in_(identifiers)
                                                  | RELATIONS.c.target_id.in_(identifiers))).all()
                by_id = {item["id"]: item for item in matches}
                for item in matches:
                    item["predicates"] = []
                for source, target, kind in edges:
                    for identifier in (source, target):
                        item = by_id.get(identifier)
                        if item is not None and kind not in item["predicates"]:
                            item["predicates"].append(kind)
        return matches

    def counts(self, *, group_id=None, layer=None):
        with self._engine.connect() as connection:
            entity_query = select(func.count()).select_from(ENTITIES)
            relation_query = select(func.count()).select_from(RELATIONS)
            if group_id is not None:
                entity_query = entity_query.where(ENTITIES.c.group_id == str(group_id))
                relation_query = relation_query.where(RELATIONS.c.group_id == str(group_id))
            entity_query = _layered(entity_query, ENTITIES, layer)
            relation_query = _layered(relation_query, RELATIONS, layer)
            return {
                "entities": connection.execute(entity_query).scalar_one(),
                "relations": connection.execute(relation_query).scalar_one(),
            }

    def close(self):
        # The engine is owned by the plugin client, which disposes it on card close.
        return None
