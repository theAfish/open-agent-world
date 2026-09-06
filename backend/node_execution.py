"""Host execution service for any plugin implementing the work-source contract."""
from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document
from backend.plugins.execution import ExecutionPolicy, WorkItem, WorkOutcome
from backend.runs.models import TERMINAL_RUN_STATUSES
from backend.state import StateContext

logger = logging.getLogger(__name__)


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


@dataclass
class NodeExecutionService:
    services: object
    workers: dict[str, asyncio.Task] = field(default_factory=dict)
    stopping: set[str] = field(default_factory=set)

    def spec(self, node_id):
        node = self.services.world.get_card(node_id)
        spec = self.services.plugins.node_type(node.type).execution
        if spec is None:
            raise ResourceValidationError("This plugin does not provide execution")
        return spec

    def _scope(self, node_id):
        self.spec(node_id)
        return self.services.state.ensure_scope("node_document", node_id, schema_id="core.node_document")

    def state(self, node_id):
        return self.services.state.resolve(StateContext((self._scope(node_id),)), "execution").value or {
            "status": "idle", "attempts": [], "error": None,
        }

    def save(self, node_id, state):
        self.services.state.set(self._scope(node_id), "execution", state)

    def active(self, node_id):
        return node_id in self.workers

    def assert_editable(self, node_id):
        if self.active(node_id):
            raise ConflictError("Stop execution before changing or deleting this work source")

    def executors(self, node_id):
        relationship = self.spec(node_id).executor_relationship
        ids = {edge.target for edge in self.services.world.list_edges_from(node_id)
               if edge.relationship == relationship}
        return [{"id": node.id, "name": node.name} for node in self.services.world.list_cards()
                if node.id in ids and "core.agent" in self.services.plugins.node_type(node.type).traits]

    def snapshot(self, node_id):
        items = self.items(node_id, read_document(self.services, node_id)["value"])
        return {**self.state(node_id), "active": self.active(node_id), "executors": self.executors(node_id),
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
            self.stopping.discard(node_id)
            # Explicit dispatch creates an independent, bounded batch. Never
            # inherit an Agent invocation or a node-mutation context into it.
            self.workers[node_id] = asyncio.create_task(
                self._run(node_id, request.item_id, capability), context=contextvars.Context(),
                name=f"node-execution:{node_id}")
            return self.snapshot(node_id)

    async def stop(self, node_id, *, capability=None):
        async with self.services._node_mutation():
            self.authorize(node_id, capability)
            context = self.services.run_manager.current_context
            if context and any(attempt.get("run_id") == context.run_id and attempt["status"] == "running"
                               for attempt in self.state(node_id)["attempts"]):
                raise ConflictError("An executor cannot synchronously stop its own batch; use the board controls")
            self.stopping.add(node_id)
            worker = self.workers.get(node_id)
        # Cancellation joins provider cleanup; never hold the graph mutation
        # lock while waiting for a provider which may itself use graph tools.
        for attempt in self.state(node_id)["attempts"]:
            if attempt.get("run_id") and attempt["status"] == "running":
                await self.services.run_manager.cancel_run(attempt["run_id"])
        if worker:
            await asyncio.shield(worker)
        self.stopping.discard(node_id)
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
                    if node_id not in self.stopping and not (failed and policy.pause_on_failure):
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
                                caller_id=state["batch_id"], task_id=f"{node_id}:{item.id}", detached=True)
                            entry["run_id"] = record.run_id
                            self.save(node_id, state)
                            waiter = asyncio.create_task(manager.wait_terminal(record.run_id))
                            active[waiter] = entry
                            self.apply(node_id, WorkOutcome(item_id=item.id, run_id=record.run_id, status="running"))
                    if not active:
                        state["status"] = "stopped" if node_id in self.stopping else "failed" if failed else "idle"
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
            self.workers.pop(node_id, None)

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
            if self.services.plugins.node_type(node.type).execution and self.state(node.id)["status"] == "running":
                await self.reconcile(node.id)

    async def shutdown(self):
        for node_id in tuple(self.workers):
            await self.stop(node_id)
