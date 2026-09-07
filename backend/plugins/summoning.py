"""Plugin contract for libraries of callable subgraphs."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class NodeSummoningDefinition:
    capability_kind: str


class SummoningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_depth: int = Field(default=4, ge=1, le=16)
    max_concurrent: int = Field(default=4, ge=1, le=32)
    max_instances: int = Field(default=16, ge=1, le=100)


class SummoningAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "summon", "inspect", "message", "stop", "reclaim"] = "list"
    agent_id: str | None = None
    instance_id: str | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=100000)
