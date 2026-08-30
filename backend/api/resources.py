from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from backend.api.dependencies import get_services
from backend.resources.models import (
    ImageImport,
    ResourceRecord,
    ResourceRevision,
    TextDocument,
    TextPatch,
    TextReplace,
)
from backend.services import ApplicationServices
from backend.world.models import CardType


router = APIRouter(tags=["resources"])


@router.get("/resources/{resource_id}", response_model=ResourceRecord)
async def get_resource(
    resource_id: str, services: ApplicationServices = Depends(get_services)
) -> ResourceRecord:
    return services.resources.get_record(resource_id)


@router.post("/resources/{resource_id}/image", response_model=ResourceRecord, status_code=201)
async def import_image(
    resource_id: str,
    request: ImageImport,
    services: ApplicationServices = Depends(get_services),
) -> ResourceRecord:
    return await services.import_image(resource_id, request)


@router.get("/resources/{resource_id}/text", response_model=TextDocument)
async def read_text(
    resource_id: str, services: ApplicationServices = Depends(get_services)
) -> TextDocument:
    return services.resources.read_text(resource_id)


@router.put("/resources/{resource_id}/text", response_model=TextDocument)
async def replace_text(
    resource_id: str,
    request: TextReplace,
    services: ApplicationServices = Depends(get_services),
) -> TextDocument:
    return await services.replace_text(resource_id, request)


@router.patch("/resources/{resource_id}/text", response_model=TextDocument)
async def patch_text(
    resource_id: str,
    request: TextPatch,
    services: ApplicationServices = Depends(get_services),
) -> TextDocument:
    return await services.patch_text(resource_id, request)


@router.get("/resources/{resource_id}/history", response_model=list[ResourceRevision])
async def resource_history(
    resource_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    services: ApplicationServices = Depends(get_services),
) -> list[ResourceRevision]:
    return services.resources.list_history(resource_id, limit=limit)


@router.get("/resources/{resource_id}/content", response_class=FileResponse)
async def resource_content(
    resource_id: str, services: ApplicationServices = Depends(get_services)
) -> FileResponse:
    record, path = services.resources.read_bytes(resource_id)
    return FileResponse(path, media_type=record.media_type, filename=record.filename)


@router.get(
    "/agents/{agent_id}/resources/{resource_id}/text", response_model=TextDocument
)
@router.get(
    "/agents/{agent_id}/text/{resource_id}", response_model=TextDocument, include_in_schema=False
)
async def agent_read_text(
    agent_id: str,
    resource_id: str,
    services: ApplicationServices = Depends(get_services),
) -> TextDocument:
    return services.capabilities.read_text(agent_id, resource_id)


@router.put(
    "/agents/{agent_id}/resources/{resource_id}/text", response_model=TextDocument
)
@router.put(
    "/agents/{agent_id}/text/{resource_id}", response_model=TextDocument, include_in_schema=False
)
async def agent_replace_text(
    agent_id: str,
    resource_id: str,
    request: TextReplace,
    services: ApplicationServices = Depends(get_services),
) -> TextDocument:
    return await services.replace_text(resource_id, request, agent_id=agent_id)


@router.patch(
    "/agents/{agent_id}/resources/{resource_id}/text", response_model=TextDocument
)
@router.patch(
    "/agents/{agent_id}/text/{resource_id}", response_model=TextDocument, include_in_schema=False
)
async def agent_patch_text(
    agent_id: str,
    resource_id: str,
    request: TextPatch,
    services: ApplicationServices = Depends(get_services),
) -> TextDocument:
    return await services.patch_text(resource_id, request, agent_id=agent_id)


@router.get("/agents/{agent_id}/resources/{resource_id}/image", response_class=FileResponse)
async def agent_view_image(
    agent_id: str,
    resource_id: str,
    services: ApplicationServices = Depends(get_services),
) -> FileResponse:
    record, path = services.capabilities.view_image(agent_id, resource_id)
    if record.kind is not CardType.IMAGE:
        # The broker already guards this; keeping the response branch explicit
        # documents that text bytes are never returned through the image tool.
        raise AssertionError("image capability returned a non-image resource")
    return FileResponse(path, media_type=record.media_type, filename=record.filename)
