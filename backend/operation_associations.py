"""Invocation identities shared by capabilities, execution and host actions.

The context only collects identities during dispatch. Durable owners copy its
payload into their existing records; no live Python context owns later work.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OperationAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=128)
    id: str = Field(min_length=1, max_length=256)
    object_id: str | None = None
    produced: bool = False


@dataclass
class OperationContext:
    invocation_id: str
    parent_invocation_id: str | None = None
    caller_object_id: str | None = None
    target_object_id: str | None = None
    run_id: str | None = None
    parent_run_id: str | None = None
    root_run_id: str | None = None
    task_id: str | None = None
    context_id: str | None = None
    conversation_id: str | None = None
    continuation_owner: Literal["legacy", "state_machine"] | None = None
    associations: list[OperationAssociation] = field(default_factory=list)

    def associate(self, kind, id, *, object_id=None, produced=False):
        reference = OperationAssociation(kind=kind, id=id, object_id=object_id, produced=produced)
        if reference not in self.associations:
            self.associations.append(reference)
        return reference

    def payload(self):
        return {
            "invocation_id": self.invocation_id,
            "parent_invocation_id": self.parent_invocation_id,
            "caller_object_id": self.caller_object_id,
            "target_card_id": self.target_object_id,
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "root_run_id": self.root_run_id,
            "task_id": self.task_id,
            "context_id": self.context_id,
            "conversation_id": self.conversation_id,
            "continuation_owner": self.continuation_owner,
            "associations": [reference.model_dump(mode="json") for reference in self.associations],
        }


active_operation: ContextVar[OperationContext | None] = ContextVar("host_operation", default=None)


@contextmanager
def operation_context(request_id: str, **scope):
    """Bind one invocation; explicit identity reuse denotes the same dispatch."""
    parent = active_operation.get()
    if parent is not None and parent.invocation_id == request_id:
        yield parent
        return
    if parent is not None:
        scope.setdefault("parent_invocation_id", parent.invocation_id)
        for field_name in ("caller_object_id", "target_object_id", "run_id", "parent_run_id", "root_run_id",
                           "task_id", "context_id", "conversation_id", "continuation_owner"):
            if scope.get(field_name) is None:
                scope[field_name] = getattr(parent, field_name)
    context = OperationContext(request_id, **scope)
    token = active_operation.set(context)
    try:
        yield context
    finally:
        active_operation.reset(token)
        if parent is not None:
            if parent.continuation_owner is None:
                parent.continuation_owner = context.continuation_owner
            # An adapter's results also belong to its enclosing call, but a
            # sibling invocation must not inherit those produced objects.
            for reference in context.associations:
                parent.associate(reference.kind, reference.id,
                                 object_id=reference.object_id, produced=reference.produced)


def associate_execution(kind, id, *, object_id=None, produced=False):
    context = active_operation.get()
    if context is not None:
        return context.associate(kind, id, object_id=object_id, produced=produced)
    return None


def resolve_associated_objects(event, *, produced_only=False):
    """Resolve only explicit references from this invocation, never type/template."""
    objects = []
    for value in event.payload.get("associations", []):
        reference = OperationAssociation.model_validate(value)
        # Creating a Run against an existing Agent does not produce a new Agent.
        if produced_only and (reference.kind != "object" or not reference.produced):
            continue
        object_id = reference.object_id or (reference.id if reference.kind == "object" else None)
        if object_id and object_id not in objects:
            objects.append(object_id)
    return objects
