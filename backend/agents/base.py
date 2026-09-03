"""Agent reasoning boundary and live capability provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol

from .models import AgentConfig, AgentEvent, AgentInfo, ScopedToolDefinition


class AgentCapabilityProvider(Protocol):
    """Bridge to the authoritative capability broker.

    ``invoke_tool`` is intentionally separate from ``list_tools``.  The broker
    must resolve authorization again in ``invoke_tool`` so an edge removed
    after the model saw a tool cannot still be exercised.
    """

    async def list_tools(self, agent_id: str) -> Sequence[ScopedToolDefinition]: ...

    async def invoke_tool(
        self,
        agent_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any: ...


class AgentRuntime(ABC):
    """Internal runtime boundary; provider SDK objects never cross it."""

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
    def run(
        self, agent_id: str, prompt: str, *, context_id: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        pass

    @abstractmethod
    async def stop(self, agent_id: str) -> None:
        pass

    @abstractmethod
    async def get_agent(self, agent_id: str) -> AgentInfo:
        pass
