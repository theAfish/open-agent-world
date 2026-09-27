from __future__ import annotations

from datetime import datetime
from uuid import UUID
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from backend.agents.context import ContextStatus


class ConversationSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=200)] = "New session"
    group_id: str | None = None
    group_title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    participant_ids: Annotated[list[str], Field(max_length=24)] = Field(default_factory=list)


class ConversationParticipantsAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_ids: Annotated[list[str], Field(min_length=1, max_length=24)]


class ConversationSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    conversation_name: str | None = None
    title: str
    group_id: str
    group_title: str
    auto_title: bool = False
    is_default: bool = False
    participant_ids: list[str]
    created_at: datetime
    updated_at: datetime
    revision: int


class ConversationAttachmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str
    path: str


class ConversationAttachment(ConversationAttachmentRef):
    name: str
    size_bytes: int
    media_type: str


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    session_id: str
    sender_kind: Literal["user", "agent", "system"]
    sender_id: str | None = None
    sender_name: str
    content: str
    attachments: list[ConversationAttachment] = Field(default_factory=list)
    mention_agent_ids: list[str]
    sequence: int = 0
    kind: str = "text"
    is_final: bool = True
    run_id: str | None = None
    created_at: datetime


class ConversationPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID | None = None

    content: Annotated[str, Field(max_length=100_000)] = ""
    attachments: list[ConversationAttachmentRef] = Field(default_factory=list, max_length=20)
    mention_agent_ids: Annotated[list[str], Field(max_length=8)] = Field(default_factory=list)


class ConversationPostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: ConversationMessage
    accepted_agent_ids: list[str]


class ConversationAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    status: str
    model: str
    connected: bool


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    sessions: list[ConversationSession]
    agents: list[ConversationAgent]
    context_statuses: dict[str, dict[str, ContextStatus]] = Field(default_factory=dict)


class ConversationSessionRename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, Field(min_length=1, max_length=200)]


class ConversationRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    awaiting: str | None = None
    progress: str | None = None
    tool_count: int = 0
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)


class ConversationDeliveryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    agent_id: str
    status: Literal["queued", "claimed"]
    claimed_run_id: str | None = None


class ConversationMessagePage(BaseModel):
    active_agent_ids: list[str] = Field(default_factory=list)
    active_runs: list[ConversationRunSummary] = Field(default_factory=list)
    deliveries: list[ConversationDeliveryState] = Field(default_factory=list)
    run_summaries: dict[str, ConversationRunSummary] = Field(default_factory=dict)
    items: list[ConversationMessage]
    has_before: bool
    has_after: bool
