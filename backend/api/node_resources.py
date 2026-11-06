from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.services import ApplicationServices

router = APIRouter(prefix="/nodes", tags=["node-resources"])


@router.post("/{node_id}/resource/{action}/files")
async def file_action(node_id: str, action: str, request: Request,
                      services: ApplicationServices = Depends(get_services)):
    from backend.resource_files import resource_files
    from backend.hosting.policy import unsafe_host_configuration
    from backend.errors import ResourceValidationError
    node = services.world.get_card(node_id)
    if action not in services.plugins.node_type(node.type).resource_actions:
        raise ResourceValidationError("Unknown resource action")
    async with resource_files(request) as (metadata, files):
        if request.scope.get('oaw.hosted') and unsafe_host_configuration(metadata.arguments):
            from fastapi import HTTPException
            raise HTTPException(403, "Host configuration is managed by the operator")
        return await invoke_resource_action(services, node_id, action, metadata, files=files)


@router.get('/{node_id}/execution-sources')
async def execution_sources(node_id: str, services: ApplicationServices = Depends(get_services)):
    from backend.execution_graph import connected_sources
    async with services._node_mutation(read_only=True):
        return {'sources': connected_sources(services, node_id)}


@router.get('/{node_id}/execution-sources/{source_id}')
async def execution_snapshot(node_id: str, source_id: str, limit: int | None = Query(None, ge=1, le=2000),
                             cursor: str | None = Query(None, max_length=2048),
                             services: ApplicationServices = Depends(get_services)):
    return await invoke_resource_action(services, source_id, 'execution',
        ResourceActionRequest(arguments={'limit': limit, 'cursor': cursor}), execution_reader=node_id)


@router.get("/{node_id}/data-sources")
async def data_sources(node_id: str, services: ApplicationServices = Depends(get_services)):
    from backend.data_sources import connected_sources
    return {"sources": connected_sources(services, node_id)}


@router.post("/{node_id}/data-sources/{source_id}/{operation}")
async def data_source(node_id: str, source_id: str, operation: str, request: ResourceActionRequest,
                      services: ApplicationServices = Depends(get_services)):
    return await invoke_resource_action(services, source_id, operation, request, data_reader=node_id)


class SchemaOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    relationship: str = Field(min_length=1)


@router.post("/{node_id}/data-source-schemas")
async def schema_options(node_id: str, request: SchemaOptionsRequest,
                         services: ApplicationServices = Depends(get_services)):
    return await invoke_resource_action(services, request.source_id, "schemas", ResourceActionRequest(),
                                        data_reader=node_id, data_relationship=request.relationship)


@router.post("/{node_id}/resource/{action}")
async def action(node_id: str, action: str, request: ResourceActionRequest,
                 services: ApplicationServices = Depends(get_services)):
    return await invoke_resource_action(services, node_id, action, request)
