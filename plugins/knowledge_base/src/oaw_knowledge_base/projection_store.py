"""Durable project/Space runs and immutable result history.

A selection is a batch of project ids, never the identity of a result. SQLite's
write lock serializes claiming and completing a run, including independent clients.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import uuid

from sqlalchemy import Column, Index, Integer, MetaData, String, Table, Text, func, insert, select, update

from .errors import KnowledgeError
from .experiment_store import ConflictingRevision


METADATA = MetaData()
RUNS = Table(
    "oaw_kb_projection", METADATA,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), nullable=False),
    Column("project_name", Text, nullable=False),
    Column("space_id", String(36), nullable=False),
    Column("space_name", Text, nullable=False),
    Column("space_version", Integer, nullable=False),
    Column("kind", String(20), nullable=False),
    Column("space_snapshot", Text, nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("sources", Text, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("data", Text, nullable=True),
    Column("error", Text, nullable=True),
    Column("parent_id", String(36), nullable=True),
    Column("note", Text, nullable=True),
    Column("created_by", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Index("ix_oaw_projection_pair", "project_id", "space_id", "generation", unique=True),
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _row(row):
    value = dict(row)
    for key in ("space_snapshot", "sources", "data"):
        if value[key] is not None:
            value[key] = json.loads(value[key])
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


class ProjectionStore:
    def __init__(self, engine):
        self.engine = engine
        METADATA.create_all(engine)

    @contextmanager
    def _write(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _require(connection, identifier):
        row = connection.execute(select(RUNS).where(RUNS.c.id == identifier)).mappings().first()
        if row is None:
            raise KnowledgeError("Projection not found")
        return _row(row)

    @staticmethod
    def _latest(connection, project_id, space_id):
        row = connection.execute(select(RUNS).where(
            RUNS.c.project_id == project_id, RUNS.c.space_id == space_id,
        ).order_by(RUNS.c.generation.desc()).limit(1)).mappings().first()
        return _row(row) if row else None

    def require(self, identifier):
        with self.engine.connect() as connection:
            return self._require(connection, identifier)

    def list(self, *, project_id=None, space_id=None, include_history=False, limit=100, offset=0):
        conditions = []
        if project_id:
            conditions.append(RUNS.c.project_id == project_id)
        if space_id:
            conditions.append(RUNS.c.space_id == space_id)
        if include_history:
            query = select(RUNS).where(*conditions)
        else:
            ranked = select(RUNS.c.id, func.row_number().over(
                partition_by=(RUNS.c.project_id, RUNS.c.space_id),
                order_by=RUNS.c.generation.desc(),
            ).label("position")).where(*conditions).subquery()
            query = select(RUNS).join(ranked, ranked.c.id == RUNS.c.id).where(ranked.c.position == 1)
        with self.engine.connect() as connection:
            return [_row(row) for row in connection.execute(
                query.order_by(RUNS.c.updated_at.desc(), RUNS.c.id).limit(limit).offset(offset),
            ).mappings()]

    def prepare(self, *, project_id, project_name, space, sources, fingerprint, actor, rerun=False):
        with self._write() as connection:
            current = self._latest(connection, project_id, space["id"])
            if current and current["status"] == "running" and not rerun:
                return current, "running"
            if (current and not rerun and current["status"] == "completed"
                    and current["space_version"] == space["version"]
                    and current["source_fingerprint"] == fingerprint):
                return current, "reused"
            now = _now()
            if current and current["status"] == "running":
                connection.execute(update(RUNS).where(RUNS.c.id == current["id"]).values(
                    status="failed", error="Superseded by an explicit rerun", updated_at=now,
                    revision=current["revision"] + 1,
                ))
            identifier = str(uuid.uuid4())
            connection.execute(insert(RUNS).values(
                id=identifier, project_id=project_id, project_name=project_name,
                space_id=space["id"], space_name=space["name"], space_version=space["version"],
                kind=space["kind"], space_snapshot=_json(space), source_fingerprint=fingerprint,
                sources=_json(sources), generation=(current["generation"] + 1 if current else 1),
                revision=1, status="running", parent_id=current["id"] if current else None,
                created_by=actor, created_at=now, updated_at=now,
            ))
            return self._require(connection, identifier), "created"

    def complete(self, identifier, data, *, expected_revision, validate_current):
        encoded = _json(data)
        with self._write() as connection:
            current = self._require(connection, identifier)
            if current["status"] == "completed" and _json(current["data"]) == encoded:
                return current  # A retry of the same completion is harmless.
            if current["revision"] != expected_revision:
                raise ConflictingRevision("Projection changed; refresh it before saving")
            if current["status"] != "running":
                raise KnowledgeError("Only a running projection can accept extraction results")
            validate_current(current)
            connection.execute(update(RUNS).where(RUNS.c.id == identifier).values(
                data=encoded, status="completed", error=None,
                revision=current["revision"] + 1, updated_at=_now(),
            ))
            return self._require(connection, identifier)

    def fail(self, identifier, error, *, expected_revision):
        with self._write() as connection:
            current = self._require(connection, identifier)
            if current["status"] != "running":
                return current
            if current["revision"] != expected_revision:
                raise ConflictingRevision("Projection changed; refresh it before marking failure")
            connection.execute(update(RUNS).where(RUNS.c.id == identifier).values(
                status="failed", error=error, revision=current["revision"] + 1, updated_at=_now(),
            ))
            return self._require(connection, identifier)

    def revise(self, identifier, data, *, expected_revision, note, actor, validate_current):
        with self._write() as connection:
            current = self._require(connection, identifier)
            latest = self._latest(connection, current["project_id"], current["space_id"])
            if current["revision"] != expected_revision or latest["id"] != identifier:
                raise ConflictingRevision("Projection changed; refresh the latest result before revising")
            if current["status"] != "completed":
                raise KnowledgeError("Only a completed result can be revised")
            validate_current(current)
            identifier, now = str(uuid.uuid4()), _now()
            values = dict(current)
            values.update(id=identifier, parent_id=current["id"], generation=current["generation"] + 1,
                          revision=1, data=data, note=note, created_by=actor, created_at=now, updated_at=now)
            for key in ("space_snapshot", "sources", "data"):
                values[key] = _json(values[key])
            connection.execute(insert(RUNS).values(**values))
            return self._require(connection, identifier)
