"""Deterministic runtime available only by explicit development/test selection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ._state import AgentRecord, validate_agent_config
from .base import AgentCapabilityProvider, RuntimeProvider
from .models import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentInfo,
    AgentNotFoundError,
    AgentStateError,
    AgentStatus,
)
from backend.runs.models import InvocationContext, RunStatus, RuntimeInput


class MockAgentRuntime(RuntimeProvider):
    """Predictable non-model runtime for tests and local UI development.

    It never substitutes for a Google ADK initialization or execution failure.
    The application has to choose ``mock`` before constructing this class.
    """

    def __init__(self, capability_provider: AgentCapabilityProvider) -> None:
        self._provider = capability_provider
        self._records: dict[str, AgentRecord] = {}
        self._records_lock = asyncio.Lock()

    async def create_agent(self, config: AgentConfig) -> AgentInfo:
        validate_agent_config(config)
        async with self._records_lock:
            if config.agent_id in self._records:
                raise AgentStateError(f"agent already exists: {config.agent_id}")
            record = AgentRecord(
                config=config, session_id=f"mock-session-{config.agent_id}"
            )
            self._records[config.agent_id] = record
            return record.info()

    async def update_agent(self, config: AgentConfig) -> AgentInfo:
        validate_agent_config(config)
        record = await self._record(config.agent_id)
        async with record.lock:
            if record.status == AgentStatus.RUNNING:
                raise AgentStateError("cannot update a running agent")
            record.config = config
            record.last_error = None
            return record.info()

    async def delete_agent(self, agent_id: str) -> None:
        record = await self._record(agent_id)
        async with self._records_lock:
            if self._records.get(agent_id) is record:
                del self._records[agent_id]

    async def execute(
        self,
        config: AgentConfig,
        context: InvocationContext,
        runtime_input: RuntimeInput,
    ) -> AsyncIterator[AgentEvent]:
        agent_id = context.agent_id
        prompt = runtime_input.prompt
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentStateError("prompt must not be empty")
        record = await self._record(agent_id)
        async with record.lock:
            record.config = config
            record.last_error = None
        run_id = context.run_id
        try:
            definitions = tuple(await self._provider.list_tools(agent_id))
            # Stable response makes UI and backend lifecycle tests reproducible.
            text = f"Mock response: {prompt.strip()}"
            yield AgentEvent(
                agent_id,
                run_id,
                AgentEventType.MESSAGE,
                {
                    "text": text,
                    "final": True,
                    "available_tools": [item.name for item in definitions],
                },
            )
            yield AgentEvent(
                agent_id,
                run_id,
                AgentEventType.COMPLETED,
                {"text": text},
                run_status=RunStatus.SUCCEEDED,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise

    async def stop(self, run_id: str) -> None:
        del run_id

    async def get_agent(self, agent_id: str) -> AgentInfo:
        return (await self._record(agent_id)).info()

    async def _record(self, agent_id: str) -> AgentRecord:
        async with self._records_lock:
            try:
                return self._records[agent_id]
            except KeyError as exc:
                raise AgentNotFoundError(f"agent not found: {agent_id}") from exc

