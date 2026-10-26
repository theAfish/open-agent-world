"""Small host bridge: extraction uses the existing protected LLM connection.

The card's Extract buttons need a model call that no plugin may make itself — plugins
never see API credentials. Each route resolves the OAW model connection, calls the
provider, and hands the result straight back to the plugin's own save action, so
what was extracted is validated and evidence-linked by the plugin as usual.

The prompts themselves are built by the plugin (``oaw_knowledge_base.knowledge_prompt``,
``experiment_prompt``, ``insight_prompt``), which is also what the ``kb`` CLI uses
outside OAW, so the two paths cannot drift.

The ``run_*`` functions are the reusable core: the engineering routes below and the
deployment routes in ``backend/deployment_workspace.py`` both call them, so a
published release runs the exact same model calls the engineering canvas does.
"""
import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.dependencies import get_services
from backend.errors import ResourceValidationError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.security.model_connections import MODEL_REF_PREFIX, ModelConnectionStore

router = APIRouter()

NODE_TYPE = "knowledge.base"
MINERU_REFERENCE = "OAW_MINERU_TOKEN"


def _mineru_status(services, node):
    from backend.security.resource_credentials import resource_secret_resolver

    local = services.execution_credentials.configured(node.id, MINERU_REFERENCE)
    configured = local or bool(resource_secret_resolver(services, node)(MINERU_REFERENCE))
    return {"configured": configured, "source": "card" if local else "external" if configured else None}


@router.get("/knowledge/{node_id}/mineru-token")
async def mineru_token_status(node_id: str, services=Depends(get_services)):
    async with services._node_mutation(read_only=True):
        node = services.world.get_card(node_id)
        if node.type != NODE_TYPE:
            raise ResourceValidationError("Expected a Knowledge base node")
        return _mineru_status(services, node)


@router.put("/knowledge/{node_id}/mineru-token")
async def save_mineru_token(node_id: str, request: Request, services=Depends(get_services)):
    # Parse manually: Pydantic validation errors may echo submitted secret values.
    try:
        payload = await request.json()
    except ValueError:
        raise ResourceValidationError("Invalid credential request") from None
    if not isinstance(payload, dict) or set(payload) != {"value"}:
        raise ResourceValidationError("Supply a credential value, or null to remove it")
    value = payload["value"]
    if value is not None and (not isinstance(value, str) or not value.strip()
                             or "\0" in value or len(value.encode()) > 16000):
        raise ResourceValidationError("Credential must be a nonempty NUL-free string up to 16 KB")
    async with services._node_mutation():
        node = services.world.get_card(node_id)
        if node.type != NODE_TYPE:
            raise ResourceValidationError("Expected a Knowledge base node")
        services.node_execution.assert_editable(node_id)
        if value is None:
            services.execution_credentials.unbind(node_id, MINERU_REFERENCE)
        else:
            services.execution_credentials.bind(node_id, MINERU_REFERENCE, value)
        return _mineru_status(services, node)


def _builders(module, *names):
    """Imported lazily: the plugin reaches ``sys.path`` only once the loader runs."""
    try:
        imported = __import__(f"oaw_knowledge_base.{module}", fromlist=list(names))
    except ImportError:  # pragma: no cover - the route is dead without the plugin
        raise HTTPException(503, "The knowledge base plugin is not available") from None
    return tuple(getattr(imported, name) for name in names)


class KnowledgeExtractRequest(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    # Empty the draft and read every source again; otherwise only the sources not yet extracted.
    replace: bool = False


class KnowledgeAuditRequest(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)


class KnowledgeGraphAuditRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    group_id: str | None = Field(default=None, max_length=64)
    all_groups: bool = False


class RecordExtractRequest(BaseModel):
    schema_id: str = Field(min_length=1, max_length=64)
    source_ids: list[str] = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    replace_drafts: bool = False


class ExperimentExtractRequest(BaseModel):
    bundle_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    replace_drafts: bool = False


class InsightExtractRequest(BaseModel):
    # One schema, several, or neither: every experiment schema with published records.
    schema_id: str | None = Field(default=None, max_length=64)
    schema_ids: list[str] = Field(default_factory=list, max_length=50)
    model: str = Field(min_length=1, max_length=200)
    group_id: str | None = Field(default=None, max_length=64)
    all_groups: bool = False
    # Which section's schemas an open scope takes (see ``insights.Scoped``).
    schema_kind: Literal["literature", "experiment"] = "experiment"
    replace_drafts: bool = False


def _credentials(services, reference):
    if reference.startswith(MODEL_REF_PREFIX):
        try:
            adapter, model, base_url, api_key = ModelConnectionStore(services.llm_settings).resolve(reference)
        except ResourceValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        if adapter not in {"openai", "legacy"}:
            raise HTTPException(422, "Select an OpenAI-compatible model for extraction")
        base_url = base_url or "https://api.openai.com/v1"
    else:
        connection = services.llm_settings.read()
        base_url, api_key, model = connection.base_url, connection.api_key, reference
        if not api_key or not base_url:
            raise HTTPException(422, "Configure the OAW model connection first")
    return base_url, api_key, model.removeprefix("openai/")


# One document (or table) is read in full, up to this many characters, so a long
# source is not silently cut at the first window the way a single page would be.
EXTRACT_MAX_CHARS = 400_000


async def _check_node(node_id, services):
    async with services._node_mutation(read_only=True):
        if services.world.get_card(node_id).type != NODE_TYPE:
            raise HTTPException(422, "Expected a Knowledge base node")


async def _complete(base_url, api_key, model, messages, parse, *, timeout=300):
    """One JSON-mode chat completion, parsed by the plugin. Never returns provider
    exception bodies: they may contain request credentials."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key and api_key != "oaw-no-auth" else {},
                json={"model": model, "response_format": {"type": "json_object"}, "messages": messages})
        if response.status_code != 200:
            raise HTTPException(502, f"The model returned HTTP {response.status_code}; check the model, quota and connection settings")
        return parse(response.json()["choices"][0]["message"]["content"])
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "The extraction request failed or returned malformed JSON; check the connection and retry") from None


async def _paged_prompt(services, node_id, action, arguments):
    """One prompt's whole document, following ``has_more`` up to ``EXTRACT_MAX_CHARS``."""
    prompt = await invoke_resource_action(services, node_id, action,
        ResourceActionRequest(arguments={**arguments, "limit": 200_000}))
    text = prompt["markdown"]
    while prompt["has_more"] and len(text) < EXTRACT_MAX_CHARS:
        prompt = await invoke_resource_action(services, node_id, action,
            ResourceActionRequest(arguments={**arguments, "limit": 200_000, "offset": len(text)}))
        text += prompt["markdown"]
    return prompt, text, bool(prompt["has_more"])


Progress = Callable[[dict], Awaitable[None]] | None


async def _report(progress: Progress, **event) -> None:
    if progress is not None:
        await progress(event)


def _stream(run: Callable[[Progress], Awaitable[dict]]) -> StreamingResponse:
    """Run ``run`` while streaming its progress as NDJSON: one ``{"event": "progress", ...}``
    line per step, then ``{"event": "done", "result": ...}`` or ``{"event": "error", "detail": ...}``.
    A long extraction (several model calls per source) thus shows the card where it is
    instead of one silent request; a client that ignores the stream still gets the
    result as the last line."""
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def progress(event: dict) -> None:
        await queue.put({"event": "progress", **event})

    async def work() -> None:
        try:
            result = await run(progress)
            await queue.put({"event": "done", "result": result})
        except HTTPException as exc:
            await queue.put({"event": "error", "status": exc.status_code, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - the stream must end; the detail stays generic
            await queue.put({"event": "error", "status": 500, "detail": "The request failed"})
        finally:
            await queue.put(None)

    async def lines():
        task = asyncio.create_task(work())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield json.dumps(item) + "\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# One chunk of a document per knowledge call: consecutive sections are grouped up to
# this many characters, so a long paper is read a few sections at a time (as MKB's
# agent does) instead of as one 400k-character prompt that dilutes the model's attention.
KNOWLEDGE_CHUNK_CHARS = 40_000


def _chunks(sections, total_characters, *, chunk_chars=KNOWLEDGE_CHUNK_CHARS, max_chars=EXTRACT_MAX_CHARS):
    """``[(offset, length, heading_paths)]`` covering the document in reading order.

    A document that fits one chunk is one call, as before. Otherwise consecutive
    sections are grouped until the next would overflow; a single section longer than
    a chunk is split by character windows. Reading stops at ``max_chars``; the caller
    reports the document as truncated when it does.
    """
    limit = min(total_characters, max_chars)
    if limit <= chunk_chars:
        return [(0, limit, [])] if limit else []
    chunks, start, length, paths = [], None, 0, []

    def flush():
        nonlocal start, length, paths
        if start is not None and length:
            chunks.append((start, length, paths))
        start, length, paths = None, 0, []

    for section in sections:
        offset, size, path = section["offset"], section["length"], section["heading_path"]
        if offset >= limit:
            break
        size = min(size, limit - offset)
        if size > chunk_chars:
            flush()
            for window in range(offset, offset + size, chunk_chars):
                chunks.append((window, min(chunk_chars, offset + size - window), [path]))
            continue
        if length and length + size > chunk_chars:
            flush()
        if start is None:
            start = offset
        length += size
        if path and path not in paths:
            paths.append(path)
    flush()
    return chunks


async def run_knowledge_extract(node_id: str, request: KnowledgeExtractRequest, services,
                                progress: Progress = None) -> dict:
    """The agent-free **Extract knowledge**: for each source of the draft not yet
    extracted (every one, when replacing), the document's outline is read, grouped
    into chunks of a few sections, and each chunk goes through one JSON-mode model
    call handed to the plugin's own ``knowledge_save`` — the same
    ``knowledge_extract_prompt`` → ``knowledge_save`` pair a connected Lab assistant
    drives section by section through its tools, so the draft merges identically
    whichever path produced it. Each prompt already carries the draft's names and the
    published concepts the chunk mentions, so later chunks reuse earlier ones."""
    await _check_node(node_id, services)
    draft = (await invoke_resource_action(services, node_id, "knowledge_drafts",
        ResourceActionRequest(arguments={"operation": "get", "draft_id": request.draft_id})))["draft"]
    pending = [item["source_id"] for item in draft["sources"]
               if request.replace or item["extracted_at"] is None]
    if not pending:
        raise HTTPException(422, "Every source of this draft is extracted already; choose Replace to read them again")
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_graph = _builders("knowledge_prompt", "build_messages", "parse_graph")

    await _report(progress, stage="outline", message=f"Reading the outline of {len(pending)} source(s)", done=0, total=None)
    outlines = {}
    for source_id in pending:
        outline = await invoke_resource_action(services, node_id, "knowledge_source_outline",
            ResourceActionRequest(arguments={"source_id": source_id}))
        outlines[source_id] = (outline, _chunks(outline["sections"], outline["total_characters"]))
    total = sum(len(chunks) for _, chunks in outlines.values())
    step = 0

    results, saved = [], None
    first_save = True
    for source_id in pending:
        outline, chunks = outlines[source_id]
        added = {"entities": 0, "relations": 0}
        for position, (offset, length, paths) in enumerate(chunks, start=1):
            where = " / ".join(paths) if paths else None
            await _report(progress, stage="read", done=step, total=total, source_id=source_id,
                          filename=outline.get("filename"), chunk=position, chunks=len(chunks), heading=where,
                          message=(f"Reading {outline.get('filename') or source_id}"
                                   + (f" · part {position}/{len(chunks)}" if len(chunks) > 1 else "")
                                   + (f" · {where}" if where else "")))
            prompt = await invoke_resource_action(services, node_id, "knowledge_extract_prompt",
                ResourceActionRequest(arguments={"source_id": source_id, "draft_id": request.draft_id,
                                                 "offset": offset, "limit": max(500, length)}))
            prompt["heading_path"] = " / ".join(paths) if paths else None
            entities, relations = await _complete(
                base_url, api_key, model, build_messages(prompt),
                lambda answer: parse_graph(answer, allow_empty=len(chunks) > 1))
            if not entities:
                # One chunk naming nothing (a reference list, a figure index) is not a failure.
                step += 1
                continue
            saved = await invoke_resource_action(services, node_id, "knowledge_save",
                ResourceActionRequest(arguments={"draft_id": request.draft_id, "source_id": source_id,
                                                 "entities": entities, "relations": relations,
                                                 "replace": request.replace and first_save}))
            first_save = False
            added["entities"] += saved["added"]["entities"]
            added["relations"] += saved["added"]["relations"]
            step += 1
            await _report(progress, stage="saved", done=step, total=total, source_id=source_id,
                          filename=outline.get("filename"), chunk=position, chunks=len(chunks),
                          entities=added["entities"], relations=added["relations"],
                          message=f"{outline.get('filename') or source_id}: {added['entities']} concepts, {added['relations']} relations so far")
        results.append({"source_id": source_id, "filename": outline.get("filename"), "chunks": len(chunks),
                        "truncated": outline["total_characters"] > EXTRACT_MAX_CHARS, **added})
    if saved is None:
        raise HTTPException(502, "The model named no concepts in any source of this draft")
    return {"draft": saved["draft"], "sources": results}


async def run_knowledge_audit(node_id: str, request: KnowledgeAuditRequest, services,
                              progress: Progress = None) -> dict:
    """The agent-free **Review draft**: the plugin's findings and the draft go through
    one JSON-mode model call whose typed operations are handed to the plugin's own
    ``knowledge_audit_apply`` — the same pair a connected Agent drives through its
    tools, minus the agent's option of checking a source section before a merge."""
    await _check_node(node_id, services)
    await _report(progress, stage="findings", message="Computing findings over the draft", done=0, total=3)
    prompt = await invoke_resource_action(services, node_id, "knowledge_audit_prompt",
        ResourceActionRequest(arguments={"draft_id": request.draft_id}))
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_operations = _builders("knowledge_audit", "build_messages", "parse_operations")
    statistics = prompt["statistics"]
    await _report(progress, stage="model", done=1, total=3,
                  message=(f"Asking the model about {statistics['concepts']} concepts and {statistics['relations']} relations "
                           f"({len(statistics['concept_clusters'])} look-alike group(s), {len(statistics['isolated'])} isolated)"))
    operations = await _complete(base_url, api_key, model, build_messages(prompt), parse_operations)
    await _report(progress, stage="apply", done=2, total=3, operations=len(operations),
                  message=f"Applying {len(operations)} operation(s)" if operations else "Nothing to change")
    if not operations:
        draft = (await invoke_resource_action(services, node_id, "knowledge_drafts",
            ResourceActionRequest(arguments={"operation": "get", "draft_id": request.draft_id})))["draft"]
        return {"draft": draft, "applied": 0, "skipped": 0, "review": None}
    return await invoke_resource_action(services, node_id, "knowledge_audit_apply",
        ResourceActionRequest(arguments={"draft_id": request.draft_id, "operations": operations,
                                         "expected_revision": prompt["revision"]}))


async def run_knowledge_graph_audit(node_id: str, request: KnowledgeGraphAuditRequest, services,
                                    progress: Progress = None) -> dict:
    """**Review graph**: findings over the whole published literature layer go through
    one JSON-mode model call. Unlike a draft review, nothing is applied here — the
    proposed operations go back to the card, which applies them through the plugin's
    ``knowledge_graph_audit_apply`` only after the person confirms, since they rewrite
    published knowledge."""
    await _check_node(node_id, services)
    scope = {"all_groups": request.all_groups, **({"group_id": request.group_id} if request.group_id else {})}
    await _report(progress, stage="findings", message="Computing findings over the published knowledge", done=0, total=2)
    prompt = await invoke_resource_action(services, node_id, "knowledge_graph_audit_prompt",
        ResourceActionRequest(arguments=scope))
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_operations = _builders("knowledge_audit", "build_messages", "parse_operations")
    statistics = prompt["statistics"]
    await _report(progress, stage="model", done=1, total=2,
                  message=(f"Asking the model about {statistics['concepts']} concepts and {statistics['relations']} relations "
                           f"from {len(prompt['draft_ids'])} published draft(s)"))
    operations = await _complete(base_url, api_key, model, build_messages(prompt), parse_operations)
    return {"operations": operations, "drafts": prompt["draft_ids"], "statistics": prompt["statistics"]}


async def run_record_extract(node_id: str, request: RecordExtractRequest, services) -> dict:
    """The agent-free **Extract records** from literature: one JSON-mode call per
    source, each handed to the plugin's own ``record_save``."""
    await _check_node(node_id, services)
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_extract = _builders("experiment_prompt", "build_messages", "parse_extract")

    results, records, invalid = [], [], 0
    for source_id in request.source_ids:
        prompt, markdown, truncated = await _paged_prompt(services, node_id, "record_extract_prompt",
                                                          {"schema_id": request.schema_id, "source_id": source_id})
        extracted = await _complete(base_url, api_key, model, build_messages(prompt, markdown=markdown), _parse_records(parse_extract))
        saved = await invoke_resource_action(services, node_id, "record_save",
            ResourceActionRequest(arguments={"schema_id": request.schema_id, "source_id": source_id,
                                             "records": extracted, "replace_drafts": request.replace_drafts}))
        records.extend(saved["records"])
        invalid += saved["invalid"]
        results.append({"source_id": source_id, "filename": prompt.get("filename"),
                        "records": len(saved["records"]), "invalid": saved["invalid"], "truncated": truncated})
    return {"records": records, "invalid": invalid, "sources": results, "schema_id": request.schema_id}


def _parse_records(parse_extract):
    """A literature source may legitimately report nothing for a schema; the records
    themselves carry no ``source_ids`` because the source is the request's."""
    def parse(answer):
        return [{"name": item["name"], "data": item["data"], "note": item["note"]}
                for item in parse_extract(answer, allow_empty=True)]
    return parse


async def run_experiment_extract(node_id: str, request: ExperimentExtractRequest, services) -> dict:
    """The agent-free **Extract records**: page through the upload's prompt, make one
    JSON-mode model call, and hand the records to the plugin's own ``experiment_save``
    — the same two actions a connected Lab assistant drives through its tools, so a
    record is validated and evidence-linked identically whichever path produced it."""
    await _check_node(node_id, services)
    prompt, markdown, truncated = await _paged_prompt(services, node_id, "experiment_extract_prompt",
                                                      {"bundle_id": request.bundle_id})
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_extract = _builders("experiment_prompt", "build_messages", "parse_extract")
    records = await _complete(base_url, api_key, model, build_messages(prompt, markdown=markdown), parse_extract)

    saved = await invoke_resource_action(services, node_id, "experiment_save",
        ResourceActionRequest(arguments={"bundle_id": request.bundle_id, "records": records,
                                         "replace_drafts": request.replace_drafts}))
    return {"records": saved["records"], "invalid": saved["invalid"], "bundle_id": request.bundle_id,
            "truncated": truncated}


async def run_insight_extract(node_id: str, request: InsightExtractRequest, services) -> dict:
    """The agent-free **Extract insights**: page through the schema's record table,
    make one JSON-mode model call, and hand the insights to the plugin's own
    ``insight_save`` — the same two actions a connected Lab assistant drives through
    its tools, so an insight is evidence-checked identically whichever path produced it."""
    await _check_node(node_id, services)
    scope = {"schema_ids": request.schema_ids, "all_groups": request.all_groups,
             "schema_kind": request.schema_kind,
             **({"schema_id": request.schema_id} if request.schema_id else {}),
             **({"group_id": request.group_id} if request.group_id else {})}
    prompt, table, truncated = await _paged_prompt(services, node_id, "insight_prompt", scope)
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_insights = _builders("insight_prompt", "build_messages", "parse_insights")
    insights = await _complete(base_url, api_key, model, build_messages(prompt, table=table), parse_insights)

    saved = await invoke_resource_action(services, node_id, "insight_save",
        ResourceActionRequest(arguments={**scope, "insights": insights,
                                         "replace_drafts": request.replace_drafts}))
    return {"insights": saved["insights"], "warnings": saved["warnings"],
            "schema_ids": saved["schema_ids"], "truncated": truncated}


@router.post("/knowledge/{node_id}/knowledge")
async def extract_knowledge(node_id: str, request: KnowledgeExtractRequest, services=Depends(get_services)):
    return _stream(lambda progress: run_knowledge_extract(node_id, request, services, progress))


@router.post("/knowledge/{node_id}/audit")
async def audit_knowledge(node_id: str, request: KnowledgeAuditRequest, services=Depends(get_services)):
    return _stream(lambda progress: run_knowledge_audit(node_id, request, services, progress))


@router.post("/knowledge/{node_id}/graph-audit")
async def audit_knowledge_graph(node_id: str, request: KnowledgeGraphAuditRequest, services=Depends(get_services)):
    return _stream(lambda progress: run_knowledge_graph_audit(node_id, request, services, progress))


@router.post("/knowledge/{node_id}/records")
async def extract_records(node_id: str, request: RecordExtractRequest, services=Depends(get_services)):
    return await run_record_extract(node_id, request, services)


@router.post("/knowledge/{node_id}/extract")
async def extract(node_id: str, request: ExperimentExtractRequest, services=Depends(get_services)):
    return await run_experiment_extract(node_id, request, services)


@router.post("/knowledge/{node_id}/insights")
async def extract_insights(node_id: str, request: InsightExtractRequest, services=Depends(get_services)):
    return await run_insight_extract(node_id, request, services)
