from __future__ import annotations

from datetime import datetime
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.world.models import Card, Edge, EdgeDirection, Point, Size


class LegionBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: Annotated[float, Field(gt=0)]
    height: Annotated[float, Field(gt=0)]


class LegionCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    node_ids: Annotated[list[str], Field(min_length=2, max_length=100)]

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
    type: str
    plugin_id: str
    name: str
    position: Point
    size: Size
    expanded: bool
    status: str
    config: dict[str, Any]
    dependencies: list[LegionTemplateDependency] = Field(default_factory=list)
    payload_version: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_payload_version_pair(self) -> "LegionTemplateNode":
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


class LegionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    blueprint: LegionBlueprint
    created_at: datetime
    updated_at: datetime
    revision: int


class LegionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    node_count: int
    edge_count: int
    bounds: LegionBounds
    node_types: list[str]
    plugin_ids: list[str]
    compatible: bool
    issues: list[str]
    created_at: datetime
    updated_at: datetime
    revision: int


class LegionInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legion_id: str
    nodes: list[Card]
    edges: list[Edge]
