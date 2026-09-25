"""Resource actions: every user and agent operation on a knowledge base card.

Each handler is deliberately short — validate, make a few MKB calls, return bounded
JSON — because the host holds the global node mutation lock for the whole call. Slow
work belongs in the ``oaw.to_markdown`` job or in the host LLM bridge.

``ingest`` and ``review`` carry no capability kind in ``__init__.py``, which makes them
desktop-only: uploading raw data and publishing a fact stay human acts.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .client import DEFAULT_COLLECTION, collection, open_client
from .errors import KnowledgeError
from .markdown import available_engines
from .pipelines import PIPELINE_NAME, submit_markdown_job

MAX_RESULT_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_CHARS = 200_000
DEFAULT_SETTINGS = {"collection_name": DEFAULT_COLLECTION, "pdf_engine": "auto",
                    "mineru_base_url": ""}
ACTIVE_JOBS = {"QUEUED", "RUNNING", "PENDING", "RETRYING"}


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


def settings_of(context):
    stored = context.state.get()["value"] if context.state is not None else {}
    values = dict(DEFAULT_SETTINGS)
    saved = stored.get("settings")
    if isinstance(saved, dict):
        values.update({key: saved[key] for key in DEFAULT_SETTINGS if key in saved})
    return values


def _base(context):
    """The client plus the card's single collection, opened lazily on first use."""
    settings = settings_of(context)
    kb = open_client(context.node_id, context.storage_path)
    return kb, collection(kb, settings["collection_name"]), settings


def _bounded(payload):
    # Round-trip through JSON so timestamps and UUIDs leave as plain strings and an
    # oversized result is rejected here rather than halfway through the response.
    encoded = json.dumps(payload, default=str).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise KnowledgeError(
            "Result exceeds 1 MiB; narrow the request with a smaller limit or an id")
    return json.loads(encoded)


def _actor(context):
    return f"agent:{context.actor_id}" if context.actor_id else "user:desktop"


def _uuid(value, label):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise KnowledgeError(f"{label} must be a UUID") from None


# ---------------------------------------------------------------- overview


class Overview(Request):
    pass


def overview(context, arguments):
    Overview.model_validate(arguments)
    kb, group, settings = _base(context)
    sources = kb.sources.list(collection_id=group.id, limit=500)
    jobs = kb.jobs.list(limit=50)
    drafts = kb.knowledge.list_drafts(collection_id=group.id, limit=200)
    counts = kb.oaw_graph_store.counts()
    return _bounded({
        "collection": {"id": str(group.id), "name": group.name},
        "settings": settings,
        "engines": available_engines(settings["mineru_base_url"] or None),
        "counts": {
            "sources": len(sources),
            "records": len(kb.records.list(collection_id=group.id, limit=500)),
            "schemas": len(kb.schemas.list(limit=200)),
            "projections": len(kb.projections.list(collection_id=group.id, limit=500)),
            "drafts": len(drafts),
            "pending_review": len([d for d in drafts if d.status in {"DRAFT", "SUBMITTED"}]),
            "facts": len(kb.knowledge.list_facts(collection_id=group.id, limit=500)),
            "entities": counts["entities"],
            "relations": counts["relations"],
        },
        "active_jobs": len([job for job in jobs if job.status in ACTIVE_JOBS]),
    })


# ---------------------------------------------------------------- settings


class Settings(Request):
    collection_name: str | None = Field(default=None, max_length=200)
    pdf_engine: Literal["auto", "pymupdf4llm", "mineru", "text"] | None = None
    mineru_base_url: str | None = Field(default=None, max_length=500)


def update_settings(context, arguments):
    request = Settings.model_validate(arguments)
    if context.state is None:
        raise KnowledgeError("Card state is unavailable")
    patch = {key: value for key, value in request.model_dump().items() if value is not None}
    if patch:
        current = settings_of(context)
        context.state.update({"settings": {**current, **patch}})
    return _bounded({"settings": settings_of(context)})


# ---------------------------------------------------------------- sources


class Sources(Request):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


def _record_link(kb, artifact_id):
    """The markdown artifact's record link.

    Evidence is listed per artifact, and ``save_projection`` deliberately copies a
    record's evidence onto the projection, so the same artifact accumulates links of
    several output types. Only the record one answers "what holds this markdown".
    """
    for link in kb.evidence.list(artifact_id=artifact_id, limit=100):
        if link.output_type == "record":
            return link
    return None


def sources(context, arguments):
    request = Sources.model_validate(arguments)
    kb, group, _ = _base(context)
    items = []
    for source in kb.sources.list(collection_id=group.id, limit=request.limit,
                                  offset=request.offset):
        artifacts = kb.artifacts.list(source_id=source.id, limit=10)
        markdown = next((a for a in artifacts if a.processing_type == "MARKDOWN"), None)
        record_id = None
        if markdown is not None:
            link = _record_link(kb, markdown.id)
            record_id = str(link.output_id) if link else None
        items.append({
            "id": str(source.id), "filename": source.filename,
            "media_type": source.media_type, "size": source.size,
            "created_at": source.created_at,
            "markdown": None if markdown is None else {
                "artifact_id": str(markdown.id), "size": markdown.size,
                "engine": (markdown.metadata or {}).get("engine")},
            "record_id": record_id,
            "projections": 0 if record_id is None else len(
                kb.projections.list(record_id=record_id, newest_only=True, limit=50)),
        })
    return _bounded({"sources": items, "offset": request.offset})


# ---------------------------------------------------------------- ingest


class Ingest(Request):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    media_type: str = Field(default="application/octet-stream", max_length=200)


def ingest(context, arguments):
    request = Ingest.model_validate(arguments)
    if "/" in request.filename or "\\" in request.filename:
        raise KnowledgeError("filename must not contain a path separator")
    try:
        data = base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise KnowledgeError("content_base64 is not valid base64") from None
    if not data:
        raise KnowledgeError("The uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise KnowledgeError("Uploads are limited to 32 MiB")

    kb, group, settings = _base(context)
    source = kb.sources.add_bytes(group.id, data, filename=request.filename,
                                  media_type=request.media_type,
                                  metadata={"uploaded_by": _actor(context)})
    # Conversion runs on the client's job thread; this handler must not block the lock.
    job = submit_markdown_job(kb, source, group.id, engine=settings["pdf_engine"],
                              mineru_base_url=settings["mineru_base_url"] or None)
    return _bounded({"source": {"id": str(source.id), "filename": source.filename,
                                "size": source.size, "sha256": source.sha256},
                     "job": {"id": str(job.id), "status": job.status}})


# ---------------------------------------------------------------- markdown


class Markdown(Request):
    source_id: str | None = None
    record_id: str | None = None
    limit: int = Field(default=40_000, ge=500, le=MAX_MARKDOWN_CHARS)
    offset: int = Field(default=0, ge=0)


def _markdown_record(kb, *, source_id=None, record_id=None):
    if record_id is not None:
        record = kb.records.require(_uuid(record_id, "record_id"))
    elif source_id is not None:
        identifier = _uuid(source_id, "source_id")
        artifacts = [a for a in kb.artifacts.list(source_id=identifier, limit=10)
                     if a.processing_type == "MARKDOWN"]
        if not artifacts:
            raise KnowledgeError(
                "This source has no markdown yet; wait for its conversion job to finish")
        link = _record_link(kb, artifacts[0].id)
        if link is None:
            raise KnowledgeError("The markdown artifact has no record link yet")
        record = kb.records.require(link.output_id)
    else:
        raise KnowledgeError("Provide either source_id or record_id")
    text = (record.data or {}).get("markdown")
    if not isinstance(text, str):
        raise KnowledgeError("That record does not hold markdown")
    return record, text


def markdown(context, arguments):
    request = Markdown.model_validate(arguments)
    kb, _, _ = _base(context)
    record, text = _markdown_record(kb, source_id=request.source_id,
                                    record_id=request.record_id)
    window = text[request.offset:request.offset + request.limit]
    return _bounded({
        "record_id": str(record.id), "filename": (record.data or {}).get("filename"),
        "engine": (record.data or {}).get("engine"),
        "total_characters": len(text), "offset": request.offset,
        "markdown": window,
        "has_more": request.offset + len(window) < len(text),
    })


# ---------------------------------------------------------------- schemas


class Schemas(Request):
    operation: Literal["list", "get", "create", "update"] = "list"
    schema_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    domain: str = Field(default="materials", max_length=100)
    definition: dict[str, Any] | None = None
    system_prompt: str | None = Field(default=None, max_length=20_000)
    field_descriptions: dict[str, Any] | None = None


def _schema_json(schema):
    return {"id": str(schema.id), "name": schema.name, "description": schema.description,
            "domain": schema.domain, "version": schema.version,
            "definition": schema.definition, "system_prompt": schema.system_prompt,
            "field_descriptions": schema.field_descriptions}


def schemas(context, arguments):
    request = Schemas.model_validate(arguments)
    kb, _, _ = _base(context)
    if request.operation == "list":
        return _bounded({"schemas": [
            {"id": str(s.id), "name": s.name, "description": s.description,
             "domain": s.domain, "version": s.version}
            for s in kb.schemas.list(limit=100)]})
    if request.operation == "get":
        if not request.schema_id:
            raise KnowledgeError("schema_id is required")
        return _bounded({"schema": _schema_json(kb.schemas.require(request.schema_id))})
    if request.operation == "create":
        if not (request.name and request.definition and request.system_prompt):
            raise KnowledgeError(
                "name, definition and system_prompt are required to create a schema")
        _validate_definition(request.definition)
        schema = kb.schemas.create(
            name=request.name, domain=request.domain, definition=request.definition,
            system_prompt=request.system_prompt, description=request.description,
            field_descriptions=request.field_descriptions)
        return _bounded({"schema": _schema_json(schema)})
    if not request.schema_id:
        raise KnowledgeError("schema_id is required")
    if request.definition is not None:
        _validate_definition(request.definition)
    schema = kb.schemas.update(
        request.schema_id, name=request.name, description=request.description,
        definition=request.definition, system_prompt=request.system_prompt,
        field_descriptions=request.field_descriptions)
    return _bounded({"schema": _schema_json(schema)})


def _validate_definition(definition):
    if definition.get("type") != "object" or not isinstance(definition.get("properties"), dict):
        raise KnowledgeError(
            "definition must be a JSON Schema object with a properties map")


# ---------------------------------------------------------------- projection


class ProjectionPrompt(Request):
    schema_id: str
    source_id: str | None = None
    record_id: str | None = None
    limit: int = Field(default=60_000, ge=500, le=MAX_MARKDOWN_CHARS)


def projection_prompt(context, arguments):
    """Everything the host bridge or an agent needs to produce the structured JSON."""
    request = ProjectionPrompt.model_validate(arguments)
    kb, _, _ = _base(context)
    schema = kb.schemas.require(request.schema_id)
    record, text = _markdown_record(kb, source_id=request.source_id,
                                    record_id=request.record_id)
    links = kb.evidence.list(output_id=record.id, limit=5)
    return _bounded({
        "schema_id": str(schema.id), "schema_name": schema.name,
        "schema_version": schema.version,
        "system_prompt": schema.system_prompt, "definition": schema.definition,
        "field_descriptions": schema.field_descriptions,
        "record_id": str(record.id),
        "artifact_id": str(links[0].artifact_id) if links else None,
        "source_id": str(links[0].source_id) if links else request.source_id,
        "filename": (record.data or {}).get("filename"),
        "markdown": text[:request.limit],
        "truncated": len(text) > request.limit,
    })


class SaveProjection(Request):
    schema_id: str
    record_id: str
    data: dict[str, Any]
    notes: str | None = Field(default=None, max_length=4000)
    model: str | None = Field(default=None, max_length=200)


def save_projection(context, arguments):
    request = SaveProjection.model_validate(arguments)
    kb, _, _ = _base(context)
    schema = kb.schemas.require(request.schema_id)
    record = kb.records.require(_uuid(request.record_id, "record_id"))
    validation = _check(schema.definition, request.data)

    with kb.transaction():
        projection = kb.projections.create(
            schema_id=schema.id, record_id=record.id, data=request.data,
            source_type="record", validation=validation, notes=request.notes)
        # Carry the record's own evidence forward so the projection stays traceable
        # to the exact artifact and uploaded file it came from (rule 4.2).
        for link in kb.evidence.list(output_id=record.id, limit=5):
            kb.evidence.create(
                output_type="projection", output_id=projection.id,
                source_id=link.source_id, artifact_id=link.artifact_id,
                locator=link.locator,
                metadata={"schema": schema.name, "schema_version": schema.version,
                          "model": request.model, "actor": _actor(context)})
    return _bounded({"projection": {"id": str(projection.id), "status": projection.status,
                                    "validation": validation}})


def _check(definition, data):
    """Best-effort JSON Schema validation; ``jsonschema`` is optional."""
    try:
        import jsonschema
    except ImportError:
        missing = [key for key in (definition.get("required") or []) if key not in data]
        return {"validator": "required-keys", "valid": not missing, "missing": missing}
    validator = jsonschema.Draft202012Validator(definition)
    errors = [f"{'/'.join(str(p) for p in error.path)}: {error.message}"
              for error in list(validator.iter_errors(data))[:20]]
    return {"validator": "jsonschema", "valid": not errors, "errors": errors}


class Projections(Request):
    projection_id: str | None = None
    record_id: str | None = None
    schema_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


def projections(context, arguments):
    request = Projections.model_validate(arguments)
    kb, group, _ = _base(context)
    if request.projection_id:
        item = kb.projections.require(_uuid(request.projection_id, "projection_id"))
        links = kb.evidence.list(output_id=item.id, limit=20)
        return _bounded({"projection": {
            "id": str(item.id), "schema_id": str(item.schema_id),
            "record_id": str(item.record_id), "status": item.status,
            "validation": item.validation, "notes": item.notes, "data": item.data,
            "evidence": [{"id": str(link.id), "source_id": str(link.source_id),
                          "artifact_id": str(link.artifact_id), "locator": link.locator}
                         for link in links]}})
    items = kb.projections.list(
        collection_id=group.id, record_id=request.record_id, schema_id=request.schema_id,
        newest_only=True, limit=request.limit)
    return _bounded({"projections": [
        {"id": str(item.id), "schema_id": str(item.schema_id),
         "record_id": str(item.record_id), "status": item.status,
         "valid": (item.validation or {}).get("valid"),
         "extracted_at": item.extracted_at,
         "summary": _summarize(item.data)} for item in items]})


def _summarize(data):
    if isinstance(data, dict):
        for key in ("name", "title", "sample_id", "id"):
            if isinstance(data.get(key), str):
                return data[key]
        return ", ".join(sorted(data)[:6])
    return str(data)[:120]


# ---------------------------------------------------------------- draft


class Draft(Request):
    operation: Literal["list", "get", "create"] = "list"
    draft_id: str | None = None
    projection_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=50, ge=1, le=200)


def draft(context, arguments):
    request = Draft.model_validate(arguments)
    kb, group, _ = _base(context)
    if request.operation == "list":
        return _bounded({"drafts": [
            {"id": str(item.id), "status": item.status, "revision": item.current_revision,
             "created_by": item.created_by, "updated_at": item.updated_at}
            for item in kb.knowledge.list_drafts(collection_id=group.id, limit=request.limit)]})
    if request.operation == "get":
        if not request.draft_id:
            raise KnowledgeError("draft_id is required")
        item = kb.knowledge.get_draft(_uuid(request.draft_id, "draft_id"))
        if item is None:
            raise KnowledgeError("No such draft")
        revision = kb.knowledge.get_revision(item.id)
        return _bounded({"draft": {
            "id": str(item.id), "status": item.status, "revision": item.current_revision,
            "created_by": item.created_by,
            "graph": revision.graph if revision else {},
            "evidence_ids": [str(value) for value in (revision.evidence_ids if revision else [])]}})

    if not request.projection_ids:
        raise KnowledgeError("Select at least one projection")
    graph, evidence_ids = build_graph(kb, request.projection_ids)
    if not graph["entities"]:
        raise KnowledgeError(
            "Those projections produced no entities; check the schema output shape")
    item, revision = kb.knowledge.create_draft(
        group.id, graph, evidence_ids=evidence_ids, actor=_actor(context))
    return _bounded({"draft": {"id": str(item.id), "status": item.status,
                               "revision": revision.revision,
                               "entities": len(graph["entities"]),
                               "relations": len(graph["relations"])}})


SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value):
    return SLUG.sub("_", str(value).strip().lower()).strip("_") or "value"


def build_graph(kb, projection_ids):
    """Turn selected projections into the ``{entities, relations}`` shape MKB extracts.

    A schema may emit that shape directly, which is passed through. Anything else is
    mapped generically: one entity per projection holding its scalar fields, with
    nested objects becoming linked child entities.
    """
    entities, relations, evidence_ids, seen = [], [], [], set()
    for identifier in projection_ids:
        item = kb.projections.require(_uuid(identifier, "projection_id"))
        for link in kb.evidence.list(output_id=item.id, limit=20):
            evidence_ids.append(str(link.id))
        data = item.data if isinstance(item.data, dict) else {"value": item.data}
        schema = kb.schemas.get(item.schema_id)
        if isinstance(data.get("entities"), list):
            _extend(entities, data["entities"], seen)
            if isinstance(data.get("relations"), list):
                relations.extend(data["relations"])
            continue
        root = _generic(data, schema, item, entities, relations, seen)
        if root is None:
            continue
    return {"entities": entities, "relations": relations}, evidence_ids


def _extend(entities, candidates, seen):
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = (str(candidate.get("type") or "concept"), str(candidate.get("name") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        entities.append(candidate)


def _generic(data, schema, projection, entities, relations, seen):
    root_type = _slug(schema.name if schema else "record")
    root_name = _summarize(data) or f"projection {str(projection.id)[:8]}"
    scalars = {key: value for key, value in data.items()
               if isinstance(value, (str, int, float, bool)) or value is None}
    _extend(entities, [{"type": root_type, "name": root_name, **scalars,
                        "projection_id": str(projection.id)}], seen)
    for key, value in data.items():
        children = value if isinstance(value, list) else [value]
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            child_name = _summarize(child) or f"{key} {index + 1}"
            _extend(entities, [{"type": _slug(key), "name": child_name,
                                **{k: v for k, v in child.items()
                                   if isinstance(v, (str, int, float, bool))}}], seen)
            relations.append({"source": root_name, "target": child_name, "type": _slug(key)})
    return root_name


# ---------------------------------------------------------------- review


class Review(Request):
    operation: Literal["submit", "approve", "reject"]
    draft_id: str
    expected_revision: int = Field(ge=1)
    notes: str | None = Field(default=None, max_length=4000)


def review(context, arguments):
    """Publishing a fact is the only thing that writes the graph (rule 4.1)."""
    request = Review.model_validate(arguments)
    kb, _, _ = _base(context)
    identifier = _uuid(request.draft_id, "draft_id")

    if request.operation == "submit":
        decision = kb.knowledge.submit_review(
            identifier, expected_revision=request.expected_revision,
            actor=_actor(context), notes=request.notes)
        return _bounded({"decision": decision.decision, "draft_id": str(decision.draft_id)})
    if request.operation == "reject":
        decision = kb.knowledge.reject(
            identifier, expected_revision=request.expected_revision,
            actor=_actor(context), notes=request.notes)
        return _bounded({"decision": decision.decision, "draft_id": str(decision.draft_id)})

    if not context.confirmed:
        return {"status": "confirmation_required",
                "reasons": ["Approval publishes this draft as a permanent fact revision "
                            "and writes it into the knowledge graph."],
                "message": "Approve this draft?"}
    revision_row = kb.knowledge.get_revision(identifier, request.expected_revision)
    if revision_row is None:
        raise KnowledgeError("No such draft revision")
    decision, fact, event = kb.knowledge.approve(
        identifier, expected_revision=request.expected_revision,
        actor=_actor(context), notes=request.notes)
    # The published fact — never the raw model output — is what enters the graph.
    result = kb.graph.extract(fact.data, extractor=lambda payload: payload)
    return _bounded({
        "decision": decision.decision,
        "fact": {"id": str(fact.id), "fact_set_id": str(fact.fact_set_id),
                 "revision": fact.revision, "status": fact.status},
        "event": {"id": str(event.id), "type": event.event_type},
        "graph": {"entities": len(result.entities), "relations": len(result.relations)},
    })


# ---------------------------------------------------------------- graph


class GraphQuery(Request):
    operation: Literal["query", "traverse"] = "query"
    entity_id: str | None = None
    entity_type: str | None = Field(default=None, max_length=200)
    relation_type: str | None = Field(default=None, max_length=200)
    name_contains: str | None = Field(default=None, max_length=200)
    max_depth: int = Field(default=1, ge=1, le=5)
    direction: Literal["both", "out", "in"] = "both"
    limit: int = Field(default=200, ge=1, le=1000)


def graph(context, arguments):
    request = GraphQuery.model_validate(arguments)
    kb, _, _ = _base(context)
    if request.operation == "traverse":
        if not request.entity_id:
            raise KnowledgeError("entity_id is required to traverse")
        result = kb.graph.traverse(_uuid(request.entity_id, "entity_id"),
                                   max_depth=request.max_depth, direction=request.direction)
    else:
        result = kb.graph.query(entity_type=request.entity_type,
                                relation_type=request.relation_type,
                                name_contains=request.name_contains)
    entities = list(result.entities)[:request.limit]
    kept = {str(entity.id) for entity in entities}
    return _bounded({
        "entities": [{"id": str(e.id), "type": e.type, "name": e.name,
                      "properties": e.properties} for e in entities],
        "relations": [{"id": str(r.id), "type": r.type, "source_id": str(r.source_id),
                       "target_id": str(r.target_id), "properties": r.properties}
                      for r in result.relations
                      if str(r.source_id) in kept and str(r.target_id) in kept][:request.limit],
        "truncated": len(result.entities) > len(entities),
    })


# ---------------------------------------------------------------- jobs


class Jobs(Request):
    job_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    after: int = Field(default=0, ge=0)


def jobs(context, arguments):
    request = Jobs.model_validate(arguments)
    kb, _, _ = _base(context)
    if request.job_id:
        job = kb.jobs.require(_uuid(request.job_id, "job_id"))
        # jobs.events() yields plain dicts from a generator, not typed models.
        events = list(kb.jobs.events(job.id, after=request.after))[-50:]
        return _bounded({"job": _job_json(job), "events": [
            {"event": item.get("event"), "step": item.get("step_name"),
             "message": item.get("message"), "level": item.get("level"),
             "occurred_at": item.get("occurred_at")} for item in events]})
    return _bounded({"jobs": [_job_json(job) for job in kb.jobs.list(limit=request.limit)]})


def _job_json(job):
    return {"id": str(job.id), "kind": job.kind, "status": job.status,
            "pipeline": job.pipeline_name or PIPELINE_NAME, "progress": job.progress,
            "message": job.message, "error": job.error,
            "source_id": (job.inputs or {}).get("source_id"),
            "created_at": job.created_at, "completed_at": job.completed_at}
