from fastapi import APIRouter, Depends

from backend.api.dependencies import get_services
from backend.errors import PermissionDeniedError
from backend.file_preview import FileReference, read_file

router = APIRouter(tags=["file-preview"])


@router.post("/nodes/{viewer_id}/file-preview")
async def file_preview(viewer_id: str, reference: FileReference, services=Depends(get_services)):
    viewer = services.world.get_card(viewer_id)
    if not services.plugins.has_trait(viewer.type, "core.file-viewer"):
        raise PermissionDeniedError("Node is not a file viewer")
    return await read_file(services, viewer_id, reference)
