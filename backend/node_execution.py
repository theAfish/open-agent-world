"""Host execution service for any plugin implementing the work-source contract."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document
from backend.plugins.execution import ExecutionPolicy, WorkItem, WorkOutcome
from backend.runs.models import TERMINAL_RUN_STATUSES
from backend.state import StateContext
from backend.card_state import state_session, active_state_session, DEFAULT_SESSION
from backend.node_delegation import NodeDelegationMixin

logger = logging.getLogger(__name__)


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


@dataclass
class NodeExecutionService(NodeDelegationMixin):
    services: object
    workers: dict[str | tuple[str, str], asyncio.Task] = field(default_factory=dict)
    stopping: set[str | tuple[str, str]] = field(default_factory=set)

    def spec(self, node_id):
        node = self.services.world.get_card(node_id)
        spec = self.services.plugins.node_type(node.type).execution
        if spec is None:
            raise ResourceValidationError("This plugin does not provide execution")
        return spec

    def _scope(self, node_id):
        self.spec(node_id)
        return self.services.card_state.scope(node_id)

    def state(self, node_id):
        return self.services.state.resolve(StateContext((self._scope(node_id),)), "execution").value or {
            "status": "idle", "attempts": [], "error": None,
        }

    def save(self, node_id, state):
        self.services.state.set(self._scope(node_id), "execution", state)

    def worker_key(self, node_id):
        scope_type, scope_id = self.services.card_state.identity(node_id)
        return (node_id, scope_id) if scope_type == "session" and scope_id != DEFAULT_SESSION else node_id

    def active(self, node_id):
        node = self.services.world.get_card(node_id)
        if self.services.plugins.node_type(node.type).execution is None:
            return False
        if self.worker_key(node_id) in self.workers:
            return True
        node = self.services.world.maybe_get_card(node_id)
        spec = self.services.plugins.node_type(node.type).execution if node else None
        if spec and spec.summoning:
            return self.active_attempts(self.state(node_id))
        return False

    def active_attempts(self, state):
        return any(self.services.run_manager.get_run(entry["run_id"]).status not in TERMINAL_RUN_STATUSES
                   if entry.get("run_id") else entry.get("status") in {"created", "running", "waiting"}
                   for entry in state.get("attempts", []))

    def retained_states(self, *, node_id=None, session_id=None):
        """Inspect existing ledgers without resolving or creating another namespace.

        Delegated Runs have no batch worker. Deletion must still see admissions
        and live children in namespaces other than the one currently displayed.
        """
        filters, arguments = [], []
        if node_id is not None:
            filters.append("c.card_id=?")
            arguments.append(node_id)
        if session_id is not None:
            filters.append("c.scope_type='session' AND c.scope_id=?")
            arguments.append(session_id)
        with self.services.database.locked() as db:
            rows = db.execute("SELECT v.value_json FROM card_state_instances c "
                "JOIN state_values v ON v.scope_id=c.state_scope_id "
                "WHERE v.key='execution' AND v.deleted=0" +
                (" AND " + " AND ".join(filters) if filters else ""), arguments).fetchall()
        return [json.loads(row["value_json"]) for row in rows]

    def assert_editable(self, node_id, *, allow_delegated=False, all_states=False):
        node = self.services.world.get_card(node_id)
        spec = self.services.plugins.node_type(node.type).execution
        if spec is None:
            return
        if allow_delegated and spec and spec.summoning:
            return  # Independent plan edits remain possible; attempts have host-owned state.
        other_active = all_states and (any(key == node_id or isinstance(key, tuple) and key[0] == node_id for key in self.workers)
            or any(self.active_attempts(state) for state in self.retained_states(node_id=node_id)))
        if other_active or self.active(node_id):
            raise ConflictError("Stop execution before changing or deleting this work source")

    def executors(self, node_id):
        if self.spec(node_id).summoning:
            return []
        relationship = self.spec(node_id).executor_relationship
        ids = {edge.target for edge in self.services.world.list_edges_from(node_id)
               if edge.relationship == relationship}
        return [{"id": node.id, "name": node.name} for node in self.services.world.list_cards()
                if node.id in ids and "core.agent" in self.services.plugins.node_type(node.type).traits]

    def snapshot(self, node_id):
        items = self.items(node_id, read_document(self.services, node_id)["value"])
        return {**self.state(node_id), "active": self.active(node_id), "executors": self.executors(node_id),
                'attempts': [{**attempt, 'artifacts': self.services.resources.artifacts.run_references(attempt.get('run_id'))}
                             for attempt in self.state(node_id)['attempts']],
                "items": [item.model_dump(exclude={"prompt"}) for item in items]}

    def authorize(self, node_id, capability):
        if capability is not None:
            live = self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.target_id != node_id or live.kind != self.spec(node_id).control_capability_kind:
                raise PermissionDeniedError("This connection does not allow execution control")

    def items(self, node_id, value):
        items = [WorkItem.model_validate(item) for item in self.spec(node_id).items(value)]
        if len(items) > 1000 or len({item.id for item in items}) != len(items):
            raise ResourceValidationError("Work sources require unique IDs and at most 1000 items")
        return items

    def apply(self, node_id, outcome):
        current = read_document(self.services, node_id)
        value = self.spec(node_id).apply_outcome(current["value"], outcome)
        write_document(self.services, node_id, value, current["revision"], run_id=outcome.run_id)

    async def start(self, node_id, request, *, capability=None):
        if self.spec(node_id).summoning:
            raise ResourceValidationError("Use the coordinator's task delegation tools for this work source")
        async with self.services._node_mutation():
            self.authorize(node_id, capability)
            self.assert_editable(node_id)
            current = read_document(self.services, node_id)
            if current["revision"] != request.expected_revision:
                raise RevisionConflictError("The work source changed. Reload before running it.")
            candidates = self.items(node_id, current["value"])
            if request.item_id is not None:
                candidates = [item for item in candidates if item.id == request.item_id and (item.ready or item.retryable)]
            else:
                candidates = [item for item in candidates if item.ready]
            if not candidates:
                raise ResourceValidationError("No runnable work. Complete prerequisites or resolve blockers first.")
            allowed = {agent["id"] for agent in self.executors(node_id)}
            if any(item.agent_id not in allowed for item in candidates):
                raise PermissionDeniedError("Choose an executor connected from this work source using its execution relationship")
            previous = self.state(node_id)
            self.save(node_id, {"status": "running", "batch_id": str(uuid4()), "error": None,
                                "attempts": previous["attempts"][-200:]})
            self.stopping.discard(self.worker_key(node_id))
            # Explicit dispatch creates an independent, bounded batch. Never
            # inherit an Agent invocation or a node-mutation context into it.
            batch_context = contextvars.Context()
            # Preserve only the resolved state namespace, never the caller's
            # mutation transaction or Agent invocation. Switching tabs is irrelevant.
            scope_type, scope_id = self.services.card_state.identity(node_id)
            invocation = self.services.run_manager.current_context
            origin_session = invocation.context_id if invocation else active_state_session.get()
            batch_context.run(active_state_session.set, scope_id if scope_type == "session" else origin_session)
            self.workers[self.worker_key(node_id)] = asyncio.create_task(
                self._run(node_id, request.item_id, capability), context=batch_context,
                name=f"node-execution:{node_id}")
            return self.snapshot(node_id)

    async def stop(self, node_id, *, capability=None):
        if self.spec(node_id).summoning:
            async with self.services._node_mutation():
                self.authorize(node_id, capability)
                self.stopping.add(self.worker_key(node_id))
                attempts = self.state(node_id)["attempts"]
            try:
                for entry in attempts:
                    if entry.get("run_id"):
                        await self.services.run_manager.cancel_run(entry["run_id"])
                async with self.services._node_mutation():
                    self.collect_delegations(node_id)
                    return self.snapshot(node_id)
            finally:
                self.stopping.discard(self.worker_key(node_id))
        async with self.services._node_mutation():
            self.authorize(node_id, capability)
            context = self.services.run_manager.current_context
            if context and any(attempt.get("run_id") == context.run_id and attempt["status"] == "running"
                               for attempt in self.state(node_id)["attempts"]):
                raise ConflictError("An executor cannot synchronously stop its own batch; use the board controls")
            self.stopping.add(self.worker_key(node_id))
            worker = self.workers.get(self.worker_key(node_id))
        # Cancellation joins provider cleanup; never hold the graph mutation
        # lock while waiting for a provider which may itself use graph tools.
        for attempt in self.state(node_id)["attempts"]:
            if attempt.get("run_id") and attempt["status"] == "running":
                await self.services.run_manager.cancel_run(attempt["run_id"])
        if worker:
            await asyncio.shield(worker)
        self.stopping.discard(self.worker_key(node_id))
        return self.snapshot(node_id)

    async def _run(self, node_id, only_item, capability):
        manager = self.services.run_manager
        active = {}
        attempted = set()
        failed = False
        try:
            while True:
                async with self.services._node_mutation():
                    state = self.state(node_id)
                    value = read_document(self.services, node_id)["value"]
                    policy = ExecutionPolicy.model_validate(self.spec(node_id).policy(value))
                    if self.worker_key(node_id) not in self.stopping and not (failed and policy.pause_on_failure):
                        self.authorize(node_id, capability)
                        node = self.services.world.get_card(node_id)
                        if node.parent_id and self.services.world.get_card(node.parent_id).config.get("paused"):
                            raise ConflictError("Legion is paused; resume it before executing work")
                        candidates = [item for item in self.items(node_id, value)
                                      if item.id not in attempted and (item.ready or (only_item == item.id and item.retryable))
                                      and (only_item is None or item.id == only_item)]
                        for item in candidates:
                            if len(active) >= policy.max_parallel or len(attempted) >= 1000:
                                break
                            # Serialize tasks assigned to the same Agent. Other
                            # Agents can progress concurrently within the limit.
                            if any(entry["agent_id"] == item.agent_id for entry in active.values()):
                                continue
                            if item.agent_id not in {agent["id"] for agent in self.executors(node_id)}:
                                raise PermissionDeniedError("Executor connection is missing or was revoked")
                            manager.assert_can_start(item.agent_id)
                            # Reserve before awaiting Run admission; restart can
                            # recover the Run by batch caller + stable task ID.
                            entry = {"item_id": item.id, "agent_id": item.agent_id, "run_id": None,
                                     "status": "running", "batch_id": state["batch_id"], "error": None, "applied": False}
                            state["attempts"].append(entry)
                            self.save(node_id, state)
                            attempted.add(item.id)
                            record = await manager.start_run(item.agent_id, item.prompt, caller_kind="work",
                                caller_id=state["batch_id"], task_id=f"{node_id}:{item.id}", detached=True,
                                context_id=active_state_session.get())
                            entry["run_id"] = record.run_id
                            self.save(node_id, state)
                            waiter = asyncio.create_task(manager.wait_terminal(record.run_id))
                            active[waiter] = entry
                            self.apply(node_id, WorkOutcome(item_id=item.id, run_id=record.run_id, status="running"))
                    if not active:
                        state["status"] = "stopped" if self.worker_key(node_id) in self.stopping else "failed" if failed else "idle"
                        if len(attempted) >= 1000:
                            state.update(status="paused", error="Batch limit reached (1000 attempts). Start again to continue.")
                        self.save(node_id, state)
                        break
                completed, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                async with self.services._node_mutation():
                    state = self.state(node_id)
                    for waiter in completed:
                        entry = active.pop(waiter)
                        record = waiter.result()
                        status = record.status.value
                        failed = failed or status != "succeeded"
                        for stored in state["attempts"]:
                            if stored.get("run_id") == record.run_id:
                                stored.update(status=status, error=record.error)
                        self.save(node_id, state)
                        self.apply(node_id, WorkOutcome(item_id=entry["item_id"], run_id=record.run_id,
                            artifacts=self.services.resources.artifacts.run_references(record.run_id),
                            status=status, text=manager.final_text(record.run_id), error=record.error))
                        for stored in state["attempts"]:
                            if stored.get("run_id") == record.run_id:
                                stored["applied"] = True
                        self.save(node_id, state)
        except Exception as error:
            logger.exception("Work source execution failed: %s", node_id)
            # Abort admission and settle every already-started Run. No orphan
            # execution continues behind a failed board/controller.
            for waiter, entry in active.items():
                await manager.cancel_run(entry["run_id"])
                waiter.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            async with self.services._node_mutation():
                await self.reconcile(node_id, status="failed", error=str(error))
        finally:
            self.workers.pop(self.worker_key(node_id), None)

    async def reconcile(self, node_id, *, status="interrupted", error=None):
        state = self.state(node_id)
        for attempt in state["attempts"]:
            if attempt.get("applied"):
                continue
            run_id = attempt.get("run_id")
            if run_id is None:
                matches = [run for run in self.services.run_manager.list_runs(task_id=f"{node_id}:{attempt['item_id']}")
                           if run.caller_id == attempt["batch_id"]]
                run_id = matches[0].run_id if matches else None
            record = self.services.run_manager.get_run(run_id) if run_id else None
            outcome_status = record.status.value if record and record.status in TERMINAL_RUN_STATUSES else "interrupted"
            attempt.update(run_id=run_id, status=outcome_status, error=error or (record.error if record else "Admission interrupted"))
            try:
                self.apply(node_id, WorkOutcome(item_id=attempt["item_id"], run_id=run_id, status=outcome_status,
                    artifacts=self.services.resources.artifacts.run_references(run_id),
                    text=self.services.run_manager.final_text(run_id) if run_id else "", error=attempt["error"]))
                attempt["applied"] = True
            except Exception as callback_error:
                # Preserve attempt truth even when a changed plugin cannot
                # reconcile its old document; user sees the recovery error.
                error = f"Result reconciliation failed: {callback_error}"
        state.update(status=status, error=error)
        self.save(node_id, state)

    async def startup(self):
        for node in self.services.world.list_cards():
            if self.services.plugins.node_type(node.type).execution is None:
                continue
            namespaces = self.services.card_state.existing(node.id)
            # Old documents remain eligible for adoption/recovery. Never visit
            # new cards or unseen sessions just because the backend restarted.
            with self.services.database.locked() as db:
                legacy = db.execute("SELECT 1 FROM state_scopes WHERE scope_kind='node_document' AND owner_id=?", (node.id,)).fetchone()
            if legacy and not namespaces:
                namespaces = [self.services.card_state.identity(node.id)]
            for kind, namespace in namespaces:
                with state_session(namespace if kind == "session" else None):
                    if self.spec(node.id).summoning:
                        self.collect_delegations(node.id)
                    elif self.state(node.id)["status"] == "running":
                        await self.reconcile(node.id)

    async def shutdown(self):
        for key in tuple(self.workers):
            node_id, session_id = key if isinstance(key, tuple) else (key, None)
            with state_session(session_id):
                await self.stop(node_id)

    def assert_session_idle(self, session_id):
        if (any(isinstance(key, tuple) and key[1] == session_id for key in self.workers)
                or any(self.active_attempts(state) for state in self.retained_states(session_id=session_id))):
            raise ConflictError("Stop work in this conversation before deleting it")
