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
    SandboxValidationError,
)
from .materialization import RuntimeMount


SandboxEventSink: TypeAlias = Callable[[SandboxEvent], Awaitable[None] | None]


class SandboxBackend(ABC):
    """Generic execution-environment contract.

    Implementations must provide an OS security boundary.  A backend is not
    permitted to implement any operation by launching an ordinary host process.
    """

    supports_invocation_environment: bool = False
    supports_execution_policy: bool = False

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
        invocation_env: Mapping[str, str] | None = None,
        runtime_mount: RuntimeMount | None = None,
        execution_policy: Mapping[str, object] | None = None,
    ) -> CommandResult:
        """Run argv with an optional command-scoped, read-only runtime bundle.

        Replace its argument_index with the materialized runtime file path.
        Cached bundles must remain inaccessible to commands without a mount.
        invocation_env contains explicitly authorized application variables and
        the reserved target JSON carrier. Validate it with the shared policy and
        apply it only to the isolated command, never to host helpers. Unsupported
        configuration must raise SandboxValidationError, never be ignored.
        execution_policy is host-owned configuration captured at admission;
        backends advertise support before it is passed. It is never an Agent
        tool parameter and never changes process-global defaults.
        """

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

    async def configure(
        self, sandbox_id: str, *, workspace_path: str | None,
        workspace_access: ResourceAccess,
    ) -> SandboxInfo:
        """Bind an existing host directory while stopped, without owning its data."""
        if workspace_path is not None or workspace_access != ResourceAccess.READ_WRITE:
            raise SandboxValidationError("this runtime does not support workspace configuration")
        return await self.get(sandbox_id)

    async def bundle_status(self, sandbox_id, bundle):
        return {"cached": False, "current": False}

    async def reset_cache(self, sandbox_id):
        raise SandboxValidationError("This runtime does not support cache reset")

    async def cancel(self, sandbox_id):
        raise SandboxValidationError("This runtime cannot cancel without stopping")

    async def file_operation(self, sandbox_id, operation, **options):
        raise SandboxValidationError("This runtime does not support scoped file operations")

    async def events(self, sandbox_id: str) -> AsyncIterator[SandboxEvent]:
        """Optional pull-style event stream.

        The Windows implementation uses the constructor event sink so the
        application can forward events directly to its central WebSocket bus.
        """

        if False:  # pragma: no cover - makes this an async generator by design
            yield cast(SandboxEvent, None)
        raise NotImplementedError("this backend publishes through its event sink")
