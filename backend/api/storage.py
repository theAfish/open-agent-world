from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend.storage_location import schedule_storage, storage_status

router = APIRouter(tags=["settings"])


class StorageLocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_path: str | None = Field(default=None, max_length=4096)
    expected_revision: int = Field(ge=0)


@router.get("/settings/storage")
async def get_storage_settings(services: ApplicationServices = Depends(get_services)) -> dict:
    return storage_status(services.settings)


@router.put("/settings/storage")
async def save_storage_settings(request: StorageLocationRequest,
                                services: ApplicationServices = Depends(get_services)) -> dict:
    return schedule_storage(services.settings, request.target_path, request.expected_revision)
