from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from backend.api.dependencies import get_services
from backend.errors import GraphValidationError
from backend.services import ApplicationServices
from backend.world.models import (
    Card,
    CardCreate,
    CardPatch,
    Edge,
    EdgeCreate,
    EdgePatch,
    WorldSnapshot,
)


router = APIRouter(tags=["world"])
_CHUNK = re.compile(r"^(-?\d+):(-?\d+)$")


def parse_chunks(value: str | None) -> list[tuple[int, int]] | None:
    if value is None or value.strip() == "":
        return None
    chunks: list[tuple[int, int]] = []
    for raw in value.split(","):
        match = _CHUNK.fullmatch(raw.strip())
        if match is None:
            raise GraphValidationError(
                "chunks must be a comma-separated list in x:y form, for example -1:0,0:0"
            )
        chunks.append((int(match.group(1)), int(match.group(2))))
    chunks = list(dict.fromkeys(chunks))
    if len(chunks) > 400:
        raise GraphValidationError("a world request may load at most 400 chunks")
    return chunks


@router.get("/world", response_model=WorldSnapshot)
async def get_world(
    chunks: str | None = Query(default=None),
    services: ApplicationServices = Depends(get_services),
) -> WorldSnapshot:
    return services.snapshot(parse_chunks(chunks))


@router.get("/chunks", response_model=WorldSnapshot)
async def get_chunk_range(
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
    prefetch: Annotated[int, Query(ge=0, le=3)] = 0,
    services: ApplicationServices = Depends(get_services),
) -> WorldSnapshot:
    if min_x > max_x or min_y > max_y:
        raise GraphValidationError("minimum chunk coordinates must not exceed maximums")
    coordinates = [
        (x, y)
        for x in range(min_x - prefetch, max_x + prefetch + 1)
        for y in range(min_y - prefetch, max_y + prefetch + 1)
    ]
    if len(coordinates) > 400:
        raise GraphValidationError("a chunk range may load at most 400 chunks")
    return services.snapshot(coordinates)


@router.get("/nodes", response_model=list[Card])
@router.get("/cards", response_model=list[Card], include_in_schema=False)
async def list_cards(
    chunks: str | None = Query(default=None),
    services: ApplicationServices = Depends(get_services),
) -> list[Card]:
    return services.snapshot(parse_chunks(chunks)).nodes


@router.post("/nodes", response_model=Card, status_code=status.HTTP_201_CREATED)
@router.post(
    "/cards", response_model=Card, status_code=status.HTTP_201_CREATED, include_in_schema=False
)
async def create_card(
    request: CardCreate,
    services: ApplicationServices = Depends(get_services),
) -> Card:
    return await services.create_card(request)


@router.get("/nodes/{card_id}", response_model=Card)
@router.get("/cards/{card_id}", response_model=Card, include_in_schema=False)
async def get_card(
    card_id: str, services: ApplicationServices = Depends(get_services)
) -> Card:
    return services.get_card(card_id)


@router.patch("/nodes/{card_id}", response_model=Card)
@router.patch("/cards/{card_id}", response_model=Card, include_in_schema=False)
async def update_card(
    card_id: str,
    request: CardPatch,
    services: ApplicationServices = Depends(get_services),
) -> Card:
    return await services.update_card(card_id, request)


@router.delete("/nodes/{card_id}", response_model=Card)
@router.delete("/cards/{card_id}", response_model=Card, include_in_schema=False)
async def delete_card(
    card_id: str, services: ApplicationServices = Depends(get_services)
) -> Card:
    return await services.delete_card(card_id)


@router.get("/edges", response_model=list[Edge])
async def list_edges(
    services: ApplicationServices = Depends(get_services),
) -> list[Edge]:
    return services.world.list_edges()


@router.post("/edges", response_model=Edge, status_code=status.HTTP_201_CREATED)
async def create_edge(
    request: EdgeCreate,
    services: ApplicationServices = Depends(get_services),
) -> Edge:
    return await services.create_edge(request)


@router.get("/edges/{edge_id}", response_model=Edge)
async def get_edge(
    edge_id: str, services: ApplicationServices = Depends(get_services)
) -> Edge:
    return services.world.get_edge(edge_id)


@router.patch("/edges/{edge_id}", response_model=Edge)
async def update_edge(
    edge_id: str,
    request: EdgePatch,
    services: ApplicationServices = Depends(get_services),
) -> Edge:
    return await services.update_edge(edge_id, request)


@router.delete("/edges/{edge_id}", response_model=Edge)
async def delete_edge(
    edge_id: str, services: ApplicationServices = Depends(get_services)
) -> Edge:
    return await services.delete_edge(edge_id)
