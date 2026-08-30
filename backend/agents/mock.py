"""Deterministic runtime available only by explicit development/test selection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ._state import AgentRecord, validate_agent_config
from .base import AgentCapabilityProvider, AgentRuntime
from .models import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentInfo,
    AgentNotFoundError,
    AgentStateError,
    AgentStatus,
)


class MockAgentRuntime(AgentRuntime):
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
        await self.stop(agent_id)
        async with self._records_lock:
            if self._records.get(agent_id) is record:
                del self._records[agent_id]

    async def run(self, agent_id: str, prompt: str) -> AsyncIterator[AgentEvent]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentStateError("prompt must not be empty")
        record = await self._record(agent_id)
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always supplies this
            raise AgentStateError("agent run requires an asyncio task")
        async with record.lock:
            if record.status == AgentStatus.RUNNING:
                raise AgentStateError(f"agent is already running: {agent_id}")
            record.run_counter += 1
            run_id = f"mock-{agent_id}-{record.run_counter}"
            record.status = AgentStatus.RUNNING
            record.active_run_id = run_id
            record.active_task = task
            record.last_error = None

        yield AgentEvent(agent_id, run_id, AgentEventType.STARTED, {"model": record.config.model})
        yield self._status_event(record, run_id)
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
            async with record.lock:
                record.status = AgentStatus.IDLE
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(agent_id, run_id, AgentEventType.COMPLETED, {"text": text})
            yield self._status_event(record, run_id)
        except asyncio.CancelledError:
            async with record.lock:
                record.status = AgentStatus.IDLE
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(agent_id, run_id, AgentEventType.STOPPED, {})
            yield self._status_event(record, run_id)
        except Exception as exc:
            async with record.lock:
                record.status = AgentStatus.ERROR
                record.last_error = str(exc)
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(
                agent_id, run_id, AgentEventType.ERROR, {"error": str(exc)}
            )
            yield self._status_event(record, run_id)
            raise

    async def stop(self, agent_id: str) -> None:
        record = await self._record(agent_id)
        async with record.lock:
            task = record.active_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.sleep(0)

    async def get_agent(self, agent_id: str) -> AgentInfo:
        return (await self._record(agent_id)).info()

    async def _record(self, agent_id: str) -> AgentRecord:
        async with self._records_lock:
            try:
                return self._records[agent_id]
            except KeyError as exc:
                raise AgentNotFoundError(f"agent not found: {agent_id}") from exc

    @staticmethod
    def _status_event(record: AgentRecord, run_id: str) -> AgentEvent:
        return AgentEvent(
            record.config.agent_id,
            run_id,
            AgentEventType.STATUS_CHANGED,
            {"status": record.status.value},
        )

