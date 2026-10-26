"""One-shot re-keying of knowledge drafts and the literature layer to the concept-only model.

The first release keyed every literature node by type and name
(``concept:<type>:<slug>``), so the same name read once as a ``method`` and once as a
``concept`` was two nodes, and relation types came from a closed list. The model is now
concept-only with free predicates (see ``knowledge_prompt.py``): ids are
``concept:<slug>`` and ``<source>|<predicate>|<target>``. Drafts and the published
graph must be re-keyed by the same functions, because unpublishing a draft removes
graph elements by the ids the draft lists.

Runs once per card database, guarded by a version row in ``oaw_kb_meta``; a second
run is a no-op. Everything is rewritten inside one transaction per table group.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, MetaData, String, Table, delete, insert, select, update

from .graph_store import ENTITIES, LITERATURE_LAYER, RELATIONS
from .knowledge_prompt import canonical_predicate, entity_id, normalize, relation_id, slug
from .knowledge_store import DRAFTS

logger = logging.getLogger(__name__)

KNOWLEDGE_MODEL_VERSION = 2
VERSION_KEY = "knowledge_model"

METADATA = MetaData()
META = Table(
    "oaw_kb_meta", METADATA,
    Column("key", String(64), primary_key=True),
    Column("value", String(255), nullable=False),
)


def _element_key(value):
    # The uuid ``kb.graph.extract`` derives from a non-UUID string id (``knowledge.py``).
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


def current_version(engine):
    METADATA.create_all(engine, checkfirst=True)
    with engine.connect() as connection:
        row = connection.execute(select(META.c.value).where(META.c.key == VERSION_KEY)).first()
    return int(row[0]) if row else 1


def _set_version(connection, version):
    if connection.execute(select(META.c.key).where(META.c.key == VERSION_KEY)).first():
        connection.execute(update(META).where(META.c.key == VERSION_KEY).values(value=str(version)))
    else:
        connection.execute(insert(META).values(key=VERSION_KEY, value=str(version)))


def migrate_knowledge_model(engine):
    """Bring drafts and the literature layer to the current model; returns counts or
    ``None`` when nothing had to run."""
    if current_version(engine) >= KNOWLEDGE_MODEL_VERSION:
        return None
    drafts = _migrate_drafts(engine)
    graph = _migrate_literature_layer(engine)
    with engine.begin() as connection:
        _set_version(connection, KNOWLEDGE_MODEL_VERSION)
    logger.info("Knowledge model migrated to v%d: %d drafts, %d graph entities, %d graph relations",
                KNOWLEDGE_MODEL_VERSION, drafts, graph["entities"], graph["relations"])
    return {"drafts": drafts, **graph}


def _migrate_drafts(engine):
    """Re-key every draft through ``normalize``: old ids on entities resolve the old
    endpoints relations still name, the legacy ``type`` becomes the predicate."""
    count = 0
    with engine.begin() as connection:
        rows = connection.execute(select(DRAFTS.c.id, DRAFTS.c.entities, DRAFTS.c.relations)).mappings().all()
        for row in rows:
            entities, relations = normalize(json.loads(row["entities"]), json.loads(row["relations"]))
            connection.execute(update(DRAFTS).where(DRAFTS.c.id == row["id"]).values(
                entities=json.dumps(entities), relations=json.dumps(relations)))
            count += 1
    return count


def _merge_properties(current, incoming):
    merged = dict(current)
    for key, value in incoming.items():
        if key in ("aliases", "source_ids"):
            seen = list(merged.get(key) or [])
            merged[key] = seen + [item for item in (value or []) if item not in seen]
        elif merged.get(key) in (None, "", []):
            merged[key] = value
    return merged


def _migrate_literature_layer(engine):
    """Collapse typed literature nodes onto ``concept:<slug>`` and rewrite the
    relations between them with normalized predicates; the experiment layer is untouched."""
    with engine.begin() as connection:
        entity_rows = connection.execute(
            select(ENTITIES).where(ENTITIES.c.layer == LITERATURE_LAYER)).mappings().all()
        relation_rows = connection.execute(
            select(RELATIONS).where(RELATIONS.c.layer == LITERATURE_LAYER)).mappings().all()
        if not entity_rows and not relation_rows:
            return {"entities": 0, "relations": 0}

        now = datetime.now(timezone.utc)
        old_to_new, entities = {}, {}
        for row in entity_rows:
            key = entity_id(row["name"])
            new_id = _element_key(key)
            old_to_new[row["id"]] = (new_id, key)
            properties = json.loads(row["properties"] or "{}")
            # A name that was its own node and another's alias collapses onto the name.
            properties["aliases"] = [alias for alias in properties.get("aliases") or []
                                     if slug(alias) != slug(row["name"])]
            current = entities.get(new_id)
            if current is None:
                entities[new_id] = {
                    "id": new_id, "type": "concept", "name": row["name"], "properties": properties,
                    "created_at": row["created_at"], "updated_at": now,
                    "group_id": row["group_id"], "layer": LITERATURE_LAYER}
            else:
                current["properties"] = _merge_properties(current["properties"], properties)
                current["created_at"] = min(current["created_at"], row["created_at"])
                current["group_id"] = current["group_id"] or row["group_id"]

        relations = {}
        for row in relation_rows:
            source, target = old_to_new.get(row["source_id"]), old_to_new.get(row["target_id"])
            if source is None or target is None or source[0] == target[0]:
                continue
            predicate = canonical_predicate(row["type"])
            key = relation_id(source[1], predicate, target[1])
            new_id = _element_key(key)
            properties = json.loads(row["properties"] or "{}")
            current = relations.get(new_id)
            if current is None:
                relations[new_id] = {
                    "id": new_id, "source_id": source[0], "target_id": target[0], "type": predicate,
                    "properties": properties, "created_at": row["created_at"], "updated_at": now,
                    "group_id": row["group_id"], "layer": LITERATURE_LAYER}
            else:
                current["properties"] = _merge_properties(current["properties"], properties)
                current["created_at"] = min(current["created_at"], row["created_at"])

        connection.execute(delete(RELATIONS).where(RELATIONS.c.layer == LITERATURE_LAYER))
        connection.execute(delete(ENTITIES).where(ENTITIES.c.layer == LITERATURE_LAYER))
        if entities:
            connection.execute(insert(ENTITIES), [
                {**item, "properties": json.dumps(item["properties"], sort_keys=True)}
                for item in entities.values()])
        if relations:
            connection.execute(insert(RELATIONS), [
                {**item, "properties": json.dumps(item["properties"], sort_keys=True)}
                for item in relations.values()])
    return {"entities": len(entities), "relations": len(relations)}
