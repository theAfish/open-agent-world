"""Agent reasoning boundary and live capability provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .models import AgentConfig, AgentEvent, AgentInfo, RuntimeModelConnection, ScopedToolDefinition
from backend.file_transfer import MAX_FILE_BYTES, INLINE_FILE_BYTES

if TYPE_CHECKING:
    from backend.runs.models import InvocationContext, RuntimeInput
    from backend.agents.execution import RuntimeExecution


class AgentCapabilityProvider(Protocol):
    """Bridge to the authoritative capability broker.

    ``invoke_tool`` is intentionally separate from ``list_tools``.  The broker
    must resolve authorization again in ``invoke_tool`` so an edge removed
    after the model saw a tool cannot still be exercised.
    """

    async def read_own_document(self, agent_id: str) -> dict[str, Any]: ...

    def resolve_model(self, model: str) -> RuntimeModelConnection: ...

    def runtime_state_directory(self, agent_id: str) -> Path: ...

    def managed_environment_root(self) -> Path:
        """Profile data root for host-managed, rebuildable Pack environments."""
        ...

    def subprocess_environment(self) -> dict[str, str]:
        """Fresh host-approved environment for trusted runtime subprocesses.

        Plugins may narrow it and add their own explicit worker settings.
        Hosted workers omit deployment credentials; this is not OS isolation.
        """
        ...

    def runtime_execution(self, agent_id: str, run_id: str, *, workspace: Path,
                          home: Path) -> AbstractAsyncContextManager[RuntimeExecution]:
        """Open this live Run's host-selected process/file/bridge environment.

        workspace/home preserve personal native paths; remote providers choose
        their own private roots. No provider may silently fall back to native.
        """
        ...

    def runtime_execution_configuration(self, agent_id: str) -> dict[str, Any]:
        """Read host-selected runtime availability without starting anything."""
        ...

    async def publish_execution(self, agent_id: str, run_id: str, snapshot: Mapping[str, Any]) -> None:
        """Publish a bounded ExecutionSnapshot for this runtime's current Run."""
        ...

    async def list_tools(self, agent_id: str) -> Sequence[ScopedToolDefinition]: ...

    async def pending_conversation_input(self, agent_id: str, run_id: str) -> list[dict[str, Any]]:
        """Read queued input for this Run's session, rechecking live access."""
        ...

    async def acknowledge_conversation_input(self, agent_id: str, run_id: str, message_ids: list[str]) -> None:
        """Claim input only after the runtime has included/accepted it."""
        ...

    async def conversation_attachments(self, agent_id: str, run_id: str) -> list[dict[str, Any]]:
        """List sent attachments visible to this Run's current conversation history.

        Returns references and metadata, never file bytes or host paths. A Run
        outside a conversation returns an empty list.
        """
        ...

    async def read_conversation_attachment(
        self, agent_id: str, run_id: str, version_id: str, path: str,
        *, max_bytes: int = INLINE_FILE_BYTES,
    ) -> bytes:
        """Read verified, bounded bytes from a listed current-session attachment."""
        ...

    def stream_conversation_attachment(
        self, agent_id: str, run_id: str, version_id: str, path: str,
        *, max_bytes: int = MAX_FILE_BYTES,
    ) -> AsyncIterator[bytes]:
        """Stream a visible attachment, checking live access and its final checksum.

        Consumers must exhaust the iterator before publishing the destination,
        and close it when interrupted. Bytes stay outside the model prompt.
        """
        ...

    async def publish_conversation_attachment(
        self, agent_id: str, run_id: str, name: str, data: bytes, *, request_key: str,
    ) -> dict[str, Any]:
        """Retain runtime-produced bytes and return an attachment reference.

        Repeating the same key and contents is idempotent. Publish does not send
        a message; invoke the authorized send_conversation_message tool with the
        returned version_id/path to deliver the file.
        """
        ...

    async def publish_conversation_attachment_stream(
        self, agent_id: str, run_id: str, name: str, chunks: AsyncIterator[bytes], *,
        request_key: str, expected_sha256: str | None = None, max_bytes: int = MAX_FILE_BYTES,
    ) -> dict[str, Any]:
        """Retain a verified file stream and return its reference, never its bytes."""
        ...

    async def invoke_tool(
        self,
        agent_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any: ...


class ModelConnectionResolver(Protocol):
    """Resolve a managed OAW model reference for a trusted runtime provider."""

    def resolve_runtime(self, model_reference: str) -> RuntimeModelConnection: ...


class RuntimeProvider(ABC):
    """Provider-neutral runtime boundary; provider SDK objects never cross it."""
    # Compatibility declaration by trusted adapters. All agent-generated command
    # execution must use the host-selected SDK or authorized Sandbox tools.
    supports_managed_execution: bool = False

    async def notify_input(self, run_id: str) -> None:
        """Wake an active provider after durable input arrives; no receipt implied.

        Pull-based model callbacks need no wakeup. Event-driven runtimes may
        override this and use the capability provider's input/acknowledgement API.
        """

    @abstractmethod
    async def create_agent(self, config: AgentConfig) -> AgentInfo:
        pass

    @abstractmethod
    async def update_agent(self, config: AgentConfig) -> AgentInfo:
        pass

    @abstractmethod
    async def delete_agent(self, agent_id: str) -> None:
        pass

    @abstractmethod
    def execute(
        self,
        config: AgentConfig,
        context: InvocationContext,
        runtime_input: RuntimeInput,
    ) -> AsyncIterator[AgentEvent]:
        """Emit result events; release execution resources when the stream closes.

        A terminal event records an outcome, not reusable Agent capacity. The
        host joins stream closure before admitting a replacement. Cleanup must
        be bounded, and ``stop`` must be idempotent and able to retry failed
        cleanup. Explicit suspension retains its declared capacity policy.
        """
        pass

    @abstractmethod
    async def stop(self, run_id: str) -> None:
        pass

    @abstractmethod
    async def get_agent(self, agent_id: str) -> AgentInfo:
        pass
