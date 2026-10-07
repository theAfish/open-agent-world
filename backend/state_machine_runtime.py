"""Durable node-state transitions over existing host operations.

The journal consumer owns no Run or execution lifecycle. Its only writes are
node states, evaluation receipts and action intents. Dispatch goes through the
same capability/Run/document boundaries as other OAW callers.
"""
from __future__ import annotations

import asyncio
from collections import deque
import contextvars
import json
import logging
from uuid import NAMESPACE_URL, uuid5

from backend.agents.tool_execution import execute_tool
from backend.errors import DomainError, NotFoundError, PermissionDeniedError, RuntimeUnavailableError
from backend.events.models import EventType, RuntimeEvent
from backend.state_machine_preview import RuleMemory, SimulationEvent, TriggerEvaluator, evaluate_observation, rule_actions

logger = logging.getLogger(__name__)


class StateMachineRuntime:
    def __init__(self, services):
        self.services = services
        self.database = services.database
        self.store = services.state_machines
        self.journal = services.operation_events
        self.task = None
        self.closed = True
        self.loop = None
        self.signal = asyncio.Event()
        self.lock = asyncio.Lock()
        self.dispatches = {}
        with self.database.transaction(immediate=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS state_machine_receipts (
                instance_id TEXT NOT NULL, event_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                result_json TEXT NOT NULL, PRIMARY KEY(instance_id,event_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS state_machine_actions (
                id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, event_id TEXT NOT NULL,
                rule_id TEXT NOT NULL, status TEXT NOT NULL, intent_json TEXT NOT NULL,
                result_json TEXT, error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE INDEX IF NOT EXISTS state_machine_actions_status ON state_machine_actions(status)")
        self.journal.wake = self.wake

    def wake(self):
        if not self.closed and self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.signal.set)

    def observe_agent(self, agent_id, event_key, *, run_id=None, conversation_id=None, session_id=None):
        return self.observe_system(agent_id, event_key, owner="host.run_manager", run_id=run_id,
                                   conversation_id=conversation_id, session_id=session_id)

    def observe_system(self, agent_id, event_key, *, owner, run_id=None, conversation_id=None, session_id=None):
        """Commit a host fact and drain the node's single machine in journal order.

        This synchronous path makes REST/card status current before RunManager
        returns. It uses exactly the same receipts/evaluator as the background worker.
        External actions are only queued here, never dispatched inline.
        """
        if self.services.world.maybe_get_card(agent_id) is None:
            return
        self.store.initialize_node(agent_id)
        bound = self.store.node_instance(agent_id)
        event = RuntimeEvent(type=EventType.AGENT_ACTIVITY, node_id=agent_id, agent_id=agent_id,
            run_id=run_id, conversation_id=conversation_id, session_id=session_id,
            payload={"event": event_key, "target_card_id": agent_id, "phase": "system", "system_owner": owner})
        with self.services.events.committed_batch(), self.database.transaction(immediate=True):
            sequence = self.journal.record(event)
            if bound:
                cursor = bound[0]["cursor"]
                while cursor < sequence:
                    batch = self.journal.after(cursor)
                    if not batch:
                        break
                    for offset, observation in batch:
                        if offset > sequence:
                            break
                        try:
                            self.process_event(bound[0]["id"], offset, observation)
                        except (DomainError, ValueError, KeyError) as exc:
                            self.disable_unavailable(bound[0], exc)
                            cursor = sequence
                            break
                        cursor = offset

    def disable_unavailable(self, instance, error):
        with self.database.transaction(immediate=True) as db:
            self.store.put_runtime(instance["id"], enabled=False)
            db.execute("INSERT OR REPLACE INTO state_machine_receipts(instance_id,event_id,sequence,result_json) VALUES (?,?,?,?)",
                (instance["id"], "definition-unavailable", instance["cursor"], json.dumps({"reason": "definition_unavailable", "error": str(error)})))

    async def startup(self):
        if not self.closed:
            return
        # Once dispatch has begun, a crash does not prove the operation failed.
        # Only a persisted host receipt (e.g. reserved Run identity) can prove it.
        with self.database.transaction(immediate=True) as db:
            rows = db.execute("SELECT * FROM state_machine_actions WHERE status='executing'").fetchall()
            for row in rows:
                intent = json.loads(row["intent_json"])
                run = self._admitted_run(row["id"]) if intent["action"]["kind"] == "run" else None
                if run:
                    self._finish(row["id"], "succeeded", {"run_id": run.run_id, "status": run.status.value})
                else:
                    self._finish(row["id"], "uncertain", error="Backend stopped during dispatch; reconcile the outcome before retrying")
        self.closed = False
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self._run(), name="state-machine-events", context=contextvars.Context())
        self.wake()

    async def shutdown(self):
        self.closed = True
        self.signal.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        for task in tuple(self.dispatches.values()):
            task.cancel()
        if self.dispatches:
            await asyncio.gather(*tuple(self.dispatches.values()), return_exceptions=True)
        self.dispatches.clear()

    async def _run(self):
        while not self.closed:
            self.signal.clear()
            try:
                await self.process_pending(wait_actions=False)
            except Exception:
                logger.exception("State-machine processing failed; durable records retained")
            try:
                await asyncio.wait_for(self.signal.wait(), timeout=1)
            except TimeoutError:
                pass

    async def process_pending(self, *, wait_actions=True):
        """One bounded drain; also useful for deterministic host integration tests."""
        async with self.lock:
            # Wait for host formation/lifecycle changes to settle before reading
            # their objects. Release the gate before dispatch creates child tasks.
            async with self.services._node_mutation(read_only=True):
                for instance in self.store.list_instances(enabled_only=False):
                    if not instance["enabled"] and not any(group.ownership == "system" for group in self.store.get_definition(instance["card_id"], instance["definition_version"]).entities):
                        continue
                    try:
                        for sequence, event in self.journal.after(instance["cursor"]):
                            self.process_event(instance["id"], sequence, event)
                    except (DomainError, ValueError, KeyError) as exc:
                        # A removed reference must not starve other owners or their
                        # queued actions. Preserve the cursor for explicit review.
                        self.disable_unavailable(instance, exc)
            with self.database.locked() as db:
                pending = db.execute("""SELECT id FROM state_machine_actions
                    WHERE status='pending' OR (status='waiting_capacity' AND updated_at <= datetime('now','-1 second'))
                    ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, updated_at, rowid LIMIT 50""").fetchall()
            for row in pending:
                if wait_actions:
                    await self.dispatch(row["id"])
                elif row["id"] not in self.dispatches and len(self.dispatches) < 16:
                    action_id = row["id"]
                    task = asyncio.create_task(self.dispatch(action_id), name="state-machine-action", context=contextvars.Context())
                    self.dispatches[action_id] = task
                    def settled(done, identity=action_id):
                        self.dispatches.pop(identity, None)
                        self.services._consume_background_task(done)
                        with self.database.locked() as db:
                            status = db.execute("SELECT status FROM state_machine_actions WHERE id=?", (identity,)).fetchone()
                        if status and status[0] != "waiting_capacity":
                            self.wake()
                    task.add_done_callback(settled)

    def process_event(self, instance_id, sequence, event):
        with self.services.events.committed_batch(), self.database.transaction(immediate=True) as db:
            instance = self.store.get_instance(instance_id)
            if sequence <= instance["cursor"]:
                return None
            if db.execute("SELECT 1 FROM state_machine_receipts WHERE instance_id=? AND event_id=?", (instance_id, event.id)).fetchone():
                self.store.put_runtime(instance_id, cursor=sequence)
                return None
            hydration_error = None
            try:
                machine = self.store.get_evaluator_definition(instance_id)
                states = self.store.get_states(instance_id)
            except (DomainError, ValueError, KeyError) as exc:
                local = self.store.get_definition(instance["card_id"], instance["definition_version"])
                if not any(group.ownership == "system" for group in local.entities):
                    raise
                self.store.validate_system_groups(instance["card_id"], local)
                # Broken orchestration must not freeze authoritative runtime facts.
                machine = local.model_copy(update={"rules": [], "references": []})
                states = dict(instance["states"])
                instance = self.store.put_runtime(instance_id, enabled=False)
                hydration_error = str(exc)
            before = dict(states)
            event_key = event.payload.get("event")
            session = instance["context"].get("session_id") or instance["context"].get("context_id")
            relevant_scope = not session or session == (event.session_id or event.payload.get("context_id"))
            actors = {event.node_id, event.agent_id, event.payload.get("caller_object_id"), event.payload.get("target_card_id")}
            entities = [entity for entity in machine.entities if (entity.card_id or instance["card_id"]) in actors]
            if event_key in {"state.entered", "state.exited"}:
                entities = [entity for entity in entities if
                    self.store.resolve_binding(instance_id, entity.id) ==
                    (event.payload.get("state_instance_id"), event.payload.get("entity_id"))
                    and (entity.card_id or instance["card_id"]) == event.node_id]
            if not relevant_scope or not event_key or not entities or event.payload.get("evaluated_instance") == instance_id:
                self.store.put_runtime(instance_id, cursor=sequence)
                return None
            evaluators = [TriggerEvaluator(rule) for rule in machine.rules]
            # Counts belong to the bound scope, exactly as in simulation.
            # Object associations below always come from this individual event;
            # accumulated signal counts never infer an execution's identity.
            correlation = "scope"
            partitions = instance["memory"]
            serialized = partitions.get(correlation, {})
            memory = {}
            for evaluator in evaluators:
                saved = serialized.get(evaluator.rule.id, {})
                initial = evaluator.new_memory()
                memory[evaluator.rule.id] = RuleMemory(
                    {key: deque(saved.get("observations", {}).get(key, [])) for key in initial.observations},
                    bool(saved.get("latched", False)))
            winners, diagnostics, system_changes = [], [], []
            cascade_limited = event.payload.get("cascade_depth", 0) >= 32
            if cascade_limited:
                diagnostics = [{"rule_id": rule.id, "reason": "cascade_limit"} for rule in machine.rules]
            else:
                observation = SimulationEvent(entity_id=entities[0].id, entity_ids=[entity.id for entity in entities], event=event_key,
                    capability=event.payload.get("capability"), target_card_id=event.payload.get("target_card_id"),
                    operation_id=event.payload.get("operation_id"), state_id=event.payload.get("state_id"), event_id=event.id,
                    scope_key=instance["scope_key"], time_ms=event.timestamp.timestamp() * 1000)
                local_groups = {group.id for group in machine.entities if group.id not in instance.get("bindings", {})
                                and (group.card_id or instance["card_id"]) == event.node_id}
                winners, diagnostics, system_changes = evaluate_observation(machine, evaluators, memory, states, observation,
                    owners={event.payload.get("system_owner")} if event.node_id == instance["card_id"] else (),
                    enabled=instance["enabled"], local_groups=local_groups)
            for rule_id, value in memory.items():
                if value.observations:
                    serialized[rule_id] = {"observations": {key: list(times) for key, times in value.observations.items()},
                                           "latched": value.latched}
            if serialized:
                partitions[correlation] = serialized
            queued = []
            system_ids = {group.id for group in machine.entities if group.ownership == "system"}
            if system_changes:
                projected = dict(instance["states"])
                projected.update({item["entity_id"]: item["state_id"] for item in system_changes})
                self.store.put_runtime(instance_id, states=projected, _projection=True)
            if winners:
                self.store.put_states(instance_id, {key: value for key, value in states.items() if before[key] != value and key not in system_ids})
            if system_changes:
                self.store.publish_status(instance_id)
            for rule in machine.rules:
                if rule.id not in winners:
                    continue
                for action in rule_actions(machine, rule):
                    action_id = str(uuid5(NAMESPACE_URL, f"state-machine:{instance_id}:{event.id}:{rule.id}:{action.id}"))
                    intent = {"action": action.model_dump(), "event": event.model_dump(mode="json"),
                              "card_id": instance["card_id"], "context": instance["context"],
                              "definition_version": instance["definition_version"],
                              "command": rule.command.model_dump() if rule.command and action.id == "command:" + rule.command.command_id else None}
                    status, error = "pending", None
                    try:
                        intent["target_id"] = self._resolve(action.target, instance["card_id"], event)
                        intent["caller_id"] = self._resolve(action.caller, instance["card_id"], event)
                    except PermissionDeniedError as exc:
                        status, error = "failed", str(exc)
                    db.execute("""INSERT OR IGNORE INTO state_machine_actions
                        (id,instance_id,event_id,rule_id,status,intent_json,error) VALUES (?,?,?,?,?,?,?)""",
                        (action_id, instance_id, event.id, rule.id, status, json.dumps(intent), error))
                    queued.append({"id": action_id, "status": status})
            for entity in machine.entities:
                if before[entity.id] == states[entity.id]:
                    continue
                owner_id, group_id = self.store.resolve_binding(instance_id, entity.id)
                for phase, state_id in (("exited", before[entity.id]), ("entered", states[entity.id])):
                    entered = RuntimeEvent(id=str(uuid5(NAMESPACE_URL, f"{phase}:{instance_id}:{event.id}:{entity.id}")),
                        type=EventType.STATE_UPDATED, node_id=entity.card_id or instance["card_id"],
                        session_id=event.session_id or event.payload.get("context_id"), run_id=event.run_id,
                        conversation_id=event.conversation_id or event.payload.get("conversation_id"),
                        payload={**event.payload, "event": "state." + phase, "from_state": before[entity.id],
                                 "state_id": state_id, "entity_id": group_id, "state_instance_id": owner_id,
                                 "ownership": entity.ownership, "system_owner": None,
                                 "evaluated_instance": instance_id if entity.ownership == "system" else None,
                                 "target_card_id": entity.card_id or instance["card_id"],
                                 "cascade_depth": event.payload.get("cascade_depth", 0) + 1})
                    self.journal.record(entered)
                    self.services.events.publish_event_nowait(entered)
            self.store.put_runtime(instance_id, memory=partitions, cursor=sequence)
            relevant = any(item["reason"] not in {"unrelated_event", "disabled"} for item in diagnostics)
            user_changes = [{"kind": "user", "entity_id": effect.entity_id, "from_state": before[effect.entity_id],
                             "state_id": states[effect.entity_id], "rule_id": rule.id,
                             "triggered_by": rule.trigger.model_dump(exclude_none=True)}
                            for rule in machine.rules if rule.id in winners
                            for effect in rule.effects if before[effect.entity_id] != states[effect.entity_id]]
            result = {"rule_id": winners[0] if winners else None, "rule_ids": winners, "rules": diagnostics, "states": states, "actions": queued,
                      "system_transitions": system_changes,
                      "user_transitions": user_changes,
                      "commands": [rule.command.model_dump() for rule in machine.rules if rule.id in winners and rule.command],
                      "reason": "cascade_limit" if cascade_limited else "transition_committed" if winners or system_changes else "condition_false" if relevant else "unrelated_event"}
            if hydration_error:
                result.update(reason="definition_unavailable", error=hydration_error)
            db.execute("INSERT INTO state_machine_receipts(instance_id,event_id,sequence,result_json) VALUES (?,?,?,?)",
                       (instance_id, event.id, sequence, json.dumps(result)))
            return result

    @staticmethod
    def _resolve(reference, card_id, event):
        if reference.kind == "current":
            return card_id
        if reference.kind == "specific":
            return reference.card_id
        from backend.operation_associations import resolve_associated_objects
        objects = resolve_associated_objects(event, produced_only=reference.kind == "produced")
        if reference.index >= len(objects):
            raise PermissionDeniedError("No object at this invocation association index")
        return objects[reference.index]

    def _admitted_run(self, action_id):
        try:
            return self.services.run_manager.get_run(action_id)
        except NotFoundError:
            return None

    def _finish(self, action_id, status, result=None, error=None):
        with self.database.transaction(immediate=True) as db:
            db.execute("UPDATE state_machine_actions SET status=?,result_json=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (status, json.dumps(result) if result is not None else None, error, action_id))

    async def dispatch(self, action_id):
        with self.database.locked() as db:
            row = db.execute("SELECT * FROM state_machine_actions WHERE id=?", (action_id,)).fetchone()
        if row is None or row["status"] not in {"pending", "waiting_capacity"}:
            return
        intent = json.loads(row["intent_json"])
        action, caller, target = intent["action"], intent["caller_id"], intent["target_id"]
        event = RuntimeEvent.model_validate(intent["event"])
        args = dict(action["arguments"])
        capability = None
        try:
            async with self.services._node_mutation(read_only=True):
                if not self.store.get_instance(row["instance_id"])["enabled"]:
                    self._finish(action_id, "cancelled", error="State-machine instance disabled before dispatch")
                    return
                if intent.get("command"):
                    # A pending request does not outlive its owning plugin contract.
                    resolved = self.store.get_evaluator_definition(row["instance_id"])
                    self.store.validate_commands(resolved)
                self.services.world.require_available_card(self.services.world.get_card(target))
                self.services.world.require_available_card(self.services.world.get_card(caller))
                if action["kind"] == "capability":
                    capability = self._capability(caller, target, action["capability"])
                elif action["kind"] == "run":
                    if event.payload.get("continuation_owner") == "legacy":
                        raise PermissionDeniedError("continuation_owned_by_legacy")
                    self.services.capabilities.require_run_request(caller, target)
                    if not isinstance(args.get("prompt"), str) or not args["prompt"].strip():
                        raise PermissionDeniedError("Run actions require a prompt")
                    if self._admitted_run(action_id):
                        self._finish(action_id, "succeeded", {"run_id": action_id})
                        return
                    self.services.run_manager.assert_can_start(target)
                    self._authorize_conversation(intent, target)
                elif action["kind"] == "node_action":
                    _, handler = self._node_action(target, action)
                    if caller != target:
                        capability = self._capability(caller, target, handler.capability_kind)
                else:
                    raise PermissionDeniedError("Unknown action kind")
        except RuntimeUnavailableError as exc:
            self._finish(action_id, "waiting_capacity", error=str(exc))
            return
        except (DomainError, ValueError) as exc:
            self._finish(action_id, "revoked" if isinstance(exc, (PermissionDeniedError, NotFoundError)) else "failed", error=str(exc))
            return
        # Claim before any side effect; no blind resubmission after this point.
        with self.database.transaction(immediate=True) as db:
            claimed = db.execute("UPDATE state_machine_actions SET status='executing',error=NULL WHERE id=? AND status IN ('pending','waiting_capacity')", (action_id,))
            if claimed.rowcount != 1:
                return
        from backend.card_state import state_session
        from backend.operation_associations import operation_context
        context = intent["context"]
        conversation_id, session_id = self._conversation(intent)
        try:
            with state_session(session_id), operation_context(
                    action_id, caller_object_id=caller, target_object_id=target,
                    run_id=event.run_id, parent_run_id=event.payload.get("parent_run_id"),
                    root_run_id=event.payload.get("root_run_id"), task_id=event.payload.get("task_id"),
                    context_id=session_id, conversation_id=conversation_id,
                    continuation_owner="state_machine") as operation:
                async def invoke():
                    if not self.store.get_instance(row["instance_id"])["enabled"]:
                        raise PermissionDeniedError("State-machine instance disabled before invocation")
                    if action["kind"] == "capability":
                        return await self.services.run_manager.capability_provider.invoke_tool(caller, capability.id, args, request_id=action_id)
                    if action["kind"] == "node_action":
                        family, _ = self._node_action(target, action)
                        if family == "resource":
                            from backend.node_resources import ResourceActionRequest, invoke_resource_action
                            return await invoke_resource_action(self.services, target, action["action"],
                                ResourceActionRequest(arguments=args), capability=capability, request_id=action_id)
                        from backend.node_documents import DocumentActionRequest, invoke_document_action, read_document
                        revision = args.pop("expected_revision", None)
                        if revision is None:
                            revision = read_document(self.services, target)["revision"]
                        return await invoke_document_action(self.services, target, action["action"],
                            DocumentActionRequest(arguments=args, expected_revision=revision), capability=capability, request_id=action_id)
                    async with self.services._node_mutation():
                        if not self.store.get_instance(row["instance_id"])["enabled"]:
                            raise PermissionDeniedError("State-machine instance disabled before Run admission")
                        self.services.world.require_available_card(self.services.world.get_card(caller))
                        self.services.capabilities.require_run_request(caller, target)
                        self._authorize_conversation(intent, target)
                        parent_id = event.run_id
                        parent = self.services.run_manager.get_run(parent_id) if parent_id else None
                        # Interrupted Runs remain interrupted; create a new root
                        # with provenance through the normal admission boundary.
                        if parent and parent.status.value in {"interrupted", "failed", "cancelled"}:
                            parent_id = None
                        prompt = args["prompt"]
                        conversation, session_id = self._conversation(intent)
                        if conversation and session_id:
                            session = self.services.conversations.get_session(conversation, session_id)
                            prompt = self.services._conversation_prompt(conversation, session, target, prompt)
                        run = await self.services.run_manager.start_run(target, prompt,
                            caller_kind="state_machine", caller_id=intent["card_id"], run_id=action_id,
                            parent_run_id=parent_id, context_id=session_id or context.get("context_id") or (parent.context_id if parent else None),
                            task_id=parent.task_id if parent else None,
                            initial_lifecycle={"state_machine_action_id": action_id, "continued_from_run": event.run_id,
                                               "conversation_id": conversation, "session_id": session_id})
                    if conversation and session_id:
                        task = asyncio.create_task(self.services._persist_conversation_run(target, run.run_id, conversation, session_id))
                        self.services.node_execution.continuation_outputs.add(task)
                        task.add_done_callback(self.services.node_execution.continuation_outputs.discard)
                        task.add_done_callback(self.services._consume_background_task)
                    return {"run_id": run.run_id}
                outcome = await execute_tool(invoke)
                result = {"response": outcome.response, "associations": operation.payload().get("associations", [])}
                code = outcome.response.get("error", {}).get("code") if isinstance(outcome.response, dict) and isinstance(outcome.response.get("error"), dict) else None
                if not outcome.ok and action["kind"] == "run" and code == "runtime_unavailable" and not self._admitted_run(action_id):
                    self._finish(action_id, "waiting_capacity", result, "Run admission deferred by the existing host")
                elif not outcome.ok and code == "permission_denied" and not self._invocation_started(action_id):
                    self._finish(action_id, "revoked", result, outcome.response["error"]["message"])
                else:
                    self._finish(action_id, "succeeded" if outcome.ok else "uncertain", result,
                                 None if outcome.ok else "Invocation did not succeed; reconcile external effects before retrying")
        except asyncio.CancelledError:
            self._finish(action_id, "uncertain", error="Dispatch interrupted; reconcile external effects before retrying")
            raise
        except Exception:
            self._finish(action_id, "uncertain", error="Dispatch result unavailable; reconcile external effects before retrying")
            logger.exception("State-machine action %s has an uncertain outcome", action_id)

    def _capability(self, caller, target, kind):
        grants = self.services.capabilities.derive(caller).capabilities
        grant = next((item for item in grants if item.kind == kind and item.target_id == target), None)
        if grant is None:
            raise PermissionDeniedError("capability_revoked: the required connection is no longer granted")
        return grant

    def _invocation_started(self, action_id):
        with self.database.locked() as db:
            return db.execute("""SELECT 1 FROM operation_events
                WHERE json_extract(event_json,'$.payload.invocation_id')=?
                AND json_extract(event_json,'$.payload.event') IN ('capability.started','operation.started','run.created') LIMIT 1""",
                (action_id,)).fetchone() is not None

    def _node_action(self, target, action):
        node = self.services.world.get_card(target)
        spec = self.services.plugins.node_type(node.type)
        choices = [(family, definitions[action["action"]])
                   for family, definitions in (("document", spec.document.actions if spec.document else {}),
                                                ("resource", spec.resource_actions))
                   if action["action"] in definitions and (not action.get("operation_id")
                       or action["operation_id"] == f"{family}:{node.type}:{action['action']}")]
        if len(choices) != 1:
            raise PermissionDeniedError("Choose an unambiguous registered node operation")
        return choices[0]

    @staticmethod
    def _conversation(intent):
        event, context = intent["event"], intent["context"]
        operation = event.get("payload", {})
        return (context.get("conversation_id") or event.get("conversation_id") or operation.get("conversation_id"),
                context.get("session_id") or context.get("context_id") or event.get("session_id") or operation.get("context_id"))

    def _authorize_conversation(self, intent, target):
        conversation, session_id = self._conversation(intent)
        if conversation and session_id:
            session = self.services.conversations.get_session(conversation, session_id)
            self.services._require_session_participant(session, target)
            self.services._require_conversation_connection(target, conversation)

    def diagnostics(self, card_id):
        instances = [item for item in self.store.list_instances(enabled_only=False) if item["card_id"] == card_id]
        ids = {item["id"] for item in instances}
        if not ids:
            return {"instances": [], "actions": [], "diagnostics": []}
        placeholders = ",".join("?" for _ in ids)
        with self.database.locked() as db:
            actions = [dict(row) for row in db.execute(f"SELECT id,instance_id,event_id,rule_id,status,error,result_json,intent_json FROM state_machine_actions WHERE instance_id IN ({placeholders}) ORDER BY rowid DESC LIMIT 200", tuple(ids))]
            receipts = [{"event_id": row["event_id"], "instance_id": row["instance_id"], **json.loads(row["result_json"])}
                        for row in db.execute(f"SELECT * FROM state_machine_receipts WHERE instance_id IN ({placeholders}) ORDER BY sequence DESC LIMIT 200", tuple(ids))]
        for action in actions:
            intent = json.loads(action.pop("intent_json"))
            action.update(kind="command" if intent.get("command") else "action", operation_id=intent["action"].get("operation_id"),
                          target=intent.get("command"), outcome="accepted" if intent.get("command") and action["status"] == "succeeded" else action["status"])
        return {"instances": instances, "actions": actions, "diagnostics": receipts}
