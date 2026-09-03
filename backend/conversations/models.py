from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=200)] = "New session"
    participant_ids: Annotated[list[str], Field(max_length=24)] = Field(default_factory=list)


class ConversationSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    conversation_name: str | None = None
    title: str
    participant_ids: list[str]
    created_at: datetime
    updated_at: datetime
    revision: int


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conversation_id: str
    session_id: str
    sender_kind: Literal["user", "agent", "system"]
    sender_id: str | None = None
    sender_name: str
    content: str
    mention_agent_ids: list[str]
    run_id: str | None = None
    created_at: datetime


class ConversationPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[str, Field(min_length=1, max_length=100_000)]
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
