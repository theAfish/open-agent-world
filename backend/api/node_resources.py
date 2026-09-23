import asyncio
import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from backend.api.dependencies import get_services
from backend.errors import NotFoundError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.plugins.resources import served_file
from backend.services import ApplicationServices

router = APIRouter(prefix="/nodes", tags=["node-resources"])


@router.post("/{node_id}/resource/{action}")
async def action(node_id: str, action: str, request: ResourceActionRequest,
                 services: ApplicationServices = Depends(get_services)):
    return await invoke_resource_action(services, node_id, action, request)


@router.get("/{node_id}/files/{key:path}")
async def read_file(node_id: str, key: str, request: Request, download: str | None = None,
                    services: ApplicationServices = Depends(get_services)):
    """Read-only access to files a node type declares in ``served_files``. Agents never reach this route."""
    async with services._node_mutation(read_only=True):
        card = services.world.get_card(node_id)
        patterns = services.plugins.node_type(card.type).served_files
        path = served_file(services.resources.node_storage_path(node_id), key, patterns)
        if path is None:
            raise NotFoundError("File not found")
        stat = path.stat()
        # Plugins replace files atomically, so size and mtime identify the content.
        etag = f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if download:
            headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(download[:200], safe='')}"
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        content = await asyncio.to_thread(path.read_bytes)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(content, media_type=media_type, headers=headers)
