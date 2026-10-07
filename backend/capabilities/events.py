"""Host-owned event contracts derived from existing operation/work registration.

Plugins register tools and execution normally. They do not register a second
event interface. Discovery and invocation share these lifecycle definitions.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import re
from uuid import uuid4, uuid5, NAMESPACE_URL

from backend.capabilities.projection import project_operations
from backend.events import EventType, RuntimeEvent
from backend.operation_associations import active_operation, operation_context
from backend.agent_state_machine import AGENT_ACTIVITY_EVENTS


@dataclass(frozen=True)
class LifecycleEvent:
    key: str
    label: str
    type: EventType
    phase: str = "invocation"

    def descriptor(self):
        return {"key": self.key, "label": self.label, "category": self.key.split(".")[0],
                "outcome": self.key.split(".")[1], "phase": self.phase, "runtime_bound": True}


CALL_EVENTS = {
    "started": LifecycleEvent("capability.started", "When the call starts", EventType.CAPABILITY_STARTED),
    "succeeded": LifecycleEvent("capability.succeeded", "When the call returns successfully", EventType.CAPABILITY_SUCCEEDED),
    "failed": LifecycleEvent("capability.failed", "When the call fails", EventType.CAPABILITY_FAILED),
    "cancelled": LifecycleEvent("capability.cancelled", "When the call is cancelled", EventType.CAPABILITY_CANCELLED),
    "timed_out": LifecycleEvent("capability.timed_out", "When the call times out", EventType.CAPABILITY_TIMED_OUT),
}
WORK_EVENTS = {
    "running": LifecycleEvent("execution.started", "When assigned work starts", EventType.WORK_STARTED, "work"),
    "succeeded": LifecycleEvent("execution.succeeded", "When assigned work completes", EventType.WORK_SUCCEEDED, "work"),
    "failed": LifecycleEvent("execution.failed", "When assigned work fails", EventType.WORK_FAILED, "work"),
    "cancelled": LifecycleEvent("execution.cancelled", "When assigned work is cancelled", EventType.WORK_CANCELLED, "work"),
    "interrupted": LifecycleEvent("execution.interrupted", "When assigned work is interrupted", EventType.WORK_INTERRUPTED, "work"),
}
ACTION_EVENTS = {
    outcome: LifecycleEvent(event.key.replace("capability.", "operation."), event.label,
                            EventType(event.type.value.replace("capability_", "operation_")))
    for outcome, event in CALL_EVENTS.items()
}
RUN_EVENTS = {
    "created": LifecycleEvent("run.created", "When a Run is admitted", EventType.RUN_CREATED, "run"),
    "running": LifecycleEvent("run.started", "When a Run starts", EventType.RUN_STARTED, "run"),
    "waiting": LifecycleEvent("run.waiting", "When a Run waits", EventType.RUN_WAITING, "run"),
    "resumed": LifecycleEvent("run.resumed", "When a Run resumes", EventType.RUN_RESUMED, "run"),
    "succeeded": LifecycleEvent("run.completed", "When a Run completes", EventType.RUN_SUCCEEDED, "run"),
    "failed": LifecycleEvent("run.failed", "When a Run fails", EventType.RUN_FAILED, "run"),
    "cancelled": LifecycleEvent("run.cancelled", "When a Run is cancelled", EventType.RUN_CANCELLED, "run"),
    "interrupted": LifecycleEvent("run.interrupted", "When a Run is interrupted", EventType.RUN_INTERRUPTED, "run"),
}
STATE_EVENTS = {
    "entered": LifecycleEvent("state.entered", "On enter", EventType.STATE_UPDATED, "state"),
    "exited": LifecycleEvent("state.exited", "On exit", EventType.STATE_UPDATED, "state"),
    "current": LifecycleEvent("state.current", "While in state", EventType.STATE_UPDATED, "state"),
    "created": LifecycleEvent("state.created", "When a stored value is created", EventType.STATE_CREATED, "state"),
    "updated": LifecycleEvent("state.updated", "When a stored value changes", EventType.STATE_UPDATED, "state"),
    "deleted": LifecycleEvent("state.deleted", "When a stored value is deleted", EventType.STATE_DELETED, "state"),
}

AGENT_EVENTS = {key: LifecycleEvent(key, label, EventType.AGENT_ACTIVITY, "agent")
                for key, label in AGENT_ACTIVITY_EVENTS.items()}



def operation_label(definition):
    # Friendly metadata is optional; old and unknown plugins work unchanged.
    words = re.sub(r"[_\W]+", " ", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", definition.tool_name)).strip()
    return definition.label or words[:1].upper() + words[1:]


def action_label(action):
    words = re.sub(r"[_\W]+", " ", action).strip()
    return words[:1].upper() + words[1:]


def discover_event_sources(services, card_id):
    node = services.world.get_card(card_id)
    services.world.require_available_card(node)
    spec = services.plugins.node_type(node.type)
    operations, sources = [], []
    if "core.agent" in spec.traits:
        for operation in project_operations(services, card_id):
            for capability in operation.capabilities:
                metadata = {"kind": capability.kind, "tool_name": operation.definition.tool_name,
                            "operation_id": f"capability:{capability.kind}", "operation_kind": "capability",
                            "label": operation_label(operation.definition), "target_card_id": capability.target_id,
                            "target_name": capability.target_name, "target_type": capability.target_type}
                operations.append(metadata)
                sources.append({"id": f"capability:{capability.id}", "label": metadata["label"],
                                "operation_id": metadata["operation_id"], "kind": "capability",
                                "target_card_id": capability.target_id, "target_name": capability.target_name,
                                "capability": capability.kind, "default_event": "capability.succeeded",
                                "events": [event.descriptor() for event in CALL_EVENTS.values()]})

        sources.append({"id": "host:agent", "kind": "agent", "label": "Agent activity",
                        "target_card_id": None, "target_name": node.name, "capability": None,
                        "default_event": "agent.work_started",
                        "events": [event.descriptor() for event in AGENT_EVENTS.values()]})
        sources.append({"id": "host:run", "operation_id": "host:run", "kind": "run",
                        "label": "Agent Run", "target_card_id": node.id, "target_name": node.name,
                        "capability": None, "default_event": "run.completed",
                        "events": [event.descriptor() for event in RUN_EVENTS.values()]})

    for kind, actions in (("document", spec.document.actions if spec.document else {}),
                          ("resource", spec.resource_actions)):
        for action, definition in actions.items():
            operation_id = f"{kind}:{node.type}:{action}"
            metadata = {"kind": definition.capability_kind, "operation_id": operation_id,
                        "operation_kind": f"{kind}_action", "action": action,
                        "label": action_label(action), "target_card_id": node.id,
                        "target_name": node.name, "target_type": node.type, "tool_name": None}
            operations.append(metadata)
            sources.append({"id": operation_id, "operation_id": operation_id, "kind": f"{kind}_action",
                            "label": metadata["label"], "target_card_id": node.id, "target_name": node.name,
                            "capability": definition.capability_kind, "default_event": "operation.succeeded",
                            "events": [event.descriptor() for event in ACTION_EVENTS.values()]})

    # Work-source -> executor is a different contract from a tool grant. Read
    # the declaration, not a card type or a relationship naming convention.
    work_sources = {node.id: node} if spec.execution else {}
    if "core.agent" in spec.traits:
        for edge in services.world.list_edges_to(card_id):
            source = services.world.get_card(edge.source)
            if edge.missing_plugin or source.missing_plugin:
                continue
            execution = services.plugins.node_type(source.type).execution
            if execution and not execution.summoning and execution.executor_relationship == edge.relationship:
                work_sources[source.id] = source
    for source in work_sources.values():
        sources.append({"id": f"execution:{source.id}", "label": "Assigned work",
                        "target_card_id": source.id, "target_name": source.name, "capability": None,
                        "default_event": "execution.succeeded",
                        "events": [event.descriptor() for event in WORK_EVENTS.values()]})
    sources.append({"id": f"user-state:{node.id}", "kind": "user_state", "label": "User-defined state",
                    "target_card_id": node.id, "target_name": node.name, "capability": None,
                    "default_event": "state.entered", "events": [STATE_EVENTS["entered"].descriptor()]})
    if spec.state or spec.document:
        sources.append({"id": f"stored-state:{node.id}", "kind": "stored_state", "label": "Stored object values",
                        "target_card_id": node.id, "target_name": node.name, "capability": None,
                        "default_event": "state.updated",
                        "events": [STATE_EVENTS[key].descriptor() for key in ("created", "updated", "deleted")]})
    return operations, sources


def publish_operation_event(services, event):
    services.operation_events.record(event)
    services.events.publish_event_nowait(event)


class OperationInvocation:
    """One authorized call; identities only, never argument or result contents."""

    def __init__(self, services, *, operation_id, target_id, caller_id=None, capability=None,
                 tool_name=None, request_id=None, events=None):
        self.services, self.operation_id = services, operation_id
        self.target_id, self.caller_id = target_id, caller_id
        self.capability, self.tool_name = capability, tool_name
        self.events = events or ACTION_EVENTS
        # A nested call is a new invocation unless its caller explicitly binds
        # the same durable request. Repeated adapters must not share event IDs.
        self.id = request_id or str(uuid4())
        context = services.run_manager.current_context if services.run_manager else None
        conversation = context.caller.id if context and context.caller.kind == "conversation" else None
        if context:
            from backend.errors import NotFoundError
            try:
                record = services.run_manager.get_run(context.run_id)
            except NotFoundError:
                # Embedders may supply a provider-neutral invocation context
                # before admitting a durable Run. Correlation still applies.
                pass
            else:
                conversation = services.run_manager._conversation_scope(record)[0]
        self.scope = operation_context(self.id, caller_object_id=caller_id, target_object_id=target_id,
            run_id=context.run_id if context else None, context_id=context.context_id if context else None,
            parent_run_id=context.parent_run_id if context else None, root_run_id=context.root_run_id if context else None,
            task_id=context.task_id if context else None, conversation_id=conversation)

    def __enter__(self):
        self.context = self.scope.__enter__()
        try:
            self.publish("started")
        except BaseException as error:
            self.scope.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def __exit__(self, kind, error, traceback):
        try:
            if error is not None:
                self.failed(error)
        finally:
            self.scope.__exit__(kind, error, traceback)

    def publish(self, outcome):
        lifecycle = self.events[outcome]
        publish_operation_event(self.services, RuntimeEvent(
            id=str(uuid5(NAMESPACE_URL, f"invocation:{self.id}:{self.operation_id}:{outcome}")),
            type=lifecycle.type, node_id=self.target_id, agent_id=self.caller_id,
            run_id=self.context.run_id, session_id=self.context.context_id,
            conversation_id=self.context.conversation_id,
            payload={**self.context.payload(), "event": lifecycle.key, "phase": "invocation",
                     "operation_id": self.operation_id, "capability": self.capability,
                     "tool_name": self.tool_name, "target_card_id": self.target_id}))

    def completed(self, result):
        task = asyncio.current_task()
        self.publish("cancelled" if task and task.cancelling() else
                     "failed" if isinstance(result, Mapping) and result.get("ok") is False else "succeeded")

    def failed(self, error):
        task = asyncio.current_task()
        self.publish("cancelled" if isinstance(error, asyncio.CancelledError) or task and task.cancelling()
                     else "timed_out" if isinstance(error, TimeoutError) else "failed")


class CapabilityInvocation(OperationInvocation):
    def __init__(self, services, capability, *, request_id=None):
        super().__init__(services, operation_id=f"capability:{capability.kind}",
                         target_id=capability.target_id, caller_id=capability.agent_id,
                         capability=capability.kind, tool_name=capability.tool_name,
                         request_id=request_id, events=CALL_EVENTS)


def run_lifecycle_event(record, *, previous_status=None):
    lifecycle = RUN_EVENTS["resumed" if record.status == "running" and previous_status == "waiting" else record.status]
    operation = dict(record.lifecycle.get("operation") or {})
    if record.lifecycle.get("work_continuation"):
        operation["continuation_owner"] = "legacy"
    associations = list(operation.get("associations") or [])
    reference = {"kind": "run", "id": record.run_id, "object_id": record.agent_id, "produced": True}
    if reference not in associations:
        associations.append(reference)
    return RuntimeEvent(
        id=str(uuid5(NAMESPACE_URL, f"run:{record.run_id}:{lifecycle.key}:{record.updated_at.isoformat()}")),
        timestamp=record.updated_at, type=lifecycle.type, node_id=record.agent_id, agent_id=record.agent_id,
        run_id=record.run_id, session_id=record.context_id,
        conversation_id=operation.get("conversation_id") or (record.caller_id if record.caller_kind == "conversation" else None),
        payload={**operation, "event": lifecycle.key, "phase": "run", "operation_id": "host:run",
                 "target_card_id": record.agent_id, "run_id": record.run_id, "root_run_id": record.root_run_id,
                 "parent_run_id": record.parent_run_id, "task_id": record.task_id, "context_id": record.context_id,
                 "associations": associations, "run": record.model_dump(mode="json"),
                 **({"error": record.error} if record.error else {})})


def publish_work_outcome(services, node_id, outcome, *, operation=None, execution_id=None):
    lifecycle = WORK_EVENTS[outcome.status]
    record = services.run_manager.get_run(outcome.run_id) if outcome.run_id else None
    scope_type, scope_id = services.card_state.identity(node_id)
    operation = (dict(record.lifecycle.get("operation") or {}) if record else None) or operation or (
        active_operation.get().payload() if active_operation.get() else {})
    publish_operation_event(services, RuntimeEvent(
        id=str(uuid5(NAMESPACE_URL, f"work:{node_id}:{scope_type}:{scope_id}:{execution_id or outcome.run_id or operation.get('invocation_id') or uuid4()}:{outcome.item_id}:{outcome.status}")),
        type=lifecycle.type, node_id=node_id, agent_id=record.agent_id if record else None,
        run_id=outcome.run_id,
        session_id=scope_id if scope_type == "session" else (record.context_id if record else None) or operation.get("context_id"),
        conversation_id=operation.get("conversation_id") or (record.caller_id if record and record.caller_kind == "conversation" else None),
        payload={**operation, "event": lifecycle.key, "phase": "work", "target_card_id": node_id, "item_id": outcome.item_id,
                 "state_scope": scope_type, "state_scope_id": scope_id}))
