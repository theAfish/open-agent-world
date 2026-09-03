"""Authoritative Run models, independent from Agent operational state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
)


class RunRecord(BaseModel):
    """Durable execution-attempt metadata.

    Agent is the actor, Task is a future work contract, Run is one attempt, and
    an external Job is future work performed during a Run. Their lifecycles are
    deliberately not stored on one another.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_id: str
    parent_run_id: str | None = None
    root_run_id: str
    task_id: str | None = None
    caller_kind: str
    caller_id: str | None = None
    context_id: str | None = None
    runtime_provider_id: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InvocationCaller:
    kind: str
    id: str | None = None


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """Immutable structural context passed across the runtime boundary.

    Provider SDK objects, HTTP objects, database connections, and the service
    container never enter this context. ``extensions`` is reserved for future
    state/group/delegation context descriptors, not those systems themselves.
    """

    run_id: str
    agent_id: str
    parent_run_id: str | None
    root_run_id: str
    caller: InvocationCaller
    context_id: str | None
    task_id: str | None
    runtime_provider_id: str
    state_context: Mapping[str, Any] | None = None
    group_context: Mapping[str, Any] | None = None
    delegation_context: Mapping[str, Any] | None = None
    extensions: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        for name in (
            "state_context",
            "group_context",
            "delegation_context",
            "extensions",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, MappingProxyType):
                object.__setattr__(self, name, MappingProxyType(dict(value)))


@dataclass(frozen=True, slots=True)
class RuntimeInput:
    prompt: str


@dataclass(frozen=True, slots=True)
class RunSuspension:
    """Process-local suspension decision, separate from durable Run status."""

    reason: str
    release_agent_slot: bool
