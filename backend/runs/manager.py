from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from backend.agents import (
    AgentConfig,
    AgentCapabilityProvider,
    AgentEvent,
    AgentEventType,
    AgentNotFoundError,
    AgentStatus,
    RuntimeProvider,
)
from backend.errors import RuntimeUnavailableError
from backend.events.hub import EventHub
from backend.events.models import EventType
from backend.plugins import PluginRegistry
from backend.world.models import Card, CardPatch
from backend.world.store import WorldStore

from .models import (
    InvocationCaller,
    InvocationContext,
    RunRecord,
    RunStatus,
    RuntimeInput,
    TERMINAL_RUN_STATUSES,
)
from .store import RunStore


_VALID_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.WAITING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.WAITING: frozenset(
        {RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.INTERRUPTED: frozenset(),
}

_RUN_EVENTS = {
    RunStatus.RUNNING: EventType.RUN_STARTED,
    RunStatus.WAITING: EventType.RUN_WAITING,
    RunStatus.SUCCEEDED: EventType.RUN_SUCCEEDED,
    RunStatus.FAILED: EventType.RUN_FAILED,
    RunStatus.CANCELLED: EventType.RUN_CANCELLED,
    RunStatus.INTERRUPTED: EventType.RUN_INTERRUPTED,
}

_current_invocation: ContextVar[InvocationContext | None] = ContextVar(
    "current_invocation", default=None
)


@dataclass(slots=True)
class RunManager:
    """Single authority for Run creation, execution, transition, and cancellation."""

    store: RunStore
    world: WorldStore
    events: EventHub
    plugins: PluginRegistry
    capability_provider: AgentCapabilityProvider
    default_runtime_provider_id: str | None = None
    provider_options: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    _providers: dict[str, RuntimeProvider] = field(default_factory=dict)
    _agent_provider_ids: dict[str, str] = field(default_factory=dict)
    _runtime_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _completion: dict[str, asyncio.Event] = field(default_factory=dict)
    _final_text: dict[str, str] = field(default_factory=dict)
    _start_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _transition_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    @property
    def current_context(self) -> InvocationContext | None:
        return _current_invocation.get()

    def provider_for_agent_configuration(
        self, provider_id: str
    ) -> RuntimeProvider:
        return self._provider(provider_id)

    def default_provider(self) -> RuntimeProvider:
        if self.default_runtime_provider_id is None:
            raise RuntimeUnavailableError("agent runtime is not configured")
        return self._provider(self.default_runtime_provider_id)

    def assert_can_start(self, agent_id: str) -> None:
        card = self._agent_card(agent_id)
        self._provider_id(card)
        self._check_concurrency(card)

    def is_agent_in_lineage(self, agent_id: str) -> bool:
        context = self.current_context
        while context is not None:
            if context.agent_id == agent_id:
                return True
            if context.parent_run_id is None:
                return False
            parent = self.get_run(context.parent_run_id)
            if parent.agent_id == agent_id:
                return True
            if parent.parent_run_id is None:
                return False
            context = InvocationContext(
                run_id=parent.run_id,
                agent_id=parent.agent_id,
                parent_run_id=parent.parent_run_id,
                root_run_id=parent.root_run_id,
                caller=InvocationCaller(parent.caller_kind, parent.caller_id),
                context_id=parent.context_id,
                task_id=parent.task_id,
                runtime_provider_id=parent.runtime_provider_id,
            )
        return False

    async def startup(self) -> None:
        for record in self.store.interrupt_incomplete():
            await self._publish_run(record, EventType.RUN_INTERRUPTED)

    async def shutdown(self) -> None:
        for record in self.list_runs():
            if record.status not in TERMINAL_RUN_STATUSES:
                await self.cancel_run(record.run_id)
        tasks = tuple(self._runtime_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def install_provider(self, provider_id: str, provider: RuntimeProvider) -> None:
        """Install an already-created provider instance, primarily for embedding/tests."""

        if not isinstance(provider, RuntimeProvider):
            raise TypeError("provider must implement RuntimeProvider")
        self._providers[provider_id] = provider

    def get_run(self, run_id: str) -> RunRecord:
        return self.store.get(run_id)

    def list_runs(
        self, *, agent_id: str | None = None, task_id: str | None = None
    ) -> list[RunRecord]:
        return self.store.list(agent_id=agent_id, task_id=task_id)

    def list_child_runs(self, parent_run_id: str) -> list[RunRecord]:
        return self.store.list_children(parent_run_id)

    async def start_run(
        self,
        agent_id: str,
        prompt: str,
        *,
        caller_kind: str = "user",
        caller_id: str | None = None,
        parent_run_id: str | None = None,
        detached: bool = False,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> RunRecord:
        lock = self._start_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            card = self._agent_card(agent_id)
            provider_id = self._provider_id(card)
            self._check_concurrency(card)
            if detached and parent_run_id is not None:
                raise ValueError("a detached Run cannot also specify parent_run_id")
            if (
                not detached
                and parent_run_id is None
                and self.current_context is not None
            ):
                parent_run_id = self.current_context.run_id
            if parent_run_id is not None:
                parent = self.get_run(parent_run_id)
                task_id = task_id or parent.task_id
                context_id = context_id or parent.context_id
            record = self.store.create(
                agent_id=agent_id,
                runtime_provider_id=provider_id,
                caller_kind=caller_kind,
                caller_id=caller_id,
                parent_run_id=parent_run_id,
                task_id=task_id,
                context_id=context_id,
            )
            self._completion[record.run_id] = asyncio.Event()
            await self._publish_run(record, EventType.RUN_CREATED)
            record = await self.transition_run(record.run_id, RunStatus.RUNNING)
            await self._publish_agent_operational(agent_id, "running", record.run_id)
        task = asyncio.create_task(
            self._execute(record, card, RuntimeInput(prompt=prompt)),
            name=f"run:{record.run_id}",
        )
        self._runtime_tasks[record.run_id] = task
        task.add_done_callback(
            lambda completed, run_id=record.run_id: self._task_finished(run_id)
        )
        return record

    async def wait_run(self, run_id: str) -> RunRecord:
        event = self._completion.get(run_id)
        if event is not None:
            await event.wait()
        return self.get_run(run_id)

    def final_text(self, run_id: str) -> str:
        return self._final_text.get(run_id, "")

    async def transition_run(
        self, run_id: str, status: RunStatus | str, *, error: str | None = None
    ) -> RunRecord:
        target = RunStatus(status)
        lock = self._transition_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current = self.store.get(run_id)
            if target not in _VALID_TRANSITIONS[current.status]:
                raise ValueError(
                    f"invalid Run transition: {current.status.value} -> {target.value}"
                )
            record = self.store.update_status(run_id, target, error=error)
        event_type = _RUN_EVENTS[target]
        if current.status is RunStatus.WAITING and target is RunStatus.RUNNING:
            event_type = EventType.RUN_RESUMED
        await self._publish_run(record, event_type)
        if target is RunStatus.WAITING and not self._executing_runs(record.agent_id):
            await self._publish_agent_operational(record.agent_id, "idle", run_id)
        elif current.status is RunStatus.WAITING and target is RunStatus.RUNNING:
            await self._publish_agent_operational(record.agent_id, "running", run_id)
        elif target in TERMINAL_RUN_STATUSES and not self._executing_runs(record.agent_id):
            await self._publish_agent_operational(record.agent_id, "idle", run_id)
        return record

    async def cancel_run(self, run_id: str, *, propagate: bool = True) -> RunRecord:
        current = self.store.get(run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return current
        if propagate:
            for child in self.list_child_runs(run_id):
                if child.status not in TERMINAL_RUN_STATUSES:
                    await self.cancel_run(child.run_id, propagate=True)
        try:
            record = await self.transition_run(run_id, RunStatus.CANCELLED)
        except ValueError:
            latest = self.get_run(run_id)
            if latest.status in TERMINAL_RUN_STATUSES:
                return latest
            raise
        provider = self._providers.get(current.runtime_provider_id)
        if provider is not None:
            await provider.stop(run_id)
        task = self._runtime_tasks.get(run_id)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        return record

    async def cancel_agent_runs(self, agent_id: str) -> list[RunRecord]:
        cancelled: list[RunRecord] = []
        for record in self.list_runs(agent_id=agent_id):
            if record.status not in TERMINAL_RUN_STATUSES:
                cancelled.append(await self.cancel_run(record.run_id))
        return cancelled

    async def register_agent(self, card: Card) -> None:
        provider_id = self._optional_provider_id(card)
        if provider_id is None:
            return
        provider = self._provider(provider_id)
        await provider.create_agent(self._agent_config(card))
        self._agent_provider_ids[card.id] = provider_id

    async def update_agent(self, card: Card) -> None:
        previous_id = self._agent_provider_ids.get(card.id)
        provider_id = self._optional_provider_id(card)
        if provider_id is None:
            if previous_id is not None:
                await self.cancel_agent_runs(card.id)
                await self._providers[previous_id].delete_agent(card.id)
                self._agent_provider_ids.pop(card.id, None)
            return
        if previous_id is not None and previous_id != provider_id:
            await self.cancel_agent_runs(card.id)
            await self._providers[previous_id].delete_agent(card.id)
            await self._provider(provider_id).create_agent(self._agent_config(card))
        elif previous_id is None:
            await self._provider(provider_id).create_agent(self._agent_config(card))
        else:
            await self._provider(provider_id).update_agent(self._agent_config(card))
        self._agent_provider_ids[card.id] = provider_id

    async def delete_agent(self, agent_id: str, *, missing_ok: bool = False) -> None:
        await self.cancel_agent_runs(agent_id)
        provider_id = self._agent_provider_ids.pop(agent_id, None)
        if provider_id is None:
            return
        try:
            await self._providers[provider_id].delete_agent(agent_id)
        except AgentNotFoundError:
            if not missing_ok:
                raise

    async def get_agent(self, agent_id: str) -> Any:
        card = self._agent_card(agent_id)
        provider_id = self._agent_provider_ids.get(agent_id, self._provider_id(card))
        info = await self._provider(provider_id).get_agent(agent_id)
        executing = self._executing_runs(agent_id)
        return replace(
            info,
            status=AgentStatus.RUNNING if executing else AgentStatus.IDLE,
            active_run_id=executing[0].run_id if executing else None,
        )

    async def _execute(
        self, record: RunRecord, card: Card, runtime_input: RuntimeInput
    ) -> None:
        context = InvocationContext(
            run_id=record.run_id,
            agent_id=record.agent_id,
            parent_run_id=record.parent_run_id,
            root_run_id=record.root_run_id,
            caller=InvocationCaller(record.caller_kind, record.caller_id),
            context_id=record.context_id,
            task_id=record.task_id,
            runtime_provider_id=record.runtime_provider_id,
        )
        token = _current_invocation.set(context)
        try:
            provider = self._provider(record.runtime_provider_id)
            if record.agent_id not in self._agent_provider_ids:
                await provider.create_agent(self._agent_config(card))
                self._agent_provider_ids[record.agent_id] = record.runtime_provider_id
            async for event in provider.execute(
                self._agent_config(card), context, runtime_input
            ):
                if event.agent_id != record.agent_id or event.run_id != record.run_id:
                    raise RuntimeError(
                        "runtime provider emitted an event for a different Agent or Run"
                    )
                await self._publish_provider_event(event, record)
                text = event.payload.get("text")
                if isinstance(text, str):
                    self._final_text[record.run_id] = text
                current = self.get_run(record.run_id)
                if (
                    event.type is AgentEventType.TOOL_STARTED
                    and current.status is RunStatus.RUNNING
                ):
                    await self.transition_run(record.run_id, RunStatus.WAITING)
                elif (
                    event.type is AgentEventType.TOOL_COMPLETED
                    and current.status is RunStatus.WAITING
                ):
                    await self.transition_run(record.run_id, RunStatus.RUNNING)
                if event.run_status is not None:
                    current = self.get_run(record.run_id)
                    if current.status not in TERMINAL_RUN_STATUSES:
                        await self.transition_run(record.run_id, event.run_status)
                    if self.get_run(record.run_id).status in TERMINAL_RUN_STATUSES:
                        break
            current = self.get_run(record.run_id)
            if current.status is RunStatus.RUNNING:
                # Stream exhaustion means the provider turn ended. It is not
                # implicit work completion; absent an explicit terminal event,
                # the durable Run remains waiting for a future resume signal.
                await self.transition_run(record.run_id, RunStatus.WAITING)
        except asyncio.CancelledError:
            current = self.get_run(record.run_id)
            if current.status not in TERMINAL_RUN_STATUSES:
                await self.transition_run(record.run_id, RunStatus.CANCELLED)
        except Exception as exc:
            current = self.get_run(record.run_id)
            if current.status not in TERMINAL_RUN_STATUSES:
                await self.transition_run(
                    record.run_id, RunStatus.FAILED, error=str(exc)
                )
        finally:
            _current_invocation.reset(token)
            self._runtime_tasks.pop(record.run_id, None)
            completion = self._completion.get(record.run_id)
            if completion is not None:
                completion.set()
            if not self._executing_runs(record.agent_id):
                await self._publish_agent_operational(
                    record.agent_id, "idle", record.run_id
                )

    def _provider(self, provider_id: str) -> RuntimeProvider:
        existing = self._providers.get(provider_id)
        if existing is not None:
            return existing
        options = dict(self.provider_options.get(provider_id, {}))
        provider = self.plugins.create_runtime_provider(
            provider_id, self.capability_provider, **options
        )
        self._providers[provider_id] = provider
        return provider

    def _task_finished(self, run_id: str) -> None:
        self._runtime_tasks.pop(run_id, None)
        completion = self._completion.get(run_id)
        if completion is not None:
            completion.set()

    def _provider_id(self, card: Card) -> str:
        provider_id = self._optional_provider_id(card)
        if provider_id is None:
            raise RuntimeUnavailableError(
                "agent runtime is not configured; set OPEN_AGENT_WORLD_AGENT_RUNTIME explicitly"
            )
        return provider_id

    def _optional_provider_id(self, card: Card) -> str | None:
        configured = card.config.get("runtime_provider_id")
        provider_id = configured if isinstance(configured, str) and configured else None
        provider_id = provider_id or self.default_runtime_provider_id
        if provider_id is None:
            return None
        if (
            not self.plugins.has_runtime_provider(provider_id)
            and provider_id not in self._providers
        ):
            raise RuntimeUnavailableError(
                f"runtime provider {provider_id!r} is not registered"
            )
        return provider_id

    def _check_concurrency(self, card: Card) -> None:
        configured = card.config.get("max_concurrent_runs", 1)
        limit = configured if isinstance(configured, int) and configured > 0 else 1
        active = sum(
            1
            for record in self.list_runs(agent_id=card.id)
            if record.status is RunStatus.RUNNING
        )
        if active >= limit:
            raise RuntimeUnavailableError(
                f"agent {card.id!r} reached max_concurrent_runs={limit}"
            )

    def _executing_runs(self, agent_id: str) -> list[RunRecord]:
        return [
            record
            for record in self.list_runs(agent_id=agent_id)
            if record.status is RunStatus.RUNNING
        ]

    def _agent_card(self, agent_id: str) -> Card:
        card = self.world.get_card(agent_id)
        if card.type != "agent":
            raise RuntimeUnavailableError(f"card {agent_id!r} is not an Agent")
        return card

    @staticmethod
    def _agent_config(card: Card) -> AgentConfig:
        provider_config = {
            key: value
            for key, value in card.config.items()
            if key
            not in {
                "system_instruction",
                "model",
                "status",
                "runtime_provider_id",
                "max_concurrent_runs",
            }
        }
        return AgentConfig(
            agent_id=card.id,
            name=card.name,
            system_instruction=str(card.config.get("system_instruction", "")),
            model=str(card.config.get("model", "gemini-3.7-flash")),
            runtime_provider_id=(
                str(card.config["runtime_provider_id"])
                if card.config.get("runtime_provider_id")
                else None
            ),
            max_concurrent_runs=int(card.config.get("max_concurrent_runs", 1)),
            provider_config=provider_config,
        )

    async def _publish_run(self, record: RunRecord, event_type: EventType) -> None:
        await self.events.publish(
            event_type,
            node_id=record.agent_id,
            agent_id=record.agent_id,
            run_id=record.run_id,
            conversation_id=(
                record.caller_id if record.caller_kind == "conversation" else None
            ),
            session_id=record.context_id,
            payload={
                "run": record.model_dump(mode="json"),
                "run_id": record.run_id,
                **({"error": record.error} if record.error else {}),
            },
        )

    async def _publish_provider_event(
        self, event: AgentEvent, record: RunRecord
    ) -> None:
        # AgentEvent.COMPLETED means one provider turn finished. Run success is
        # controlled only by the separate explicit ``run_status`` transition.
        await self.events.publish(
            EventType(event.type.value),
            node_id=record.agent_id,
            agent_id=record.agent_id,
            run_id=record.run_id,
            conversation_id=(
                record.caller_id if record.caller_kind == "conversation" else None
            ),
            session_id=record.context_id,
            payload={**dict(event.payload), "run_id": record.run_id},
        )

    async def _publish_agent_operational(
        self, agent_id: str, status: str, run_id: str
    ) -> None:
        # This is availability/load only. A normal Run failure never changes an
        # Agent to error; runtime initialization failures are handled separately.
        card = self.world.maybe_get_card(agent_id)
        if card is not None and card.status != status:
            self.world.update_card(agent_id, CardPatch(status=status))
        event_type = (
            EventType.AGENT_STARTED
            if status == "running"
            else EventType.AGENT_STATUS_CHANGED
        )
        await self.events.publish(
            event_type,
            node_id=agent_id,
            agent_id=agent_id,
            run_id=run_id,
            payload={"status": status, "run_id": run_id},
        )
