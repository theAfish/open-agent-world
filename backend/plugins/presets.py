"""Declarative plugin formations, resolved by the host's Legion template path."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PresetNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    type: str
    name: str
    x: float = 0
    y: float = 0
    parent_key: str | None = "group"
    owner_key: str | None = None
    equipment_relationship: str | None = None
    presentation: Literal["node", "preview", "inspector", "workspace"] = "node"
    config: dict[str, Any] = Field(default_factory=dict)
    initial_document: dict[str, Any] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class PresetEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str
    target: str
    relationship: str
    direction: Literal["forward", "bidirectional"] = "forward"


class LegionPresetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    revision: int = Field(default=1, ge=1)
    nodes: tuple[PresetNode, ...] = Field(min_length=2, max_length=101)
    edges: tuple[PresetEdge, ...] = ()
