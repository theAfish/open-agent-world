from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CardType(StrEnum):
    AGENT = "agent"
    TEXT = "text"
    IMAGE = "image"
    SANDBOX = "sandbox"


class Relationship(StrEnum):
    COMMUNICATE = "communicate"
    READ = "read"
    READ_EDIT = "read_edit"
    VIEW = "view"
    EXECUTE = "execute"
    MOUNT_READ_ONLY = "mount_read_only"
    MOUNT_READ_WRITE = "mount_read_write"


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
    model_config = ConfigDict(extra="forbid")

    x: float = 0
    y: float = 0


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: Annotated[float, Field(gt=0, le=4096)] = 280
    height: Annotated[float, Field(gt=0, le=4096)] = 180


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    system_instruction: str = "You are a helpful agent in Open Agent World."
    model: str = "gemini-3.7-flash"
    status: AgentStatus = AgentStatus.IDLE


class TextConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    filename: str = "untitled.txt"


class ImageConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    filename: str = "image.png"


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: SandboxStatus = SandboxStatus.STOPPED


ConfigValue = AgentConfig | TextConfig | ImageConfig | SandboxConfig


class ResourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CardType
    filename: str
    media_type: str
    size_bytes: int
    revision: int
    width: int | None = None
    height: int | None = None
    preview: str | None = None


class CardCreate(BaseModel):
    """Wire model shared with the canvas.

    `content` and `data_base64` are creation-only conveniences and are never
    stored in the card configuration JSON.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str | None = None
    type: CardType
    name: str | None = Field(default=None, min_length=1, max_length=200)
    position: Point = Field(default_factory=Point)
    size: Size | None = None
    expanded: bool = False
    status: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    content: str | None = None
    data_base64: str | None = None
    media_type: str | None = None

    @model_validator(mode="after")
    def validate_creation_payload(self) -> "CardCreate":
        if self.content is not None and self.type is not CardType.TEXT:
            raise ValueError("content is only valid for text cards")
        if self.data_base64 is not None and self.type is not CardType.IMAGE:
            raise ValueError("data_base64 is only valid for image cards")
        allowed_statuses = {
            CardType.AGENT: {item.value for item in AgentStatus},
            CardType.SANDBOX: {item.value for item in SandboxStatus},
            CardType.TEXT: {"available", "modified"},
            CardType.IMAGE: {"available", "modified"},
        }
        if self.status is not None and self.status not in allowed_statuses[self.type]:
            raise ValueError(f"status {self.status!r} is not valid for {self.type.value}")
        return self


class CardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    position: Point | None = None
    size: Size | None = None
    expanded: bool | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: CardType
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
    relationship: Relationship


class EdgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship: Relationship


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    relationship: Relationship
    created_at: datetime
    updated_at: datetime
    revision: int


class WorldSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[Card]
    edges: list[Edge]
    chunks: list[tuple[int, int]]
    chunk_size: int
