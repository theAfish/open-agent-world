"""Insights: meta-knowledge drafted from published experiment records — alongside MKB's tables.

Mirrors ``experiment_store.py``: plugin-owned SQL structure in the same SQLite file as
the rest of the card. An **insight** is one higher-level statement about the records of
one or more experiment schemas — an anomaly, a rule, a trend, a correlation, a
threshold or a note — evidence-linked to the published records it rests on. A model proposes insights
as drafts; a person edits, publishes or deletes each one. Publishing is what writes
the insight, and the record and field nodes it cites, into the knowledge graph's
``experiment`` layer (see ``insights.py``); nothing here touches the graph itself.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Float, Index, Integer, MetaData, String, Table, Text,
    delete, insert, or_, select, update,
)

from .errors import KnowledgeError
from .experiment_store import ConflictingRevision

METADATA = MetaData()

KINDS = ("anomaly", "rule", "trend", "correlation", "threshold", "note")
ROLES = ("supports", "contradicts", "observed_in")
STATUSES = ("draft", "published")

INSIGHTS = Table(
    "oaw_kb_insight", METADATA,
    Column("id", String(36), primary_key=True),
    # Null when the extraction ran over every group's records at once.
    Column("group_id", String(36), nullable=True),
    # The one schema an insight is about, or null when it spans several — then
    # ``schema_ids`` names them all (it always holds the full, sorted list).
    Column("schema_id", String(36), nullable=True),
    Column("schema_ids", Text, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("title", String(500), nullable=False),
    Column("statement", Text, nullable=False),
    # Flattened schema field names the insight is about, e.g. ["conductivity.value"].
    Column("fields", Text, nullable=False),
    Column("confidence", Float, nullable=True),
    # Free JSON the extractor attaches: the metric values behind the statement.
    Column("properties", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("note", Text, nullable=True),
    Column("run_id", String(64), nullable=True),
    Column("created_by", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_oaw_insight_group", "group_id"),
    Index("ix_oaw_insight_schema", "schema_id"),
    Index("ix_oaw_insight_status", "status"),
)

EVIDENCE = Table(
    "oaw_kb_insight_record", METADATA,
    Column("insight_id", String(36), primary_key=True),
    Column("record_id", String(36), primary_key=True),
    Column("role", String(32), nullable=False),
    Index("ix_oaw_insight_record_record", "record_id"),
)


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _row_json(row):
    return {
        "id": row["id"], "group_id": row["group_id"], "schema_id": row["schema_id"],
        "schema_ids": json.loads(row["schema_ids"]),
        "kind": row["kind"], "title": row["title"], "statement": row["statement"],
        "fields": json.loads(row["fields"]), "confidence": row["confidence"],
        "properties": json.loads(row["properties"]), "status": row["status"],
        "revision": row["revision"], "note": row["note"], "run_id": row["run_id"],
        "created_by": row["created_by"],
        "created_at": _aware(row["created_at"]), "updated_at": _aware(row["updated_at"]),
    }


def _check_kind(kind):
    if kind not in KINDS:
        raise KnowledgeError(f"kind must be one of {KINDS}")


def _schema_list(schema_ids):
    values = sorted({str(value) for value in schema_ids if value})
    if not values:
        raise KnowledgeError("An insight needs at least one schema")
    return values


def _migrate(engine):
    """Rebuild an insight table from before cross-schema insights existed.

    That shape had ``schema_id NOT NULL`` and no ``schema_ids``; SQLite cannot relax
    a NOT NULL in place, so the table is renamed, recreated and copied over, each
    old row's single schema becoming a one-element list.
    """
    with engine.begin() as connection:
        info = connection.exec_driver_sql("PRAGMA table_info(oaw_kb_insight)").fetchall()
        if not info:
            return
        columns = {row[1]: row for row in info}
        if "schema_ids" in columns and columns["schema_id"][3] == 0:
            return
        for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='oaw_kb_insight'").fetchall():
            if not row[0].startswith("sqlite_"):
                connection.exec_driver_sql(f"DROP INDEX {row[0]}")
        connection.exec_driver_sql("ALTER TABLE oaw_kb_insight RENAME TO oaw_kb_insight_legacy")
    INSIGHTS.create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO oaw_kb_insight (id, group_id, schema_id, schema_ids, kind, title, statement, "
            "fields, confidence, properties, status, revision, note, run_id, created_by, created_at, updated_at) "
            "SELECT id, group_id, schema_id, '[\"' || schema_id || '\"]', kind, title, statement, fields, "
            "confidence, properties, status, revision, note, run_id, created_by, created_at, updated_at "
            "FROM oaw_kb_insight_legacy")
        connection.exec_driver_sql("DROP TABLE oaw_kb_insight_legacy")


class InsightStore:
    """Durable insight storage, independent of the graph it publishes into."""

    def __init__(self, engine):
        self._engine = engine
        _migrate(engine)
        METADATA.create_all(engine, checkfirst=True)

    def create(self, *, schema_ids, kind, title, statement, fields=(), confidence=None,
               properties=None, group_id=None, note=None, run_id=None, created_by=None):
        _check_kind(kind)
        schemas = _schema_list(schema_ids)
        identifier = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            connection.execute(insert(INSIGHTS).values(
                id=identifier, group_id=str(group_id) if group_id else None,
                schema_id=schemas[0] if len(schemas) == 1 else None,
                schema_ids=json.dumps(schemas), kind=kind, title=title, statement=statement,
                fields=json.dumps(list(fields)), confidence=confidence,
                properties=json.dumps(properties or {}), status="draft", revision=1,
                note=note, run_id=run_id, created_by=created_by,
                created_at=now, updated_at=now))
        return self.require(identifier)

    def get(self, insight_id):
        with self._engine.connect() as connection:
            row = connection.execute(
                select(INSIGHTS).where(INSIGHTS.c.id == str(insight_id))).mappings().first()
        return _row_json(row) if row else None

    def require(self, insight_id):
        insight = self.get(insight_id)
        if insight is None:
            raise KnowledgeError(f"Insight not found: {insight_id}")
        return insight

    def list(self, *, group_id=None, schema_id=None, kind=None, status=None, text=None, limit=100):
        """``schema_id`` matches every insight that involves that schema, alone or
        together with others; ``text`` is a case-insensitive substring of the title,
        statement, fields or note."""
        query = (select(INSIGHTS).order_by(INSIGHTS.c.updated_at.desc())
                 .limit(max(1, min(limit, 500))))
        if group_id:
            query = query.where(INSIGHTS.c.group_id == str(group_id))
        if schema_id:
            query = query.where(INSIGHTS.c.schema_ids.like(f'%"{schema_id}"%'))
        if text and text.strip():
            needle = f"%{text.strip()}%"
            query = query.where(or_(INSIGHTS.c.title.like(needle), INSIGHTS.c.statement.like(needle),
                                    INSIGHTS.c.fields.like(needle), INSIGHTS.c.note.like(needle)))
        if kind:
            query = query.where(INSIGHTS.c.kind == kind)
        if status:
            query = query.where(INSIGHTS.c.status == status)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [_row_json(row) for row in rows]

    def update(self, insight_id, *, kind=None, title=None, statement=None, fields=None,
               confidence=..., properties=None, note=None, status=None, expected_revision=None):
        """``confidence`` uses an ellipsis sentinel so ``None`` can clear it explicitly."""
        if kind is not None:
            _check_kind(kind)
        if status is not None and status not in STATUSES:
            raise KnowledgeError(f"status must be one of {STATUSES}")
        with self._engine.begin() as connection:
            current = connection.execute(
                select(INSIGHTS).where(INSIGHTS.c.id == str(insight_id))).mappings().first()
            if current is None:
                raise KnowledgeError(f"Insight not found: {insight_id}")
            if expected_revision is not None and current["revision"] != expected_revision:
                raise ConflictingRevision(
                    f"Insight changed underneath this call (revision {current['revision']})")
            values = {"revision": current["revision"] + 1, "updated_at": datetime.now(timezone.utc)}
            if kind is not None:
                values["kind"] = kind
            if title is not None:
                values["title"] = title
            if statement is not None:
                values["statement"] = statement
            if fields is not None:
                values["fields"] = json.dumps(list(fields))
            if confidence is not ...:
                values["confidence"] = confidence
            if properties is not None:
                values["properties"] = json.dumps(properties)
            if note is not None:
                values["note"] = note
            if status is not None:
                values["status"] = status
            connection.execute(update(INSIGHTS).where(INSIGHTS.c.id == str(insight_id)).values(**values))
        return self.require(insight_id)

    def delete(self, insight_id):
        with self._engine.begin() as connection:
            connection.execute(delete(EVIDENCE).where(EVIDENCE.c.insight_id == str(insight_id)))
            result = connection.execute(delete(INSIGHTS).where(INSIGHTS.c.id == str(insight_id)))
            if result.rowcount == 0:
                raise KnowledgeError(f"Insight not found: {insight_id}")

    def delete_drafts(self, *, schema_ids, group_id=None):
        """Drop one scope's unpublished insights before a re-extraction replaces them.

        The scope is the exact set of schemas and the group: a group-scoped run
        replaces that group's drafts over those schemas; an all-groups run replaces
        the drafts that were themselves extracted across all groups. Published
        insights are never touched.
        """
        query = select(INSIGHTS.c.id).where(INSIGHTS.c.schema_ids == json.dumps(_schema_list(schema_ids)),
                                            INSIGHTS.c.status == "draft")
        query = query.where(INSIGHTS.c.group_id == (str(group_id) if group_id else None))
        with self._engine.begin() as connection:
            insight_ids = [row[0] for row in connection.execute(query).all()]
            if insight_ids:
                connection.execute(delete(EVIDENCE).where(EVIDENCE.c.insight_id.in_(insight_ids)))
                connection.execute(delete(INSIGHTS).where(INSIGHTS.c.id.in_(insight_ids)))
        return insight_ids

    def set_evidence(self, insight_id, links):
        """Replace every record link for one insight; ``links`` are ``{record_id, role}``."""
        rows, seen = [], set()
        for link in links:
            role = link.get("role") or "supports"
            if role not in ROLES:
                raise KnowledgeError(f"evidence role must be one of {ROLES}")
            record_id = str(link["record_id"])
            if record_id in seen:
                continue
            seen.add(record_id)
            rows.append({"insight_id": str(insight_id), "record_id": record_id, "role": role})
        with self._engine.begin() as connection:
            connection.execute(delete(EVIDENCE).where(EVIDENCE.c.insight_id == str(insight_id)))
            if rows:
                connection.execute(insert(EVIDENCE), rows)

    def evidence_for(self, insight_id):
        with self._engine.connect() as connection:
            rows = connection.execute(select(EVIDENCE.c.record_id, EVIDENCE.c.role).where(
                EVIDENCE.c.insight_id == str(insight_id)).order_by(EVIDENCE.c.record_id)).mappings().all()
        return [dict(row) for row in rows]

    def published_citing(self, record_id):
        """Ids of published insights that cite this record."""
        query = select(INSIGHTS.c.id).select_from(
            INSIGHTS.join(EVIDENCE, EVIDENCE.c.insight_id == INSIGHTS.c.id)).where(
            EVIDENCE.c.record_id == str(record_id), INSIGHTS.c.status == "published")
        with self._engine.connect() as connection:
            return [row[0] for row in connection.execute(query).all()]
