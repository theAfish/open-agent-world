from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.dependencies import get_services
from backend.legions.runtime import LegionStateWrite, read_shared_state, write_shared_state
from backend.services import ApplicationServices

router = APIRouter(prefix="/legion-groups", tags=["legion-groups"])


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="New Legion", min_length=1, max_length=120)
    node_ids: list[str] = Field(min_length=1, max_length=100)


@router.post("")
async def form_group(request: GroupCreate, services: ApplicationServices = Depends(get_services)):
    return await services.form_legion_group(request.name, request.node_ids)


@router.get("/{group_id}/state")
async def read_state(group_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return read_shared_state(services.world, services.state, group_id)


@router.put("/{group_id}/state")
async def write_state(group_id: str, request: LegionStateWrite, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation():
        return write_shared_state(services.world, services.state, group_id, request)
