"""Host-owned persistence; plugins only receive copies of their node document."""
from __future__ import annotations
import json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from backend.errors import ResourceValidationError, PermissionDeniedError, RevisionConflictError
from backend.state import StateContext


class DocumentActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)


def validation_message(error):
    if isinstance(error, ValidationError):
        first = error.errors(include_url=False)[0]
        if first.get("ctx", {}).get("error"):
            return str(first["ctx"]["error"])
        location = ".".join(str(part) for part in first["loc"])
        return f"{location}: {first['msg']}" if location else first["msg"]
    return str(error)


def definition(services, node_id):
    node = services.world.get_card(node_id)
    result = services.plugins.node_type(node.type).document
    if result is None:
        raise ResourceValidationError("This node does not provide a document")
    return result


def read_document(services, node_id):
    spec = definition(services, node_id)
    scope = services.state.ensure_scope("node_document", node_id, schema_id="core.node_document")
    current = services.state.resolve(StateContext((scope,)), "document")
    try:
        value = spec.model.model_validate(current.value).model_dump(mode="json")
    except ValueError as error:
        raise ResourceValidationError("Stored document is incompatible with this plugin: " + validation_message(error)) from error
    container = services.plugins.node_type(services.world.get_card(node_id).type).container
    if container and container.document_field:
        from backend.node_containers import member_documents
        value[container.document_field] = member_documents(services, node_id)
    return {"value": value, "revision": current.revision, "summary": spec.summarize(value)}


def write_document(services, node_id, value, expected_revision, *, actor_id=None, run_id=None):
    spec = definition(services, node_id)
    try:
        value = spec.model.model_validate(value).model_dump(mode="json")
    except (ValidationError, ValueError) as exc:
        raise ResourceValidationError(validation_message(exc)) from exc
    if len(json.dumps(value).encode("utf-8")) > spec.max_size_bytes:
        raise ResourceValidationError(f"This document is limited to {spec.max_size_bytes // 1024} KiB")
    scope = services.state.ensure_scope("node_document", node_id, schema_id="core.node_document")
    node = services.world.get_card(node_id)
    container = services.plugins.node_type(node.type).container
    entries = value.get(container.document_field) if container and container.document_field else None
    if entries is not None:
        value = {**value, container.document_field: []}
    services.state.set(scope, "document", value, expected_revision=expected_revision, actor_id=actor_id, run_id=run_id)
    from backend.node_containers import sync_members, touch_parent
    if entries is not None:
        sync_members(services, node_id, entries)
    touch_parent(services, node.parent_id)
    return read_document(services, node_id)


async def invoke_document_action(services, node_id, action, request, *, capability=None):
    async with services._node_mutation():
        spec = definition(services, node_id)
        handler = spec.actions.get(action)
        if action == "replace" or (handler is not None and not handler.read_only):
            services.node_execution.assert_editable(node_id)
        if capability is not None:
            live = services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.target_id != node_id or handler is None or handler.capability_kind != live.kind:
                raise PermissionDeniedError("This connection does not allow that document action")
        elif action == "replace":
            if request.expected_revision is None:
                raise ResourceValidationError("expected_revision is required")
            return write_document(services, node_id, request.arguments, request.expected_revision)
        if handler is None:
            raise ResourceValidationError("Unknown document action")
        current = read_document(services, node_id)
        if not handler.read_only:
            if request.expected_revision is None:
                raise ResourceValidationError("Read the board first and supply expected_revision")
            if current["revision"] != request.expected_revision:
                raise RevisionConflictError("The board changed. Reload and retry your change.")
        try:
            value = handler.handler(current["value"], request.arguments)
        except (ValidationError, ValueError) as exc:
            raise ResourceValidationError(validation_message(exc)) from exc
        if handler.read_only:
            return current
        context = services.run_manager.current_context if services.run_manager else None
        return write_document(services, node_id, value, request.expected_revision,
            actor_id=capability.agent_id if capability else None, run_id=context.run_id if context else None)
