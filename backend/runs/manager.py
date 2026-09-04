from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from backend.agents import (
    AgentConfig,
    AgentCapabilityProvider,
    AgentEvent,
    AgentNotFoundError,
    AgentStatus,
    RuntimeProvider,
)
from backend.errors import RuntimeUnavailableError
from backend.events.hub import EventHub
from backend.events.models import EventType
from backend.plugins import PluginRegistry
from backend.state import StateContext, StateScope, StateStore
from backend.world.models import Card, CardPatch
from backend.world.store import WorldStore

from .models import (
    InvocationCaller,
    InvocationContext,
    RunRecord,
    RunStatus,
    RunSuspension,
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

# Temporary provider event-stream inactivity policy. Silence is not proof that
# the underlying task failed, but without a formal provider liveness signal the
# runtime must bound how long an inactive stream occupies a Run.
# TODO: Replace this heuristic with a provider liveness/heartbeat contract.
DEFAULT_INACTIVITY_TIMEOUT_SECONDS: float = 300.0


@dataclass(slots=True)
class RunManager:
    """Single authority for Run creation, execution, transition, and cancellation."""

    store: RunStore
    world: WorldStore
    events: EventHub
    plugins: PluginRegistry
    capability_provider: AgentCapabilityProvider
    state: StateStore
    default_runtime_provider_id: str | None = None
    provider_options: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    inactivity_timeout_seconds: float | None = DEFAULT_INACTIVITY_TIMEOUT_SECONDS
    _providers: dict[str, RuntimeProvider] = field(default_factory=dict)
    _agent_provider_ids: dict[str, str] = field(default_factory=dict)
    _runtime_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _execution_done: dict[str, asyncio.Event] = field(default_factory=dict)
    _terminal_done: dict[str, asyncio.Event] = field(default_factory=dict)
    _final_text: dict[str, str] = field(default_factory=dict)
    _occupied_runs: dict[str, str] = field(default_factory=dict)
    _suspensions: dict[str, RunSuspension] = field(default_factory=dict)
    _start_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _transition_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _cancel_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _deleting_agents: set[str] = field(default_factory=set)

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
        self._assert_agent_accepts_runs(agent_id)
        card = self._agent_card(agent_id)
        self._provider_id(card)
        self._check_concurrency(card)

    async def reserve_agent_deletion(self, agent_id: str) -> None:
        """Reversibly stop new Run admission before deleting an Agent node."""

        lock = self._start_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            self._agent_card(agent_id)
            self._deleting_agents.add(agent_id)

    def release_agent_deletion(self, agent_id: str) -> None:
        """Release a process-local deletion reservation idempotently."""

        self._deleting_agents.discard(agent_id)

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
            self._terminal_done.setdefault(record.run_id, asyncio.Event()).set()
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
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Run prompt must be a non-empty string")
        lock = self._start_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            self._assert_agent_accepts_runs(agent_id)
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
            state_context = self._state_context(record)
            self.state.set(
                state_context.local_scope,
                "input",
                prompt,
                actor_id=agent_id,
                run_id=record.run_id,
            )
            self._execution_done[record.run_id] = asyncio.Event()
            self._terminal_done[record.run_id] = asyncio.Event()
            self._occupied_runs[record.run_id] = agent_id
            await self._publish_run(record, EventType.RUN_CREATED)
            record = await self._transition_run_admitted(
                record.run_id, RunStatus.RUNNING
            )
            await self._publish_agent_operational(
                agent_id, "running", record.run_id, started=True
            )
            # Register execution before releasing admission. Otherwise deletion
            # can reserve this Agent, cancel the durable Run, and still have this
            # method launch an untracked provider coroutine afterward.
            task = asyncio.create_task(
                self._execute(record, card, RuntimeInput(prompt=prompt)),
                name=f"run:{record.run_id}",
            )
            self._runtime_tasks[record.run_id] = task
            task.add_done_callback(
                lambda completed, run_id=record.run_id: self._task_finished(run_id)
            )
        return record

    async def wait_execution(self, run_id: str) -> RunRecord:
        """Wait only for the current provider coroutine/turn to finish."""

        self.get_run(run_id)
        event = self._execution_done.get(run_id)
        if event is not None and run_id in self._runtime_tasks:
            await event.wait()
        return self.get_run(run_id)

    async def wait_terminal(self, run_id: str) -> RunRecord:
        """Wait until the durable Run reaches a terminal lifecycle state."""

        current = self.get_run(run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return current
        event = self._terminal_done.setdefault(run_id, asyncio.Event())
        # Recheck after installing the waiter so a concurrent transition cannot
        # be missed between the first read and Event creation.
        if self.get_run(run_id).status in TERMINAL_RUN_STATUSES:
            event.set()
        await event.wait()
        return self.get_run(run_id)

    def final_text(self, run_id: str) -> str:
        return self._final_text.get(run_id, "")

    def holds_agent_slot(self, run_id: str) -> bool:
        self.get_run(run_id)
        return run_id in self._occupied_runs

    def get_suspension(self, run_id: str) -> RunSuspension | None:
        self.get_run(run_id)
        return self._suspensions.get(run_id)

    async def suspend_run(
        self,
        run_id: str,
        *,
        reason: str,
        release_agent_slot: bool = False,
    ) -> RunRecord:
        """Explicitly suspend a Run and optionally release Agent capacity."""

        if not reason.strip():
            raise ValueError("suspension reason must not be empty")
        current = self.get_run(run_id)
        if current.status is RunStatus.RUNNING:
            current = await self.transition_run(run_id, RunStatus.WAITING)
        elif current.status is not RunStatus.WAITING:
            raise ValueError(
                f"cannot suspend a Run in {current.status.value!r} status"
            )
        self._suspensions[run_id] = RunSuspension(
            reason=reason.strip(), release_agent_slot=release_agent_slot
        )
        if release_agent_slot:
            self._release_agent_slot(run_id)
            await self._publish_agent_operational(
                current.agent_id,
                "running" if self._occupied_agent_runs(current.agent_id) else "idle",
                run_id,
            )
        return self.get_run(run_id)

    async def transition_run(
        self, run_id: str, status: RunStatus | str, *, error: str | None = None
    ) -> RunRecord:
        target = RunStatus(status)
        if target is RunStatus.RUNNING:
            current = self.store.get(run_id)
            start_lock = self._start_locks.setdefault(
                current.agent_id, asyncio.Lock()
            )
            async with start_lock:
                return await self._transition_run_admitted(
                    run_id, target, error=error
                )
        return await self._transition_run_admitted(run_id, target, error=error)

    async def _transition_run_admitted(
        self,
        run_id: str,
        target: RunStatus,
        *,
        error: str | None = None,
    ) -> RunRecord:
        """Transition a Run; RUNNING callers must hold the Agent start lock."""

        acquired_slot = False
        lock = self._transition_locks.setdefault(run_id, asyncio.Lock())
        try:
            async with lock:
                current = self.store.get(run_id)
                if target not in _VALID_TRANSITIONS[current.status]:
                    raise ValueError(
                        f"invalid Run transition: {current.status.value} -> {target.value}"
                    )
                if target is RunStatus.RUNNING:
                    self._assert_agent_accepts_runs(current.agent_id)
                    card = self._agent_card(current.agent_id)
                    if run_id not in self._occupied_runs:
                        self._check_concurrency(card)
                        self._occupied_runs[run_id] = current.agent_id
                        acquired_slot = True
                record = self.store.update_status(run_id, target, error=error)
        except BaseException:
            if acquired_slot:
                self._release_agent_slot(run_id)
            raise
        event_type = _RUN_EVENTS[target]
        if current.status is RunStatus.WAITING and target is RunStatus.RUNNING:
            event_type = EventType.RUN_RESUMED
            self._suspensions.pop(run_id, None)
        if target in TERMINAL_RUN_STATUSES:
            self._release_agent_slot(run_id)
            self._suspensions.pop(run_id, None)
            self._terminal_done.setdefault(run_id, asyncio.Event()).set()
        await self._publish_run(record, event_type)
        if current.status is RunStatus.WAITING and target is RunStatus.RUNNING:
            await self._publish_agent_operational(record.agent_id, "running", run_id)
        elif target in TERMINAL_RUN_STATUSES:
            await self._publish_agent_operational(
                record.agent_id,
                "running" if self._occupied_agent_runs(record.agent_id) else "idle",
                run_id,
            )
        return record

    async def cancel_run(self, run_id: str, *, propagate: bool = True) -> RunRecord:
        lock = self._cancel_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await self._cancel_run_locked(run_id, propagate=propagate)

    async def _cancel_run_locked(
        self, run_id: str, *, propagate: bool
    ) -> RunRecord:
        current = self.store.get(run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            # A provider can emit a terminal status before its local stream has
            # finished unwinding. Agent deletion must join that tail before it
            # removes provider state.
            await self._join_runtime_task(run_id)
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
                await self._join_runtime_task(run_id)
                return latest
            raise
        task = self._runtime_tasks.get(run_id)
        task_to_wait = (
            task
            if task is not None
            and task is not asyncio.current_task()
            and not task.done()
            else None
        )
        # Signal the local provider consumer before awaiting provider cleanup.
        # A failing or hanging ``stop`` must not leave execution running after
        # the durable Run is already CANCELLED.
        if task_to_wait is not None:
            task_to_wait.cancel()
        provider = self._providers.get(current.runtime_provider_id)
        try:
            if provider is not None:
                await provider.stop(run_id)
        finally:
            if task_to_wait is not None:
                task_to_wait.cancel()
                await asyncio.gather(task_to_wait, return_exceptions=True)
        return record

    async def cancel_agent_runs(self, agent_id: str) -> list[RunRecord]:
        runs = self.list_runs(agent_id=agent_id)
        if not runs:
            return []
        active_run_ids = {
            record.run_id
            for record in runs
            if record.status not in TERMINAL_RUN_STATUSES
        }
        # Start every cancellation before waiting for provider cleanup. One
        # provider stop that hangs must not prevent sibling local tasks from
        # receiving cancellation after their Agent node has been deleted. Call
        # terminal Runs too: their per-Run lock joins any concurrent cancellation
        # or provider-stream tail before Agent provider state is removed.
        results = await asyncio.gather(
            *(self.cancel_run(record.run_id) for record in runs),
            return_exceptions=True,
        )
        cancelled: list[RunRecord] = []
        for result in results:
            if isinstance(result, BaseException):
                raise result
            if result.run_id in active_run_ids:
                cancelled.append(result)
        return cancelled

    async def _join_runtime_task(self, run_id: str) -> None:
        task = self._runtime_tasks.get(run_id)
        if task is None or task is asyncio.current_task() or task.done():
            return
        await asyncio.gather(task, return_exceptions=True)

    async def register_agent(self, card: Card) -> None:
        self.state.ensure_scope("agent", card.id, schema_id="core.agent")
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

    def provider_id_for_card(self, card: Card) -> str | None:
        """Resolve the provider needed to clean up a live or deleted Agent."""

        return self._agent_provider_ids.get(card.id) or self._optional_provider_id(card)

    async def delete_agent(
        self,
        agent_id: str,
        *,
        missing_ok: bool = False,
        provider_id: str | None = None,
    ) -> None:
        await self.cancel_agent_runs(agent_id)
        selected_provider_id = self._agent_provider_ids.get(agent_id) or provider_id
        if selected_provider_id is not None:
            try:
                await self._provider(selected_provider_id).delete_agent(agent_id)
            except AgentNotFoundError:
                if not missing_ok:
                    raise
            self._agent_provider_ids.pop(agent_id, None)
        self.state.delete_scope("agent", agent_id)

    async def get_agent(self, agent_id: str) -> Any:
        card = self._agent_card(agent_id)
        provider_id = self._agent_provider_ids.get(agent_id, self._provider_id(card))
        info = await self._provider(provider_id).get_agent(agent_id)
        executing = self._occupied_agent_runs(agent_id)
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
            state_context=self._state_context(record),
        )
        token = _current_invocation.set(context)
        try:
            provider = self._provider(record.runtime_provider_id)
            if record.agent_id not in self._agent_provider_ids:
                await provider.create_agent(self._agent_config(card))
                self._agent_provider_ids[record.agent_id] = record.runtime_provider_id
            timeout = self._inactivity_timeout(card)
            stream = aiter(
                provider.execute(self._agent_config(card), context, runtime_input)
            )
            try:
                while True:
                    try:
                        if timeout is None:
                            event = await anext(stream)
                        else:
                            event = await asyncio.wait_for(anext(stream), timeout)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        await provider.stop(record.run_id)
                        raise RuntimeError(
                            "runtime provider produced no activity for "
                            f"{timeout:g} seconds; the Run was failed instead of "
                            "stalling silently"
                        ) from None
                    if event.agent_id != record.agent_id or event.run_id != record.run_id:
                        raise RuntimeError(
                            "runtime provider emitted an event for a different Agent or Run"
                        )
                    await self._publish_provider_event(event, record)
                    text = event.payload.get("text")
                    if isinstance(text, str):
                        self._final_text[record.run_id] = text
                    if event.run_status is not None:
                        current = self.get_run(record.run_id)
                        if current.status not in TERMINAL_RUN_STATUSES:
                            await self.transition_run(record.run_id, event.run_status)
                        if self.get_run(record.run_id).status in TERMINAL_RUN_STATUSES:
                            break
            finally:
                closer = getattr(stream, "aclose", None)
                if closer is not None:
                    with contextlib.suppress(Exception):
                        await closer()
            current = self.get_run(record.run_id)
            if current.status is RunStatus.RUNNING:
                # Stream exhaustion means the provider turn ended. It is not
                # implicit work completion; absent an explicit terminal event,
                # the durable Run remains waiting for a future resume signal.
                # Occupancy is retained unless suspend_run explicitly releases it.
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
            execution_done = self._execution_done.get(record.run_id)
            if execution_done is not None:
                execution_done.set()
            await self._publish_agent_operational(
                record.agent_id,
                "running" if self._occupied_agent_runs(record.agent_id) else "idle",
                record.run_id,
            )

    def _state_context(self, record: RunRecord) -> StateContext:
        """Build the provider-neutral inheritance stack for one Run."""

        scopes: list[StateScope] = [
            self.state.ensure_scope("world", "default", schema_id="core.world"),
            self.state.ensure_scope("agent", record.agent_id, schema_id="core.agent"),
        ]
        if record.context_id is not None:
            scopes.append(
                self.state.ensure_scope(
                    "session", record.context_id, schema_id="core.session"
                )
            )
        scopes.append(
            self.state.ensure_scope("run", record.run_id, schema_id="core.run")
        )
        return StateContext(tuple(scopes))

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
        execution_done = self._execution_done.get(run_id)
        if execution_done is not None:
            execution_done.set()

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

    def _inactivity_timeout(self, card: Card) -> float | None:
        """Per-Agent override of the provider event-stream inactivity policy.

        A non-positive configured value disables the watchdog explicitly.
        Invalid values fall back to the manager default.
        """

        configured = card.config.get("run_inactivity_timeout_seconds")
        if isinstance(configured, (int, float)) and not isinstance(configured, bool):
            return float(configured) if configured > 0 else None
        return self.inactivity_timeout_seconds

    def _check_concurrency(self, card: Card) -> None:
        configured = card.config.get("max_concurrent_runs", 1)
        limit = configured if isinstance(configured, int) and configured > 0 else 1
        active = len(self._occupied_agent_runs(card.id))
        if active >= limit:
            raise RuntimeUnavailableError(
                f"agent {card.id!r} reached max_concurrent_runs={limit}"
            )

    def _occupied_agent_runs(self, agent_id: str) -> list[RunRecord]:
        return [
            self.get_run(run_id)
            for run_id, occupant_agent_id in self._occupied_runs.items()
            if occupant_agent_id == agent_id
        ]

    def _release_agent_slot(self, run_id: str) -> None:
        self._occupied_runs.pop(run_id, None)

    def _assert_agent_accepts_runs(self, agent_id: str) -> None:
        if agent_id in self._deleting_agents:
            raise RuntimeUnavailableError(
                f"agent {agent_id!r} is being deleted and cannot accept Runs"
            )

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
                "run_inactivity_timeout_seconds",
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
        conversation_id, session_id = self._conversation_scope(record)
        await self.events.publish(
            event_type,
            node_id=record.agent_id,
            agent_id=record.agent_id,
            run_id=record.run_id,
            conversation_id=conversation_id,
            session_id=session_id,
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
        conversation_id, session_id = self._conversation_scope(record)
        await self.events.publish(
            EventType(event.type.value),
            node_id=record.agent_id,
            agent_id=record.agent_id,
            run_id=record.run_id,
            conversation_id=conversation_id,
            session_id=session_id,
            payload={**dict(event.payload), "run_id": record.run_id},
        )

    async def _publish_agent_operational(
        self,
        agent_id: str,
        status: str,
        run_id: str,
        *,
        started: bool = False,
    ) -> None:
        # This is availability/load only. A normal Run failure never changes an
        # Agent to error; runtime initialization failures are handled separately.
        card = self.world.maybe_get_card(agent_id)
        # Agent availability is also meaningful to the caller that initiated a
        # Run.  Preserve that scope so generic consumers (including
        # Conversation) can react immediately, before a provider emits its
        # first text or tool event.
        record = self.get_run(run_id)
        conversation_id, session_id = self._conversation_scope(record)
        if card is not None and card.status != status:
            self.world.update_card(agent_id, CardPatch(status=status))
        event_type = (
            EventType.AGENT_STARTED if started else EventType.AGENT_STATUS_CHANGED
        )
        await self.events.publish(
            event_type,
            node_id=agent_id,
            agent_id=agent_id,
            run_id=run_id,
            conversation_id=conversation_id,
            session_id=session_id,
            payload={"status": status, "run_id": run_id},
        )

    def _conversation_scope(self, record: RunRecord) -> tuple[str | None, str | None]:
        """Resolve conversation scope through delegated Agent runs as well."""

        current = record
        while True:
            if current.caller_kind == "conversation":
                return current.caller_id, current.context_id
            if current.parent_run_id is None:
                return None, None
            current = self.get_run(current.parent_run_id)
