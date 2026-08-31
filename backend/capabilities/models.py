from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.world.models import CardType


class CapabilityKind(StrEnum):
    AGENT_COMMUNICATE = "agent.communicate"
    TEXT_READ = "text.read"
    TEXT_EDIT = "text.edit"
    IMAGE_VIEW = "image.view"
    SANDBOX_EXECUTE = "sandbox.execute"


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    kind: CapabilityKind
    agent_id: str
    target_id: str
    target_type: CardType
    target_name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class CapabilitySet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    capabilities: list[Capability]
