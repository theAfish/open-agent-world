from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.services import ApplicationServices

router = APIRouter(prefix="/nodes", tags=["node-resources"])


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
