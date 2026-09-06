from fastapi import APIRouter, Depends
from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend.node_documents import DocumentActionRequest, read_document, invoke_document_action

router = APIRouter(prefix="/nodes", tags=["node-documents"])

@router.get("/{node_id}/document")
async def get_document(node_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return read_document(services, node_id)

@router.post("/{node_id}/actions/{action}")
async def action(node_id: str, action: str, request: DocumentActionRequest, services: ApplicationServices = Depends(get_services)):
    return await invoke_document_action(services, node_id, action, request)
