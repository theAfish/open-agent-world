from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentInfo,
    AgentStatus,
    RuntimeProvider,
)
from backend.config import Settings
from backend.errors import RuntimeUnavailableError
from backend.plugins import create_builtin_registry
from backend.runs import InvocationContext, RunStatus, RuntimeInput
from backend.services import create_services
from backend.world.models import CardCreate


class RecordingProvider(RuntimeProvider):
    def __init__(self, *, mode: str = "success") -> None:
        self.mode = mode
        self.configs: dict[str, AgentConfig] = {}
        self.contexts: list[InvocationContext] = []
        self.started = asyncio.Event()

    async def create_agent(self, config: AgentConfig) -> AgentInfo:
        self.configs[config.agent_id] = config
        return await self.get_agent(config.agent_id)

    async def update_agent(self, config: AgentConfig) -> AgentInfo:
        self.configs[config.agent_id] = config
        return await self.get_agent(config.agent_id)

    async def delete_agent(self, agent_id: str) -> None:
        self.configs.pop(agent_id, None)

    async def execute(
        self,
        config: AgentConfig,
        context: InvocationContext,
        runtime_input: RuntimeInput,
    ) -> AsyncIterator[AgentEvent]:
        del config
        self.contexts.append(context)
        self.started.set()
        if self.mode == "block":
            await asyncio.Event().wait()
        if self.mode == "failure":
            raise RuntimeError("provider exploded")
        yield AgentEvent(
            context.agent_id,
            context.run_id,
            AgentEventType.MESSAGE,
            {"text": runtime_input.prompt},
        )
        if self.mode == "success":
            yield AgentEvent(
                context.agent_id,
                context.run_id,
                AgentEventType.COMPLETED,
                {"text": runtime_input.prompt},
                run_status=RunStatus.SUCCEEDED,
            )

    async def stop(self, run_id: str) -> None:
        del run_id

    async def get_agent(self, agent_id: str) -> AgentInfo:
        return AgentInfo(
            config=self.configs[agent_id],
            status=AgentStatus.IDLE,
            session_id=f"test-{agent_id}",
        )


def _services(tmp_path: Path, provider: RecordingProvider):
    registry = create_builtin_registry()
    registry.register_runtime_provider("test.runtime", lambda capabilities: provider)
    settings = replace(
        Settings.for_data_root(tmp_path / "managed"),
        agent_runtime="test.runtime",
    )
    return create_services(settings, plugins=registry)


@pytest.mark.asyncio
async def test_root_and_child_run_lineage_and_registry_provider(tmp_path: Path) -> None:
    provider = RecordingProvider()
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        root = await manager.start_run(agent.id, "root", task_id="task-a")
        await manager.wait_run(root.run_id)
        child = await manager.start_run(
            agent.id,
            "child",
            caller_kind="agent",
            caller_id=agent.id,
            parent_run_id=root.run_id,
            task_id="task-a",
        )
        await manager.wait_run(child.run_id)

        assert root.parent_run_id is None
        assert root.root_run_id == root.run_id
        assert child.parent_run_id == root.run_id
        assert child.root_run_id == root.run_id
        assert child.runtime_provider_id == "test.runtime"
        assert manager.list_child_runs(root.run_id) == [manager.get_run(child.run_id)]
        assert [item.run_id for item in manager.list_runs(agent_id=agent.id)] == [
            root.run_id,
            child.run_id,
        ]
        assert provider.contexts[-1].root_run_id == root.run_id
    finally:
        services.close()


@pytest.mark.asyncio
async def test_agents_in_one_world_can_select_different_providers(tmp_path: Path) -> None:
    first = RecordingProvider()
    second = RecordingProvider()
    registry = create_builtin_registry()
    registry.register_runtime_provider("test.first", lambda capabilities: first)
    registry.register_runtime_provider("test.second", lambda capabilities: second)
    services = create_services(
        Settings.for_data_root(tmp_path / "managed"), plugins=registry
    )
    try:
        one = await services.create_card(CardCreate(
            type="agent", name="One", config={"runtime_provider_id": "test.first"}
        ))
        two = await services.create_card(CardCreate(
            type="agent", name="Two", config={"runtime_provider_id": "test.second"}
        ))
        manager = services._require_run_manager()
        run_one = await manager.start_run(one.id, "one")
        run_two = await manager.start_run(two.id, "two")
        await manager.wait_run(run_one.run_id)
        await manager.wait_run(run_two.run_id)
        assert [item.agent_id for item in first.contexts] == [one.id]
        assert [item.agent_id for item in second.contexts] == [two.id]
        assert run_one.runtime_provider_id == "test.first"
        assert run_two.runtime_provider_id == "test.second"
    finally:
        services.close()


@pytest.mark.asyncio
async def test_transition_authority_and_stream_exhaustion_waiting(tmp_path: Path) -> None:
    provider = RecordingProvider(mode="waiting")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        run = await manager.start_run(agent.id, "submit external work")
        record = await manager.wait_run(run.run_id)
        assert record.status is RunStatus.WAITING
        assert run.run_id not in manager._runtime_tasks
        assert services.get_card(agent.id).status == "idle"
        another = await manager.start_run(agent.id, "another provider turn")
        assert (await manager.wait_run(another.run_id)).status is RunStatus.WAITING
        resumed = await manager.transition_run(run.run_id, RunStatus.RUNNING)
        assert resumed.status is RunStatus.RUNNING
        with pytest.raises(ValueError, match="invalid Run transition"):
            await manager.transition_run(run.run_id, RunStatus.CREATED)
        failed = await manager.transition_run(
            run.run_id, RunStatus.FAILED, error="external job rejected"
        )
        assert failed.finished_at is not None
        with pytest.raises(ValueError):
            await manager.transition_run(run.run_id, RunStatus.RUNNING)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failure_is_run_local_and_concurrency_is_explicit(tmp_path: Path) -> None:
    provider = RecordingProvider(mode="failure")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        failed = await manager.start_run(agent.id, "fail")
        record = await manager.wait_run(failed.run_id)
        assert record.status is RunStatus.FAILED
        assert record.error == "provider exploded"
        assert services.get_card(agent.id).status == "idle"

        provider.mode = "block"
        provider.started.clear()
        attempts = await asyncio.gather(
            manager.start_run(agent.id, "block"),
            manager.start_run(agent.id, "racing second"),
            return_exceptions=True,
        )
        active = next(item for item in attempts if not isinstance(item, Exception))
        rejected = next(item for item in attempts if isinstance(item, Exception))
        assert isinstance(rejected, RuntimeUnavailableError)
        assert "max_concurrent_runs=1" in str(rejected)
        await provider.started.wait()
        assert (await manager.get_agent(agent.id)).status is AgentStatus.RUNNING
        with pytest.raises(RuntimeUnavailableError, match="max_concurrent_runs=1"):
            await manager.start_run(agent.id, "second")
        cancelled = await manager.cancel_run(active.run_id)
        assert cancelled.status is RunStatus.CANCELLED
        await manager.wait_run(active.run_id)
        assert (await manager.get_agent(agent.id)).status is AgentStatus.IDLE
    finally:
        services.close()


@pytest.mark.asyncio
async def test_multiple_concurrent_runs_and_parent_cancellation_propagates(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(mode="block")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(
            type="agent",
            name="Atlas",
            config={"max_concurrent_runs": 2},
        ))
        manager = services._require_run_manager()
        parent = await manager.start_run(agent.id, "parent")
        child = await manager.start_run(
            agent.id, "child", parent_run_id=parent.run_id,
            caller_kind="agent", caller_id=agent.id,
        )
        assert len(manager.list_runs(agent_id=agent.id)) == 2
        await manager.cancel_run(parent.run_id)
        await manager.wait_run(parent.run_id)
        await manager.wait_run(child.run_id)
        assert manager.get_run(parent.run_id).status is RunStatus.CANCELLED
        assert manager.get_run(child.run_id).status is RunStatus.CANCELLED
    finally:
        services.close()


@pytest.mark.asyncio
async def test_restart_interrupts_incomplete_run_without_claiming_success(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(mode="waiting")
    first = _services(tmp_path, provider)
    agent = await first.create_card(CardCreate(type="agent", name="Atlas"))
    manager = first._require_run_manager()
    run = await manager.start_run(agent.id, "wait")
    assert (await manager.wait_run(run.run_id)).status is RunStatus.WAITING
    first.close()

    second = _services(tmp_path, RecordingProvider())
    try:
        await second.startup()
        recovered = second._require_run_manager().get_run(run.run_id)
        assert recovered.status is RunStatus.INTERRUPTED
        assert recovered.finished_at is not None
    finally:
        await second.shutdown()
        second.close()
