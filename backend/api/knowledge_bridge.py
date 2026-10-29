"""Small host bridge: extraction uses the existing protected LLM connection.

The card's Extract buttons need a model call that no plugin may make itself — plugins
never see API credentials. Each route resolves the OAW model connection, calls the
provider, and hands the result straight back to the plugin's own save action, so
what was extracted is validated and evidence-linked by the plugin as usual.

The prompts themselves are built by the plugin (``oaw_knowledge_base.spaces``,
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


class ProjectSpaceRequest(BaseModel):
    space_id: str = Field(min_length=1, max_length=64)
    project_ids: list[str] = Field(min_length=1, max_length=100)
    model: str = Field(default="", max_length=200)
    rerun: bool = False


class ProjectionReviewRequest(BaseModel):
    projection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(default="", max_length=200)
    instruction: str = Field(default="", max_length=4000)


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
async def run_project_space(node_id: str, request: ProjectSpaceRequest, services,
                            progress: Progress = None) -> dict:
    """Project source Markdown through the plugin's versioned Space operations."""
    await _check_node(node_id, services)
    reference = request.model or services.world.get_card(node_id).config.get("default_model", "")
    if not reference:
        raise HTTPException(422, "Configure a model connection or connect an Agent first")
    base_url, api_key, model = _credentials(services, reference)
    build_messages, parse_output = _builders("spaces", "build_messages", "parse_output")
    prepared = await invoke_resource_action(services, node_id, "projection_prepare",
        ResourceActionRequest(arguments={"project_ids": request.project_ids,
            "space_id": request.space_id, "rerun": request.rerun}))
    results = []
    owned = {item["id"] for item in prepared["projections"] if item["disposition"] == "created"}
    try:
        for index, item in enumerate(prepared["projections"]):
            if item["disposition"] != "created":
                results.append(item)
                continue
            await _report(progress, step="project", current=index + 1, done=index + 1, total=len(prepared["projections"]),
                project_id=item["project_id"], message=f"Projecting {item.get('project_name', 'project')}")
            try:
                prompt, markdown, truncated = await _paged_prompt(services, node_id, "projection_prompt",
                    {"projection_id": item["id"]})
                if truncated:
                    raise HTTPException(422, "This project exceeds the direct model input limit. Use a connected Agent to read all Markdown windows.")
                data = await _complete(base_url, api_key, model, build_messages(prompt, markdown=markdown), parse_output)
                saved = await invoke_resource_action(services, node_id, "projection_save",
                    ResourceActionRequest(arguments={"projection_id": item["id"], "data": data,
                        "expected_revision": prompt["expected_revision"]}))
                results.append(saved["projection"])
            except Exception as error:
                detail = error.detail if isinstance(error, HTTPException) else "Projection failed; check the Space definition and source conversion."
                failed = await invoke_resource_action(services, node_id, "projection_fail",
                    ResourceActionRequest(arguments={"projection_id": item["id"], "error": str(detail)}))
                results.append(failed["projection"])
            owned.discard(item["id"])
            await _report(progress, step="project", current=index + 1, done=index + 1, total=len(prepared["projections"]),
                project_id=item["project_id"], message=f"Processed {index + 1} / {len(prepared['projections'])} projects")
    finally:
        for projection_id in owned:
            # A disconnected client must not leave a permanently running task.
            try:
                await asyncio.shield(invoke_resource_action(services, node_id, "projection_fail",
                    ResourceActionRequest(arguments={"projection_id": projection_id,
                        "error": "Execution interrupted. Retry this project."})))
            except Exception:
                pass
    return {"projections": results, "space_id": request.space_id}


async def run_projection_review(node_id: str, request: ProjectionReviewRequest, services,
                                progress: Progress = None) -> dict:
    await _check_node(node_id, services)
    reference = request.model or services.world.get_card(node_id).config.get("default_model", "")
    if not reference:
        raise HTTPException(422, "Configure a model connection or connect an Agent first")
    base_url, api_key, model = _credentials(services, reference)
    build_messages, parse_output = _builders("spaces", "build_messages", "parse_output")
    prompt, markdown, truncated = await _paged_prompt(services, node_id, "projection_review_prompt",
        {"projection_id": request.projection_id})
    if truncated:
        raise HTTPException(422, "This project exceeds the direct model input limit. Use a connected Agent to review all source windows.")
    await _report(progress, step="review", message="Reviewing the selected result against its sources")
    messages = build_messages(prompt, markdown=markdown, review=True)
    if request.instruction.strip():
        messages.append({"role": "user", "content": request.instruction.strip()})
    data = await _complete(base_url, api_key, model, messages, parse_output)
    return await invoke_resource_action(services, node_id, "projection_revise",
        ResourceActionRequest(arguments={"projection_id": request.projection_id, "data": data,
            "expected_revision": prompt["expected_revision"], "note": request.instruction.strip() or "Optional source evidence review"}))


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


@router.post("/knowledge/{node_id}/project-space")
async def project_space(node_id: str, request: ProjectSpaceRequest, services=Depends(get_services)):
    return _stream(lambda progress: run_project_space(node_id, request, services, progress))


@router.post("/knowledge/{node_id}/review-projection")
async def review_projection(node_id: str, request: ProjectionReviewRequest, services=Depends(get_services)):
    return _stream(lambda progress: run_projection_review(node_id, request, services, progress))


@router.post("/knowledge/{node_id}/extract")
async def extract(node_id: str, request: ExperimentExtractRequest, services=Depends(get_services)):
    return await run_experiment_extract(node_id, request, services)


@router.post("/knowledge/{node_id}/insights")
async def extract_insights(node_id: str, request: InsightExtractRequest, services=Depends(get_services)):
    return await run_insight_extract(node_id, request, services)
