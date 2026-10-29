"""Reuse registered business handlers inside the administrator's published graph."""
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api import capabilities, card_state, node_documents, node_resources, resources, runtime
from backend.document_transformations import TransformationRequest
from backend.api.dependencies import get_services
from backend.plugins.deployment import default_deployment, merged_surfaces, resolve_surface
from backend.publication_scope import PRIVATE_FIELDS, PublicationScope, business_mutation, public_value
from backend.world.models import CardCreate, CardPatch, EdgeCreate

MANAGED_CONFIG = PRIVATE_FIELDS | {"runtime_provider_id", "workspace_layout", "plugin_directories"}


def business_access(manifest, services, node_id):
    if node_id in manifest.get("plugin_access", {}):
        return manifest["plugin_access"][node_id]
    scope = PublicationScope(manifest)
    scope.require(node_id, services)
    node = services.plugins.node_type(services.world.get_card(node_id).type)
    contract = default_deployment(node)
    return {**merged_surfaces([resolve_surface(contract.surface, node)]), "shared_sections": True}


def projected_card(manifest, services, card):
    node = services.plugins.node_type(card.type)
    fields = manifest.get("plugin_access", {}).get(card.id, {}).get("config_fields", default_deployment(node).surface.config_fields)
    return {**card.model_dump(mode="json"), "config": public_value({key: value for key, value in card.config.items() if key in fields})}


def business_router(manifest):
    scope = PublicationScope(manifest)
    router = APIRouter()

    def require(node_id, services):
        scope.require(node_id, services)

    def validate_config(config, services, current=None):
        from backend.hosting.policy import unsafe_host_configuration
        changed = {key: value for key, value in config.items() if current is None or value != current.config.get(key)}
        if unsafe_host_configuration(changed):
            raise HTTPException(403, "Host configuration is managed by the project owner")
        if set(config) & MANAGED_CONFIG:
            for key in set(config) & MANAGED_CONFIG:
                if current is None or config[key] != current.config.get(key):
                    raise HTTPException(403, "This configuration is managed by the administrator")
        if public_value(config) != config:
            raise HTTPException(403, "Credentials are managed by the administrator")
        scope.validate_references(config, services)

    @router.get("/world")
    async def world(services=Depends(get_services)):
        async with services._node_mutation(read_only=True):
            cards = scope.cards(services)
            ids = {card.id for card in cards}
            return {"nodes": [projected_card(manifest, services, card) for card in cards],
                    "edges": [edge.model_dump(mode="json") for edge in services.world.list_edges() if {edge.source, edge.target} <= ids],
                    "chunks": [], "chunk_size": services.world.chunk_size, "terrain_seed": services.world.terrain_seed}

    @router.get("/catalog")
    def catalog(services=Depends(get_services)):
        from backend.deployment_workspace import workspace_snapshot
        return workspace_snapshot(manifest, services)["catalog"]

    @router.get("/settings/models")
    def models(services=Depends(get_services)):
        from backend.security.model_connections import ModelConnectionStore
        catalog = ModelConnectionStore(services.llm_settings).read().model_dump(mode="json")
        # Model selection is business metadata; endpoints and credential settings stay private.
        fields = {"id", "name", "enabled", "adapter", "models"}
        return public_value({**catalog, "connections": [{key: value for key, value in item.items() if key in fields} for item in catalog["connections"]]})

    @router.get("/agents/{agent_id}")
    async def agent(agent_id: str, services=Depends(get_services)):
        require(agent_id, services)
        value = await runtime.get_agent(agent_id, services)
        return public_value(value)

    @router.get("/agents/{agent_id}/capabilities")
    async def grants(agent_id: str, services=Depends(get_services)):
        require(agent_id, services)
        value = (await capabilities.get_capabilities(agent_id, services)).model_dump(mode="json")
        ids = {card.id for card in scope.cards(services)}
        value["capabilities"] = [item for item in value["capabilities"] if not item.get("target_id") or item["target_id"] in ids]
        return value

    @router.post("/agents/{agent_id}/run", status_code=202)
    async def run(agent_id: str, request: runtime.AgentRunRequest, services=Depends(get_services)):
        require(agent_id, services)
        if request.parent_run_id is not None or request.task_id is not None:
            raise HTTPException(403, "Run ownership is assigned by the application")
        return await runtime.run_agent(agent_id, request, services)

    @router.post("/agents/{agent_id}/stop")
    async def stop(agent_id: str, services=Depends(get_services)):
        require(agent_id, services)
        return await runtime.stop_agent(agent_id, services)

    @router.patch("/nodes/{card_id}")
    async def configure(card_id: str, request: CardPatch, services=Depends(get_services)):
        require(card_id, services)
        current = services.world.get_card(card_id)
        if request.parent_id == current.parent_id and "parent_id" in request.model_fields_set:
            request = CardPatch.model_validate(request.model_dump(exclude_unset=True, exclude={"parent_id"}))
        if current.type == "legion" or request.model_fields_set - {"config", "name", "expected_revision"}:
            raise HTTPException(403, "Application layout and membership are managed by the administrator")
        validate_config(request.config or {}, services, current)
        if set(request.config or {}) - set(business_access(manifest, services, card_id)["config_fields"]):
            raise HTTPException(404, "This configuration is not published")
        with business_mutation():
            result = await services.update_card(card_id, request)
        return projected_card(manifest, services, result)

    @router.post("/nodes", status_code=201)
    async def create(request: CardCreate, services=Depends(get_services)):
        # Derived business inputs and outputs belong to an already published container.
        if request.parent_id not in scope.roots or request.type == "legion" or request.equipment or request.minister:
            raise HTTPException(403, "New application data must belong to a published workspace")
        from backend.deployment_workspace import frontend_catalog
        if services.plugins.node_type_owner_id(request.type) not in frontend_catalog(manifest, services)["enabled_plugin_ids"]:
            raise HTTPException(403, "This card type is not part of the application")
        validate_config(request.config, services)
        with business_mutation():
            result = await services.create_card(request)
        return projected_card(manifest, services, result)

    @router.post("/edges", status_code=201)
    async def connect(request: EdgeCreate, services=Depends(get_services)):
        require(request.source, services)
        require(request.target, services)
        relationships = {edge.relationship for edge in services.world.list_edges()
                         if edge.source in {card.id for card in scope.cards(services)}}
        if request.relationship not in relationships:
            raise HTTPException(403, "This relationship is not part of the application")
        with business_mutation():
            return await services.create_edge(request)

    def gate(request: Request, services=Depends(get_services)):
        for key in ("node_id", "source_id", "resource_id"):
            if request.path_params.get(key):
                require(request.path_params[key], services)

    def transformation_access(node_id: str, operation: str, services=Depends(get_services)):
        require(node_id, services)
        document = services.plugins.node_type(services.world.get_card(node_id).type).document
        if document is None or operation not in document.transformations:
            raise HTTPException(404, "This transformation is not published")

    @router.post("/nodes/{node_id}/transformations/{operation}", dependencies=[Depends(transformation_access)])
    async def transform(node_id: str, operation: str, request: TransformationRequest, services=Depends(get_services)):
        if request.source_id:
            require(request.source_id, services)
        with business_mutation():
            return await node_documents.transformation(node_id, operation, request, services)

    @router.get("/nodes/{node_id}/execution-sources")
    async def execution_sources(node_id: str, services=Depends(get_services)):
        require(node_id, services)
        value = await node_resources.execution_sources(node_id, services)
        ids = {card.id for card in scope.cards(services)}
        return {"sources": [item for item in value["sources"] if item["id"] in ids]}

    @router.get("/nodes/{node_id}/execution-sources/{source_id}")
    async def execution_snapshot(node_id: str, source_id: str, limit: int | None = None, cursor: str | None = None, services=Depends(get_services)):
        require(node_id, services)
        require(source_id, services)
        return await node_resources.execution_snapshot(node_id, source_id, limit, cursor, services)

    # These routers already implement business validation, revisions, ownership and storage limits.
    for source, names in (
        (card_state.router, {"get_state", "set_state", "update_state", "delete_state"}),
        (node_documents.router, {"delegation_action"}),
        (node_resources.router, {"data_sources", "data_source", "schema_options"}),
        (resources.router, {"replace_text", "patch_text", "import_image"}),
    ):
        for route in source.routes:
            if route.endpoint.__name__ in names:
                router.add_api_route(route.path, route.endpoint, methods=list(route.methods),
                                     status_code=route.status_code, dependencies=[Depends(gate)])
    return router
