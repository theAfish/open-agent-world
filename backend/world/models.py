from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CardType(StrEnum):
    LEGION = "legion"
    AGENT = "agent"
    CONVERSATION = "conversation"
    TEXT = "text"
    IMAGE = "image"
    SANDBOX = "sandbox"


class Relationship(StrEnum):
    COMMUNICATE = "communicate"
    PARTICIPATE = "participate"
    READ = "read"
    READ_EDIT = "read_edit"
    VIEW = "view"
    EXECUTE = "execute"
    MOUNT_READ_ONLY = "mount_read_only"
    MOUNT_READ_WRITE = "mount_read_write"


class EdgeDirection(StrEnum):
    FORWARD = "forward"
    BIDIRECTIONAL = "bidirectional"


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"


class SandboxStatus(StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    RUNNING = "running"
    ERROR = "error"


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x: float = 0
    y: float = 0


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: Annotated[float, Field(gt=0, le=4096)] = 280
    height: Annotated[float, Field(gt=0, le=4096)] = 180


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    system_instruction: str = Field(default="You are a helpful agent in Open Agent World.", json_schema_extra={"agentReadable": True, "agentWritable": True})
    model: str = Field(default="gemini-3.7-flash", json_schema_extra={"agentReadable": True, "privileged": True})
    status: AgentStatus = AgentStatus.IDLE
    runtime_provider_id: str | None = None
    max_concurrent_runs: Annotated[int, Field(ge=1, le=64)] = 1
    inherit_legion_model: bool = True
    legion_role: str = Field(default="", max_length=200, json_schema_extra={"agentReadable": True, "agentWritable": True})


class LegionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available"] = "available"
    description: str = Field(default="", max_length=2000, json_schema_extra={"agentReadable": True, "agentWritable": True})
    instruction: str = Field(default="", max_length=16000, json_schema_extra={"agentReadable": True, "agentWritable": True})
    model_override: str = Field(default="", max_length=200)
    paused: bool = False
    shared_state_access: Literal["read_only", "read_write"] = "read_write"


class TextConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    filename: str = Field(default="untitled.txt", json_schema_extra={"agentReadable": True})


class ImageConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    filename: str = Field(default="image.png", json_schema_extra={"agentReadable": True})


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: SandboxStatus = SandboxStatus.STOPPED
    runtime: str = Field(default="auto", min_length=1, max_length=200)
    workspace_path: str | None = Field(default=None, max_length=4096)
    workspace_access: Literal["read_only", "read_write"] = "read_write"
    network_enabled: bool = False
    memory_bytes: int = Field(default=512 * 1024 * 1024, ge=16 * 1024 * 1024, le=8 * 1024 * 1024 * 1024)
    active_process_limit: int = Field(default=64, ge=1, le=256)
    command_timeout: float = Field(default=60, gt=0, le=600)
    presets: dict[str, str] = Field(default_factory=dict, max_length=30)

    @field_validator("presets")
    @classmethod
    def validate_presets(cls, value):
        if any(not k.strip() or len(k) > 80 or not v.strip() or len(v) > 32768 or "\0" in v for k, v in value.items()):
            raise ValueError("Presets require names up to 80 characters and commands up to 32 KiB")
        return value


    @field_validator("runtime", "workspace_path")
    @classmethod
    def validate_sandbox_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("sandbox settings must be non-empty and NUL-free")
        return value

    @model_validator(mode="after")
    def validate_workspace_access(self) -> "SandboxConfig":
        if self.workspace_path is None and self.workspace_access != "read_write":
            raise ValueError("read-only access requires a selected working folder")
        return self


class ConversationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str = Field(default="A shared field for durable human and agent conversations.", json_schema_extra={"agentReadable": True, "agentWritable": True})


ConfigValue = AgentConfig | ConversationConfig | TextConfig | ImageConfig | SandboxConfig | LegionConfig


class ResourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    filename: str
    media_type: str
    size_bytes: int
    revision: int
    width: int | None = None
    height: int | None = None
    preview: str | None = None


class EquipmentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: str = Field(min_length=1, max_length=100)
    relationship: str | None = None


class CardCreate(BaseModel):
    """Wire model shared with the canvas.

    `content` and `data_base64` are creation-only conveniences and are never
    stored in the card configuration JSON.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = None
    parent_id: str | None = Field(default=None, max_length=100)
    equipment: EquipmentBinding | None = None
    type: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    position: Point = Field(default_factory=Point)
    size: Size | None = None
    expanded: bool = False
    status: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    content: str | None = None
    data_base64: str | None = None
    media_type: str | None = None

class CardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1, strict=True)

    parent_id: str | None = Field(default=None, max_length=100)
    equipment: EquipmentBinding | None = None

    name: str | None = Field(default=None, min_length=1, max_length=200)
    position: Point | None = None
    size: Size | None = None
    expanded: bool | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class CardsDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: Annotated[list[str], Field(min_length=1, max_length=101)]
    expected_revisions: dict[str, Annotated[int, Field(ge=1, strict=True)]] | None = None

    @field_validator("node_ids")
    @classmethod
    def unique_node_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("node_ids must not contain duplicates")
        return value


class CardBatchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=100)
    patch: CardPatch


class CardsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: Annotated[list[CardBatchPatch], Field(min_length=1, max_length=101)]

    @field_validator("updates")
    @classmethod
    def unique_node_ids(cls, value: list[CardBatchPatch]) -> list[CardBatchPatch]:
        node_ids = [item.node_id for item in value]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("updates must not contain duplicate node_ids")
        return value


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    parent_id: str | None = None
    equipment: EquipmentBinding | None = None
    type: str
    name: str
    position: Point
    size: Size
    expanded: bool
    status: str
    config: dict[str, Any]
    chunk: tuple[int, int]
    resource: ResourceSummary | None = None
    created_at: datetime
    updated_at: datetime
    revision: int


class EdgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = None
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    relationship: str = Field(min_length=1, max_length=128)
    direction: EdgeDirection = EdgeDirection.FORWARD


class EdgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1, strict=True)

    relationship: str | None = Field(default=None, min_length=1, max_length=128)
    direction: EdgeDirection | None = None

    @model_validator(mode="after")
    def require_change(self) -> "EdgePatch":
        if self.relationship is None and self.direction is None:
            raise ValueError("at least one edge field must be provided")
        return self


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    relationship: str
    direction: EdgeDirection
    created_at: datetime
    updated_at: datetime
    revision: int


class WorldSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[Card]
    edges: list[Edge]
    chunks: list[tuple[int, int]]
    chunk_size: int
