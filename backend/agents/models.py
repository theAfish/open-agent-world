"""Provider-neutral agent runtime and capability models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class AgentStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"


class AgentEventType(StrEnum):
    STARTED = "agent_started"
    STATUS_CHANGED = "agent_status_changed"
    MESSAGE = "agent_message"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    COMPLETED = "agent_completed"
    STOPPED = "agent_stopped"
    ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class AgentConfig:
    agent_id: str
    name: str
    system_instruction: str = "You are a helpful agent in Open Agent World."
    model: str = "gemini-3.7-flash"
    runtime_provider_id: str | None = None
    max_concurrent_runs: int = 1
    provider_config: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not isinstance(self.provider_config, MappingProxyType):
            object.__setattr__(
                self, "provider_config", MappingProxyType(dict(self.provider_config))
            )


@dataclass(frozen=True, slots=True)
class AgentInfo:
    config: AgentConfig
    status: AgentStatus
    session_id: str
    active_run_id: str | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentEvent:
    agent_id: str
    run_id: str
    type: AgentEventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_status: str | None = None


@dataclass(frozen=True, slots=True)
class ToolParameter:
    """A parameter exposed in the generated ADK function signature."""

    name: str
    python_type: type[Any] = str
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass(frozen=True, slots=True)
class ScopedToolDefinition:
    """One concrete graph-derived capability, never a global resource tool."""

    capability_id: str
    name: str
    description: str
    parameters: tuple[ToolParameter, ...] = ()


class AgentRuntimeError(RuntimeError):
    pass


class AgentNotFoundError(AgentRuntimeError):
    pass


class AgentStateError(AgentRuntimeError):
    pass


class AgentConfigurationError(AgentRuntimeError, ValueError):
    pass


class AgentDependencyError(AgentRuntimeError):
    pass
