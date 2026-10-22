"""Resource actions: every user and agent operation on a knowledge base card.

Each handler is deliberately short — validate, make a few MKB calls, return bounded
JSON — because the host holds the global node mutation lock for the whole call. Slow
work belongs in the ``oaw.to_markdown`` job or in the host LLM bridge.

``ingest``, ``process``, ``groups`` and ``review`` carry no capability kind in
``__init__.py``, which makes them desktop-only: uploading raw data, converting it,
managing groups and publishing a fact stay human acts.
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
from .graph_store import LAYERS
from .markdown import available_engines
from .pipelines import PIPELINE_NAME, submit_markdown_job

MAX_RESULT_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_CHARS = 200_000
# ``vision_model`` is an OAW model reference ("oaw:model:..."), or "" for the OAW
# default model; the OAW_VISION_* variables are the fallback when neither resolves.
DEFAULT_SETTINGS = {"collection_name": DEFAULT_COLLECTION, "pdf_engine": "auto",
                    "mineru_base_url": "", "vision_model": ""}
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


def _resolve_group(kb, group_id, settings):
    """One concrete group: an explicit id, or the card's default collection.

    The default collection is get-or-created from the ``collection_name`` setting,
    which is what every card had before groups existed — so an old card with no
    group management keeps working exactly as it did, as the group nobody named.
    """
    if group_id is None:
        return collection(kb, settings["collection_name"])
    return kb.collections.require(_uuid(group_id, "group_id"))


def _client(context):
    return open_client(context.node_id, context.storage_path,
                       resolve_secret=getattr(context, "resolve_secret", None),
                       resolve_model=getattr(context, "resolve_model", None))


def _base(context, group_id=None):
    """The client plus one resolved group, opened lazily on first use."""
    settings = settings_of(context)
    kb = _client(context)
    return kb, _resolve_group(kb, group_id, settings), settings


def _any_group(group_id):
    """``None`` means "every group"; otherwise the exact id to filter listings by."""
    return _uuid(group_id, "group_id") if group_id else None


def _scope(group, all_groups):
    """The collection_id to filter a listing by: one group, or every group.

    Historically a card had exactly one collection, so every listing filtered by
    it without a choice. ``all_groups`` opts a caller into the merged, cross-group
    view instead of changing what an omitted ``group_id`` already meant.
    """
    return None if all_groups else group.id


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
    group_id: str | None = None
    all_groups: bool = False


def overview(context, arguments):
    request = Overview.model_validate(arguments)
    kb, group, settings = _base(context, request.group_id)
    # An explicit group, or the resolved default: the same single-group view every
    # card had before groups existed. all_groups opts into totals for the whole card.
    scope = _scope(group, request.all_groups)
    sources = kb.sources.list(collection_id=scope, limit=500)
    jobs = kb.jobs.list(limit=50)
    drafts = kb.knowledge.list_drafts(collection_id=scope, limit=200)
    counts = kb.oaw_graph_store.counts(group_id=scope)
    default_group_id = str(collection(kb, settings["collection_name"]).id)
    return _bounded({
        "collection": {"id": str(group.id), "name": group.name},
        "groups": [_group_json(item, default_group_id) for item in kb.collections.list(limit=500)],
        "settings": settings,
        "engines": available_engines(settings["mineru_base_url"] or None,
                                     resolve_secret=getattr(context, "resolve_secret", None),
                                     resolve_model=getattr(context, "resolve_model", None),
                                     vision_model=settings["vision_model"]),
        "graph_schema_id": graph_schema_id_of(context) or None,
        "counts": {
            "sources": len(sources),
            "records": len(kb.records.list(collection_id=scope, limit=500)),
            "schemas": len(kb.schemas.list(limit=200)),
            "projections": len(kb.projections.list(collection_id=scope, limit=500)),
            "drafts": len(drafts),
            "pending_review": len([d for d in drafts if d.status in {"DRAFT", "SUBMITTED"}]),
            "facts": len(kb.knowledge.list_facts(collection_id=scope, limit=500)),
            "entities": counts["entities"],
            "relations": counts["relations"],
            "experiment_bundles": len(kb.oaw_experiments.list_bundles(
                group_id=str(scope) if scope else None, limit=500)),
            "experiment_records": len(kb.oaw_experiments.list(
                group_id=str(scope) if scope else None, limit=500)),
            "insights": len(kb.oaw_insights.list(
                group_id=str(scope) if scope else None, limit=500)),
            "published_insights": len(kb.oaw_insights.list(
                group_id=str(scope) if scope else None, status="published", limit=500)),
            "layers": {layer: kb.oaw_graph_store.counts(group_id=scope, layer=layer)
                       for layer in LAYERS},
        },
        "active_jobs": len([job for job in jobs if job.status in ACTIVE_JOBS]),
    })


# ---------------------------------------------------------------- settings


class Settings(Request):
    collection_name: str | None = Field(default=None, max_length=200)
    pdf_engine: Literal["auto", "pymupdf4llm", "mineru", "text"] | None = None
    mineru_base_url: str | None = Field(default=None, max_length=500)
    vision_model: str | None = Field(default=None, max_length=200)


def update_settings(context, arguments):
    request = Settings.model_validate(arguments)
    if context.state is None:
        raise KnowledgeError("Card state is unavailable")
    patch = {key: value for key, value in request.model_dump().items() if value is not None}
    if patch:
        current = settings_of(context)
        context.state.update({"settings": {**current, **patch}})
    return _bounded({"settings": settings_of(context)})


# ---------------------------------------------------------------- groups


class Groups(Request):
    operation: Literal["list", "create", "rename", "delete"] = "list"
    group_id: str | None = None
    name: str | None = Field(default=None, max_length=200)


def _group_json(item, default_group_id):
    return {"id": str(item.id), "name": item.name, "source_count": item.source_count,
            "created_at": item.created_at, "is_default": str(item.id) == default_group_id}


def groups(context, arguments):
    """Create, rename and delete the groups sources are filed into.

    A group is one MKB collection: sources, and everything derived from them
    (records, projections, drafts and the graph) already filter by ``collection_id``
    everywhere MKB's own repositories do, so using collections as groups gets that
    scoping for free instead of the plugin re-implementing it.
    """
    request = Groups.model_validate(arguments)
    settings = settings_of(context)
    kb = _client(context)
    default_group_id = str(collection(kb, settings["collection_name"]).id)

    if request.operation == "list":
        return _bounded({"groups": [
            _group_json(item, default_group_id) for item in kb.collections.list(limit=500)]})

    clean_name = request.name.strip() if request.name else None
    if request.operation == "create":
        if not clean_name:
            raise KnowledgeError("name is required to create a group")
        if any(item.name == clean_name for item in kb.collections.list(limit=500)):
            raise KnowledgeError(f"A group named '{clean_name}' already exists")
        item = kb.collections.create(name=clean_name)
        return _bounded({"group": _group_json(item, default_group_id)})

    if not request.group_id:
        raise KnowledgeError("group_id is required")
    identifier = _uuid(request.group_id, "group_id")
    if request.operation == "rename":
        if not clean_name:
            raise KnowledgeError("name is required to rename a group")
        item = kb.collections.update(identifier, name=clean_name)
        return _bounded({"group": _group_json(item, default_group_id)})

    # delete
    if len(kb.collections.list(limit=500)) <= 1:
        raise KnowledgeError("Cannot delete the last remaining group")
    kb.collections.delete(identifier)
    return _bounded({"deleted": str(identifier)})


# ---------------------------------------------------------------- sources


class Sources(Request):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    group_id: str | None = None
    all_groups: bool = False
    # Files uploaded into an experiment bundle belong to the Experiment section;
    # Literature's source list leaves them out unless asked otherwise.
    workflow: Literal["literature", "experiment", "all"] = "literature"


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
    kb, group, _ = _base(context, request.group_id)
    # An explicit group, or the default; all_groups lists every source across every
    # group instead, each still carrying its own group for a per-group header.
    scope = _scope(group, request.all_groups)
    names = {str(item.id): item.name for item in kb.collections.list(limit=500)}
    tagged = kb.oaw_experiments.tagged_source_ids()
    items = []
    for source in kb.sources.list(collection_id=scope,
                                  limit=request.limit, offset=request.offset):
        is_experiment = str(source.id) in tagged
        if request.workflow == "literature" and is_experiment:
            continue
        if request.workflow == "experiment" and not is_experiment:
            continue
        artifacts = kb.artifacts.list(source_id=source.id, limit=10)
        markdown = next((a for a in artifacts if a.processing_type == "MARKDOWN"), None)
        record_id = None
        if markdown is not None:
            link = _record_link(kb, markdown.id)
            record_id = str(link.output_id) if link else None
        source_group_id = str(source.collection_ids[0]) if source.collection_ids else None
        items.append({
            "id": str(source.id), "filename": source.filename,
            "media_type": source.media_type, "size": source.size,
            "created_at": source.created_at,
            "group_id": source_group_id, "group_name": names.get(source_group_id),
            "workflow": "experiment" if is_experiment else "literature",
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
    group_id: str | None = None


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

    kb, group, _ = _base(context, request.group_id)
    source = kb.sources.add_bytes(group.id, data, filename=request.filename,
                                  media_type=request.media_type,
                                  metadata={"uploaded_by": _actor(context)})
    # Storing a source no longer queues its own conversion: uploads can be batched
    # and converted together with knowledge_process, on the caller's own schedule.
    return _bounded({"source": {"id": str(source.id), "filename": source.filename,
                                "size": source.size, "sha256": source.sha256},
                     "group_id": str(group.id)})


# ---------------------------------------------------------------- process


class ProcessSources(Request):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    group_id: str | None = None


def _has_markdown(kb, source_id):
    return any(a.processing_type == "MARKDOWN"
               for a in kb.artifacts.list(source_id=source_id, limit=10))


def _active_job_for(jobs, source_id):
    target = str(source_id)
    for job in jobs:
        if (job.inputs or {}).get("source_id") == target and job.status in ACTIVE_JOBS:
            return job
    return None


def process_sources(context, arguments):
    """Convert a batch of sources to markdown: the explicit step ``ingest`` no
    longer takes on its own. With no ``source_ids``, this processes every
    unconverted source (optionally narrowed to one group) — the "process all
    pending" button.
    """
    request = ProcessSources.model_validate(arguments)
    settings = settings_of(context)
    kb = _client(context)

    if request.source_ids:
        sources_to_run = [kb.sources.require(_uuid(value, "source_ids"))
                          for value in request.source_ids]
    else:
        sources_to_run = [source for source in
                          kb.sources.list(collection_id=_any_group(request.group_id), limit=500)
                          if not _has_markdown(kb, source.id)]

    active_jobs = kb.jobs.list(limit=200)
    results = []
    for source in sources_to_run:
        if _has_markdown(kb, source.id):
            results.append({"source_id": str(source.id), "filename": source.filename,
                            "skipped": "already converted"})
            continue
        active = _active_job_for(active_jobs, source.id)
        if active is not None:
            results.append({"source_id": str(source.id), "filename": source.filename,
                            "job": {"id": str(active.id), "status": active.status}})
            continue
        source_group_id = (source.collection_ids[0] if source.collection_ids
                           else collection(kb, settings["collection_name"]).id)
        job = submit_markdown_job(kb, source, source_group_id, engine=settings["pdf_engine"],
                                  mineru_base_url=settings["mineru_base_url"] or None,
                                  vision_model=settings["vision_model"])
        results.append({"source_id": str(source.id), "filename": source.filename,
                        "job": {"id": str(job.id), "status": job.status}})
    return _bounded({"jobs": results})


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


# ---------------------------------------------------------------- search


class Search(Request):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)
    group_id: str | None = None
    all_groups: bool = False


def search(context, arguments):
    """Full-text search over every converted document's markdown.

    Returns raw passages from the document text, ranked by keyword match — never a
    verified fact. A caller (agent or person) must attribute an answer built from
    these results to the ``filename``/``heading_path`` returned with each one.
    """
    request = Search.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    scope = _scope(group, request.all_groups)
    from .search_store import search as search_chunks

    names = {str(item.id): item.name for item in kb.collections.list(limit=500)}
    filenames: dict[str, str | None] = {}
    results = []
    for hit in search_chunks(kb.oaw_engine, request.query, limit=request.limit, group_id=scope):
        source_id = hit["source_id"]
        if source_id not in filenames:
            try:
                filenames[source_id] = kb.sources.require(source_id).filename
            except Exception:
                # A source can be gone by the time its chunk surfaces; the excerpt
                # and heading still stand on their own, just without a filename.
                filenames[source_id] = None
        results.append({
            "source_id": source_id, "filename": filenames[source_id],
            "record_id": hit["record_id"], "group_id": hit["group_id"],
            "group_name": names.get(hit["group_id"]),
            "heading_path": hit["heading_path"] or None,
            "excerpt": hit["text"][:800],
        })
    return _bounded({"query": request.query, "results": results})


# ---------------------------------------------------------------- schemas


SCHEMA_KINDS = ("literature", "experiment")


def _schema_kind(schema):
    """MKB's own ``purpose`` field, read back as one of OAW's two workflow kinds.

    Every schema created before this existed has ``purpose="freeform"`` (MKB's own
    default): treat anything that is not explicitly ``"experiment"`` as literature, so
    no migration is needed for schemas that already exist.
    """
    return "experiment" if schema.purpose == "experiment" else "literature"


class Schemas(Request):
    operation: Literal["list", "get", "create", "update"] = "list"
    schema_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    domain: str = Field(default="materials", max_length=100)
    definition: dict[str, Any] | None = None
    system_prompt: str | None = Field(default=None, max_length=20_000)
    field_descriptions: dict[str, Any] | None = None
    # For "list": filters to one kind, or every schema when omitted. For "create": the
    # kind to tag the new schema with (defaults to "literature"). For "update": the kind
    # to retag it as, or leave unchanged when omitted.
    kind: Literal["literature", "experiment"] | None = None


def _schema_json(schema):
    return {"id": str(schema.id), "name": schema.name, "description": schema.description,
            "domain": schema.domain, "kind": _schema_kind(schema), "version": schema.version,
            "definition": schema.definition, "system_prompt": schema.system_prompt,
            "field_descriptions": schema.field_descriptions}


def schemas(context, arguments):
    request = Schemas.model_validate(arguments)
    kb, _, _ = _base(context)
    if request.operation == "list":
        items = kb.schemas.list(limit=100)
        if request.kind is not None:
            items = [s for s in items if _schema_kind(s) == request.kind]
        return _bounded({"schemas": [
            {"id": str(s.id), "name": s.name, "description": s.description,
             "domain": s.domain, "kind": _schema_kind(s), "version": s.version}
            for s in items]})
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
            field_descriptions=request.field_descriptions,
            purpose=request.kind or "literature")
        return _bounded({"schema": _schema_json(schema)})
    if not request.schema_id:
        raise KnowledgeError("schema_id is required")
    if request.definition is not None:
        _validate_definition(request.definition)
    schema = kb.schemas.update(
        request.schema_id, name=request.name, description=request.description,
        definition=request.definition, system_prompt=request.system_prompt,
        field_descriptions=request.field_descriptions, purpose=request.kind)
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
    group_id: str | None = None
    all_groups: bool = False
    limit: int = Field(default=50, ge=1, le=200)


def projections(context, arguments):
    request = Projections.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
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
        collection_id=_scope(group, request.all_groups), record_id=request.record_id,
        schema_id=request.schema_id, newest_only=True, limit=request.limit)
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
    group_id: str | None = None
    all_groups: bool = False
    limit: int = Field(default=50, ge=1, le=200)


def draft(context, arguments):
    request = Draft.model_validate(arguments)
    if request.operation == "list":
        kb, group, _ = _base(context, request.group_id)
        return _bounded({"drafts": [
            {"id": str(item.id), "status": item.status, "revision": item.current_revision,
             "created_by": item.created_by, "updated_at": item.updated_at}
            for item in kb.knowledge.list_drafts(
                collection_id=_scope(group, request.all_groups), limit=request.limit)]})
    kb = _client(context)
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
    group_id = _projections_group(kb, request.projection_ids)
    graph, evidence_ids = build_graph(kb, request.projection_ids)
    if not graph["entities"]:
        raise KnowledgeError(
            "Those projections produced no entities; check the schema output shape")
    item, revision = kb.knowledge.create_draft(
        group_id, graph, evidence_ids=evidence_ids, actor=_actor(context))
    return _bounded({"draft": {"id": str(item.id), "status": item.status,
                               "revision": revision.revision,
                               "entities": len(graph["entities"]),
                               "relations": len(graph["relations"])}})


def _projections_group(kb, projection_ids):
    """The one group every selected projection's record belongs to.

    A draft becomes one collection_id in ``kb.knowledge.create_draft``, so mixing
    projections from two groups into it would blur which group's sources back the
    published facts — keep that traceable by refusing the mix instead.
    """
    group_ids = set()
    for identifier in projection_ids:
        item = kb.projections.require(_uuid(identifier, "projection_id"))
        record = kb.records.require(item.record_id)
        group_ids.add(str(record.collection_id))
    if len(group_ids) > 1:
        raise KnowledgeError(
            "Select projections from a single group to build one draft")
    return uuid.UUID(next(iter(group_ids)))


# ---------------------------------------------------------------- experiments


EXTRACT_INSTRUCTION = (
    "The document below is one experiment upload: several files (a photographed table, a "
    "spreadsheet, a note) joined in order under '## File:' headings, plus the uploader's own "
    "notes. Project it onto the JSON Schema: produce one record per specimen, sample, run or "
    "table row the files describe, each with its fields filled from the document only. Leave a "
    "field out rather than guess it. Reply with exactly this JSON shape: "
    "{\"records\": [{\"name\": <short label, e.g. the sample id>, \"data\": <object matching the "
    "schema>, \"source_ids\": [<ids of the files this record was read from>], "
    "\"note\": <optional: where in the files it came from, or what was unclear>}]}.")


def _experiment_schema(kb, schema_id):
    schema = kb.schemas.require(schema_id)
    if _schema_kind(schema) != "experiment":
        raise KnowledgeError('Only a schema tagged kind="experiment" can hold experiment records')
    return schema


def _bundle_files(kb, bundle):
    """Each file in the bundle with its conversion state, in upload order."""
    jobs = kb.jobs.list(limit=200)
    files = []
    for source_id in kb.oaw_experiments.bundle_source_ids(bundle["id"]):
        source = kb.sources.get(source_id)
        if source is None:
            continue
        markdown = next((a for a in kb.artifacts.list(source_id=source.id, limit=10)
                         if a.processing_type == "MARKDOWN"), None)
        record_id = None
        if markdown is not None:
            link = _record_link(kb, markdown.id)
            record_id = str(link.output_id) if link else None
        job = next((j for j in jobs if (j.inputs or {}).get("source_id") == str(source.id)), None)
        if record_id:
            state = "converted"
        elif job is not None and job.status in ACTIVE_JOBS:
            state = "converting"
        elif job is not None and job.status == "FAILED":
            state = "failed"
        else:
            state = "pending"
        files.append({
            "source_id": str(source.id), "filename": source.filename,
            "media_type": source.media_type, "size": source.size,
            "artifact_id": str(markdown.id) if markdown else None, "record_id": record_id,
            "engine": (markdown.metadata or {}).get("engine") if markdown else None,
            "state": state, "job_id": str(job.id) if job else None,
            "error": job.error if job is not None and job.status == "FAILED" else None,
        })
    return files


def _bundle_json(kb, bundle, *, files=True):
    payload = dict(bundle)
    if files:
        payload["files"] = _bundle_files(kb, bundle)
    payload["records"] = len(kb.oaw_experiments.list(bundle_id=bundle["id"], limit=500))
    return payload


class ExperimentBundles(Request):
    operation: Literal["list", "get"] = "list"
    bundle_id: str | None = None
    group_id: str | None = None
    all_groups: bool = False
    schema_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


def experiment_bundles(context, arguments):
    """List or read experiment uploads: the files in each and how far their conversion
    has got. Read-only; creating one and filing files into it are human acts."""
    request = ExperimentBundles.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    if request.operation == "get":
        if not request.bundle_id:
            raise KnowledgeError("bundle_id is required")
        return _bounded({"bundle": _bundle_json(kb, kb.oaw_experiments.require_bundle(request.bundle_id))})
    scope = _scope(group, request.all_groups)
    items = kb.oaw_experiments.list_bundles(
        group_id=str(scope) if scope else None, schema_id=request.schema_id, limit=request.limit)
    return _bounded({"bundles": [_bundle_json(kb, item) for item in items]})


class ExperimentBundleManage(Request):
    operation: Literal["create", "update", "delete"]
    bundle_id: str | None = None
    schema_id: str | None = None
    group_id: str | None = None
    name: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=20_000)
    # The card sets "extracting" with the run it started; the agent's save sets
    # "extracted"; the card may set "failed" if the run ends with nothing saved.
    status: Literal["ready", "extracting", "extracted", "failed"] | None = None
    run_id: str | None = Field(default=None, max_length=64)


def experiment_bundle_manage(context, arguments):
    request = ExperimentBundleManage.model_validate(arguments)
    if request.operation == "create":
        if not (request.schema_id and request.name):
            raise KnowledgeError("schema_id and name are required to create an experiment upload")
        kb, group, _ = _base(context, request.group_id)
        schema = _experiment_schema(kb, request.schema_id)
        bundle = kb.oaw_experiments.create_bundle(
            group_id=group.id, schema_id=str(schema.id), name=request.name,
            notes=request.notes or "", created_by=_actor(context))
        return _bounded({"bundle": _bundle_json(kb, bundle)})
    if not request.bundle_id:
        raise KnowledgeError("bundle_id is required")
    kb = _client(context)
    if request.operation == "delete":
        removed = kb.oaw_experiments.delete_bundle(request.bundle_id)
        return _bounded({"deleted": request.bundle_id, "records_removed": len(removed)})
    bundle = kb.oaw_experiments.update_bundle(
        request.bundle_id, name=request.name, notes=request.notes, status=request.status,
        schema_id=str(_experiment_schema(kb, request.schema_id).id) if request.schema_id else None,
        run_id=request.run_id if request.status is not None else ...)
    return _bounded({"bundle": _bundle_json(kb, bundle)})


class ExperimentIngest(Request):
    bundle_id: str
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    media_type: str = Field(default="application/octet-stream", max_length=200)


def experiment_ingest(context, arguments):
    """Upload one file into an experiment upload and queue its conversion at once.

    Unlike Literature's ``ingest``, there is no separate Process step: a bundle's
    files are only ever there to be projected together, so converting them is never
    a choice the person needs to make.
    """
    request = ExperimentIngest.model_validate(arguments)
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

    settings = settings_of(context)
    kb = _client(context)
    bundle = kb.oaw_experiments.require_bundle(request.bundle_id)
    if bundle["status"] == "deleted":
        raise KnowledgeError("That experiment upload was deleted")
    group_id = _uuid(bundle["group_id"], "group_id")
    source = kb.sources.add_bytes(group_id, data, filename=request.filename,
                                  media_type=request.media_type,
                                  metadata={"uploaded_by": _actor(context),
                                            "workflow": "experiment", "bundle_id": bundle["id"]})
    kb.oaw_experiments.add_bundle_source(bundle["id"], source.id)
    job = submit_markdown_job(kb, source, group_id, engine=settings["pdf_engine"],
                              mineru_base_url=settings["mineru_base_url"] or None,
                              vision_model=settings["vision_model"])
    if bundle["status"] != "ready":
        kb.oaw_experiments.update_bundle(bundle["id"], status="ready", run_id=None)
    return _bounded({"source": {"id": str(source.id), "filename": source.filename,
                                "size": source.size, "sha256": source.sha256},
                     "job": {"id": str(job.id), "status": job.status},
                     "bundle_id": bundle["id"]})


def _combined_markdown(kb, files):
    """Every converted file's markdown, in upload order, under a heading that names
    the file and its source id so a record can cite exactly where it was read from."""
    parts = []
    for item in files:
        record = kb.records.require(_uuid(item["record_id"], "record_id"))
        text = (record.data or {}).get("markdown")
        if not isinstance(text, str):
            continue
        parts.append(f"## File: {item['filename']} (source_id={item['source_id']})\n\n{text.strip()}\n")
    return "\n".join(parts)


class ExperimentExtractPrompt(Request):
    bundle_id: str
    limit: int = Field(default=60_000, ge=500, le=MAX_MARKDOWN_CHARS)
    offset: int = Field(default=0, ge=0)


def experiment_extract_prompt(context, arguments):
    """Everything an agent needs to project one experiment upload onto its schema:
    the schema, the uploader's notes and the files' markdown joined into one document,
    windowed like ``markdown`` so a long upload is read in pages. The agent produces
    the records and submits them with ``experiment_save``; the plugin owns the write."""
    request = ExperimentExtractPrompt.model_validate(arguments)
    kb = _client(context)
    bundle = kb.oaw_experiments.require_bundle(request.bundle_id)
    schema = _experiment_schema(kb, bundle["schema_id"])
    files = _bundle_files(kb, bundle)
    if not files:
        raise KnowledgeError("This experiment upload has no files yet")
    unready = [f for f in files if f["state"] != "converted"]
    if unready:
        names = ", ".join(f"{f['filename']} ({f['state']})" for f in unready)
        raise KnowledgeError(f"Not every file is converted yet: {names}. Wait for the "
                             "conversion jobs, or remove the failed file.")
    text = _combined_markdown(kb, files)
    window = text[request.offset:request.offset + request.limit]
    return _bounded({
        "bundle_id": bundle["id"], "bundle_name": bundle["name"], "notes": bundle["notes"],
        "group_id": bundle["group_id"], "schema_id": str(schema.id), "schema_name": schema.name,
        "schema_version": schema.version, "system_prompt": schema.system_prompt,
        "definition": schema.definition, "field_descriptions": schema.field_descriptions,
        "instruction": EXTRACT_INSTRUCTION,
        "files": [{"source_id": f["source_id"], "filename": f["filename"]} for f in files],
        "total_characters": len(text), "offset": request.offset, "markdown": window,
        "has_more": request.offset + len(window) < len(text),
    })


class ExperimentRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=500)
    data: dict[str, Any]
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    note: str | None = Field(default=None, max_length=4000)


class ExperimentSave(Request):
    bundle_id: str
    records: list[ExperimentRecordInput] = Field(min_length=1, max_length=500)
    # A re-extraction replaces the bundle's earlier drafts; published records stay.
    replace_drafts: bool = False


def _record_json(kb, record, *, sources=False):
    payload = dict(record)
    if sources:
        names = {}
        for link in kb.oaw_experiments.sources_for(record["id"]):
            source = kb.sources.get(link["source_id"])
            names[link["source_id"]] = source.filename if source else None
        payload["sources"] = [{"source_id": source_id, "filename": filename}
                              for source_id, filename in names.items()]
    return payload


def experiment_save(context, arguments):
    """Store the records projected from one experiment upload as drafts, each
    validated against the schema and linked to the files it was read from."""
    request = ExperimentSave.model_validate(arguments)
    kb = _client(context)
    bundle = kb.oaw_experiments.require_bundle(request.bundle_id)
    if bundle["status"] == "deleted":
        raise KnowledgeError("That experiment upload was deleted")
    schema = _experiment_schema(kb, bundle["schema_id"])
    files = {f["source_id"]: f for f in _bundle_files(kb, bundle)}
    if not files:
        raise KnowledgeError("This experiment upload has no files to cite as evidence")

    if request.replace_drafts:
        kb.oaw_experiments.delete_drafts_for_bundle(bundle["id"])
    saved = []
    for item in request.records:
        cited = [source_id for source_id in item.source_ids if source_id in files]
        if item.source_ids and not cited:
            raise KnowledgeError(
                f'Record "{item.name}" cites source_ids that are not in this upload')
        record = kb.oaw_experiments.create(
            group_id=bundle["group_id"], schema_id=str(schema.id), bundle_id=bundle["id"],
            name=item.name, data=item.data, validation=_check(schema.definition, item.data),
            note=item.note, created_by=_actor(context))
        kb.oaw_experiments.set_sources(record["id"], [
            {"source_id": source_id, "artifact_id": files[source_id]["artifact_id"]}
            for source_id in (cited or list(files))])
        saved.append(_record_json(kb, record, sources=True))
    kb.oaw_experiments.update_bundle(bundle["id"], status="extracted", run_id=None)
    return _bounded({"records": saved, "bundle_id": bundle["id"],
                     "invalid": len([r for r in saved if (r["validation"] or {}).get("valid") is False])})


class Experiments(Request):
    operation: Literal["list", "get"] = "list"
    record_id: str | None = None
    group_id: str | None = None
    all_groups: bool = False
    schema_id: str | None = None
    bundle_id: str | None = None
    status: Literal["draft", "published"] | None = None
    limit: int = Field(default=50, ge=1, le=200)


def experiments(context, arguments):
    """List or read experiment records. Read-only: editing a draft is
    knowledge_experiment_update; publishing one is a person's click."""
    request = Experiments.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    if request.operation == "list":
        scope = _scope(group, request.all_groups)
        items = kb.oaw_experiments.list(
            group_id=str(scope) if scope else None, schema_id=request.schema_id,
            bundle_id=request.bundle_id, status=request.status, limit=request.limit)
        return _bounded({"experiments": items})
    if not request.record_id:
        raise KnowledgeError("record_id is required")
    record = kb.oaw_experiments.require(request.record_id)
    return _bounded({"experiment": _record_json(kb, record, sources=True)})


class ExperimentUpdate(Request):
    record_id: str
    name: str | None = Field(default=None, max_length=500)
    data: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=4000)
    expected_revision: int | None = None


def experiment_update(context, arguments):
    """Edit one draft record — by hand in the form, or by an agent asked to in
    conversation. A published record is the approved dataset and is not edited in
    place: unpublish it first."""
    request = ExperimentUpdate.model_validate(arguments)
    kb = _client(context)
    current = kb.oaw_experiments.require(request.record_id)
    if current["status"] == "published":
        raise KnowledgeError("This record is published; unpublish it before editing")
    validation = None
    if request.data is not None:
        schema = kb.schemas.require(current["schema_id"])
        validation = _check(schema.definition, request.data)
    record = kb.oaw_experiments.update(
        request.record_id, name=request.name, data=request.data, note=request.note,
        validation=validation, expected_revision=request.expected_revision)
    return _bounded({"experiment": _record_json(kb, record, sources=True)})


class ExperimentReview(Request):
    operation: Literal["publish", "unpublish", "delete"]
    record_id: str
    expected_revision: int | None = None


def experiment_review(context, arguments):
    """Publish, unpublish or delete one record: the person's judgment on what the
    extraction produced. Publishing asks for confirmation, like approving a draft,
    because a published record is what every reader of the dataset will trust."""
    request = ExperimentReview.model_validate(arguments)
    kb = _client(context)
    current = kb.oaw_experiments.require(request.record_id)
    if request.operation == "delete":
        if current["status"] == "published":
            raise KnowledgeError("Unpublish this record before deleting it")
        kb.oaw_experiments.delete(request.record_id)
        return _bounded({"deleted": request.record_id})
    if request.operation == "unpublish":
        record = kb.oaw_experiments.update(
            request.record_id, status="draft", expected_revision=request.expected_revision)
        return _bounded({"experiment": _record_json(kb, record, sources=True)})
    if current["status"] == "published":
        raise KnowledgeError("This record is already published")
    if (current["validation"] or {}).get("valid") is False:
        raise KnowledgeError("This record does not satisfy its schema; fix it before publishing")
    if not context.confirmed:
        return {"status": "confirmation_required",
                "reasons": ["Publishing marks this record as approved data that every "
                            "reader of this knowledge base will trust."],
                "message": f'Publish "{current["name"]}"?'}
    record = kb.oaw_experiments.update(
        request.record_id, status="published", expected_revision=request.expected_revision)
    return _bounded({"experiment": _record_json(kb, record, sources=True)})


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
    draft_row = kb.knowledge.get_draft(identifier)
    decision, fact, event = kb.knowledge.approve(
        identifier, expected_revision=request.expected_revision,
        actor=_actor(context), notes=request.notes)
    # The published fact — never the raw model output — is what enters the graph.
    result = kb.graph.extract(fact.data, extractor=lambda payload: payload)
    if draft_row is not None:
        # kb.graph.extract builds Entity/Relation objects itself, with no group on
        # them, so stamp the draft's group onto exactly what it just touched.
        kb.oaw_graph_store.tag_group([entity.id for entity in result.entities],
                                     [relation.id for relation in result.relations],
                                     draft_row.collection_id)
    return _bounded({
        "decision": decision.decision,
        "fact": {"id": str(fact.id), "fact_set_id": str(fact.fact_set_id),
                 "revision": fact.revision, "status": fact.status},
        "event": {"id": str(event.id), "type": event.event_type},
        "graph": {"entities": len(result.entities), "relations": len(result.relations)},
    })


# ---------------------------------------------------------------- graph


def graph_schema_id_of(context):
    """The one literature schema whose projections build the published graph.

    Stored under its own top-level state key, not inside ``settings``: unlike the
    collection name, PDF engine or MinerU URL, choosing it is an ordinary business
    action (like Process or Build draft), so it must stay changeable from a deployed
    release too, not only from the engineering-only Settings section.
    """
    if context.state is None:
        return ""
    return context.state.get()["value"].get("graph_schema_id") or ""


class GraphSchema(Request):
    operation: Literal["get", "set"] = "get"
    schema_id: str | None = None


def graph_schema(context, arguments):
    request = GraphSchema.model_validate(arguments)
    if context.state is None:
        raise KnowledgeError("Card state is unavailable")
    if request.operation == "set":
        if request.schema_id:
            kb = _client(context)
            schema = kb.schemas.require(request.schema_id)
            if _schema_kind(schema) != "literature":
                raise KnowledgeError(
                    "The graph schema must be a literature-kind schema")
        context.state.update({"graph_schema_id": request.schema_id or ""})
    return _bounded({"schema_id": graph_schema_id_of(context)})


class GraphQuery(Request):
    operation: Literal["query", "traverse"] = "query"
    entity_id: str | None = None
    entity_type: str | None = Field(default=None, max_length=200)
    relation_type: str | None = Field(default=None, max_length=200)
    name_contains: str | None = Field(default=None, max_length=200)
    max_depth: int = Field(default=1, ge=1, le=5)
    direction: Literal["both", "out", "in"] = "both"
    limit: int = Field(default=200, ge=1, le=1000)
    group_id: str | None = None
    # "literature": facts an approved draft published. "experiment": insights
    # published from experiment records and the record/field nodes they cite.
    # Omitted: the whole graph, both layers.
    layer: Literal["literature", "experiment"] | None = None


def graph(context, arguments):
    request = GraphQuery.model_validate(arguments)
    kb = _client(context)
    scope = _any_group(request.group_id)
    store = kb.oaw_graph_store
    narrowed = scope is not None or request.layer is not None
    if request.operation == "traverse":
        if not request.entity_id:
            raise KnowledgeError("entity_id is required to traverse")
        result = kb.graph.traverse(_uuid(request.entity_id, "entity_id"),
                                   max_depth=request.max_depth, direction=request.direction)
        all_entities, all_relations = list(result.entities), list(result.relations)
        if narrowed:
            # kb.graph.traverse walks the whole store; group and layer are tags on
            # published elements (see graph_store.py), so narrow after the walk
            # rather than teaching mkb's traversal about them.
            in_scope = {item.id for item in store.list_relations(group_id=scope, layer=request.layer)}
            all_relations = [item for item in all_relations if item.id in in_scope]
            kept_ids = {item.source_id for item in all_relations} | \
                {item.target_id for item in all_relations} | {_uuid(request.entity_id, "entity_id")}
            all_entities = [item for item in all_entities if item.id in kept_ids]
    elif narrowed:
        # mkb's Graph.query has no group or layer concept to pass through, so this
        # replicates its own entity/relation filtering against the store directly.
        all_entities = store.list_entities(type=request.entity_type, group_id=scope, layer=request.layer)
        if request.name_contains:
            needle = request.name_contains.casefold()
            all_entities = [item for item in all_entities if needle in item.name.casefold()]
        entity_ids = {item.id for item in all_entities}
        all_relations = store.list_relations(type=request.relation_type, group_id=scope, layer=request.layer)
        if request.entity_type or request.name_contains:
            all_relations = [item for item in all_relations
                             if item.source_id in entity_ids or item.target_id in entity_ids]
    else:
        result = kb.graph.query(entity_type=request.entity_type,
                                relation_type=request.relation_type,
                                name_contains=request.name_contains)
        all_entities, all_relations = list(result.entities), list(result.relations)

    entities = all_entities[:request.limit]
    kept = {str(entity.id) for entity in entities}
    return _bounded({
        "entities": [{"id": str(e.id), "type": e.type, "name": e.name,
                      "properties": e.properties} for e in entities],
        "relations": [{"id": str(r.id), "type": r.type, "source_id": str(r.source_id),
                       "target_id": str(r.target_id), "properties": r.properties}
                      for r in all_relations
                      if str(r.source_id) in kept and str(r.target_id) in kept][:request.limit],
        "truncated": len(all_entities) > len(entities),
    })


# ---------------------------------------------------------------- jobs


class Jobs(Request):
    job_id: str | None = None
    group_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    after: int = Field(default=0, ge=0)


def jobs(context, arguments):
    request = Jobs.model_validate(arguments)
    kb = _client(context)
    if request.job_id:
        job = kb.jobs.require(_uuid(request.job_id, "job_id"))
        # jobs.events() yields plain dicts from a generator, not typed models.
        events = list(kb.jobs.events(job.id, after=request.after))[-50:]
        return _bounded({"job": _job_json(job), "events": [
            {"event": item.get("event"), "step": item.get("step_name"),
             "message": item.get("message"), "level": item.get("level"),
             "occurred_at": item.get("occurred_at")} for item in events]})
    items = kb.jobs.list(limit=request.limit)
    if request.group_id:
        items = [job for job in items if (job.inputs or {}).get("collection_id") == request.group_id]
    return _bounded({"jobs": [_job_json(job) for job in items]})


def _job_json(job):
    return {"id": str(job.id), "kind": job.kind, "status": job.status,
            "pipeline": job.pipeline_name or PIPELINE_NAME, "progress": job.progress,
            "message": job.message, "error": job.error,
            "source_id": (job.inputs or {}).get("source_id"),
            "group_id": (job.inputs or {}).get("collection_id"),
            "created_at": job.created_at, "completed_at": job.completed_at}
