from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.api.dependencies import get_services
from backend.legions import (
    LegionCapture,
    LegionInstance,
    LegionInstantiate,
    LegionSummary,
)
from backend.services import ApplicationServices


router = APIRouter(prefix="/legions", tags=["legions"])


@router.get("", response_model=list[LegionSummary])
async def list_legions(
    services: ApplicationServices = Depends(get_services),
) -> list[LegionSummary]:
    return services.list_legions()


@router.post("", response_model=LegionSummary, status_code=status.HTTP_201_CREATED)
async def capture_legion(
    request: LegionCapture,
    services: ApplicationServices = Depends(get_services),
) -> LegionSummary:
    return await services.capture_legion(request)


@router.delete("/{legion_id}", response_model=LegionSummary)
async def delete_legion(
    legion_id: str,
    services: ApplicationServices = Depends(get_services),
) -> LegionSummary:
    return await services.delete_legion(legion_id)


@router.post(
    "/{legion_id}/instances",
    response_model=LegionInstance,
    status_code=status.HTTP_201_CREATED,
)
async def instantiate_legion(
    legion_id: str,
    request: LegionInstantiate,
    services: ApplicationServices = Depends(get_services),
) -> LegionInstance:
    return await services.instantiate_legion(legion_id, request)
