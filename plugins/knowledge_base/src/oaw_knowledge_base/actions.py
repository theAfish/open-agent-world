"""Resource actions: every user and agent operation on a knowledge base card.

Each handler is deliberately short — validate, make a few MKB calls, return bounded
JSON — because the host holds the global node mutation lock for the whole call. Slow
work belongs in the ``oaw.to_markdown`` job or in the host LLM bridge.

Raw-byte upload, unrestricted batch management, settings and publication remain
desktop operations. Authorized agents import file references, convert selected
sources and prepare unpublished drafts through their narrower operations.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import embeddings, graph_search
from .client import DEFAULT_COLLECTION, collection, open_client
from .errors import KnowledgeError
from .graph_store import LAYERS
from .markdown import available_engines
from .pipelines import PIPELINE_NAME, RETRYABLE_STATUSES, submit_markdown_job

logger = logging.getLogger(__name__)

MAX_RESULT_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_CHARS = 200_000
# ``vision_model`` is an OAW model reference ("oaw:model:..."), or "" for the OAW
# default model; the OAW_VISION_* variables are the fallback when neither resolves.
# ``embedding_model`` is the same kind of reference for graph search's semantic
# channel, but "" means off: a chat default model cannot embed.
DEFAULT_SETTINGS = {"collection_name": DEFAULT_COLLECTION, "pdf_engine": "auto",
                    "mineru_base_url": "", "vision_model": "", "embedding_model": ""}
ACTIVE_JOBS = {"QUEUED", "RUNNING", "PENDING", "RETRYING", "CANCELLING"}


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Marks a request field the UI and CLI may pass but the agent's tool schema omits.
AGENT_HIDDEN = {"agent_hidden": True}


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
    return kb, kb.collections.require(_uuid(group_id, "group_id")) if group_id else None, settings


def _any_group(group_id):
    """``None`` means "every group"; otherwise the exact id to filter listings by."""
    return _uuid(group_id, "group_id") if group_id else None


def _scope(group, all_groups):
    """The collection_id to filter a listing by: one group, or every group.

    Historically a card had exactly one collection, so every listing filtered by
    it without a choice. ``all_groups`` opts a caller into the merged, cross-group
    view instead of changing what an omitted ``group_id`` already meant.
    """
    return None if all_groups or group is None else group.id


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
    kb, settings = _client(context), settings_of(context)
    group = kb.collections.require(_uuid(request.group_id, "group_id")) if request.group_id else None
    # An explicit group, or the resolved default: the same single-group view every
    # card had before groups existed. all_groups opts into totals for the whole card.
    scope = group.id if group is not None and not request.all_groups else None
    sources = kb.sources.list(collection_id=scope, limit=500)
    jobs = kb.jobs.list(limit=50)
    counts = kb.oaw_graph_store.counts(group_id=scope)
    default_group_id = str(group.id) if group else ""
    project_items = kb.collections.list(limit=500)
    projection_items = kb.oaw_projections.list(project_id=str(scope) if scope else None, limit=500)
    return _bounded({
        "collection": {"id": str(group.id) if group else "", "name": group.name if group else settings["collection_name"]},
        "groups": [_group_json(item, default_group_id) for item in kb.collections.list(limit=500)],
        "settings": settings,
        "engines": available_engines(settings["mineru_base_url"] or None,
                                     resolve_secret=getattr(context, "resolve_secret", None),
                                     resolve_model=getattr(context, "resolve_model", None),
                                     vision_model=settings["vision_model"]),
        "counts": {
            "sources": len(sources),
            "records": len(kb.records.list(collection_id=scope, limit=500)),
            "schemas": len(kb.schemas.list(limit=200)),
            "projects": len(project_items),
            "spaces": len([s for s in kb.schemas.list(limit=500) if s.purpose in {"projection:table", "projection:graph"}]),
            "projections": len(projection_items),
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
    embedding_model: str | None = Field(default=None, max_length=200)


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


def _group_json(item, default_group_id):
    return {"id": str(item.id), "name": item.name, "source_count": item.source_count,
            "created_at": item.created_at, "is_default": str(item.id) == default_group_id}


class Sources(Request):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    group_id: str | None = None
    all_groups: bool = True
    # Files uploaded into an experiment bundle belong to the Experiment section;
    # Literature's source list leaves them out unless asked otherwise.
    workflow: Literal["project", "experiment", "all"] = "all"


def _record_link(kb, artifact_id):
    """The markdown artifact's record link.

    Evidence is listed per artifact and an artifact may accumulate links of several
    output types; only the record one answers "what holds this markdown".
    """
    for link in kb.evidence.list(artifact_id=artifact_id, limit=100):
        if link.output_type == "record":
            return link
    return None


def sources(context, arguments):
    request = Sources.model_validate(arguments)
    kb = _client(context)
    # An explicit group, or the default; all_groups lists every source across every
    # group instead, each still carrying its own group for a per-group header.
    scope = _any_group(request.group_id)
    names = {str(item.id): item.name for item in kb.collections.list(limit=500)}
    tagged = kb.oaw_experiments.tagged_source_ids()
    from .projects import project_source_ids
    active = [kb.sources.require(identifier) for identifier in project_source_ids(kb, scope)]
    active = [source for source in active if request.workflow == "all" or
        (str(source.id) in tagged) == (request.workflow == "experiment")]
    active.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
    items = []
    for source in active[request.offset:request.offset + request.limit]:
        is_experiment = str(source.id) in tagged
        if request.workflow == "project" and is_experiment:
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
            "workflow": "experiment" if is_experiment else "project",
            "markdown": None if markdown is None else {
                "artifact_id": str(markdown.id), "size": markdown.size,
                "engine": (markdown.metadata or {}).get("engine")},
            "record_id": record_id,
            "records": len(kb.oaw_experiments.list(source_id=str(source.id), limit=200)),
        })
    return _bounded({"sources": items, "offset": request.offset})


# ---------------------------------------------------------------- ingest


class ProcessSources(Request):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    group_id: str | None = None


class ProcessSelected(Request):
    source_ids: list[str] = Field(min_length=1, max_length=100)


def process_selected(context, arguments):
    """Convert only the sources explicitly selected by an agent."""
    request = ProcessSelected.model_validate(arguments)
    return process_sources(context, request.model_dump())


def _has_markdown(kb, source_id):
    return any(a.processing_type == "MARKDOWN" and _record_link(kb, a.id) is not None
               for a in kb.artifacts.list(source_id=source_id, limit=10))


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
                          for value in dict.fromkeys(request.source_ids)]
    else:
        sources_to_run = kb.sources.list(collection_id=_any_group(request.group_id), limit=500)

    wanted = {str(source.id) for source in sources_to_run}
    latest_jobs = {}
    offset = 0
    # MKB's portable store lists jobs newest first. Stop once every selected
    # source has its latest conversion, or the history is exhausted.
    while wanted - latest_jobs.keys():
        page = kb.jobs.list(limit=200, offset=offset)
        for job in page:
            source_id = (job.inputs or {}).get("source_id")
            if job.pipeline_name == PIPELINE_NAME and source_id in wanted:
                latest_jobs.setdefault(source_id, job)
        if len(page) < 200:
            break
        offset += len(page)
    results = []
    for source in sources_to_run:
        previous = latest_jobs.get(str(source.id))
        if previous is not None and previous.status in ACTIVE_JOBS:
            results.append({"source_id": str(source.id), "filename": source.filename,
                            "job": {"id": str(previous.id), "status": previous.status}})
            continue
        # A failed index step may already have committed markdown and evidence.
        # Resume it before declaring the source ready, so search is repaired too.
        if _has_markdown(kb, source.id) and (previous is None or previous.status not in RETRYABLE_STATUSES):
            if request.source_ids:
                results.append({"source_id": str(source.id), "filename": source.filename,
                                "skipped": "already converted"})
            continue
        source_group_id = (source.collection_ids[0] if source.collection_ids
                           else collection(kb, settings["collection_name"]).id)
        job = submit_markdown_job(kb, source, source_group_id, engine=settings["pdf_engine"],
                                  mineru_base_url=settings["mineru_base_url"] or None,
                                  vision_model=settings["vision_model"], previous_job=previous)
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
    kb = _client(context)
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
    from .projects import project_source_ids
    active_ids = project_source_ids(kb, scope)
    for hit in search_chunks(kb.oaw_engine, request.query, limit=request.limit, group_id=scope, source_ids=active_ids):
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
    """Spaces and experiment record schemas have explicit, distinct purposes."""
    return schema.purpose


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
    kind: Literal["experiment"] | None = None


class SchemaRead(Request):
    operation: Literal["list", "get"] = "list"
    schema_id: str | None = None
    kind: Literal["experiment"] | None = None


class SchemaManage(Schemas):
    operation: Literal["create", "update"] = Field()


def _schema_json(schema):
    return {"id": str(schema.id), "name": schema.name, "description": schema.description,
            "domain": schema.domain, "kind": _schema_kind(schema), "version": schema.version,
            "definition": schema.definition, "system_prompt": schema.system_prompt,
            "field_descriptions": schema.field_descriptions}


def schemas(context, arguments):
    if arguments.get("operation", "list") not in {"list", "get"}:
        raise KnowledgeError("knowledge_schemas is read-only; use knowledge_schema_manage to change a schema")
    request = SchemaRead.model_validate(arguments)
    return _schemas(context, request)


def schema_manage(context, arguments):
    request = SchemaManage.model_validate(arguments)
    if context.actor_id is not None and request.operation == "update" and request.schema_id:
        if _client(context).oaw_experiments.list(schema_id=request.schema_id, status="published", limit=1):
            raise KnowledgeError("This schema has published records; create a new schema instead of changing their definition")
    return _schemas(context, request)


def _schemas(context, request):
    kb = _client(context)
    if request.operation == "list":
        items = [item for item in kb.schemas.list(limit=500) if item.purpose == "experiment"]
        if request.kind is not None:
            items = [s for s in items if _schema_kind(s) == request.kind]
        return _bounded({"schemas": [
            {"id": str(s.id), "name": s.name, "description": s.description,
             "domain": s.domain, "kind": _schema_kind(s), "version": s.version}
            for s in items]})
    if request.operation == "get":
        if not request.schema_id:
            raise KnowledgeError("schema_id is required")
        return _bounded({"schema": _schema_json(_experiment_schema(kb, request.schema_id))})
    if request.operation == "create":
        if not (request.name and request.definition and request.system_prompt):
            raise KnowledgeError(
                "name, definition and system_prompt are required to create a schema")
        _validate_definition(request.definition)
        schema = kb.schemas.create(
            name=request.name, domain=request.domain, definition=request.definition,
            system_prompt=request.system_prompt, description=request.description,
            field_descriptions=request.field_descriptions,
            purpose="experiment")
        return _bounded({"schema": _schema_json(schema)})
    if not request.schema_id:
        raise KnowledgeError("schema_id is required")
    if request.definition is not None:
        _validate_definition(request.definition)
    _experiment_schema(kb, request.schema_id)
    schema = kb.schemas.update(
        request.schema_id, name=request.name, description=request.description,
        definition=request.definition, system_prompt=request.system_prompt,
        field_descriptions=request.field_descriptions, purpose=request.kind)
    return _bounded({"schema": _schema_json(schema)})


def _validate_definition(definition):
    if definition.get("type") != "object" or not isinstance(definition.get("properties"), dict):
        raise KnowledgeError(
            "definition must be a JSON Schema object with a properties map")


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


def _summarize(data):
    if isinstance(data, dict):
        for key in ("name", "title", "sample_id", "id"):
            if isinstance(data.get(key), str):
                return data[key]
        return ", ".join(sorted(data)[:6])
    return str(data)[:120]


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
    has got. Creating or changing one requires a separate write capability."""
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
        from .projects import ensure_project
        kb = _client(context)
        schema = _experiment_schema(kb, request.schema_id)
        group = ensure_project(kb, request.name, project_id=request.group_id)
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


class ExperimentBundlePrepare(Request):
    operation: Literal["create", "update"] = "create"
    bundle_id: str | None = None
    schema_id: str | None = None
    group_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=20_000)


def experiment_bundle_prepare(context, arguments):
    """Prepare an unpublished upload without exposing deletion or run-state edits."""
    request = ExperimentBundlePrepare.model_validate(arguments)
    kb = _client(context)
    group_id = _any_group(request.group_id)
    store = kb.oaw_experiments
    if request.operation == "create":
        if not (request.schema_id and request.name):
            raise KnowledgeError("schema_id and name are required to create an experiment upload")
        schema = _experiment_schema(kb, request.schema_id)
        for bundle in store.list_bundles(group_id=group_id, schema_id=str(schema.id), limit=500):
            if (bundle["name"] == request.name and bundle["notes"] == (request.notes or "")
                    and not store.list(bundle_id=bundle["id"], status="published", limit=1)):
                return _bounded({"bundle": _bundle_json(kb, bundle)})
        return experiment_bundle_manage(context, request.model_dump(exclude_none=True))
    if not request.bundle_id:
        raise KnowledgeError("bundle_id is required")
    bundle = store.require_bundle(request.bundle_id)
    if bundle["status"] == "deleted":
        raise KnowledgeError("That experiment upload was deleted")
    if request.group_id is not None and bundle["group_id"] != str(group_id):
        raise KnowledgeError("That experiment upload belongs to another group")
    if store.list(bundle_id=bundle["id"], status="published", limit=1):
        raise KnowledgeError("This upload has published records; its preparation cannot be changed by an agent")
    # Repeating an update with the current schema must not fail just because draft
    # records have since been saved. A genuine schema change keeps the store guard.
    values = request.model_dump(exclude_none=True)
    if request.schema_id == bundle["schema_id"]:
        values.pop("schema_id", None)
    return experiment_bundle_manage(context, values)


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
    from .projects import register_project_file
    register_project_file(kb, group_id, source)
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
    # Records read from one file: a bundle's, or a literature source's.
    source_id: str | None = None
    status: Literal["draft", "published"] | None = None
    limit: int = Field(default=50, ge=1, le=200)


def experiments(context, arguments):
    """List or read structured records — from an experiment upload or a literature
    source. Read-only: editing a draft is knowledge_experiment_update; publishing one
    is a person's click."""
    request = Experiments.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    if request.operation == "list":
        scope = _scope(group, request.all_groups)
        items = kb.oaw_experiments.list(
            group_id=str(scope) if scope else None, schema_id=request.schema_id,
            bundle_id=request.bundle_id, source_id=request.source_id, status=request.status,
            limit=request.limit)
        return _bounded({"experiments": items})
    if not request.record_id:
        raise KnowledgeError("record_id is required")
    record = kb.oaw_experiments.require(request.record_id)
    return _bounded({"experiment": _record_json(kb, record, sources=True)})


# ---------------------------------------------------------------- literature records


RECORD_INSTRUCTION = (
    "The document below is one converted literature source. Project it onto the JSON "
    "Schema: produce one record per specimen, material, sample or case the document "
    "reports values for, each with its fields filled from the document only. Leave a "
    "field out rather than guess it; skip the document entirely if it reports nothing the "
    "schema asks for by returning an empty list. Reply with exactly this JSON shape: "
    "{\"records\": [{\"name\": <short label, e.g. the material or sample>, \"data\": <object "
    "matching the schema>, \"note\": <optional: where in the document it came from, or "
    "what was unclear>}]}.")


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


# ---------------------------------------------------------------- graph


class GraphQuery(Request):
    operation: Literal["query", "traverse", "connect"] = "query"
    # A question or keywords in plain words: concepts are found by name, alias or
    # acronym anywhere in it, and relations are ranked by how well they fit — by
    # words and, when the card has an embedding model, by meaning.
    query: str | None = Field(default=None, max_length=500)
    entity_id: str | None = None
    # The fields below are for the graph map and the CLI; the agent only sees ``query``
    # and ``names``, so there is no lexical-only knob for it to pick by mistake.
    entity_type: str | None = Field(default=None, max_length=200, json_schema_extra=AGENT_HIDDEN)
    relation_type: str | None = Field(default=None, max_length=200, json_schema_extra=AGENT_HIDDEN)
    # Concepts to look up, by name or alias; spacing and punctuation do not matter
    # ("dpa sse" finds "DPA-SSE"). ``name_contains`` is the single-needle form.
    names: list[str] = Field(default_factory=list, max_length=20)
    name_contains: str | None = Field(default=None, max_length=200, json_schema_extra=AGENT_HIDDEN)
    # A word to find in a relation's predicate, statement or snippet.
    relation_contains: str | None = Field(default=None, max_length=200, json_schema_extra=AGENT_HIDDEN)
    # The graph map's name filter passes false: a filter should not call a model.
    semantic: bool = Field(default=True, json_schema_extra=AGENT_HIDDEN)
    # The UI passes true to get every element's properties. The agent's view is compact
    # and bounded (see ``_fit``): the host projects a tool result over ~8000 characters
    # into a stub the model cannot read, which is worse than fewer relations.
    full: bool = Field(default=False, json_schema_extra=AGENT_HIDDEN)
    # Hops to walk: 1 for traverse, 3 for connect, unless given.
    max_depth: int | None = Field(default=None, ge=1, le=5)
    direction: Literal["both", "out", "in"] = "both"
    limit: int = Field(default=200, ge=1, le=1000)
    group_id: str | None = None
    # "literature": facts an approved draft published. "experiment": insights
    # published from experiment records and the record/field nodes they cite.
    # Omitted: the whole graph, both layers.
    layer: Literal["literature", "experiment"] | None = None

    def search_text(self):
        """Everything the caller typed, as one string to search for."""
        parts = [self.query or "", *self.names, self.name_contains or "", self.relation_contains or ""]
        return " ".join(part.strip() for part in parts if part and part.strip())


def _entity_json(entity, *, matched=None):
    properties = entity.properties or {}
    payload = {"id": str(entity.id), "type": entity.type, "name": entity.name,
               "aliases": [a for a in properties.get("aliases") or [] if isinstance(a, str)],
               "description": properties.get("description") if isinstance(properties.get("description"), str) else None,
               "properties": properties}
    if matched is not None:
        payload["matched"] = matched
    return payload


def _relation_json(relation, names):
    """A relation as a readable statement: both endpoints by name, the predicate, and
    what the source claims — so a reader need not join ids to find out what an edge says."""
    properties = relation.properties or {}
    return {"id": str(relation.id), "type": relation.type, "predicate": relation.type,
            "source_id": str(relation.source_id), "source_name": names.get(str(relation.source_id)),
            "target_id": str(relation.target_id), "target_name": names.get(str(relation.target_id)),
            "statement": properties.get("description") if isinstance(properties.get("description"), str) else None,
            "evidence_level": properties.get("evidence_level") if isinstance(properties.get("evidence_level"), int) else None,
            "snippet": properties.get("snippet") if isinstance(properties.get("snippet"), str) else None,
            "source_ids": [v for v in properties.get("source_ids") or [] if isinstance(v, str)],
            "properties": properties}


QUERY_ENTITIES = 10
QUERY_RELATIONS = 30
CONNECT_DEPTH = 3
# The agent's compact result: fewer, shorter, and trimmed to fit the host's window.
COMPACT_ENTITIES = 8
COMPACT_RELATIONS = 12
COMPACT_BYTES = 7000
STATEMENT_CHARS = 240


def _clip(text, limit):
    return text if not isinstance(text, str) or len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _compact_entity(entity, matched):
    properties = entity.properties or {}
    item = {"name": entity.name, "type": entity.type, "matched": matched}
    if matched:
        # Only a matched concept's background and id (for traverse) are worth the space.
        item["id"] = str(entity.id)
        aliases = [a for a in properties.get("aliases") or [] if isinstance(a, str)]
        if aliases:
            item["aliases"] = aliases
        if isinstance(properties.get("description"), str):
            item["description"] = _clip(properties["description"], STATEMENT_CHARS)
    return item


def _compact_relation(relation, names):
    properties = relation.properties or {}
    item = {"source_name": names.get(str(relation.source_id)), "predicate": relation.type,
            "target_name": names.get(str(relation.target_id))}
    if isinstance(properties.get("description"), str):
        item["statement"] = _clip(properties["description"], STATEMENT_CHARS)
    if isinstance(properties.get("evidence_level"), int):
        item["evidence_level"] = properties["evidence_level"]
    sources = [v for v in properties.get("source_ids") or [] if isinstance(v, str)]
    if sources:
        item["source_ids"] = sources
    return item


def _compact_path(first, second, path, names):
    return {"from": names[str(first)], "to": names[str(second)], "length": len(path),
            "steps": [f"{names.get(str(r.source_id))} —{r.type}→ {names.get(str(r.target_id))}" for r in path]}


def _fit(result, budget=COMPACT_BYTES):
    """Drop the lowest-ranked relations, then unmatched concepts, until the JSON fits."""
    def size():
        return len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))
    while size() > budget and len(result.get("relations", [])) > 1:
        result["relations"].pop()
        result["truncated"] = True
    while size() > budget and any(not e.get("matched") for e in result.get("entities", [])):
        index = max(i for i, e in enumerate(result["entities"]) if not e.get("matched"))
        result["entities"].pop(index)
        result["truncated"] = True
    return result


def _graph_result(entity_ids, by_id, relations, matched_ids, **extra):
    """Entities (matched ones first in the given order, then the rest by name) and
    relations as readable JSON, every edge resolvable by name."""
    ordered = [identifier for identifier in dict.fromkeys(entity_ids) if identifier in by_id]
    matched = [identifier for identifier in ordered if identifier in matched_ids]
    others = sorted((identifier for identifier in ordered if identifier not in matched_ids),
                    key=lambda identifier: by_id[identifier].name.casefold())
    entities = [by_id[identifier] for identifier in matched + others]
    names = {str(entity.id): entity.name for entity in entities}
    return _bounded({"entities": [_entity_json(e, matched=e.id in matched_ids) for e in entities],
                     "relations": [_relation_json(r, names) for r in relations], **extra})


def _path_json(first, second, path, names):
    return {"from": names[str(first)], "to": names[str(second)], "length": len(path),
            "relations": [_relation_json(r, names) for r in path]}


def _semantic_scores(context, kb, query, entities, relations):
    """``({element: cosine}, status)`` from the card's embedding model. ``status`` is empty
    when the model is off, and says why the answer fell back to words when it cannot be
    used — an unreachable endpoint must not make the graph unsearchable."""
    model = settings_of(context)["embedding_model"]
    if not model:
        return {}, {}
    try:
        connection = embeddings.connection_for(model, getattr(context, "resolve_model", None))
        texts = embeddings.graph_texts(entities, relations)
        vectors = embeddings.sync(kb.oaw_engine, texts, connection)
        query_vector = embeddings.embed([query], connection)[0]
        found = {}
        for group in (entities, relations):
            keyed = {str(item.id): item.id for item in group}
            found.update({keyed[key]: score for key, score in embeddings.scores(
                query_vector, {key: vectors[key] for key in keyed}).items()})
    except Exception as error:  # an optional channel must never make the graph unsearchable
        message = (str(error) or type(error).__name__)[:300]
        logger.warning("knowledge_graph semantic channel off for this query (%s): %s", model, message)
        _remember_index(context, last_error=message)
        return {}, {"semantic": False, "semantic_error": message}
    logger.info("knowledge_graph semantic channel: %s stood out of %d elements (%s)",
                len(found), len(entities) + len(relations), model)
    return found, {"semantic": True}


def _ranked_query(request, entities, relations, by_id, needles=(), semantic=None):
    """A plain-words question: concepts found by name, alias or acronym anywhere in it,
    the relations that fit it best (those joining two named concepts first), and — when
    it names two or more concepts — the shortest paths between them."""
    entity_scores, relation_scores = graph_search.search(
        entities, relations, request.search_text(), needles=needles, semantic=semantic)
    top = sorted(entity_scores, key=lambda identifier: -entity_scores[identifier])
    top = top[:min(request.limit, max(QUERY_ENTITIES, len(needles)))]
    by_relation = {r.id: r for r in relations}
    best = sorted(relation_scores, key=lambda identifier: -relation_scores[identifier])
    shown = [by_relation[identifier] for identifier in best[:min(request.limit * 5, QUERY_RELATIONS)]]
    named = [identifier for identifier in top if entity_scores[identifier] >= graph_search.STRONG][:3]

    paths = []
    if len(named) > 1:
        adjacency = graph_search.adjacency(relations)
        for index, first in enumerate(named):
            for second in named[index + 1:]:
                paths += [(first, second, path) for path in graph_search.shortest_paths(
                    adjacency, first, second, max_depth=request.max_depth or CONNECT_DEPTH, max_paths=2)]
    ids = list(top) + [i for r in shown for i in (r.source_id, r.target_id)]
    ids += [r.source_id for _, _, path in paths for r in path] + [r.target_id for _, _, path in paths for r in path]
    names = {str(identifier): by_id[identifier].name for identifier in ids if identifier in by_id}
    if request.full:
        result = _graph_result(ids, by_id, shown, set(top), matched=len(top),
                               truncated=len(best) > len(shown))
        result["connections"] = [_path_json(first, second, path, names) for first, second, path in paths]
        return result
    top, shown = top[:COMPACT_ENTITIES], shown[:COMPACT_RELATIONS]
    ordered = [i for i in dict.fromkeys(ids) if i in by_id]
    others = sorted((i for i in ordered if i not in set(top)), key=lambda i: by_id[i].name.casefold())
    return _fit(_bounded({
        "entities": [_compact_entity(by_id[i], i in set(top)) for i in list(top) + others],
        "relations": [_compact_relation(r, names) for r in shown],
        "connections": [_compact_path(first, second, path, names) for first, second, path in paths],
        "matched": len(top), "truncated": len(best) > len(shown),
    }))


def _connect(request, entities, relations, by_id):
    """How two named concepts are connected: the shortest relation chains between them,
    each relation readable as a statement. Each name may resolve to several concepts
    (an acronym and its spelled-out form); the shortest chain over any pairing wins."""
    if len(request.names) != 2:
        raise KnowledgeError("connect needs exactly two names")
    sides = [graph_search.resolve(entities, name)[:5] for name in request.names]
    missing = [name for name, side in zip(request.names, sides) if not side]
    if missing:
        return {"connected": False, "unresolved": missing, "paths": [], "entities": []}
    adjacency = graph_search.adjacency(relations)
    depth = request.max_depth or CONNECT_DEPTH
    found = []
    for rank_a, first in enumerate(sides[0]):
        for rank_b, second in enumerate(sides[1]):
            for path in graph_search.shortest_paths(adjacency, first.id, second.id, max_depth=depth):
                found.append((len(path), rank_a + rank_b, first.id, second.id, path))
    found.sort(key=lambda item: item[:2])
    found = found[:5]
    ids = [e.id for side in sides for e in side] + [
        i for _, _, _, _, path in found for r in path for i in (r.source_id, r.target_id)]
    names = {str(identifier): by_id[identifier].name for identifier in ids if identifier in by_id}
    resolved = [[e.name for e in side] for side in sides]
    if request.full:
        return _graph_result(ids, by_id, [], {e.id for side in sides for e in side},
                             connected=bool(found), resolved=resolved,
                             paths=[_path_json(first, second, path, names) for _, _, first, second, path in found])
    # Compact: each chain as readable steps, with every relation's statement once.
    seen, relations = set(), []
    for _, _, _, _, path in found:
        for relation in path:
            if relation.id not in seen:
                seen.add(relation.id)
                relations.append(_compact_relation(relation, names))
    return _fit(_bounded({"connected": bool(found), "resolved": resolved,
                          "paths": [_compact_path(first, second, path, names) for _, _, first, second, path in found],
                          "relations": relations, "entities": []}))


def graph(context, arguments):
    """The published graph. A name query answers "what does the base say about X (and
    Y)": the matching concepts, every relation touching them, and the concepts on the
    other end, so each relation reads as source → predicate → target with its statement.
    ``query`` takes a plain-words question and ranks; ``connect`` finds the paths
    between two named concepts."""
    request = GraphQuery.model_validate(arguments)
    kb = _client(context)
    scope = _any_group(request.group_id)
    store = kb.oaw_graph_store

    if request.operation == "traverse":
        if not request.entity_id:
            raise KnowledgeError("entity_id is required to traverse")
        start = _uuid(request.entity_id, "entity_id")
        result = kb.graph.traverse(start, max_depth=request.max_depth or 1, direction=request.direction)
        all_entities, all_relations = list(result.entities), list(result.relations)
        if scope is not None or request.layer is not None:
            # kb.graph.traverse walks the whole store; group and layer are tags on
            # published elements (see graph_store.py), so narrow after the walk.
            in_scope = {item.id for item in store.list_relations(group_id=scope, layer=request.layer)}
            all_relations = [item for item in all_relations if item.id in in_scope]
            kept_ids = {item.source_id for item in all_relations} | {item.target_id for item in all_relations} | {start}
            all_entities = [item for item in all_entities if item.id in kept_ids]
        entities = all_entities[:request.limit]
        kept = {str(entity.id) for entity in entities}
        names = {str(entity.id): entity.name for entity in entities}
        return _bounded({
            "entities": [_entity_json(e, matched=str(e.id) == str(start)) for e in entities],
            "relations": [_relation_json(r, names) for r in all_relations
                          if str(r.source_id) in kept and str(r.target_id) in kept][:request.limit * 5],
            "truncated": len(all_entities) > len(entities),
        })

    needles = [graph_search.phrase(value) for value in request.names]
    if request.name_contains:
        needles.append(graph_search.phrase(request.name_contains))
    needles = [needle for needle in needles if needle]

    all_entities = store.list_entities(type=request.entity_type, group_id=scope, layer=request.layer)
    by_id = {entity.id: entity for entity in all_entities}
    all_relations = store.list_relations(type=request.relation_type, group_id=scope, layer=request.layer)
    all_relations = [r for r in all_relations if r.source_id in by_id and r.target_id in by_id]

    if request.operation == "connect":
        return _connect(request, all_entities, all_relations, by_id)

    # One search path. Whatever the caller typed — a question, concept names, a word
    # from a statement — is searched by words and (when the card has an embedding
    # model) by meaning, so no choice of parameter can bypass either.
    search_text = request.search_text()
    if graph_search.phrase(search_text):
        semantic, status = ({}, {})
        if request.semantic:
            semantic, status = _semantic_scores(context, kb, search_text, all_entities, all_relations)
        return {**_ranked_query(request, all_entities, all_relations, by_id, needles, semantic),
                "search_text": search_text, **status}

    # No search at all: the plain listing the graph map loads.
    shown = all_entities[:request.limit]
    shown_ids = {entity.id for entity in shown}
    relations = [r for r in all_relations if r.source_id in shown_ids and r.target_id in shown_ids][:request.limit * 5]
    names = {str(entity.id): entity.name for entity in shown}
    return _bounded({
        "entities": [_entity_json(e) for e in shown],
        "relations": [_relation_json(r, names) for r in relations],
        "matched": None,
        "truncated": len(all_entities) > len(shown) or len(all_relations) > len(relations),
    })


# ---------------------------------------------------------------- graph index


class GraphIndex(Request):
    operation: Literal["status", "build"] = "status"
    group_id: str | None = None


class GraphIndexStatus(Request):
    operation: Literal["status"] = "status"
    group_id: str | None = None


class GraphIndexBuild(Request):
    group_id: str | None = None


def _index_state(context):
    stored = context.state.get()["value"] if context.state is not None else {}
    saved = stored.get("semantic_index")
    return dict(saved) if isinstance(saved, dict) else {}


def _remember_index(context, **patch):
    if context.state is not None:
        context.state.update({"semantic_index": {**_index_state(context), **patch}})


def graph_index(context, arguments):
    """The semantic index behind graph search: which embedding model, how much of the
    published graph it covers, and the last failure. ``build`` embeds everything that
    is missing or stale now, so the first search is not the slow one."""
    request = GraphIndex.model_validate(arguments)
    if context.actor_id is not None and request.operation != "status":
        raise KnowledgeError("knowledge_graph_index is read-only; use knowledge_graph_index_build to build the index")
    return _graph_index(context, request)


def graph_index_build(context, arguments):
    request = GraphIndexBuild.model_validate(arguments)
    return _graph_index(context, GraphIndex(operation="build", group_id=request.group_id))


def _graph_index(context, request):
    kb = _client(context)
    scope = _any_group(request.group_id)
    store = kb.oaw_graph_store
    entities = store.list_entities(group_id=scope)
    relations = store.list_relations(group_id=scope)
    texts = embeddings.graph_texts(entities, relations)
    model = settings_of(context)["embedding_model"]
    result = {"embedding_model": model or None, "model_id": None, "total": len(texts),
              "embedded": 0, "pending": len(texts), "stale": 0, **_index_state(context)}
    if not model:
        return _bounded({**result, "enabled": False})
    try:
        connection = embeddings.connection_for(model, getattr(context, "resolve_model", None))
        result["model_id"] = connection[2]
        if request.operation == "build":
            embeddings.sync(kb.oaw_engine, texts, connection)
            _remember_index(context, last_error=None, built_at=_now())
            result.update(_index_state(context))
        result.update(embeddings.status(kb.oaw_engine, texts, connection[2]))
    except Exception as error:
        message = (str(error) or type(error).__name__)[:300]
        if request.operation == "build":
            _remember_index(context, last_error=message)
        result["last_error"] = message
        if result["model_id"]:
            result.update(embeddings.status(kb.oaw_engine, texts, result["model_id"]))
    return _bounded({**result, "enabled": True})


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


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
