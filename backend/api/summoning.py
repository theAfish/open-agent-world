from fastapi import APIRouter, Depends
from backend.api.dependencies import get_services
from backend.plugins.summoning import SummoningAction
from backend.services import ApplicationServices

router = APIRouter(prefix="/nodes", tags=["summoning"])


@router.get("/{node_id}/summoning")
async def snapshot(node_id: str, services: ApplicationServices = Depends(get_services)):
    async with services._node_mutation(read_only=True):
        return services.summoning.snapshot(node_id)


@router.post("/{node_id}/summoning/actions")
async def action(node_id: str, request: SummoningAction, services: ApplicationServices = Depends(get_services)):
    return await services.summoning.action(node_id, request)
