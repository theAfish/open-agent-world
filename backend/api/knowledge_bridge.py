"""Small host bridge: projection uses the existing protected LLM connection.

The card's Project button needs a model call that no plugin may make itself — plugins
never see API credentials. This route resolves the OAW model connection, calls the
provider, and hands the result straight back to the plugin's own ``save_projection``
action, so the projection is validated and evidence-linked by the plugin as usual.

The prompt itself is built by the plugin (``oaw_knowledge_base.projection``), which is
also what ``kb project`` uses outside OAW, so the two paths cannot drift.
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_services
from backend.errors import ResourceValidationError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.security.model_connections import MODEL_REF_PREFIX, ModelConnectionStore

router = APIRouter()

NODE_TYPE = "knowledge.base"


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


@router.post("/knowledge/{node_id}/project")
async def project(node_id: str, request: ProjectionRequest, services=Depends(get_services)):
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
