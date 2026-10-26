"""Knowledge drafts: the general concept graph proposed from literature — alongside MKB's tables.

Mirrors ``insight_store.py``: plugin-owned SQL structure in the same SQLite file as
the rest of the card. A **knowledge draft** is one proposed subgraph — concepts and
the relations a set of papers state between them — built by extracting each of the
draft's sources in turn into the same draft. Elements carry content-keyed ids (see
``knowledge_prompt.py``), so a second source naming the same concept merges into the
existing node and only adds itself as evidence. A person edits the draft, then
publishes it; publishing is what writes it into the graph's ``literature`` layer (see
``knowledge.py``); nothing here touches the graph itself.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Index, Integer, MetaData, String, Table, Text,
    delete, insert, select, update,
)

from .errors import KnowledgeError
from .experiment_store import ConflictingRevision
from .knowledge_prompt import slug

METADATA = MetaData()

STATUSES = ("draft", "published")

DRAFTS = Table(
    "oaw_kb_knowledge_draft", METADATA,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), nullable=False),
    Column("title", String(500), nullable=False),
    # JSON lists of ``{id, name, description, aliases, source_ids}`` and
    # ``{id, source, target, predicate, description, evidence_level, snippet, source_ids}``; see
    # ``knowledge_prompt.normalize``.
    Column("entities", Text, nullable=False),
    Column("relations", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("run_id", String(64), nullable=True),
    # JSON list of audit runs: ``{at, actor, operations, skipped}`` (see ``knowledge_audit.py``).
    Column("review_log", Text, nullable=False, server_default="[]"),
    Column("created_by", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_oaw_knowledge_draft_group", "group_id"),
    Index("ix_oaw_knowledge_draft_status", "status"),
)

SOURCES = Table(
    "oaw_kb_knowledge_draft_source", METADATA,
    Column("draft_id", String(36), primary_key=True),
    Column("source_id", String(36), primary_key=True),
    Column("position", Integer, nullable=False),
    # Null until this source has been extracted into the draft.
    Column("extracted_at", DateTime(timezone=True), nullable=True),
    Index("ix_oaw_knowledge_draft_source_source", "source_id"),
)


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _add_column(engine, table_name, column, ddl):
    # ``create_all(checkfirst=True)`` only creates missing tables; a card from before
    # audits existed needs the column added by hand.
    with engine.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql(
            f"PRAGMA table_info({table_name})").fetchall()}
        if column not in columns:
            connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column} {ddl}")


def _row_json(row):
    return {
        "id": row["id"], "group_id": row["group_id"], "title": row["title"],
        "entities": json.loads(row["entities"]), "relations": json.loads(row["relations"]),
        "status": row["status"], "revision": row["revision"], "run_id": row["run_id"],
        "review_log": json.loads(row["review_log"] or "[]"), "created_by": row["created_by"],
        "created_at": _aware(row["created_at"]), "updated_at": _aware(row["updated_at"]),
    }


def _merge(existing, incoming, source_id):
    """Union two element lists by ``id``; an element seen again gains the source, any
    new aliases, the stronger evidence level and a snippet or description it lacked."""
    merged = {item["id"]: dict(item) for item in existing}
    for item in incoming:
        current = merged.get(item["id"])
        if current is None:
            current = merged[item["id"]] = {**item, "source_ids": list(item.get("source_ids") or [])}
        else:
            for key in ("description", "snippet"):
                if item.get(key) and not current.get(key):
                    current[key] = item[key]
            if "aliases" in item or "aliases" in current:
                known = {slug(current.get("name", ""))} | {slug(a) for a in current.get("aliases") or []}
                current["aliases"] = list(current.get("aliases") or [])
                for alias in item.get("aliases") or []:
                    if slug(alias) not in known:
                        known.add(slug(alias))
                        current["aliases"].append(alias)
            if "evidence_level" in item or "evidence_level" in current:
                current["evidence_level"] = min(current.get("evidence_level", 4), item.get("evidence_level", 4))
        if source_id and source_id not in current["source_ids"]:
            current["source_ids"].append(source_id)
    return list(merged.values())


class KnowledgeDraftStore:
    """Durable draft storage, independent of the graph it publishes into."""

    def __init__(self, engine):
        self._engine = engine
        METADATA.create_all(engine, checkfirst=True)
        _add_column(engine, "oaw_kb_knowledge_draft", "review_log", "TEXT NOT NULL DEFAULT '[]'")

    def create(self, *, group_id, title, source_ids, created_by=None):
        sources = list(dict.fromkeys(str(value) for value in source_ids if value))
        if not sources:
            raise KnowledgeError("A knowledge draft needs at least one source")
        identifier = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            connection.execute(insert(DRAFTS).values(
                id=identifier, group_id=str(group_id), title=title, entities="[]", relations="[]",
                status="draft", revision=1, run_id=None, created_by=created_by,
                created_at=now, updated_at=now))
            connection.execute(insert(SOURCES), [
                {"draft_id": identifier, "source_id": source_id, "position": index, "extracted_at": None}
                for index, source_id in enumerate(sources)])
        return self.require(identifier)

    def get(self, draft_id):
        with self._engine.connect() as connection:
            row = connection.execute(select(DRAFTS).where(DRAFTS.c.id == str(draft_id))).mappings().first()
        return _row_json(row) if row else None

    def require(self, draft_id):
        draft = self.get(draft_id)
        if draft is None:
            raise KnowledgeError(f"Knowledge draft not found: {draft_id}")
        return draft

    def list(self, *, group_id=None, status=None, limit=100):
        query = select(DRAFTS).order_by(DRAFTS.c.updated_at.desc()).limit(max(1, min(limit, 500)))
        if group_id:
            query = query.where(DRAFTS.c.group_id == str(group_id))
        if status:
            query = query.where(DRAFTS.c.status == status)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [_row_json(row) for row in rows]

    def sources_for(self, draft_id):
        with self._engine.connect() as connection:
            rows = connection.execute(select(SOURCES.c.source_id, SOURCES.c.extracted_at).where(
                SOURCES.c.draft_id == str(draft_id)).order_by(SOURCES.c.position)).mappings().all()
        return [{"source_id": row["source_id"], "extracted_at": _aware(row["extracted_at"])} for row in rows]

    def _current(self, connection, draft_id, expected_revision=None):
        current = connection.execute(select(DRAFTS).where(DRAFTS.c.id == str(draft_id))).mappings().first()
        if current is None:
            raise KnowledgeError(f"Knowledge draft not found: {draft_id}")
        if expected_revision is not None and current["revision"] != expected_revision:
            raise ConflictingRevision(
                f"Knowledge draft changed underneath this call (revision {current['revision']})")
        return _row_json(current)

    def _write(self, connection, current, **values):
        connection.execute(update(DRAFTS).where(DRAFTS.c.id == current["id"]).values(
            revision=current["revision"] + 1, updated_at=datetime.now(timezone.utc), **values))

    def update(self, draft_id, *, title=None, entities=None, relations=None, source_ids=None,
               status=None, run_id=..., review_log=None, expected_revision=None):
        """Replace whole element lists; ``run_id`` uses an ellipsis sentinel so
        ``None`` can clear it. Changing ``source_ids`` keeps the extraction state of
        sources that stay and drops the elements only removed sources evidenced."""
        if status is not None and status not in STATUSES:
            raise KnowledgeError(f"status must be one of {STATUSES}")
        values = {}
        if title is not None:
            values["title"] = title
        if entities is not None:
            values["entities"] = json.dumps(list(entities))
        if relations is not None:
            values["relations"] = json.dumps(list(relations))
        if status is not None:
            values["status"] = status
        if run_id is not ...:
            values["run_id"] = run_id
        if review_log is not None:
            values["review_log"] = json.dumps(list(review_log))
        with self._engine.begin() as connection:
            current = self._current(connection, draft_id, expected_revision)
            if source_ids is not None:
                wanted = list(dict.fromkeys(str(value) for value in source_ids if value))
                if not wanted:
                    raise KnowledgeError("A knowledge draft needs at least one source")
                extracted = {row["source_id"]: row["extracted_at"] for row in connection.execute(
                    select(SOURCES).where(SOURCES.c.draft_id == str(draft_id))).mappings().all()}
                connection.execute(delete(SOURCES).where(SOURCES.c.draft_id == str(draft_id)))
                connection.execute(insert(SOURCES), [
                    {"draft_id": str(draft_id), "source_id": source_id, "position": index,
                     "extracted_at": extracted.get(source_id)} for index, source_id in enumerate(wanted)])
                keep = set(wanted)
                for key in ("entities", "relations"):
                    if key not in values:
                        values[key] = json.dumps([
                            {**item, "source_ids": [s for s in item["source_ids"] if s in keep]}
                            for item in current[key] if not item["source_ids"] or keep & set(item["source_ids"])])
            self._write(connection, current, **values)
        return self.require(draft_id)

    def merge(self, draft_id, *, source_id, entities, relations):
        """Fold one source's extraction into the draft and mark the source extracted."""
        with self._engine.begin() as connection:
            current = self._current(connection, draft_id)
            merged_entities = _merge(current["entities"], entities, str(source_id))
            known = {item["id"] for item in merged_entities}
            merged_relations = _merge(current["relations"], [
                item for item in relations if item["source"] in known and item["target"] in known], str(source_id))
            self._write(connection, current, entities=json.dumps(merged_entities),
                        relations=json.dumps(merged_relations))
            connection.execute(update(SOURCES).where(
                SOURCES.c.draft_id == str(draft_id), SOURCES.c.source_id == str(source_id)).values(
                extracted_at=datetime.now(timezone.utc)))
        return self.require(draft_id)

    def clear(self, draft_id):
        """Drop every element and forget which sources were extracted, before a re-extraction."""
        with self._engine.begin() as connection:
            self._write(connection, self._current(connection, draft_id), entities="[]", relations="[]")
            connection.execute(update(SOURCES).where(SOURCES.c.draft_id == str(draft_id)).values(extracted_at=None))
        return self.require(draft_id)

    def delete(self, draft_id):
        with self._engine.begin() as connection:
            connection.execute(delete(SOURCES).where(SOURCES.c.draft_id == str(draft_id)))
            result = connection.execute(delete(DRAFTS).where(DRAFTS.c.id == str(draft_id)))
            if result.rowcount == 0:
                raise KnowledgeError(f"Knowledge draft not found: {draft_id}")

    def published_element_ids(self, *, except_draft_id=None):
        """Every element id some published draft other than ``except_draft_id`` lists —
        what unpublishing one draft must leave in the graph."""
        query = select(DRAFTS.c.id, DRAFTS.c.entities, DRAFTS.c.relations).where(DRAFTS.c.status == "published")
        if except_draft_id:
            query = query.where(DRAFTS.c.id != str(except_draft_id))
        ids = set()
        with self._engine.connect() as connection:
            for row in connection.execute(query).mappings().all():
                ids.update(item["id"] for item in json.loads(row["entities"]))
                ids.update(item["id"] for item in json.loads(row["relations"]))
        return ids
