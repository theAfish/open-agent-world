from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from backend.api.dependencies import get_services
from backend.minister import InspectRequest, ensure_chat, inspect

router = APIRouter(tags=["ministers"])


@router.get("/ministers/{node_id}/world")
async def minister_world(node_id: str, query: str = Query(default="", max_length=200),
                         offset: int = Query(default=0, ge=0), services=Depends(get_services)):
    return await inspect(services, node_id, InspectRequest(query=query, offset=offset))


@router.post("/ministers/{node_id}/chat")
async def minister_chat(node_id: str, services=Depends(get_services)):
    return await ensure_chat(services, node_id)


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


@router.get("/ministers/{node_id}/proposals")
async def proposals(node_id: str, services=Depends(get_services)):
    from backend.minister import minister_card
    from backend.minister_policy import pending_proposals
    async with services._node_mutation(read_only=True):
        minister_card(services, node_id)
        return pending_proposals(services, node_id)


@router.post("/ministers/{node_id}/proposals/{proposal_id}")
async def review_proposal(node_id: str, proposal_id: str, request: ReviewDecision, services=Depends(get_services)):
    # Desktop host endpoint, deliberately absent from Minister capabilities.
    from backend.minister_policy import decide
    return await decide(services, node_id, proposal_id, request.approve)
