"""Agent-directed work dispatch through the existing execution and Summoning hosts."""
from __future__ import annotations

import asyncio
import hashlib
import copy
from uuid import uuid4

from pydantic import ValidationError

from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document
from backend.plugins.execution import ExecutionPolicy, WorkOutcome, DelegationRequest, DelegationWait, DelegationStop
from backend.plugins.summoning import SummoningAction
from backend.runs.models import TERMINAL_RUN_STATUSES


class NodeDelegationMixin:
    def delegation_authorize(self, node_id, capability):
        if not self.spec(node_id).summoning:
            raise ResourceValidationError("This work source does not support delegated tasks")
        self.authorize(node_id, capability)

    def summoning_capability(self, agent_id, library_id):
        kind = self.services.summoning.spec(library_id).capability_kind
        candidates = self.services.capabilities.derive(agent_id).capabilities
        found = next((c for c in candidates if c.target_id == library_id and c.kind == kind), None)
        if found is None:
            raise PermissionDeniedError("Equip Summoning and connect it to the selected Barracks")
        return found

    def collect_delegations(self, node_id):
        """Reconcile only durable terminal Runs; never infer completion from provider silence."""
        state = self.state(node_id)
        before = copy.deepcopy(state)
        manager = self.services.run_manager
        for index, entry in enumerate(state["attempts"]):
            if not entry.get("delegated") or entry.get("applied"):
                continue
            # Recover an admission whose process stopped before the board saved its handle.
            if not entry.get("instance_id"):
                instance = next((r for r in self.services.summoning.records()
                    if r.get("dispatch_id") == entry["dispatch_id"]), None)
                if instance:
                    entry.update(instance_id=instance["id"], agent_id=instance["entry_agent_id"],
                        run_id=instance["attempts"][-1]["run_id"] if instance["attempts"] else None)
                    if not entry["run_id"]:
                        matches = manager.list_runs(agent_id=entry["agent_id"], task_id=f"{node_id}:{entry['item_id']}")
                        entry["run_id"] = matches[0].run_id if matches else None
            record = manager.get_run(entry["run_id"]) if entry.get("run_id") else None
            if record and record.status not in TERMINAL_RUN_STATUSES:
                entry["status"] = record.status.value
                continue
            status = record.status.value if record else "interrupted"
            entry.update(status=status, error=record.error if record else entry.get("error") or "Admission interrupted",
                text=manager.final_text(record.run_id) if record else "")
            if any(later["item_id"] == entry["item_id"] for later in state["attempts"][index + 1:]):
                entry["applied"] = True
                continue  # A late recovery must not replace a newer attempt's acceptance.
            try:
                self.apply(node_id, WorkOutcome(item_id=entry["item_id"], run_id=entry.get("run_id"),
                    status=status, text=entry["text"], error=entry["error"],
                    artifacts=self.services.resources.artifacts.run_references(entry.get("run_id"))))
                entry["applied"] = True
                entry.pop("reconciliation_error", None)
            except (ValueError, ResourceValidationError) as error:
                entry["reconciliation_error"] = str(error)
        state["status"] = "running" if any(e.get("delegated") and e["status"] in {"created", "running", "waiting"}
            for e in state["attempts"]) else "idle"
        if state != before:
            self.save(node_id, state)
        return {"document": read_document(self.services, node_id), "execution": self.snapshot(node_id)}

    async def delegate(self, node_id, request, *, capability):
        services = self.services
        manager = services.run_manager
        # Same lock order as Summoning and portable capture. No waits for child work inside.
        async with services.summoning.admission(request.agent_id):
            self.delegation_authorize(node_id, capability)
            if self.worker_key(node_id) in self.stopping:
                raise ConflictError("This work source is stopping; new tasks cannot be admitted")
            context = manager.current_context
            if context is None or context.agent_id != capability.agent_id:
                raise PermissionDeniedError("Delegate tasks from an active coordinator Run")
            if manager.get_run(context.run_id).status in TERMINAL_RUN_STATUSES:
                raise ConflictError("The coordinator Run has ended")
            summon_cap = self.summoning_capability(capability.agent_id, request.library_id)
            state = self.state(node_id)
            previous = next((e for e in state["attempts"] if e.get("request_id") == request.request_id), None)
            if previous:
                if any(previous.get(k) != v for k, v in {"item_id": request.item_id,
                        "library_id": request.library_id, "template_agent_id": request.agent_id,
                        "caller_agent_id": capability.agent_id}.items()):
                    raise ConflictError("request_id already belongs to a different delegation")
                return {"attempt": previous, **self.collect_delegations(node_id)}
            current = read_document(services, node_id)
            if current["revision"] != request.expected_revision:
                raise RevisionConflictError("The task board changed. Read it before dispatching.")
            if any(e["item_id"] == request.item_id and (not e.get("applied") or e["status"] in {"running", "waiting"})
                   for e in state["attempts"]):
                raise ConflictError("This task already has an uncollected attempt; collect it before retrying")
            item = next((i for i in self.items(node_id, current["value"]) if i.id == request.item_id), None)
            if item is None or not (item.ready or item.retryable):
                raise ResourceValidationError("Task is not ready. Resolve dependencies or return it for revision first.")
            policy = ExecutionPolicy.model_validate(self.spec(node_id).policy(current["value"]))
            active = sum(bool(e.get("run_id")) and manager.get_run(e["run_id"]).status not in TERMINAL_RUN_STATUSES
                         for e in state["attempts"])
            if active >= policy.max_parallel:
                raise ConflictError("This work source reached its concurrent task limit; wait for an active task first")
            if len(state["attempts"]) >= 1000:
                raise ConflictError("This board reached its retained attempt limit (1000)")
            dispatch_id = str(uuid4())
            folder = f"research/{hashlib.sha256(node_id.encode()).hexdigest()[:12]}/{hashlib.sha256(item.id.encode()).hexdigest()[:12]}/{dispatch_id}"
            entry = dict(delegated=True, item_id=item.id, request_id=request.request_id, dispatch_id=dispatch_id,
                library_id=request.library_id, template_agent_id=request.agent_id, caller_agent_id=capability.agent_id,
                coordinator_run_id=context.run_id, agent_id=None, run_id=None, instance_id=None,
                status="running", applied=False, error=None, output_directory=folder)
            entry["metadata"] = item.metadata
            state["attempts"].append(entry)
            state["status"] = "running"
            self.save(node_id, state)
            self.apply(node_id, WorkOutcome(item_id=item.id, status="running"))
            try:
                result = await services.summoning.action(request.library_id, SummoningAction(
                    action="summon", agent_id=request.agent_id, wait=False, context_mode="task",
                    prompt=item.prompt + f"\n\nWrite all new outputs under {folder}/ in the authorized Sandbox. "
                        "Do not change other tasks' inputs or outputs. Return evidence, relative output paths, and limitations."),
                    capability=summon_cap, dispatch_id=dispatch_id, task_id=f"{node_id}:{item.id}", _capture_held=True)
                entry.update(instance_id=result["id"], agent_id=result["entry_agent_id"], run_id=result["run_id"],
                    status=result["status"], error=result.get("error"))
                self.save(node_id, state)
            except BaseException as error:
                entry["error"] = f"{type(error).__name__}: {error}"
                self.save(node_id, state)
                self.collect_delegations(node_id)
                raise
            return {"attempt": entry, **self.collect_delegations(node_id)}

    async def delegation_action(self, node_id, action, arguments, *, capability=None):
        try:
            return await self._delegation_action(node_id, action, arguments, capability=capability)
        except ValidationError as error:
            from backend.node_documents import validation_message
            raise ResourceValidationError(validation_message(error)) from error

    async def _delegation_action(self, node_id, action, arguments, *, capability=None):
        self.delegation_authorize(node_id, capability)
        if action == "delegate":
            if capability is None:
                raise PermissionDeniedError("Ask the connected coordinator to delegate this task")
            return await self.delegate(node_id, DelegationRequest.model_validate(arguments), capability=capability)
        if action == "collect":
            if arguments:
                raise ResourceValidationError("Collect takes no arguments")
            async with self.services._node_mutation():
                self.delegation_authorize(node_id, capability)
                return self.collect_delegations(node_id)
        if action not in {"wait", "stop"}:
            raise ResourceValidationError("Unknown delegation action")
        request = DelegationWait.model_validate(arguments) if action == "wait" else DelegationStop.model_validate(arguments)
        ids = request.instance_ids if action == "wait" else [request.instance_id]
        if len(set(ids)) != len(ids):
            raise ResourceValidationError("Supply unique instance IDs")
        attempts = [next((e for e in self.state(node_id)["attempts"] if e.get("instance_id") == key), None) for key in ids]
        if any(e is None for e in attempts):
            raise ResourceValidationError("Instance is not assigned to this board")
        if capability and any(e["caller_agent_id"] != capability.agent_id for e in attempts):
            raise PermissionDeniedError("Only the coordinator that delegated an instance can manage it")
        # Resolve all grants before performing any stop or waiting.
        groups = {}
        for entry in attempts:
            groups.setdefault(entry["library_id"], []).append(entry["instance_id"])
        grants = {library: self.summoning_capability(capability.agent_id, library) if capability else None for library in groups}
        if action == "stop":
            entry = attempts[0]
            await self.services.summoning.action(entry["library_id"], SummoningAction(action="stop", instance_id=entry["instance_id"]),
                capability=grants[entry["library_id"]])
        else:
            # Work on one board may use several Barracks. Join actual Run events once.
            manager = self.services.run_manager
            pending = [e for e in attempts if e.get("run_id") and manager.get_run(e["run_id"]).status not in TERMINAL_RUN_STATUSES]
            waiters = []
            try:
                if pending and request.timeout_seconds and not (request.wait_mode == "any" and len(pending) < len(attempts)):
                    waiters = [asyncio.create_task(manager.wait_terminal(e["run_id"])) for e in pending]
                    await asyncio.wait(waiters, timeout=request.timeout_seconds,
                        return_when=asyncio.FIRST_COMPLETED if request.wait_mode == "any" else asyncio.ALL_COMPLETED)
            finally:
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
                if waiters:
                    await asyncio.gather(*waiters, return_exceptions=True)
        async with self.services._node_mutation():
            self.delegation_authorize(node_id, capability)
            for library, grant in grants.items():
                self.services.summoning.authorize(library, grant)
            return self.collect_delegations(node_id)
