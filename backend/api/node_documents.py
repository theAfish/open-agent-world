from fastapi import APIRouter, Depends
from fastapi.responses import Response
from urllib.parse import quote
from backend.errors import ResourceValidationError
from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend.node_documents import DocumentActionRequest, read_document, invoke_document_action
from backend.node_execution import ExecutionRequest

router = APIRouter(prefix="/nodes", tags=["node-documents"])

from backend.document_transformations import TransformationRequest, transform_document

@router.post("/{node_id}/transformations/{operation}")
async def transformation(node_id: str, operation: str, request: TransformationRequest, services: ApplicationServices = Depends(get_services)):
    return await transform_document(services, node_id, operation, request)

@router.get("/{node_id}/document/downloads/{name}")
async def download_document(node_id: str, name: str, services: ApplicationServices = Depends(get_services)):
    from backend.node_documents import definition
    async with services._node_mutation(read_only=True):
        exporter = definition(services, node_id).downloads.get(name)
        if exporter is None:
            raise ResourceValidationError("Unknown document download")
        result = exporter(read_document(services, node_id)["value"])
    return Response(result.content, media_type=result.media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(result.filename, safe='')}"})

@router.get("/{node_id}/document")
async def get_document(node_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return read_document(services, node_id)

@router.post("/{node_id}/actions/{action}")
async def action(node_id: str, action: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    return await invoke_document_action(services, node_id, action, request)


@router.get("/{node_id}/execution")
async def execution(node_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return services.node_execution.snapshot(node_id)


@router.post("/{node_id}/execution/start")
async def start_execution(node_id: str, request: ExecutionRequest, services: ApplicationServices = Depends(get_services)):
    return await services.node_execution.start(node_id, request)


@router.post("/{node_id}/execution/stop")
async def stop_execution(node_id: str, services: ApplicationServices = Depends(get_services)):
    return await services.node_execution.stop(node_id)
