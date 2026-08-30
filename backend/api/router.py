from __future__ import annotations

from fastapi import APIRouter

from backend.api.capabilities import router as capabilities_router
from backend.api.resources import router as resources_router
from backend.api.runtime import router as runtime_router
from backend.api.world import router as world_router


api_router = APIRouter(prefix="/api")


@api_router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


api_router.include_router(world_router)
api_router.include_router(resources_router)
api_router.include_router(capabilities_router)
api_router.include_router(runtime_router)
