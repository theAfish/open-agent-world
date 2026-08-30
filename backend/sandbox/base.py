"""The sandbox backend boundary used by the rest of the application."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeAlias, cast

from .models import (
    CommandResult,
    ResourceAccess,
    ResourceAttachment,
    SandboxEvent,
    SandboxInfo,
)


SandboxEventSink: TypeAlias = Callable[[SandboxEvent], Awaitable[None] | None]


class SandboxBackend(ABC):
    """Generic execution-environment contract.

    Implementations must provide an OS security boundary.  A backend is not
    permitted to implement any operation by launching an ordinary host process.
    """

    @abstractmethod
    async def create(self, sandbox_id: str) -> SandboxInfo:
        """Create managed storage and its security identity."""

    @abstractmethod
    async def start(self, sandbox_id: str) -> SandboxInfo:
        """Validate and prepare an existing sandbox for commands."""

    @abstractmethod
    async def execute(
        self,
        sandbox_id: str,
        argv: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run one argv vector inside the sandbox security boundary."""

    @abstractmethod
    async def terminate(self, sandbox_id: str) -> None:
        """Terminate the complete active process tree, if any."""

    @abstractmethod
    async def attach_resource(
        self,
        sandbox_id: str,
        resource_id: str,
        source: Path,
        relative_path: str,
        access: ResourceAccess,
    ) -> ResourceAttachment:
        """Make one managed resource available with the requested access."""

    @abstractmethod
    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        """Revoke and remove one resource attachment."""

    @abstractmethod
    async def destroy(self, sandbox_id: str) -> None:
        """Terminate, revoke, and remove a sandbox and its native identity."""

    @abstractmethod
    async def get(self, sandbox_id: str) -> SandboxInfo:
        """Return current backend state."""

    async def events(self, sandbox_id: str) -> AsyncIterator[SandboxEvent]:
        """Optional pull-style event stream.

        The Windows implementation uses the constructor event sink so the
        application can forward events directly to its central WebSocket bus.
        """

        if False:  # pragma: no cover - makes this an async generator by design
            yield cast(SandboxEvent, None)
        raise NotImplementedError("this backend publishes through its event sink")
