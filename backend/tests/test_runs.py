from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from backend.agents import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentInfo,
    AgentStatus,
    RuntimeProvider,
    ScopedToolDefinition,
)
from backend.agents.tools import build_scoped_tool_callables
from backend.config import Settings
from backend.errors import RuntimeUnavailableError
from backend.plugins import create_builtin_registry
from backend.tests.plugin_support import install_test_plugin
from backend.runs import InvocationContext, RunStatus, RuntimeInput
from backend.services import create_services
from backend.world.models import CardCreate


class _ToolBugCapabilityProvider:
    async def list_tools(self, agent_id: str) -> tuple[ScopedToolDefinition, ...]:
        del agent_id
        return ()

    async def invoke_tool(
        self,
        agent_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        del agent_id, capability_id, arguments
        raise TypeError("plugin implementation bug")


class RecordingProvider(RuntimeProvider):
    def __init__(self, *, mode: str = "success") -> None:
        self.mode = mode
        self.configs: dict[str, AgentConfig] = {}
        self.contexts: list[InvocationContext] = []
        self.started = asyncio.Event()
        self.tool_started = asyncio.Event()
        self.continue_tool = asyncio.Event()

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
        if self.mode == "tool_failure":
            tool = build_scoped_tool_callables(
                _ToolBugCapabilityProvider(),
                context.agent_id,
                (
                    ScopedToolDefinition(
                        capability_id="test.bug",
                        name="buggy_tool",
                        description="Exercise the tool failure boundary.",
                    ),
                ),
            )[0]
            await tool()
        if self.mode == "tool":
            yield AgentEvent(
                context.agent_id,
                context.run_id,
                AgentEventType.TOOL_STARTED,
                {"name": "read_file"},
            )
            self.tool_started.set()
            await self.continue_tool.wait()
            yield AgentEvent(
                context.agent_id,
                context.run_id,
                AgentEventType.TOOL_COMPLETED,
                {"name": "read_file"},
            )
        yield AgentEvent(
            context.agent_id,
            context.run_id,
            AgentEventType.MESSAGE,
            {"text": runtime_input.prompt},
        )
        if self.mode in {"success", "tool"}:
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
    install_test_plugin(
        registry,
        "test.runtime-plugin",
        lambda registration: registration.register_runtime_provider(
            "test.runtime", lambda capabilities: provider
        ),
    )
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
        await manager.wait_terminal(root.run_id)
        child = await manager.start_run(
            agent.id,
            "child",
            caller_kind="agent",
            caller_id=agent.id,
            parent_run_id=root.run_id,
            task_id="task-a",
        )
        await manager.wait_terminal(child.run_id)

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
    def configure(registration: Any) -> None:
        registration.register_runtime_provider(
            "test.first", lambda capabilities: first
        )
        registration.register_runtime_provider(
            "test.second", lambda capabilities: second
        )

    install_test_plugin(registry, "test.multi-runtime", configure)
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
        await manager.wait_terminal(run_one.run_id)
        await manager.wait_terminal(run_two.run_id)
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
        record = await manager.wait_execution(run.run_id)
        assert record.status is RunStatus.WAITING
        assert run.run_id not in manager._runtime_tasks
        assert manager.holds_agent_slot(run.run_id)
        assert services.get_card(agent.id).status == "running"
        await manager.suspend_run(run.run_id, reason="awaiting_operator")
        assert manager.holds_agent_slot(run.run_id)
        assert manager.get_suspension(run.run_id).release_agent_slot is False
        with pytest.raises(RuntimeUnavailableError, match="max_concurrent_runs=1"):
            await manager.start_run(agent.id, "slot is still occupied")

        await manager.suspend_run(
            run.run_id, reason="external_job", release_agent_slot=True
        )
        assert not manager.holds_agent_slot(run.run_id)
        assert services.get_card(agent.id).status == "idle"
        another = await manager.start_run(agent.id, "another provider turn")
        assert (await manager.wait_execution(another.run_id)).status is RunStatus.WAITING
        assert manager.holds_agent_slot(another.run_id)
        await manager.transition_run(another.run_id, RunStatus.FAILED)

        resumed = await manager.transition_run(run.run_id, RunStatus.RUNNING)
        assert resumed.status is RunStatus.RUNNING
        assert manager.holds_agent_slot(run.run_id)
        terminal_wait = asyncio.create_task(manager.wait_terminal(run.run_id))
        await asyncio.sleep(0)
        assert not terminal_wait.done()
        with pytest.raises(ValueError, match="invalid Run transition"):
            await manager.transition_run(run.run_id, RunStatus.CREATED)
        failed = await manager.transition_run(
            run.run_id, RunStatus.FAILED, error="external job rejected"
        )
        assert await terminal_wait == failed
        assert failed.finished_at is not None
        with pytest.raises(ValueError):
            await manager.transition_run(run.run_id, RunStatus.RUNNING)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_tool_activity_does_not_change_status_or_release_slot(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(mode="tool")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        run = await manager.start_run(agent.id, "read a file")
        await provider.tool_started.wait()

        assert manager.get_run(run.run_id).status is RunStatus.RUNNING
        assert manager.holds_agent_slot(run.run_id)
        with pytest.raises(RuntimeUnavailableError, match="max_concurrent_runs=1"):
            await manager.start_run(agent.id, "second")

        provider.continue_tool.set()
        assert (await manager.wait_terminal(run.run_id)).status is RunStatus.SUCCEEDED
        assert not manager.holds_agent_slot(run.run_id)
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
        record = await manager.wait_terminal(failed.run_id)
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
        await manager.wait_terminal(active.run_id)
        assert (await manager.get_agent(agent.id)).status is AgentStatus.IDLE
    finally:
        services.close()


@pytest.mark.asyncio
async def test_unexpected_tool_exception_fails_the_run(tmp_path: Path) -> None:
    provider = RecordingProvider(mode="tool_failure")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()

        started = await manager.start_run(agent.id, "exercise tool")
        failed = await manager.wait_terminal(started.run_id)

        assert failed.status is RunStatus.FAILED
        assert failed.error == "plugin implementation bug"
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
        await manager.wait_terminal(parent.run_id)
        await manager.wait_terminal(child.run_id)
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
    assert (await manager.wait_execution(run.run_id)).status is RunStatus.WAITING
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


@pytest.mark.asyncio
async def test_inactive_provider_stream_fails_run_instead_of_stalling(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(mode="block")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(
            type="agent",
            name="Atlas",
            config={"run_inactivity_timeout_seconds": 0.05},
        ))
        manager = services._require_run_manager()
        run = await manager.start_run(agent.id, "hang forever")
        record = await manager.wait_terminal(run.run_id)
        assert record.status is RunStatus.FAILED
        assert "no activity" in (record.error or "")
        assert not manager.holds_agent_slot(run.run_id)
        assert services.get_card(agent.id).status == "idle"
    finally:
        services.close()


@pytest.mark.asyncio
async def test_non_positive_inactivity_timeout_disables_watchdog(
    tmp_path: Path,
) -> None:
    provider = RecordingProvider(mode="tool")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(
            type="agent",
            name="Atlas",
            config={"run_inactivity_timeout_seconds": 0},
        ))
        manager = services._require_run_manager()
        manager.inactivity_timeout_seconds = 0.05  # would trip if not disabled
        run = await manager.start_run(agent.id, "long tool call")
        await provider.tool_started.wait()
        await asyncio.sleep(0.15)
        assert manager.get_run(run.run_id).status is RunStatus.RUNNING
        provider.continue_tool.set()
        assert (await manager.wait_terminal(run.run_id)).status is RunStatus.SUCCEEDED
    finally:
        services.close()


@pytest.mark.asyncio
async def test_watchdog_resets_on_provider_activity(tmp_path: Path) -> None:
    provider = RecordingProvider(mode="tool")
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(
            type="agent",
            name="Atlas",
            config={"run_inactivity_timeout_seconds": 0.4},
        ))
        manager = services._require_run_manager()
        run = await manager.start_run(agent.id, "steady progress")
        await provider.tool_started.wait()
        await asyncio.sleep(0.15)
        provider.continue_tool.set()
        record = await manager.wait_terminal(run.run_id)
        assert record.status is RunStatus.SUCCEEDED
    finally:
        services.close()
