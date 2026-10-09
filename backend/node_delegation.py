"""Agent-directed work dispatch through the existing execution and Summoning hosts."""
from __future__ import annotations

import asyncio
import hashlib
import copy
import json
import time
from uuid import uuid4

from pydantic import ValidationError

from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document
from backend.plugins.execution import (ExecutionPolicy, WorkOutcome, DelegationRequest, DelegationWait,
    DelegationStop, DelegationDefer, ExecutionReport)
from backend.plugins.summoning import SummoningAction
from backend.runs.models import TERMINAL_RUN_STATUSES


class NodeDelegationMixin:
    def delegation_targets(self, agent_id):
        targets = []
        for cap in self.services.capabilities.derive(agent_id).capabilities:
            node = self.services.world.get_card(cap.target_id)
            spec = self.services.plugins.node_type(node.type).summoning
            if spec and spec.capability_kind == cap.kind:
                targets.extend({"library_id": node.id, "library_name": node.name,
                    "agent_id": agent.id, "agent_name": agent.name}
                    for agent in self.services.summoning.agents(node.id))
        return list({(t["library_id"], t["agent_id"]): t for t in targets}.values())

    def resolve_delegation(self, agent_id, request):
        from backend.capabilities.projection import resolve_target
        targets = self.delegation_targets(agent_id)
        if request.library_id:
            nodes = {t["library_id"]: self.services.world.get_card(t["library_id"]) for t in targets}
            library = resolve_target(request.library_id, nodes, "library_id")
            targets = [t for t in targets if t["library_id"] == library]
        if request.agent_id:
            nodes = {t["agent_id"]: self.services.world.get_card(t["agent_id"]) for t in targets}
            executor = resolve_target(request.agent_id, nodes, "agent_id")
            targets = [t for t in targets if t["agent_id"] == executor]
        if len(targets) != 1:
            raise ResourceValidationError("Choose an authorized executor from collect.execution.executors; "
                "library_id and agent_id can be omitted only when the target is unique")
        return request.model_copy(update={key: targets[0][key] for key in ("library_id", "agent_id")})

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
            if record:
                self.record_report_check(state, entry, record)
            if any(later["item_id"] == entry["item_id"] for later in state["attempts"][index + 1:]):
                entry["applied"] = True
                continue  # A late recovery must not replace a newer attempt's acceptance.
            try:
                self.apply(node_id, WorkOutcome(item_id=entry["item_id"], run_id=entry.get("run_id"),
                    status=status, text=entry["text"], error=entry["error"],
                    report=entry.get("report"),
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
        try:
            request = self.resolve_delegation(capability.agent_id, request)
        except (PermissionDeniedError, ResourceValidationError):
            if any(e.get("request_id") == request.request_id for e in self.state(node_id)["attempts"]):
                raise ConflictError("request_id already belongs to a different delegation") from None
            raise
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
            entry.update(auto_continue=True, created_at=time.time())
            entry["metadata"] = item.metadata
            state["attempts"].append(entry)
            state["status"] = "running"
            self.save(node_id, state)
            self.watch_continuations(node_id)
            self.apply(node_id, WorkOutcome(item_id=item.id, status="running"))
            try:
                result = await services.summoning.action(request.library_id, SummoningAction(
                    action="summon", agent_id=request.agent_id, wait=False, context_mode="task",
                    prompt=item.prompt + f"\n\nWrite all new outputs under {folder}/ in the authorized Sandbox. "
                        "Do not change other tasks' inputs or outputs. Before ending, call report_delegated_task with "
                        "complete/partial/blocked/waiting, evidence and relative output paths. For remote jobs still running, "
                        "include external_jobs, next_step and check_after_seconds; the host schedules a coordinator check. "
                        "Never inline file contents in the report. Return a concise summary."),
                    capability=summon_cap, dispatch_id=dispatch_id, task_id=f"{node_id}:{item.id}", _capture_held=True,
                    _work_context={"node_id": node_id, "state_identity": self.services.card_state.identity(node_id),
                        "dispatch_id": dispatch_id})
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
            arguments = dict(arguments)
            detail = arguments.pop("detail", "summary" if capability else "full")
            cursor = arguments.pop("since", None)
            if detail not in {"summary", "full"} or (cursor is not None and (not isinstance(cursor, str) or len(cursor) > 64)):
                raise ResourceValidationError("Use detail summary/full and the cursor returned by collect")
            result = await self._delegation_action(node_id, action, arguments, capability=capability)
            if capability:
                async with self.services._node_mutation():
                    self.delegation_authorize(node_id, capability)
                    result["execution"]["executors"] = self.delegation_targets(capability.agent_id)
                    context = self.services.run_manager.current_context
                    if context and context.agent_id == capability.agent_id and action in {"collect", "wait", "inspect"}:
                        state = self.state(node_id)
                        visible = state["attempts"] if detail == "full" or action == "inspect" else state["attempts"][-20:]
                        changed = False
                        for entry in visible:
                            if (entry.get("caller_agent_id") == capability.agent_id and entry.get("applied")
                                    and (action != "inspect" or entry.get("instance_id") == arguments.get("instance_id"))):
                                if not entry.get("observed_by_run"):
                                    entry["observed_by_run"] = context.run_id
                                    changed = True
                        if changed:
                            self.save(node_id, state)
                            result["execution"]["active"] = self.active(node_id)
            return result if detail == "full" else self.delegation_summary(result, cursor)
        except ValidationError as error:
            from backend.node_documents import validation_message
            raise ResourceValidationError(validation_message(error)) from error

    @staticmethod
    def delegation_summary(result, cursor=None):
        document, execution = result["document"], result["execution"]
        fields = ("item_id", "instance_id", "run_id", "status", "output_directory", "applied", "error", "metadata",
                  "notification_run_id", "notification_error", "artifacts", "report")
        attempts = [{k: e[k] for k in fields if k in e} for e in execution["attempts"]]
        for item in attempts:
            if "report" in item:
                item["report"] = {k: v for k, v in item["report"].items() if k != "evidence"}
        snapshot = {"document": {k: document[k] for k in ("revision", "summary")},
            "execution": {"status": execution["status"], "active": execution["active"],
                "executors": execution["executors"], "items": execution["items"],
                "attempts": attempts[-20:], "attempt_count": len(attempts),
                "wakeups": [{k: e[k] for k in ("request_id", "due_at", "reason", "external_jobs", "notification_error") if k in e}
                    for e in execution.get("wakeups", []) if (not e.get("notification_suppressed") or e.get("notification_error")) and not e.get("notification_run_id")][-20:]},
            "next_step": "Use inspect with instance_id for an executor's full report, or task_board_read for task details. "
                "Unobserved completed tasks wake the coordinator after its turn ends. defer schedules an external-job check."}
        for key in ("attempt", "wake_up", "inspected"):
            if key in result:
                snapshot[key] = result[key] if key == "inspected" else {k: v for k, v in result[key].items() if k not in {"text"}}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if cursor == digest:
            return {"unchanged": True, "cursor": digest, "document": snapshot["document"],
                "active": execution["active"]}
        return {**snapshot, "cursor": digest, "unchanged": False}

    async def report_delegated_task(self, agent_id, arguments):
        report = ExecutionReport.model_validate(arguments)
        context = self.services.run_manager.current_context
        if context is None or context.agent_id != agent_id or not context.delegation_context:
            raise PermissionDeniedError("Only the active assigned Executor can report this work")
        assignment = context.delegation_context
        node_id, identity = assignment["node_id"], tuple(assignment["state_identity"])
        async with self.services._node_mutation():
            self.spec(node_id)
            run = self.services.run_manager.get_run(context.run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                raise PermissionDeniedError("This Executor Run has ended")
            state = self.state(node_id, state_identity=identity)
            entry = next((e for e in state["attempts"] if e.get("run_id") == context.run_id
                and e.get("agent_id") == agent_id and e.get("dispatch_id") == assignment["dispatch_id"]), None)
            if entry is None:
                raise PermissionDeniedError("Run is not assigned to this work source")
            self.summoning_capability(entry["caller_agent_id"], entry["library_id"])
            entry["report"] = report.model_dump(mode="json")
            self.save(node_id, state, state_identity=identity)
        return {"recorded": True, "outcome": report.outcome, "acceptance": "Coordinator verification is still required"}

    async def _delegation_action(self, node_id, action, arguments, *, capability=None):
        self.delegation_authorize(node_id, capability)
        if action in {"defer", "cancel_defer"}:
            context = self.services.run_manager.current_context
            if action == "defer" and (capability is None or context is None or context.agent_id != capability.agent_id):
                raise PermissionDeniedError("Schedule checks from the active coordinator Run")
            async with self.services._node_mutation():
                self.delegation_authorize(node_id, capability)
                state = self.state(node_id)
                if action == "cancel_defer":
                    if set(arguments) != {"request_id"}:
                        raise ResourceValidationError("cancel_defer requires request_id")
                    for entry in state.get("wakeups", []):
                        parent = self.services.run_manager.get_run(entry["coordinator_run_id"])
                        if entry["request_id"] == arguments["request_id"] and (capability is None or parent.agent_id == capability.agent_id):
                            entry["notification_suppressed"] = True
                else:
                    request = DelegationDefer.model_validate(arguments)
                    if self.services.run_manager.get_run(context.run_id).status in TERMINAL_RUN_STATUSES:
                        raise ConflictError("The coordinator Run has ended")
                    wakeups = state.setdefault("wakeups", [])
                    entry = next((w for w in wakeups if w["request_id"] == request.request_id), None)
                    if entry:
                        if entry.get("request") != request.model_dump() or self.services.run_manager.get_run(entry["coordinator_run_id"]).agent_id != capability.agent_id:
                            raise ConflictError("request_id already belongs to a different scheduled check")
                    else:
                        if len(wakeups) >= 1000:
                            raise ConflictError("This board reached its retained check limit (1000)")
                        entry = dict(request_id=request.request_id, request=request.model_dump(), reason=request.reason,
                            external_jobs=request.external_jobs, due_at=time.time() + request.delay_seconds,
                            coordinator_run_id=context.run_id, auto_continue=True)
                        wakeups.append(entry)
                self.save(node_id, state)
                self.watch_continuations(node_id)
                return self.collect_delegations(node_id)
        if action == "inspect":
            request = DelegationStop.model_validate(arguments)
            async with self.services._node_mutation():
                self.delegation_authorize(node_id, capability)
                result = self.collect_delegations(node_id)
                entry = next((e for e in result["execution"]["attempts"] if e.get("instance_id") == request.instance_id), None)
                if entry is None:
                    raise ResourceValidationError("Instance is not assigned to this board")
                result["inspected"] = entry
                return result
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
            async with self.services._node_mutation():
                state = self.state(node_id)
                for saved in [*state["attempts"], *state.get("wakeups", [])]:
                    if saved.get("instance_id") == entry["instance_id"] or saved.get("source_run_id") == entry.get("run_id"):
                        saved["notification_suppressed"] = True
                self.save(node_id, state)
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
