"""Experiment records: one upload bundle, many structured records — alongside MKB's tables.

Mirrors ``graph_store.py``'s pattern of plugin-owned SQL structure in the same
SQLite file as the rest of the card. A **bundle** is one experiment upload: several
files (a photographed table, a spreadsheet, a note) that together describe a set of
specimens, projected as one long document onto one ``kind="experiment"`` schema. The
projection yields **records** — one per specimen — each evidence-linked back to the
bundle's source files. The Literature section's Records tab writes the same table
with no bundle: a record extracted straight from one converted source, linked to it.
A person edits a record directly or through an agent, then
publishes it; a published record is the approved dataset and nothing here ever
touches ``oaw_kg_entity``/``oaw_kg_relation`` or the draft/review flow.
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

METADATA = MetaData()

BUNDLES = Table(
    "oaw_kb_experiment_bundle", METADATA,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), nullable=False),
    Column("schema_id", String(36), nullable=False),
    Column("name", String(500), nullable=False),
    # Free text the uploader adds alongside the files: what the table columns mean,
    # which specimen a photo shows — handed to the extraction prompt verbatim.
    Column("notes", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("run_id", String(64), nullable=True),
    Column("created_by", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_oaw_experiment_bundle_group", "group_id"),
    Index("ix_oaw_experiment_bundle_schema", "schema_id"),
)

# Membership doubles as the "experiment workflow" tag on a source: a source filed
# into any bundle belongs to the Experiment section, not Literature's source list.
BUNDLE_SOURCES = Table(
    "oaw_kb_experiment_bundle_source", METADATA,
    Column("source_id", String(36), primary_key=True),
    Column("bundle_id", String(36), nullable=False),
    Column("position", Integer, nullable=False),
    Index("ix_oaw_experiment_bundle_source_bundle", "bundle_id"),
)

RECORDS = Table(
    "oaw_kb_experiment_record", METADATA,
    Column("id", String(36), primary_key=True),
    Column("group_id", String(36), nullable=False),
    Column("schema_id", String(36), nullable=False),
    Column("name", String(500), nullable=False),
    Column("data", Text, nullable=False),
    # Legacy: the multi-file merge flow listed disagreeing values here. Kept so an
    # existing database needs no table rebuild; always ``[]`` for new records.
    Column("conflicts", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_by", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("bundle_id", String(36), nullable=True),
    Column("validation", Text, nullable=True),
    Column("note", Text, nullable=True),
    Index("ix_oaw_experiment_record_group", "group_id"),
    Index("ix_oaw_experiment_record_schema", "schema_id"),
    Index("ix_oaw_experiment_record_bundle", "bundle_id"),
)

RECORD_SOURCES = Table(
    "oaw_kb_experiment_record_source", METADATA,
    Column("record_id", String(36), primary_key=True),
    Column("source_id", String(36), primary_key=True),
    Column("artifact_id", String(36), nullable=True),
    Index("ix_oaw_experiment_record_source_record", "record_id"),
)

LEGACY_EVIDENCE = "oaw_kb_experiment_record_evidence"

STATUSES = ("draft", "published")
# "deleted" is a soft state: MKB cannot delete a source, so a removed bundle keeps
# its membership rows and its files stay out of Literature's source list.
BUNDLE_STATUSES = ("ready", "extracting", "extracted", "failed", "deleted")


class ConflictingRevision(KnowledgeError):
    """Raised when an optimistic experiment-record write loses a race."""


def _aware(value):
    # SQLite returns naive datetimes; keep every timestamp this store hands back
    # timezone-aware, matching what the rest of the card already returns.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _loads(value, default):
    if value is None:
        return default
    return json.loads(value)


def _row_json(row):
    return {
        "id": row["id"], "group_id": row["group_id"], "schema_id": row["schema_id"],
        "bundle_id": row["bundle_id"], "name": row["name"], "data": json.loads(row["data"]),
        "validation": _loads(row["validation"], None), "note": row["note"],
        "status": row["status"], "revision": row["revision"], "created_by": row["created_by"],
        "created_at": _aware(row["created_at"]), "updated_at": _aware(row["updated_at"]),
    }


def _bundle_json(row):
    return {
        "id": row["id"], "group_id": row["group_id"], "schema_id": row["schema_id"],
        "name": row["name"], "notes": row["notes"], "status": row["status"],
        "run_id": row["run_id"], "created_by": row["created_by"],
        "created_at": _aware(row["created_at"]), "updated_at": _aware(row["updated_at"]),
    }


def _columns(connection, table_name):
    return {row[1] for row in connection.exec_driver_sql(
        f"PRAGMA table_info({table_name})").fetchall()}


def _migrate(engine):
    """Bring a database from the multi-file merge era up to this layout, once.

    ``create_all(checkfirst=True)`` only creates missing tables, so the record
    table's new columns are added by hand, ``confirmed`` becomes ``published`` (the
    same human judgment under its new name), and the projection-keyed evidence table
    is folded into the source-keyed one and dropped.
    """
    with engine.begin() as connection:
        existing = _columns(connection, "oaw_kb_experiment_record")
        for name, ddl in (("bundle_id", "VARCHAR(36)"), ("validation", "TEXT"), ("note", "TEXT")):
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE oaw_kb_experiment_record ADD COLUMN {name} {ddl}")
        connection.exec_driver_sql(
            "UPDATE oaw_kb_experiment_record SET status='published' WHERE status='confirmed'")
        tables = {row[0] for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if LEGACY_EVIDENCE in tables:
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO oaw_kb_experiment_record_source (record_id, source_id, artifact_id) "
                f"SELECT record_id, source_id, MIN(artifact_id) FROM {LEGACY_EVIDENCE} "
                "GROUP BY record_id, source_id")
            connection.exec_driver_sql(f"DROP TABLE {LEGACY_EVIDENCE}")


class ExperimentRecordStore:
    """Durable bundle and record storage, independent of the draft/review/graph flow."""

    def __init__(self, engine):
        self._engine = engine
        METADATA.create_all(engine, checkfirst=True)
        _migrate(engine)

    # ------------------------------------------------------------ bundles

    def create_bundle(self, *, group_id, schema_id, name, notes="", created_by=None):
        identifier = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            connection.execute(insert(BUNDLES).values(
                id=identifier, group_id=str(group_id), schema_id=str(schema_id), name=name,
                notes=notes or "", status="ready", run_id=None, created_by=created_by,
                created_at=now, updated_at=now))
        return self.require_bundle(identifier)

    def get_bundle(self, bundle_id):
        with self._engine.connect() as connection:
            row = connection.execute(
                select(BUNDLES).where(BUNDLES.c.id == str(bundle_id))).mappings().first()
        return _bundle_json(row) if row else None

    def require_bundle(self, bundle_id):
        bundle = self.get_bundle(bundle_id)
        if bundle is None:
            raise KnowledgeError(f"Experiment upload not found: {bundle_id}")
        return bundle

    def list_bundles(self, *, group_id=None, schema_id=None, limit=100):
        query = (select(BUNDLES).where(BUNDLES.c.status != "deleted")
                 .order_by(BUNDLES.c.updated_at.desc()).limit(max(1, min(limit, 500))))
        if group_id:
            query = query.where(BUNDLES.c.group_id == str(group_id))
        if schema_id:
            query = query.where(BUNDLES.c.schema_id == str(schema_id))
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [_bundle_json(row) for row in rows]

    def update_bundle(self, bundle_id, *, name=None, notes=None, schema_id=None, status=None,
                      run_id=...):
        """``run_id`` uses an ellipsis sentinel so ``None`` can clear it explicitly.

        ``schema_id`` may only move while the bundle holds no records: each record was
        validated against the schema it was extracted with, so the two must agree.
        """
        if status is not None and status not in BUNDLE_STATUSES:
            raise KnowledgeError(f"status must be one of {BUNDLE_STATUSES}")
        values = {"updated_at": datetime.now(timezone.utc)}
        if name is not None:
            values["name"] = name
        if notes is not None:
            values["notes"] = notes
        if schema_id is not None:
            if self.list(bundle_id=bundle_id, limit=1):
                raise KnowledgeError("This upload already has records; re-extract after "
                                     "removing them to change its schema")
            values["schema_id"] = str(schema_id)
        if status is not None:
            values["status"] = status
        if run_id is not ...:
            values["run_id"] = run_id
        with self._engine.begin() as connection:
            result = connection.execute(
                update(BUNDLES).where(BUNDLES.c.id == str(bundle_id)).values(**values))
            if result.rowcount == 0:
                raise KnowledgeError(f"Experiment upload not found: {bundle_id}")
        return self.require_bundle(bundle_id)

    def delete_bundle(self, bundle_id):
        """Remove a bundle's records and mark it deleted.

        Membership rows stay: MKB offers no source deletion, and dropping the tag
        would surface the bundle's files in Literature as if they were papers.
        """
        identifier = str(bundle_id)
        with self._engine.begin() as connection:
            record_ids = [row[0] for row in connection.execute(
                select(RECORDS.c.id).where(RECORDS.c.bundle_id == identifier)).all()]
            if record_ids:
                connection.execute(delete(RECORD_SOURCES).where(
                    RECORD_SOURCES.c.record_id.in_(record_ids)))
                connection.execute(delete(RECORDS).where(RECORDS.c.id.in_(record_ids)))
            result = connection.execute(update(BUNDLES).where(BUNDLES.c.id == identifier).values(
                status="deleted", run_id=None, updated_at=datetime.now(timezone.utc)))
            if result.rowcount == 0:
                raise KnowledgeError(f"Experiment upload not found: {identifier}")
        return record_ids

    def add_bundle_source(self, bundle_id, source_id):
        with self._engine.begin() as connection:
            positions = [row[0] for row in connection.execute(
                select(BUNDLE_SOURCES.c.position).where(
                    BUNDLE_SOURCES.c.bundle_id == str(bundle_id))).all()]
            connection.execute(insert(BUNDLE_SOURCES).values(
                source_id=str(source_id), bundle_id=str(bundle_id),
                position=(max(positions) + 1) if positions else 0))

    def remove_bundle_source(self, source_id):
        with self._engine.begin() as connection:
            connection.execute(delete(BUNDLE_SOURCES).where(
                BUNDLE_SOURCES.c.source_id == str(source_id)))

    def bundle_source_ids(self, bundle_id):
        """Source ids in upload order."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(BUNDLE_SOURCES.c.source_id).where(
                    BUNDLE_SOURCES.c.bundle_id == str(bundle_id))
                .order_by(BUNDLE_SOURCES.c.position)).all()
        return [row[0] for row in rows]

    def bundle_of_source(self, source_id):
        with self._engine.connect() as connection:
            row = connection.execute(
                select(BUNDLE_SOURCES.c.bundle_id).where(
                    BUNDLE_SOURCES.c.source_id == str(source_id))).first()
        return row[0] if row else None

    def tagged_source_ids(self):
        """Every source that belongs to some bundle — Literature hides these."""
        with self._engine.connect() as connection:
            rows = connection.execute(select(BUNDLE_SOURCES.c.source_id)).all()
        return {row[0] for row in rows}

    # ------------------------------------------------------------ records

    def create(self, *, group_id, schema_id, name, data, bundle_id=None, validation=None,
              note=None, created_by=None, record_id=None):
        identifier = str(record_id or uuid.uuid4())
        now = datetime.now(timezone.utc)
        with self._engine.begin() as connection:
            connection.execute(insert(RECORDS).values(
                id=identifier, group_id=str(group_id), schema_id=str(schema_id),
                bundle_id=str(bundle_id) if bundle_id else None,
                name=name, data=json.dumps(data), conflicts="[]",
                validation=json.dumps(validation) if validation is not None else None,
                note=note, status="draft", revision=1, created_by=created_by,
                created_at=now, updated_at=now))
        return self.require(identifier)

    def get(self, record_id):
        with self._engine.connect() as connection:
            row = connection.execute(
                select(RECORDS).where(RECORDS.c.id == str(record_id))).mappings().first()
        return _row_json(row) if row else None

    def require(self, record_id):
        record = self.get(record_id)
        if record is None:
            raise KnowledgeError(f"Experiment record not found: {record_id}")
        return record

    def list(self, *, group_id=None, schema_id=None, bundle_id=None, source_id=None, status=None, limit=100):
        """``source_id`` narrows to records read from that file — a bundle's file, or
        a literature source records were extracted from directly."""
        query = select(RECORDS).order_by(RECORDS.c.updated_at.desc()).limit(max(1, min(limit, 500)))
        if group_id:
            query = query.where(RECORDS.c.group_id == str(group_id))
        if schema_id:
            query = query.where(RECORDS.c.schema_id == str(schema_id))
        if bundle_id:
            query = query.where(RECORDS.c.bundle_id == str(bundle_id))
        if source_id:
            query = query.where(RECORDS.c.id.in_(
                select(RECORD_SOURCES.c.record_id).where(RECORD_SOURCES.c.source_id == str(source_id))))
        if status:
            query = query.where(RECORDS.c.status == status)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [_row_json(row) for row in rows]

    def update(self, record_id, *, name=None, data=None, validation=None, note=None,
              status=None, expected_revision=None):
        with self._engine.begin() as connection:
            current = connection.execute(
                select(RECORDS).where(RECORDS.c.id == str(record_id))).mappings().first()
            if current is None:
                raise KnowledgeError(f"Experiment record not found: {record_id}")
            if expected_revision is not None and current["revision"] != expected_revision:
                raise ConflictingRevision(
                    f"Experiment record changed underneath this call (revision {current['revision']})")
            if status is not None and status not in STATUSES:
                raise KnowledgeError(f"status must be one of {STATUSES}")
            values = {"revision": current["revision"] + 1, "updated_at": datetime.now(timezone.utc)}
            if name is not None:
                values["name"] = name
            if data is not None:
                values["data"] = json.dumps(data)
            if validation is not None:
                values["validation"] = json.dumps(validation)
            if note is not None:
                values["note"] = note
            if status is not None:
                values["status"] = status
            connection.execute(update(RECORDS).where(RECORDS.c.id == str(record_id)).values(**values))
        return self.require(record_id)

    def delete(self, record_id):
        with self._engine.begin() as connection:
            connection.execute(delete(RECORD_SOURCES).where(
                RECORD_SOURCES.c.record_id == str(record_id)))
            result = connection.execute(delete(RECORDS).where(RECORDS.c.id == str(record_id)))
            if result.rowcount == 0:
                raise KnowledgeError(f"Experiment record not found: {record_id}")

    def delete_drafts_for_bundle(self, bundle_id):
        """Drop a bundle's unpublished records before a re-extraction replaces them."""
        return self._delete_drafts(RECORDS.c.bundle_id == str(bundle_id))

    def delete_drafts_for_source(self, *, schema_id, source_id):
        """Drop the unpublished records one literature source produced for one schema
        before a re-extraction replaces them; a bundle's records are left alone."""
        return self._delete_drafts(
            RECORDS.c.schema_id == str(schema_id), RECORDS.c.bundle_id.is_(None),
            RECORDS.c.id.in_(select(RECORD_SOURCES.c.record_id).where(
                RECORD_SOURCES.c.source_id == str(source_id))))

    def _delete_drafts(self, *conditions):
        with self._engine.begin() as connection:
            record_ids = [row[0] for row in connection.execute(
                select(RECORDS.c.id).where(*conditions, RECORDS.c.status == "draft")).all()]
            if record_ids:
                connection.execute(delete(RECORD_SOURCES).where(
                    RECORD_SOURCES.c.record_id.in_(record_ids)))
                connection.execute(delete(RECORDS).where(RECORDS.c.id.in_(record_ids)))
        return record_ids

    def set_sources(self, record_id, links):
        """Replace every source link for one record."""
        with self._engine.begin() as connection:
            connection.execute(delete(RECORD_SOURCES).where(
                RECORD_SOURCES.c.record_id == str(record_id)))
            rows, seen = [], set()
            for link in links:
                source_id = str(link["source_id"])
                if source_id in seen:
                    continue
                seen.add(source_id)
                rows.append({"record_id": str(record_id), "source_id": source_id,
                             "artifact_id": str(link["artifact_id"]) if link.get("artifact_id") else None})
            if rows:
                connection.execute(insert(RECORD_SOURCES), rows)

    def sources_for(self, record_id):
        with self._engine.connect() as connection:
            rows = connection.execute(select(RECORD_SOURCES).where(
                RECORD_SOURCES.c.record_id == str(record_id))).mappings().all()
        return [dict(row) for row in rows]
