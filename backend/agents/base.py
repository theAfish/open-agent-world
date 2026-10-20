"""Agent reasoning boundary and live capability provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .models import AgentConfig, AgentEvent, AgentInfo, RuntimeModelConnection, ScopedToolDefinition

if TYPE_CHECKING:
    from backend.runs.models import InvocationContext, RuntimeInput


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
