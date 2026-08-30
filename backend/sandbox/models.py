"""Backend-neutral models for sandbox lifecycle and execution.

These types deliberately contain no Win32 handles or implementation details.  The
API and capability layers can therefore depend on them without learning how the
Windows security boundary is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class SandboxState(StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    RUNNING = "running"
    ERROR = "error"


class ResourceAccess(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class SandboxEventType(StrEnum):
    STATE_CHANGED = "sandbox_state_changed"
    COMMAND_STARTED = "sandbox_command_started"
    STDOUT = "sandbox_stdout"
    STDERR = "sandbox_stderr"
    COMMAND_FINISHED = "sandbox_command_finished"
    RESOURCE_ATTACHED = "sandbox_resource_attached"
    RESOURCE_DETACHED = "sandbox_resource_detached"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Limits applied to every command process tree by a Windows Job Object."""

    memory_bytes: int = 512 * 1024 * 1024
    active_process_limit: int = 16
    default_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.memory_bytes < 16 * 1024 * 1024:
            raise ValueError("memory_bytes must be at least 16 MiB")
        if self.active_process_limit < 1:
            raise ValueError("active_process_limit must be positive")
        if self.default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ResourceAttachment:
    sandbox_id: str
    resource_id: str
    source: Path
    relative_path: str
    access: ResourceAccess


@dataclass(frozen=True, slots=True)
class SandboxInfo:
    sandbox_id: str
    state: SandboxState
    workspace: Path
    attachments: tuple[ResourceAttachment, ...] = ()
    security_boundary: str = "windows-appcontainer"
    network_enabled: bool = False
    active_command: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    sandbox_id: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    sandbox_id: str
    type: SandboxEventType
    payload: Mapping[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SandboxError(RuntimeError):
    """Base class for sandbox lifecycle and execution failures."""


class SandboxNotFoundError(SandboxError):
    pass


class SandboxStateError(SandboxError):
    pass


class SandboxSecurityError(SandboxError):
    """A required native security primitive could not be established."""


class SandboxValidationError(SandboxError, ValueError):
    pass

