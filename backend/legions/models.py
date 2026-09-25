from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.world.models import Card, Edge, EdgeDirection, Point, Size


class LegionBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: Annotated[float, Field(gt=0)]
    height: Annotated[float, Field(gt=0)]


class LegionNodePresentation(BaseModel):
    """Portable editor state, separate from an Agent's execution status."""

    model_config = ConfigDict(extra="forbid")
    level: Literal["node", "preview", "inspector", "workspace"]
    base_level: Literal["node", "preview"] | None = None
    workspace_size: Size | None = None
    surface_sizes: dict[Literal["node", "preview", "inspector", "workspace"], Size] = Field(default_factory=dict)


class LegionCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    node_ids: Annotated[list[str], Field(min_length=2, max_length=101)]
    presentation: dict[str, LegionNodePresentation] = Field(default_factory=dict, max_length=1000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("node_ids")
    @classmethod
    def unique_node_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("node_ids must not contain duplicates")
        return value


class LegionInstantiate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Point = Field(default_factory=Point)
    as_group: bool = False
    unwrap: bool = False

    @model_validator(mode="after")
    def validate_deployment_mode(self) -> "LegionInstantiate":
        if self.as_group and self.unwrap:
            raise ValueError("Choose either grouped or unwrapped deployment")
        return self


class LegionTemplateDependency(BaseModel):
    """Resolved owner of one plugin contribution required by a node template."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "node_type",
        "relationship",
        "capability_handler",
        "runtime_provider",
        "state_schema",
    ]
    id: str = Field(min_length=1, max_length=128)
    plugin_id: str = Field(min_length=1, max_length=128)


class LegionTemplateNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    parent_key: str | None = None
    owner_key: str | None = None
    equipment_relationship: str | None = None
    state_scope: Literal["shared", "session"] | None = None
    initial_document: dict[str, Any] | None = None
    initial_shared_state: dict[str, Any] | None = None
    type: str
    plugin_id: str
    name: str
    position: Point
    size: Size
    expanded: bool
    status: str
    config: dict[str, Any]
    presentation: LegionNodePresentation | None = None
    dependencies: list[LegionTemplateDependency] = Field(default_factory=list)
    payload_version: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_payload_version_pair(self) -> "LegionTemplateNode":
        if self.type == "legion":
            self.config = {"workspace_layout": None, **self.config}
        if self.type == "legion" and "mode" not in self.config:
            self.config = {**self.config, "mode": "team"}
        if self.initial_shared_state is not None:
            if self.type != "legion":
                raise ValueError("Only Legion containers may define initial shared state")
            if len(json.dumps(self.initial_shared_state).encode("utf-8")) > 64 * 1024:
                raise ValueError("Legion shared state is limited to 64 KiB")
        if (self.payload is None) != (self.payload_version is None):
            raise ValueError("payload and payload_version must be provided together")
        return self


class LegionTemplateEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    source: str
    target: str
    relationship: str
    plugin_id: str
    direction: EdgeDirection


class LegionBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: int = 1
    bounds: LegionBounds
    nodes: list[LegionTemplateNode]
    edges: list[LegionTemplateEdge]

    @model_validator(mode="after")
    def validate_memberships(self) -> "LegionBlueprint":
        nodes = {node.key: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Template node keys must be unique")
        for node in self.nodes:
            if node.parent_key and node.owner_key:
                raise ValueError("A node cannot be both a member and equipment")
            if (node.owner_key or node.parent_key) is not None:
                parent = nodes.get(node.owner_key or node.parent_key)
                if parent is None or parent.key == node.key:
                    raise ValueError("Template parents must reference another container")
                visited = {node.key}
                while parent:
                    if parent.key in visited:
                        raise ValueError("Template memberships cannot form a cycle")
                    visited.add(parent.key)
                    parent = nodes.get(parent.owner_key or parent.parent_key)
        return self


class LegionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    blueprint: LegionBlueprint
    created_at: datetime
    updated_at: datetime
    revision: int


class LegionMemberSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str


class LegionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: bool = False
    starter: bool = False
    id: str
    name: str
    description: str
    node_count: int
    edge_count: int
    bounds: LegionBounds
    node_types: list[str]
    members: list[LegionMemberSummary] = Field(default_factory=list)
    required_card_ids: list[str] = Field(default_factory=list)
    plugin_ids: list[str]
    compatible: bool
    issues: list[str]
    created_at: datetime
    updated_at: datetime
    revision: int


class LegionInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legion_id: str
    node_ids: dict[str, str] = Field(default_factory=dict)
    nodes: list[Card]
    edges: list[Edge]
    presentation: dict[str, LegionNodePresentation] = Field(default_factory=dict)
