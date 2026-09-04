from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.capabilities import router as capabilities_router
from backend.api.conversations import router as conversations_router
from backend.api.dependencies import get_services
from backend.api.legions import router as legions_router
from backend.api.resources import router as resources_router
from backend.api.runtime import router as runtime_router
from backend.api.world import router as world_router
from backend.plugins import PluginCatalog
from backend.services import ApplicationServices


api_router = APIRouter(prefix="/api")


@api_router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/catalog", response_model=PluginCatalog, tags=["system"])
async def plugin_catalog(
    services: ApplicationServices = Depends(get_services),
) -> PluginCatalog:
    return services.plugins.catalog()


api_router.include_router(world_router)
api_router.include_router(legions_router)
api_router.include_router(resources_router)
api_router.include_router(capabilities_router)
api_router.include_router(conversations_router)
api_router.include_router(runtime_router)
