"""Plugin contract for libraries of callable subgraphs."""
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from backend.legions.models import LegionBlueprint, LegionCapture


@dataclass(frozen=True, slots=True)
class NodeSummoningDefinition:
    capability_kind: str
    templates_field: str | None = None


class SummoningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_depth: int = Field(default=4, ge=1, le=16)
    max_concurrent: int = Field(default=4, ge=1, le=32)
    max_instances: int = Field(default=16, ge=1, le=100)


class SharedBinding(BaseModel):
    internal_key: str
    internal_is_source: bool
    external_id: str
    external_type: str
    external_plugin_id: str
    relationship: str
    plugin_id: str
    direction: Literal["forward", "bidirectional"]


class CallableTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex)
    node_id: str | None = None
    name: str = Field(default="Agent template", min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    entry_agent_key: str = ""
    blueprint: LegionBlueprint | None = None
    bindings: list[SharedBinding] = Field(default_factory=list)
    policy: SummoningPolicy = Field(default_factory=SummoningPolicy)


class SummoningCapture(LegionCapture):
    node_ids: list[str] = Field(min_length=1, max_length=101)
    entry_agent_id: str
    shared_node_ids: list[str] = Field(default_factory=list)
    expected_revision: int = Field(ge=0)


class SummoningAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "summon", "inspect", "message", "stop", "reclaim"] = "list"
    template_id: str | None = None
    instance_id: str | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=100000)
