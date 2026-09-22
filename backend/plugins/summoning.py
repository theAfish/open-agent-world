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
    action: Literal["list", "summon", "inspect", "message", "wait", "stop", "reclaim"] = "list"
    agent_id: str | None = None
    instance_id: str | None = None
    instance_ids: list[str] = Field(default_factory=list, max_length=32)
    wait: bool = Field(default=True, description="Set false to start work and immediately receive its instance handle.")
    wait_mode: Literal["any", "all"] = "all"
    timeout_seconds: float = Field(default=30, ge=0, le=60)
    context_mode: Literal["task", "inherit"] = Field(default="task", description="Task mode keeps a private context across follow-up turns; include required inputs in prompt.")
    prompt: str | None = Field(default=None, max_length=100000,
                              description="Non-empty task text for summon or message. Omit for list, inspect, stop, or reclaim.")
