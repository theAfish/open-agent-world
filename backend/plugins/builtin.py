from __future__ import annotations

from backend.plugins.containers import NodeContainerDefinition

from dataclasses import dataclass
from contextlib import suppress
import logging
from typing import Any, Mapping

from backend.errors import PluginCompatibilityError, ResourceValidationError
from backend.sandbox.models import SandboxError
from backend.plugins.registry import (
    PLUGIN_API_VERSION,
    CapabilityGrantDefinition,
    CapabilityDefinition,
    NodeTypeDefinition,
    PackDefinition, PluginDescriptor,
    PluginRegistration,
    PluginRegistry,
    RelationshipDefinition,
)
from backend.plugins.lifecycle import (
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeLifecycleTransaction,
)
from backend.plugins.template import (
    NodeTemplateCaptureContext,
    NodeTemplateDependency,
    NodeTemplateHandler,
    NodeTemplateRestoreContext,
)
from backend.plugins.capability import CapabilityContext
from backend.world.models import (
    AgentConfig,
    Card,
    CardCreate,
    CardPatch,
    ConversationConfig,
    ImageConfig,
    LegionConfig,
    SandboxConfig,
    TextConfig,
)
from pydantic import BaseModel


class VirtualWorkspaceConfig(BaseModel):
    pass


class _LifecycleOperation(NodeLifecycleTransaction):
    def __init__(
        self,
        commit: Any,
        rollback: Any,
        *,
        finalize: Any | None = None,
        commit_before_node_ids: frozenset[str] = frozenset(),
        delete_recovery_payload: Mapping[str, Any] | None = None,
    ) -> None:
        self._commit = commit
        self._rollback = rollback
        self._finalize = finalize
        self.commit_before_node_ids = commit_before_node_ids
        self._delete_recovery_payload = dict(delete_recovery_payload or {})

    @property
    def has_delete_finalizer(self) -> bool:
        return self._finalize is not None

    @property
    def delete_recovery_payload(self) -> Mapping[str, Any]:
        return self._delete_recovery_payload

    async def commit(self) -> None:
        await self._commit()

    async def rollback(self, error: BaseException) -> None:
        await self._rollback(error)

    async def finalize(self) -> None:
        if self._finalize is not None:
            await self._finalize()


@dataclass(frozen=True, slots=True)
class LegionContainerDefinition(NodeContainerDefinition):
    parentable: bool = False
    connectable: bool = False
    min_size: tuple[int, int] = (800, 550)
    content_inset: tuple[int, int, int, int] = (320, 100, 24, 24)


class AgentNodeBehavior(NodeLifecycleHandler):
    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.agents is not None:
            await context.agents.create(node)
        if node.status != "idle":
            context.nodes.update_status(node.id, "idle")

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.agents is not None:
            with suppress(Exception):
                await context.agents.stop(node.id)

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del request

        async def commit() -> None:
            if context.agents is not None:
                await context.agents.create(node)

        async def rollback(error: BaseException) -> None:
            del error
            if context.agents is not None:
                await context.agents.delete(node.id, missing_ok=True)

        return _LifecycleOperation(commit, rollback)

    async def prepare_update(
        self,
        context: NodeLifecycleContext,
        current: Card,
        updated: Card,
        request: CardPatch,
    ) -> NodeLifecycleTransaction:
        changed = request.name is not None or request.config is not None

        async def commit() -> None:
            if context.agents is not None and changed:
                await context.agents.update(updated)

        async def rollback(error: BaseException) -> None:
            del error
            if context.agents is not None and changed:
                await context.agents.update(current)

        return _LifecycleOperation(commit, rollback)

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        provider_id = (
            context.agents.provider_id(node) if context.agents is not None else None
        )

        async def commit() -> None:
            # Run cancellation is irreversible: RunStatus has no transition
            # out of CANCELLED and providers expose stop, not pause/resume.
            # Reserve admission but leave live Runs untouched until the graph
            # deletion commits.
            if context.agents is not None:
                await context.agents.reserve_delete(node.id)

        async def rollback(error: BaseException) -> None:
            del error
            if context.agents is not None:
                context.agents.release_delete(node.id)

        async def finalize() -> None:
            if context.agents is not None:
                await context.agents.delete(node.id, missing_ok=True)
                context.agents.release_delete(node.id)

        return _LifecycleOperation(
            commit,
            rollback,
            finalize=finalize,
            delete_recovery_payload={"runtime_provider_id": provider_id},
        )

    async def prepare_delete_recovery(
        self,
        context: NodeLifecycleContext,
        node: Card,
        *,
        plugin_version: str,
        payload: Mapping[str, Any],
    ) -> NodeLifecycleTransaction:
        del plugin_version
        if set(payload) != {"runtime_provider_id"}:
            raise PluginCompatibilityError("invalid Agent deletion cleanup payload")
        raw_provider_id = payload["runtime_provider_id"]
        if raw_provider_id is not None and not isinstance(raw_provider_id, str):
            raise PluginCompatibilityError("invalid Agent runtime provider cleanup id")

        async def commit() -> None:
            pass

        async def rollback(error: BaseException) -> None:
            del error
            if context.agents is not None:
                context.agents.release_delete(node.id)

        async def finalize() -> None:
            if context.agents is not None:
                await context.agents.delete(
                    node.id,
                    missing_ok=True,
                    provider_id=raw_provider_id,
                )
                context.agents.release_delete(node.id)

        return _LifecycleOperation(
            commit,
            rollback,
            finalize=finalize,
            delete_recovery_payload=payload,
        )


class SandboxNodeBehavior(NodeLifecycleHandler):
    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.sandboxes is None:
            return
        try:
            status = await context.sandboxes.ensure(node.id)
            await context.sandboxes.configure(node.id, node.config)
        except SandboxError:
            # Invalid persisted state or missing host resources disable this
            # card, not the whole app. Keep the binding for diagnosis/recovery.
            logging.getLogger(__name__).exception("Sandbox %s could not restore startup settings", node.id)
            status = "error"
        if node.status != status:
            context.nodes.update_status(node.id, status)

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.sandboxes is not None:
            try:
                await context.sandboxes.terminate(node.id, missing_ok=True)
            except SandboxError:
                # Keep the runtime's persisted cleanup obligation, and allow
                # the other nodes/providers to receive their shutdown callback.
                logging.getLogger(__name__).exception("Sandbox %s could not finish shutdown cleanup", node.id)

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del request

        async def commit() -> None:
            if context.sandboxes is not None:
                await context.sandboxes.create(node.id)
                await context.sandboxes.configure(node.id, node.config)

        async def rollback(error: BaseException) -> None:
            del error
            if context.sandboxes is not None:
                await context.sandboxes.destroy(node.id, missing_ok=True)

        return _LifecycleOperation(commit, rollback)

    async def prepare_update(
        self, context: NodeLifecycleContext, current: Card,
        updated: Card, request: CardPatch,
    ) -> NodeLifecycleTransaction:
        del request
        keys = ("runtime", "workspace_path", "workspace_access", "network_enabled", "memory_bytes", "active_process_limit", "command_timeout")
        if all(current.config.get(key) == updated.config.get(key) for key in keys):
            return NodeLifecycleTransaction()
        configured = False

        async def commit() -> None:
            nonlocal configured
            if context.sandboxes is not None:
                await context.sandboxes.configure(current.id, updated.config)
                configured = True

        async def rollback(error: BaseException) -> None:
            del error
            if configured and context.sandboxes is not None:
                await context.sandboxes.configure(current.id, current.config)

        return _LifecycleOperation(commit, rollback)

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        async def commit() -> None:
            # Native Sandbox commands cannot be suspended and resumed safely.
            # Keep the reversible phase side-effect free; ``destroy`` performs
            # the irreversible process-tree termination only after the world
            # graph deletion has committed.
            pass

        async def rollback(error: BaseException) -> None:
            del error

        async def finalize() -> None:
            if context.sandboxes is not None:
                await context.sandboxes.destroy(node.id, missing_ok=True)

        return _LifecycleOperation(
            commit,
            rollback,
            finalize=finalize,
            delete_recovery_payload={
                "sandbox_backend_required": context.sandboxes is not None,
                # Keep the existing payload key so journals written by the
                # earlier terminate-before-commit implementation remain
                # recoverable. New deletions never need a compensating start.
                "restart_after_rollback": False,
            },
        )

    async def prepare_delete_recovery(
        self,
        context: NodeLifecycleContext,
        node: Card,
        *,
        plugin_version: str,
        payload: Mapping[str, Any],
    ) -> NodeLifecycleTransaction:
        del plugin_version
        if set(payload) != {
            "sandbox_backend_required",
            "restart_after_rollback",
        } or not all(isinstance(payload[key], bool) for key in payload):
            raise PluginCompatibilityError("invalid Sandbox deletion cleanup payload")
        if payload["sandbox_backend_required"] and context.sandboxes is None:
            raise PluginCompatibilityError(
                "Sandbox deletion cleanup requires the configured sandbox backend"
            )
        restart_after_rollback = bool(payload["restart_after_rollback"])

        async def commit() -> None:
            pass

        async def rollback(error: BaseException) -> None:
            del error
            if context.sandboxes is not None and restart_after_rollback:
                await context.sandboxes.start(node.id)

        async def finalize() -> None:
            if context.sandboxes is not None:
                await context.sandboxes.destroy(node.id, missing_ok=True)

        return _LifecycleOperation(
            commit,
            rollback,
            finalize=finalize,
            delete_recovery_payload=payload,
        )


class ConversationNodeBehavior(NodeLifecycleHandler):
    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        del request

        async def commit() -> None:
            context.conversations.create_initial_session(node.id, "General")

        async def rollback(error: BaseException) -> None:
            del error
            context.conversations.delete_session_state(node.id)

        return _LifecycleOperation(commit, rollback)

class ManagedResourceNodeBehavior(NodeLifecycleHandler):
    _mount_relationships = frozenset({"mount_read_only", "mount_read_write"})

    async def _prepare_create(
        self, context: NodeLifecycleContext, node: Card, create: Any
    ) -> NodeLifecycleTransaction:
        async def commit() -> None:
            create()

        async def rollback(error: BaseException) -> None:
            del error
            context.resources.remove_file(node.id)

        return _LifecycleOperation(commit, rollback)

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        edges = tuple(context.nodes.list_edges_from(node.id))
        removal = context.resources.prepare_file_removal(node.id)

        async def commit() -> None:
            # Detaching a mount may terminate an active Sandbox command so the
            # host can revoke its open handles. That work is irreversible and
            # must wait until the authoritative graph deletion commits.
            pass

        async def rollback(error: BaseException) -> None:
            del error

        async def finalize() -> None:
            if context.sandboxes is not None:
                for edge in edges:
                    if edge.relationship in self._mount_relationships:
                        await context.sandboxes.detach_resource(
                            edge.target, node.id, missing_ok=True
                        )
            if removal is not None:
                removal.commit()

        return _LifecycleOperation(
            commit,
            rollback,
            finalize=finalize,
            commit_before_node_ids=frozenset(
                edge.target
                for edge in edges
                if edge.relationship in self._mount_relationships
            ),
            delete_recovery_payload={
                "sandbox_backend_required": (
                    context.sandboxes is not None
                    and any(
                        edge.relationship in self._mount_relationships
                        for edge in edges
                    )
                )
            },
        )

    async def prepare_delete_recovery(
        self,
        context: NodeLifecycleContext,
        node: Card,
        *,
        plugin_version: str,
        payload: Mapping[str, Any],
    ) -> NodeLifecycleTransaction:
        del plugin_version
        if set(payload) != {"sandbox_backend_required"} or not isinstance(
            payload["sandbox_backend_required"], bool
        ):
            raise PluginCompatibilityError(
                "invalid managed-resource deletion cleanup payload"
            )
        if payload["sandbox_backend_required"] and context.sandboxes is None:
            raise PluginCompatibilityError(
                "managed-resource deletion cleanup requires the configured sandbox backend"
            )
        return await self.prepare_delete(context, node)


class TextNodeBehavior(ManagedResourceNodeBehavior):
    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        filename = str(node.config.get("filename", "untitled.txt"))
        initial_content = request.content
        if initial_content is None:
            configured_content = request.config.get("content", "")
            initial_content = configured_content if isinstance(configured_content, str) else ""
        return await self._prepare_create(
            context,
            node,
            lambda: context.resources.create_text(node.id, filename, initial_content),
        )


class ImageNodeBehavior(ManagedResourceNodeBehavior):
    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        def create() -> None:
            if request.data_base64 is not None:
                filename = str(node.config.get("filename", "image.png"))
                context.resources.create_image(
                    node.id, filename, request.media_type or "", request.data_base64
                )

        return await self._prepare_create(context, node, create)


class _CoreConfigProjection:
    portable_config_fields: frozenset[str]

    def capture_config(self, node: Card) -> dict[str, Any]:
        return {
            key: value
            for key, value in node.config.items()
            if key in self.portable_config_fields
        }


class AgentNodeTemplateHandler(_CoreConfigProjection, NodeTemplateHandler):
    portable_config_fields = frozenset({
        "description",
        "system_instruction",
        "model",
        "status",
        "runtime_provider_id",
        "max_concurrent_runs",
    })

    def dependencies(
        self, config: Mapping[str, Any]
    ) -> tuple[NodeTemplateDependency, ...]:
        provider_id = config.get("runtime_provider_id")
        if not isinstance(provider_id, str) or not provider_id:
            return ()
        return (NodeTemplateDependency("runtime_provider", provider_id),)


class SandboxNodeTemplateHandler(_CoreConfigProjection, NodeTemplateHandler):
    portable_config_fields = frozenset({"status", "runtime"})


class TextNodeTemplateHandler(_CoreConfigProjection, NodeTemplateHandler):
    portable_config_fields = frozenset({"filename", "status"})

    def validate_payload(
        self, payload: Mapping[str, Any], payload_version: int
    ) -> None:
        super().validate_payload(payload, payload_version)
        content = payload.get("content")
        if set(payload) != {"content"} or not isinstance(content, str):
            raise PluginCompatibilityError("invalid text template payload")

    async def capture(
        self,
        context: NodeTemplateCaptureContext,
        node: Card,
        node_keys: Mapping[str, str],
    ) -> dict[str, Any]:
        del node_keys
        return {"content": context.resources.read_text(node.id)}

    async def prepare_restore(
        self,
        context: NodeTemplateRestoreContext,
        node: Card,
        payload: Mapping[str, Any],
        payload_version: int,
        node_ids: Mapping[str, str],
    ) -> NodeLifecycleTransaction:
        del node_ids
        self.validate_payload(payload, payload_version)
        content = payload.get("content")
        assert isinstance(content, str)

        async def commit() -> None:
            context.resources.replace_text(node.id, content)

        async def rollback(error: BaseException) -> None:
            del error
            with suppress(Exception):
                context.resources.replace_text(node.id, "")

        return _LifecycleOperation(commit, rollback)


class ImageNodeTemplateHandler(_CoreConfigProjection, NodeTemplateHandler):
    portable_config_fields = frozenset({"filename", "status"})

    def validate_payload(
        self, payload: Mapping[str, Any], payload_version: int
    ) -> None:
        super().validate_payload(payload, payload_version)
        if set(payload) != {"resource"}:
            raise PluginCompatibilityError("invalid image template payload")
        resource = payload.get("resource")
        if resource is None:
            return
        if not isinstance(resource, dict) or set(resource) != {
            "filename",
            "media_type",
            "data_base64",
        }:
            raise PluginCompatibilityError("invalid image template payload")
        if not all(isinstance(resource[key], str) for key in resource):
            raise PluginCompatibilityError("invalid image template payload")

    async def capture(
        self,
        context: NodeTemplateCaptureContext,
        node: Card,
        node_keys: Mapping[str, str],
    ) -> dict[str, Any]:
        del node_keys
        binary = context.resources.read_binary(node.id)
        if binary is None:
            return {"resource": None}
        return {
            "resource": {
                "filename": binary.filename,
                "media_type": binary.media_type,
                "data_base64": binary.data_base64,
            }
        }

    async def prepare_restore(
        self,
        context: NodeTemplateRestoreContext,
        node: Card,
        payload: Mapping[str, Any],
        payload_version: int,
        node_ids: Mapping[str, str],
    ) -> NodeLifecycleTransaction:
        del node_ids
        self.validate_payload(payload, payload_version)
        resource = payload.get("resource")
        if resource is None:
            return NodeLifecycleTransaction()
        assert isinstance(resource, dict)

        async def commit() -> None:
            context.resources.create_image(
                node.id,
                resource["filename"],
                resource["media_type"],
                resource["data_base64"],
            )

        async def rollback(error: BaseException) -> None:
            del error
            context.resources.remove_file(node.id)

        return _LifecycleOperation(commit, rollback)


async def _communicate(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    message = values.get("message")
    if set(values) != {"message"} or not isinstance(message, str) or not message.strip():
        raise ResourceValidationError(
            "agent communication capability requires one non-empty message"
        )
    return await context.communicate(
        capability.agent_id, capability.target_id, message
    )


async def _request_conversation_turn(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    if set(values) != {"agent_id", "message"}:
        raise ResourceValidationError(
            "conversation turn capability requires agent_id and message"
        )
    if not all(isinstance(values[key], str) and values[key].strip() for key in values):
        raise ResourceValidationError("conversation turn arguments must be non-empty strings")
    return await context.request_conversation_turn(
        capability.agent_id,
        capability.target_id,
        values["agent_id"],
        values["message"],
    )


async def _send_conversation_message(context, capability, arguments):
    return await context.send_conversation_message(capability, arguments)


async def _read_text(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    if values:
        raise ResourceValidationError("text read capability takes no arguments")
    return context.read_text(capability.agent_id, capability.target_id)


async def _edit_text(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    if set(values) != {"content"} or not isinstance(values["content"], str):
        raise ResourceValidationError(
            "text edit capability requires one string content argument"
        )
    return await context.replace_text(
        capability.agent_id, capability.target_id, values["content"]
    )


async def _view_image(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    if values:
        raise ResourceValidationError("image view capability takes no arguments")
    return context.view_image(capability.agent_id, capability.target_id)


async def _copy_skill_resource(context, capability, arguments):
    return await context.copy_skill_resource(capability.agent_id, capability.target_id, arguments)


async def _run_skill_script(context, capability, arguments):
    return await context.run_skill_script(capability.agent_id, capability.target_id, arguments)


async def _legion_state(context, capability, arguments):
    return await context.legion_state_action(capability, arguments)


async def _inspect_sandbox(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    if values:
        raise ResourceValidationError("sandbox inspect takes no arguments")
    return await context.inspect_sandbox(capability.agent_id, capability.target_id)


async def _start_sandbox(context, capability, values):
    if values:
        raise ResourceValidationError("sandbox start takes no arguments")
    return await context.start_sandbox(capability.agent_id, capability.target_id)


async def _stop_sandbox(context, capability, values):
    if values:
        raise ResourceValidationError("sandbox stop takes no arguments")
    return await context.stop_sandbox(capability.agent_id, capability.target_id)


async def _execute_sandbox(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    argv = values.get("argv")
    if (
        set(values) - {"argv", "environment_id", "target_id", "timeout_seconds"}
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise ResourceValidationError(
            "sandbox execute capability requires a non-empty argv array"
        )
    return await context.execute_sandbox(
        capability.agent_id, capability.target_id, argv,
        **{key: values[key] for key in ("environment_id", "target_id", "timeout_seconds") if key in values}
    )


async def _cancel_sandbox_command(context, capability, values):
    command_id = values.get("command_id")
    if set(values) != {"command_id"} or not isinstance(command_id, str) or not command_id:
        raise ResourceValidationError("command_id is required; inspect the Sandbox first")
    return await context.cancel_sandbox_command(capability.agent_id, capability.target_id, command_id)


async def _install_python_packages(context, capability, values):
    return await context.install_python_packages(capability.agent_id, capability.target_id, values.get("requirements"))


def _register_builtin(registry: PluginRegistration) -> None:
    from backend.execution_config import register_execution_configuration, EXECUTION_SELECTORS, EnvironmentProfile
    from backend.plugins.documents import NodeDocumentDefinition
    register_execution_configuration(registry)
    for operation in ("read", "patch"):
        registry.register_capability(CapabilityDefinition(
            kind=f"legion.state.{operation}", tool_name=f"{operation}_legion_state", target_parameter="legion",
            description=("Read shared team state and revision." if operation == "read" else
                         "Merge top-level keys into shared team state. Read first and supply the revision; refresh on conflict."),
            input_schema={"type": "object", "properties": {} if operation == "read" else {
                "value": {"type": "object", "description": "Top-level state keys to merge."},
                "expected_revision": {"type": "integer", "description": "Revision returned by read_legion_state."},
            }, "required": [] if operation == "read" else ["value", "expected_revision"], "additionalProperties": False},
        ), _legion_state)
    from backend.agents import GoogleAdkAgentRuntime, MockAgentRuntime
    from backend.state import MergePolicy, StateFieldDefinition, StateSchema

    def common_fields(scope_kind: str) -> dict[str, StateFieldDefinition]:
        allowed = frozenset({scope_kind})
        return {
            "workspace": StateFieldDefinition(
                value_type=Any, allowed_scope_kinds=allowed
            ),
            "shared_working_memory": StateFieldDefinition(
                value_type=Any, allowed_scope_kinds=allowed
            ),
            "observations": StateFieldDefinition(
                value_type=list[Any],
                allowed_scope_kinds=allowed,
                merge_policy=MergePolicy.APPEND,
            ),
            "artifacts": StateFieldDefinition(
                value_type=list[Any],
                allowed_scope_kinds=allowed,
                merge_policy=MergePolicy.APPEND_UNIQUE,
            ),
        }

    registry.register_state_schema(StateSchema(id="core.world", fields={
        **common_fields("world"),
    }))
    registry.register_state_schema(StateSchema(id="core.node_document", fields={
        "document": StateFieldDefinition(value_type=dict[str, Any], allowed_scope_kinds=frozenset({"node_document"}), default={}),
        "execution": StateFieldDefinition(value_type=dict[str, Any], allowed_scope_kinds=frozenset({"node_document"}), default={}),
    }))
    registry.register_state_schema(StateSchema(id="core.legion", fields={
        "shared_working_memory": StateFieldDefinition(
            value_type=dict[str, Any], allowed_scope_kinds=frozenset({"legion"}),
            merge_policy=MergePolicy.MERGE_DICT, default={},
        ),
    }))
    registry.register_state_schema(StateSchema(id="core.agent", fields={
        **common_fields("agent"),
        "memory": StateFieldDefinition(
            value_type=Any, allowed_scope_kinds=frozenset({"agent"})
        ),
    }))
    registry.register_state_schema(StateSchema(id="core.session", fields={
        **common_fields("session"),
    }))
    run_only = frozenset({"run"})
    registry.register_state_schema(StateSchema(id="core.run", fields={
        **common_fields("run"),
        "legion_context": StateFieldDefinition(value_type=dict[str, Any], allowed_scope_kinds=run_only),
        "input": StateFieldDefinition(
            value_type=str, allowed_scope_kinds=run_only
        ),
        "progress": StateFieldDefinition(
            value_type=float, allowed_scope_kinds=run_only
        ),
        "current_step": StateFieldDefinition(
            value_type=Any, allowed_scope_kinds=run_only
        ),
        "scratch": StateFieldDefinition(
            value_type=dict[str, Any],
            allowed_scope_kinds=run_only,
            merge_policy=MergePolicy.MERGE_DICT,
        ),
        "intermediate_results": StateFieldDefinition(
            value_type=list[Any],
            allowed_scope_kinds=run_only,
            merge_policy=MergePolicy.APPEND,
        ),
        "result": StateFieldDefinition(
            value_type=Any, allowed_scope_kinds=run_only
        ),
        "output_message_id": StateFieldDefinition(value_type=str, allowed_scope_kinds=run_only, default=""),
        "output_text": StateFieldDefinition(value_type=str, allowed_scope_kinds=run_only, default=""),
    }))
    registry.register_runtime_provider(
        "google.adk",
        lambda capability_provider, **options: GoogleAdkAgentRuntime(
            capability_provider, **options
        ),
    )
    registry.register_runtime_provider(
        "core.mock",
        lambda capability_provider, **options: MockAgentRuntime(
            capability_provider, **options
        ),
    )
    from backend.skill_runtime import SKILL_SELECTOR, skill_script_schema
    from backend.resources.artifact_capabilities import register as register_artifacts
    register_artifacts(registry)
    from backend.conversations.models import ConversationPost
    message_schema = ConversationPost.model_json_schema()
    for key in ('message_id', 'mention_agent_ids'):
        message_schema['properties'].pop(key, None)
    registry.register_capability(CapabilityDefinition(
        kind='conversation.send_message', tool_name='send_conversation_message', target_parameter='conversation',
        description='Send text and/or published file attachments to the current session. Publish Sandbox files to this conversation first; attach version_id and path. Images are clickable previews.',
        input_schema=message_schema), _send_conversation_message)
    registry.register_capability(CapabilityDefinition(
        kind='agent.communicate', tool_name='send_message', target_parameter='target',
        description='Send a message to the selected Agent and receive its response.',
        input_schema={"type": "object", "properties": {"message": {"type": "string", "description": "Message or question for the connected agent."}}, "required": ["message"], "additionalProperties": False}), _communicate)
    registry.register_capability(CapabilityDefinition(
        kind='conversation.request_turn', tool_name='request_conversation_turn', target_parameter='conversation',
        description="Ask a different participant to speak in the selected conversation. The current conversation session is supplied automatically; provide another participant's agent id. Never use your own agent id; if no other participant is available, answer directly without this tool.",
        input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "Agent id of a different participant to address. "
                            "This must never be your own agent id."
                        ),
                    },
                    "message": {"type": "string", "description": "Message or question for that participant."},
                },
                "required": ["agent_id", "message"],
                "additionalProperties": False,
            }), _request_conversation_turn)
    registry.register_capability(CapabilityDefinition(
        kind='text.read', tool_name='read_text', target_parameter='target',
        description='Read the selected managed text resource.',
        input_schema={"type": "object", "properties": {}, "additionalProperties": False}), _read_text)
    registry.register_capability(CapabilityDefinition(
        kind='text.edit', tool_name='edit_text', target_parameter='target',
        description='Replace the contents of the selected target.',
        input_schema={"type": "object", "properties": {"content": {"type": "string", "description": "Complete replacement text for this resource."}}, "required": ["content"], "additionalProperties": False}), _edit_text)
    registry.register_capability(CapabilityDefinition(
        kind='image.view', tool_name='view_image', target_parameter='target',
        description='Inspect the selected managed image.',
        input_schema={"type": "object", "properties": {}, "additionalProperties": False}), _view_image)
    for operation, handler in (("start", _start_sandbox), ("stop", _stop_sandbox)):
        registry.register_capability(CapabilityDefinition(
            kind=f"sandbox.{operation}", tool_name=f"{operation}_sandbox", target_parameter="sandbox",
            description=("Start the selected Sandbox using its saved runtime, workspace and network settings. Inspect it before executing commands."
                         if operation == "start" else "Stop the selected Sandbox, terminating any active command and waiting for cleanup. This affects every agent sharing this Sandbox."),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False}), handler)
    registry.register_capability(CapabilityDefinition(
        kind='sandbox.execute', tool_name='execute_command', target_parameter='sandbox',
        selectors=EXECUTION_SELECTORS,
        description='Execute an argv command in the selected sandbox. First inspect its runtime shell, cwd and resource paths. The configured working folder is live; edits there change real files. Attached resources are available through SANDBOX_RESOURCES. Calls use fresh non-interactive processes: cd/export/venv activation do not carry over. For installations set timeout_seconds explicitly and keep progress visible; do not pipe installers to tail. Shell pipelines report the final command status: use bash -o pipefail or download with curl -f to a file and only execute it after success. Use install_python_packages for shared Python dependencies.',
        input_schema={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Executable and arguments as a non-empty string array; argv[0] cannot be a shell built-in."}, "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600, "description": "Command wall-clock budget in seconds. Omit to use Sandbox settings; set explicitly for slow installs."}}, "required": ["argv"], "additionalProperties": False}), _execute_sandbox)
    registry.register_capability(CapabilityDefinition(
        kind='sandbox.cancel_command', tool_name='cancel_command', target_parameter='sandbox',
        description='Cancel the current Sandbox command and wait for process cleanup. First inspect the Sandbox and supply its current_command_id. A stale ID cannot cancel a newer command.',
        input_schema={"type": "object", "properties": {"command_id": {"type": "string", "minLength": 1}}, "required": ["command_id"], "additionalProperties": False}), _cancel_sandbox_command)
    registry.register_capability(CapabilityDefinition(
        kind='sandbox.install_python_packages', tool_name='install_python_packages', target_parameter='sandbox',
        description='Install missing Python packages into the persistent shared sandbox Python environment, then retry execution. Packages become available to all sandboxes on this execution platform. Supply index package names with optional extras/version constraints. Installation is serialized by the environment manager; source builds, paths and URLs are unsupported.',
        input_schema={"type": "object", "properties": {"requirements": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100}}, "required": ["requirements"], "additionalProperties": False}), _install_python_packages)
    registry.register_capability(CapabilityDefinition(
        kind='sandbox.run_skill_script', tool_name='run_skill_script', target_parameter='sandbox',
        description='Run a file from the selected Skill in the selected sandbox. Both resources require independent live authorization. The current bundle is mounted read-only outside the workspace; cwd and generated outputs use the sandbox workspace.',
        input_schema=skill_script_schema(), selectors=(SKILL_SELECTOR, *EXECUTION_SELECTORS), target_capabilities=frozenset({"sandbox.execute"})), _run_skill_script)
    registry.register_capability(CapabilityDefinition(
        kind='sandbox.inspect', tool_name='inspect_sandbox', target_parameter='sandbox',
        description='Inspect the selected sandbox before executing: returns its operating system, shell argv prefix, cwd, read/write access, resource directory and availability.',
        input_schema={"type": "object", "properties": {}, "additionalProperties": False}), _inspect_sandbox)
    registry.register_node_type(NodeTypeDefinition(
        id="agent", label="Agent", description="Reasoning worker", icon="bot",
        color="#75736c", deck_id="agents", deck_label="Agents", deck_icon="bot",
        default_name="New Agent", default_size=(300, 190), default_status="idle",
        statuses=frozenset({"idle", "running", "waiting", "error"}),
        config_model=AgentConfig, traits=frozenset({"core.agent"}),
        surfaces={"preview": True, "inspector": True, "workspace": True},
        lifecycle=AgentNodeBehavior(),
        templateable=True, template_status="idle",
        template_handler=AgentNodeTemplateHandler(),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="core.virtual-workspace", label="Virtual workspace", description="Temporary space for generated cards",
        icon="boxes", color="#697c78", deck_id="fields", deck_label="Fields", deck_icon="workflow",
        default_name="Workspace", default_size=(800, 500), default_status="available",
        statuses=frozenset({"available"}), config_model=VirtualWorkspaceConfig,
        container=NodeContainerDefinition(virtual=True), user_creatable=False,
    ))
    registry.register_relationship(RelationshipDefinition(
        id="core.generated", label="Generated workspace", short_label="generated",
        description="Created by an Agent or tool; retained until its workspace is reclaimed.",
        target_types=frozenset({"core.virtual-workspace"}), generated=True,
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="legion", label="Legion", description="Team space with shared context and settings",
        icon="workflow", color="#697c78", deck_id="fields", deck_label="Fields",
        deck_icon="workflow", default_name="New Legion", default_size=(1100, 700),
        default_status="available", statuses=frozenset({"available"}),
        config_model=LegionConfig, traits=frozenset({"core.legion", "ui.legion.v1"}),
        container=LegionContainerDefinition(),
        user_creatable=False, templateable=True,
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="conversation", label="Conversation", description="Shared communication field",
        icon="messages-square", color="#9a6954", deck_id="fields",
        deck_label="Fields", deck_icon="workflow", default_name="New Conversation",
        default_size=(320, 210), default_status="available",
        statuses=frozenset({"available"}), config_model=ConversationConfig,
        traits=frozenset({"core.field", "core.conversation", "core.file-source"}),
        surfaces={"preview": True, "inspector": True, "workspace": True},
        lifecycle=ConversationNodeBehavior(),
        templateable=True,
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="text", label="Text file", description="Managed knowledge", icon="file-text",
        color="#7c7267", deck_id="objects", deck_label="Objects", deck_icon="boxes",
        default_name="Untitled Text", default_size=(300, 220), default_status="available",
        statuses=frozenset({"available", "modified"}), config_model=TextConfig,
        traits=frozenset({"core.resource", "core.text"}),
        creation_fields=frozenset({"content"}),
        lifecycle=TextNodeBehavior(),
        templateable=True, template_handler=TextNodeTemplateHandler(),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="image", label="Image file", description="Visual resource", icon="image",
        color="#8a7560", deck_id="objects", deck_label="Objects", deck_icon="boxes",
        default_name="Untitled Image", default_size=(280, 240), default_status="available",
        statuses=frozenset({"available", "modified"}), config_model=ImageConfig,
        traits=frozenset({"core.resource", "core.image"}),
        creation_fields=frozenset({"data_base64"}),
        lifecycle=ImageNodeBehavior(),
        templateable=True, template_handler=ImageNodeTemplateHandler(),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="sandbox", label="Sandbox", description="Secure work field", icon="workflow",
        color="#696c66", deck_id="fields", deck_label="Fields", deck_icon="workflow",
        default_name="New Sandbox", default_size=(340, 220), default_status="stopped",
        statuses=frozenset({"stopped", "ready", "running", "error"}),
        config_model=SandboxConfig, traits=frozenset({"core.sandbox", "core.file-source"}),
        document=NodeDocumentDefinition(model=EnvironmentProfile),
        surfaces={"preview": True, "inspector": True, "workspace": True},
        lifecycle=SandboxNodeBehavior(),
        templateable=True, template_status="stopped",
        template_handler=SandboxNodeTemplateHandler(),
    ))

    registry.register_relationship(RelationshipDefinition(
        id="communicate", label="Communicate", short_label="message",
        description="The agent can send a scoped message to this agent and receive its response.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.agent"}),
        directions=frozenset({"forward", "bidirectional"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(kind='agent.communicate'),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="participate", label="Participate", short_label="join",
        description="The agent can join sessions and speak inside this Conversation field.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"core.conversation"}),
        templateable=True,
        capabilities=tuple(CapabilityGrantDefinition(kind=kind) for kind in (
            'conversation.request_turn', 'conversation.send_message', 'artifact.read', 'artifact.publish', 'artifact.materialize')),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="conversation_notes", label="Meeting notes", short_label="notes",
        description="Attach meeting notes. Participating agents can read and edit this text; disconnect to revoke access.",
        source_traits=frozenset({"core.conversation"}), target_traits=frozenset({"core.text"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(kind='text.read'), CapabilityGrantDefinition(kind='text.edit')),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="read", label="Read", short_label="read",
        description="The agent can inspect this text through a scoped tool.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.text"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(kind='text.read'),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="read_edit", label="Read + edit", short_label="read + edit",
        description="The agent can inspect and modify this text through scoped tools.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.text"}),
        templateable=True,
        capabilities=(
            CapabilityGrantDefinition(kind='text.read'),
            CapabilityGrantDefinition(kind='text.edit'),
        ),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="view", label="View", short_label="view",
        description="The agent can inspect the image content.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.image"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(kind='image.view'),),
    ))
    registry.register_capability(CapabilityDefinition(kind="sandbox.copy_skill_resource", tool_name="copy_skill_resource",
        description="Explicitly copy an authorized Skill resource to the writable workspace. Existing files require overwrite=true.",
        target_parameter="sandbox", selectors=(SKILL_SELECTOR,), target_capabilities=frozenset({"sandbox.execute"}),
        input_schema={"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}}, "required": ["source", "destination"], "additionalProperties": False}), _copy_skill_resource)
    registry.register_relationship(RelationshipDefinition(
        id="execute", label="Execute", short_label="execute",
        description="The agent can run commands in this isolated workplace. Starting and stopping require manual control.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.sandbox"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(kind='sandbox.execute'), CapabilityGrantDefinition(kind='sandbox.cancel_command'), CapabilityGrantDefinition(kind='sandbox.install_python_packages'), CapabilityGrantDefinition(kind='sandbox.run_skill_script'), CapabilityGrantDefinition(kind='sandbox.inspect'), CapabilityGrantDefinition(kind='sandbox.copy_skill_resource')),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="execute_manage", label="Execute + Start/Stop", short_label="execute + manage",
        description="The agent can run commands, start this Sandbox and stop it, including active commands.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.sandbox"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(kind='sandbox.start'), CapabilityGrantDefinition(kind='sandbox.stop'), CapabilityGrantDefinition(kind='sandbox.execute'), CapabilityGrantDefinition(kind='sandbox.cancel_command'), CapabilityGrantDefinition(kind='sandbox.install_python_packages'), CapabilityGrantDefinition(kind='sandbox.run_skill_script'), CapabilityGrantDefinition(kind='sandbox.inspect'), CapabilityGrantDefinition(kind='sandbox.copy_skill_resource')),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="mount_read_only", label="Mount read-only", short_label="read-only",
        description="The resource is visible in the sandbox but cannot be changed there.",
        source_traits=frozenset({"core.resource"}), target_traits=frozenset({"core.sandbox"}),
        templateable=True,
    ))
    registry.register_relationship(RelationshipDefinition(
        id="mount_read_write", label="Mount read/write", short_label="read/write",
        description="The text resource can be read and changed inside the sandbox.",
        source_traits=frozenset({"core.text"}), target_traits=frozenset({"core.sandbox"}),
        templateable=True,
    ))
class CorePlugin:
    descriptor = PluginDescriptor(
        id="open-agent-world.core",
        version="0.1.0",
        plugin_api_version=PLUGIN_API_VERSION,
        name="Open Agent World Core",
        description="Built-in nodes, relationships, state schemas, and runtimes.",
    )

    def register(self, registration: PluginRegistration) -> None:
        _register_builtin(registration)
        from backend.file_preview import register_file_preview
        register_file_preview(registration)
        registration.register_pack(PackDefinition(id='open-agent-world.core.default', name='Core essentials',
            description='Agents, resources and workspaces for your world.', cards=tuple(registration.nodes)))


def create_builtin_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.install(CorePlugin())
    return registry
