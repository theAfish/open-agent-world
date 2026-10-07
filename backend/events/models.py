from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    CARD_LIBRARY_UPDATED = "card_library_updated"
    MINISTER_REVIEW = "minister_review"
    CONNECTION_READY = "connection_ready"
    CARD_CREATED = "card_created"
    NODES_GENERATED = "nodes_generated"
    CARD_UPDATED = "card_updated"
    CARD_DELETED = "card_deleted"
    EDGE_CREATED = "edge_created"
    EDGE_UPDATED = "edge_updated"
    EDGE_DELETED = "edge_deleted"
    PERMISSION_CHANGED = "permission_changed"
    RESOURCE_MODIFIED = "resource_modified"
    ARTIFACT_UPDATED = "artifact_updated"
    CONVERSATION_SESSION_CREATED = "conversation_session_created"
    CONVERSATION_SESSION_UPDATED = "conversation_session_updated"
    CONVERSATION_SESSION_DELETED = "conversation_session_deleted"
    CONVERSATION_MESSAGE = "conversation_message"
    CONTEXT_STATUS = "context_status"
    AGENT_STARTED = "agent_started"
    AGENT_STATUS_CHANGED = "agent_status_changed"
    AGENT_ACTIVITY = "agent_activity"
    AGENT_MESSAGE = "agent_message"
    AGENT_PROGRESS = "agent_progress"
    AGENT_COMPLETED = "agent_completed"
    AGENT_STOPPED = "agent_stopped"
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_WAITING = "run_waiting"
    RUN_RESUMED = "run_resumed"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    RUN_INTERRUPTED = "run_interrupted"
    STATE_CREATED = "state_created"
    STATE_UPDATED = "state_updated"
    STATE_DELETED = "state_deleted"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    CAPABILITY_STARTED = "capability_started"
    CAPABILITY_SUCCEEDED = "capability_succeeded"
    CAPABILITY_FAILED = "capability_failed"
    CAPABILITY_CANCELLED = "capability_cancelled"
    CAPABILITY_TIMED_OUT = "capability_timed_out"
    OPERATION_STARTED = "operation_started"
    OPERATION_SUCCEEDED = "operation_succeeded"
    OPERATION_FAILED = "operation_failed"
    OPERATION_CANCELLED = "operation_cancelled"
    OPERATION_TIMED_OUT = "operation_timed_out"
    WORK_STARTED = "work_started"
    WORK_SUCCEEDED = "work_succeeded"
    WORK_FAILED = "work_failed"
    WORK_CANCELLED = "work_cancelled"
    WORK_INTERRUPTED = "work_interrupted"
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
    stream_id: str | None = None
    sequence: int | None = None
    type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    node_id: str | None = None
    agent_id: str | None = None
    sandbox_id: str | None = None
    resource_id: str | None = None
    conversation_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
