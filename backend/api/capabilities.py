from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_services
from backend.capabilities.models import CapabilitySet
from backend.services import ApplicationServices


router = APIRouter(tags=["capabilities"])


@router.get("/agents/{agent_id}/capabilities", response_model=CapabilitySet)
async def get_capabilities(
    agent_id: str, services: ApplicationServices = Depends(get_services)
) -> CapabilitySet:
    return services.capabilities.derive(agent_id)
