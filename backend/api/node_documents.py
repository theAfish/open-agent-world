from fastapi import APIRouter, Depends
from fastapi.responses import Response
from urllib.parse import quote
from backend.errors import ResourceValidationError
from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend.node_documents import DocumentActionRequest, read_document, invoke_document_action
from backend.node_execution import ExecutionRequest

router = APIRouter(prefix="/nodes", tags=["node-documents"])


@router.get("/{node_id}/document/binaries/{field}")
async def binary_history(node_id: str, field: str, services: ApplicationServices = Depends(get_services)):
    from backend.document_blobs import list_blobs
    async with services._node_mutation(read_only=True):
        return list_blobs(services, node_id, field)


@router.get("/{node_id}/document/binaries/{field}/{digest}")
async def binary_version(node_id: str, field: str, digest: str, services: ApplicationServices = Depends(get_services)):
    from backend.document_blobs import read_blob, read_snapshot
    async with services._node_mutation(read_only=True):
        raw = read_blob(services, node_id, field, digest)
        snapshot = read_snapshot(services, node_id, field, digest)
    is_pdf = raw.startswith(b"%PDF-")
    filename = digest + (".pdf" if is_pdf else ".bin")
    if is_pdf and isinstance(snapshot, dict):
        original = snapshot.get("value", snapshot).get("filename")
        if isinstance(original, str) and original.lower().endswith(".pdf"):
            filename = original.replace("\\", "/").rsplit("/", 1)[-1][:240]
    return Response(raw, media_type="application/pdf" if is_pdf else "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}", "X-Content-Type-Options": "nosniff"})


@router.get("/{node_id}/document/binaries/{field}/{digest}/snapshot")
async def binary_snapshot(node_id: str, field: str, digest: str, services: ApplicationServices = Depends(get_services)):
    from backend.document_blobs import read_snapshot
    async with services._node_mutation(read_only=True):
        return read_snapshot(services, node_id, field, digest)

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
async def get_document(node_id: str, services: ApplicationServices = Depends(get_services), summary_only: bool = False):
    async with services._node_mutation(read_only=True):
        document = read_document(services, node_id)
        return {"revision": document["revision"], "summary": document["summary"]} if summary_only else document

@router.post("/{node_id}/actions/{action}")
async def action(node_id: str, action: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    return await invoke_document_action(services, node_id, action, request)


@router.get("/{node_id}/execution")
async def execution(node_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return services.node_execution.snapshot(node_id)


@router.post("/{node_id}/execution/actions/{action}")
async def delegation_action(node_id: str, action: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    return await services.node_execution.delegation_action(node_id, action, request.arguments)


@router.post("/{node_id}/execution/start")
async def start_execution(node_id: str, request: ExecutionRequest, services: ApplicationServices = Depends(get_services)):
    return await services.node_execution.start(node_id, request)


@router.post("/{node_id}/execution/stop")
async def stop_execution(node_id: str, services: ApplicationServices = Depends(get_services)):
    return await services.node_execution.stop(node_id)
