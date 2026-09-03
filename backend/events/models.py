from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    CONNECTION_READY = "connection_ready"
    CARD_CREATED = "card_created"
    CARD_UPDATED = "card_updated"
    CARD_DELETED = "card_deleted"
    EDGE_CREATED = "edge_created"
    EDGE_UPDATED = "edge_updated"
    EDGE_DELETED = "edge_deleted"
    PERMISSION_CHANGED = "permission_changed"
    RESOURCE_MODIFIED = "resource_modified"
    CONVERSATION_SESSION_CREATED = "conversation_session_created"
    CONVERSATION_SESSION_UPDATED = "conversation_session_updated"
    CONVERSATION_SESSION_DELETED = "conversation_session_deleted"
    CONVERSATION_MESSAGE = "conversation_message"
    AGENT_STARTED = "agent_started"
    AGENT_STATUS_CHANGED = "agent_status_changed"
    AGENT_MESSAGE = "agent_message"
    AGENT_COMPLETED = "agent_completed"
    AGENT_STOPPED = "agent_stopped"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    SANDBOX_COMMAND_STARTED = "sandbox_command_started"
    SANDBOX_STATE_CHANGED = "sandbox_state_changed"
    SANDBOX_RESOURCE_ATTACHED = "sandbox_resource_attached"
    SANDBOX_RESOURCE_DETACHED = "sandbox_resource_detached"
    STDOUT = "stdout"
    STDERR = "stderr"
    COMMAND_FINISHED = "command_finished"
    RUNTIME_ERROR = "runtime_error"


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    node_id: str | None = None
    agent_id: str | None = None
    sandbox_id: str | None = None
    resource_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
