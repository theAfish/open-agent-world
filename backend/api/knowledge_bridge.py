"""Small host bridge: projection uses the existing protected LLM connection.

The card's Project button needs a model call that no plugin may make itself — plugins
never see API credentials. This route resolves the OAW model connection, calls the
provider, and hands the result straight back to the plugin's own ``save_projection``
action, so the projection is validated and evidence-linked by the plugin as usual.

The prompt itself is built by the plugin (``oaw_knowledge_base.projection``), which is
also what ``kb project`` uses outside OAW, so the two paths cannot drift.

``run_projection`` is the reusable core: the engineering route below and the
deployment route in ``backend/deployment_workspace.py`` both call it, so a published
release runs the exact same model call the engineering canvas does.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
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


def _prompt_builders():
    """Imported lazily: the plugin reaches ``sys.path`` only once the loader runs."""
    try:
        from oaw_knowledge_base.projection import build_messages, parse_projection
    except ImportError:  # pragma: no cover - the route is dead without the plugin
        raise HTTPException(503, "The knowledge base plugin is not available") from None
    return build_messages, parse_projection


class ProjectionRequest(BaseModel):
    schema_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)
    source_id: str | None = Field(default=None, max_length=64)
    record_id: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)


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
    replace_drafts: bool = False


def _credentials(services, reference):
    if reference.startswith(MODEL_REF_PREFIX):
        try:
            adapter, model, base_url, api_key = ModelConnectionStore(services.llm_settings).resolve(reference)
        except ResourceValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        if adapter not in {"openai", "legacy"}:
            raise HTTPException(422, "Select an OpenAI-compatible model for projection")
        base_url = base_url or "https://api.openai.com/v1"
    else:
        connection = services.llm_settings.read()
        base_url, api_key, model = connection.base_url, connection.api_key, reference
        if not api_key or not base_url:
            raise HTTPException(422, "Configure the OAW model connection first")
    return base_url, api_key, model.removeprefix("openai/")


async def run_projection(node_id: str, request: ProjectionRequest, services) -> dict:
    async with services._node_mutation(read_only=True):
        if services.world.get_card(node_id).type != NODE_TYPE:
            raise HTTPException(422, "Expected a Knowledge base node")

    prompt = await invoke_resource_action(services, node_id, "projection_prompt",
        ResourceActionRequest(arguments={
            "schema_id": request.schema_id, "source_id": request.source_id,
            "record_id": request.record_id}))
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_projection = _prompt_builders()

    # Never return provider exception bodies: they may contain request credentials.
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
            response = await client.post(base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key and api_key != "oaw-no-auth" else {},
                json={"model": model, "response_format": {"type": "json_object"},
                      "messages": build_messages(prompt)})
        if response.status_code != 200:
            raise HTTPException(502, f"The model returned HTTP {response.status_code}; check the model, quota and connection settings")
        data = parse_projection(response.json()["choices"][0]["message"]["content"])
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "The projection request failed or returned malformed JSON; check the connection and retry") from None

    # The plugin owns the write: it validates against the schema and links evidence.
    saved = await invoke_resource_action(services, node_id, "save_projection",
        ResourceActionRequest(arguments={
            "schema_id": request.schema_id, "record_id": prompt["record_id"],
            "data": data, "notes": request.notes, "model": request.model}))
    return {"projection": saved["projection"], "truncated": prompt["truncated"],
            "record_id": prompt["record_id"]}


def _extract_prompt_builders():
    try:
        from oaw_knowledge_base.experiment_prompt import build_messages, parse_extract
    except ImportError:  # pragma: no cover - the route is dead without the plugin
        raise HTTPException(503, "The knowledge base plugin is not available") from None
    return build_messages, parse_extract


# One upload's combined markdown is read in full, up to this many characters, so a
# long upload is not silently cut at the first window the way a single page would be.
EXTRACT_MAX_CHARS = 400_000


async def run_experiment_extract(node_id: str, request: ExperimentExtractRequest, services) -> dict:
    """The agent-free **Extract records**: page through the upload's prompt, make one
    JSON-mode model call, and hand the records to the plugin's own ``experiment_save``
    — the same two actions a connected Lab assistant drives through its tools, so a
    record is validated and evidence-linked identically whichever path produced it."""
    async with services._node_mutation(read_only=True):
        if services.world.get_card(node_id).type != NODE_TYPE:
            raise HTTPException(422, "Expected a Knowledge base node")

    prompt = await invoke_resource_action(services, node_id, "experiment_extract_prompt",
        ResourceActionRequest(arguments={"bundle_id": request.bundle_id, "limit": 200_000}))
    markdown, truncated = prompt["markdown"], False
    while prompt["has_more"] and len(markdown) < EXTRACT_MAX_CHARS:
        prompt = await invoke_resource_action(services, node_id, "experiment_extract_prompt",
            ResourceActionRequest(arguments={"bundle_id": request.bundle_id, "limit": 200_000,
                                             "offset": len(markdown)}))
        markdown += prompt["markdown"]
    if prompt["has_more"]:
        truncated = True
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_extract = _extract_prompt_builders()

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=False) as client:
            response = await client.post(base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"******"} if api_key and api_key != "oaw-no-auth" else {},
                json={"model": model, "response_format": {"type": "json_object"},
                      "messages": build_messages(prompt, markdown=markdown)})
        if response.status_code != 200:
            raise HTTPException(502, f"The model returned HTTP {response.status_code}; check the model, quota and connection settings")
        records = parse_extract(response.json()["choices"][0]["message"]["content"])
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "The extraction request failed or returned malformed JSON; check the connection and retry") from None

    saved = await invoke_resource_action(services, node_id, "experiment_save",
        ResourceActionRequest(arguments={"bundle_id": request.bundle_id, "records": records,
                                         "replace_drafts": request.replace_drafts}))
    return {"records": saved["records"], "invalid": saved["invalid"], "bundle_id": request.bundle_id,
            "truncated": truncated}


def _insight_prompt_builders():
    try:
        from oaw_knowledge_base.insight_prompt import build_messages, parse_insights
    except ImportError:  # pragma: no cover - the route is dead without the plugin
        raise HTTPException(503, "The knowledge base plugin is not available") from None
    return build_messages, parse_insights


async def run_insight_extract(node_id: str, request: InsightExtractRequest, services) -> dict:
    """The agent-free **Extract insights**: page through the schema's record table,
    make one JSON-mode model call, and hand the insights to the plugin's own
    ``insight_save`` — the same two actions a connected Lab assistant drives through
    its tools, so an insight is evidence-checked identically whichever path produced it."""
    async with services._node_mutation(read_only=True):
        if services.world.get_card(node_id).type != NODE_TYPE:
            raise HTTPException(422, "Expected a Knowledge base node")

    scope = {"schema_ids": request.schema_ids, "all_groups": request.all_groups,
             **({"schema_id": request.schema_id} if request.schema_id else {}),
             **({"group_id": request.group_id} if request.group_id else {})}
    prompt = await invoke_resource_action(services, node_id, "insight_prompt",
        ResourceActionRequest(arguments={**scope, "limit": 200_000}))
    table, truncated = prompt["markdown"], False
    while prompt["has_more"] and len(table) < EXTRACT_MAX_CHARS:
        prompt = await invoke_resource_action(services, node_id, "insight_prompt",
            ResourceActionRequest(arguments={**scope, "limit": 200_000, "offset": len(table)}))
        table += prompt["markdown"]
    if prompt["has_more"]:
        truncated = True
    base_url, api_key, model = _credentials(services, request.model)
    build_messages, parse_insights = _insight_prompt_builders()

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=False) as client:
            response = await client.post(base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key and api_key != "oaw-no-auth" else {},
                json={"model": model, "response_format": {"type": "json_object"},
                      "messages": build_messages(prompt, table=table)})
        if response.status_code != 200:
            raise HTTPException(502, f"The model returned HTTP {response.status_code}; check the model, quota and connection settings")
        insights = parse_insights(response.json()["choices"][0]["message"]["content"])
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "The extraction request failed or returned malformed JSON; check the connection and retry") from None

    saved = await invoke_resource_action(services, node_id, "insight_save",
        ResourceActionRequest(arguments={**scope, "insights": insights,
                                         "replace_drafts": request.replace_drafts}))
    return {"insights": saved["insights"], "warnings": saved["warnings"],
            "schema_ids": saved["schema_ids"], "truncated": truncated}


@router.post("/knowledge/{node_id}/project")
async def project(node_id: str, request: ProjectionRequest, services=Depends(get_services)):
    return await run_projection(node_id, request, services)


@router.post("/knowledge/{node_id}/extract")
async def extract(node_id: str, request: ExperimentExtractRequest, services=Depends(get_services)):
    return await run_experiment_extract(node_id, request, services)


@router.post("/knowledge/{node_id}/insights")
async def extract_insights(node_id: str, request: InsightExtractRequest, services=Depends(get_services)):
    return await run_insight_extract(node_id, request, services)
