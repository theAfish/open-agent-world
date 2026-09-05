from __future__ import annotations

from contextlib import suppress
import logging
from typing import Any, Mapping

from backend.errors import PluginCompatibilityError, ResourceValidationError
from backend.sandbox.models import SandboxError, SandboxSecurityError, SandboxValidationError
from backend.plugins.registry import (
    PLUGIN_API_VERSION,
    CapabilityGrantDefinition,
    NodeTypeDefinition,
    PluginDescriptor,
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
    SandboxConfig,
    TextConfig,
)


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
        except (SandboxSecurityError, SandboxValidationError):
            # Missing host folders/runtimes disable this card, not the whole app.
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
        keys = ("runtime", "workspace_path", "workspace_access")
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
    portable_config_fields = frozenset({"status"})


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


async def _inspect_sandbox(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    if values:
        raise ResourceValidationError("sandbox inspect takes no arguments")
    return await context.inspect_sandbox(capability.agent_id, capability.target_id)


async def _execute_sandbox(
    context: CapabilityContext, capability: Any, values: dict[str, Any]
) -> Any:
    argv = values.get("argv")
    if (
        set(values) != {"argv"}
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise ResourceValidationError(
            "sandbox execute capability requires a non-empty argv array"
        )
    return await context.execute_sandbox(
        capability.agent_id, capability.target_id, argv
    )


def _register_builtin(registry: PluginRegistration) -> None:
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
    registry.register_capability_handler("agent.communicate", _communicate)
    registry.register_capability_handler(
        "conversation.request_turn", _request_conversation_turn
    )
    registry.register_capability_handler("text.read", _read_text)
    registry.register_capability_handler("text.edit", _edit_text)
    registry.register_capability_handler("image.view", _view_image)
    registry.register_capability_handler("sandbox.execute", _execute_sandbox)
    registry.register_capability_handler("sandbox.inspect", _inspect_sandbox)
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
        id="conversation", label="Conversation", description="Shared communication field",
        icon="messages-square", color="#9a6954", deck_id="fields",
        deck_label="Fields", deck_icon="workflow", default_name="New Conversation",
        default_size=(320, 210), default_status="available",
        statuses=frozenset({"available"}), config_model=ConversationConfig,
        traits=frozenset({"core.field", "core.conversation"}),
        surfaces={"preview": True, "inspector": True, "workspace": True},
        lifecycle=ConversationNodeBehavior(),
        templateable=False,
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
        config_model=SandboxConfig, traits=frozenset({"core.sandbox"}),
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
        capabilities=(CapabilityGrantDefinition(
            kind="agent.communicate", tool_prefix="message_agent",
            description="Send a message to agent {target_name!r} and receive its response.",
            input_schema={"type": "object", "properties": {"message": {"type": "string", "description": "Message or question for the connected agent."}}, "required": ["message"], "additionalProperties": False},
        ),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="participate", label="Participate", short_label="join",
        description="The agent can join sessions and speak inside this Conversation field.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"core.conversation"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(
            kind="conversation.request_turn", tool_prefix="request_turn_in",
            description=(
                "Ask a different participant to speak in Conversation {target_name!r}. "
                "The current conversation session is supplied automatically; provide another participant's agent id. "
                "Never use your own agent id; if no other participant is available, "
                "answer directly without this tool."
            ),
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
            },
        ),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="read", label="Read", short_label="read",
        description="The agent can inspect this text through a scoped tool.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.text"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(
            kind="text.read", tool_prefix="read_text",
            description="Read the managed text resource {target_name!r}.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="read_edit", label="Read + edit", short_label="read + edit",
        description="The agent can inspect and modify this text through scoped tools.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.text"}),
        templateable=True,
        capabilities=(
            CapabilityGrantDefinition(
                kind="text.read", tool_prefix="read_text",
                description="Read the managed text resource {target_name!r}.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            CapabilityGrantDefinition(
                kind="text.edit", tool_prefix="replace_text",
                description="Replace the contents of {target_name!r}.",
                input_schema={"type": "object", "properties": {"content": {"type": "string", "description": "Complete replacement text for this resource."}}, "required": ["content"], "additionalProperties": False},
            ),
        ),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="view", label="View", short_label="view",
        description="The agent can inspect the image content.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.image"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(
            kind="image.view", tool_prefix="view_image",
            description="Inspect the managed image {target_name!r}.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="execute", label="Execute", short_label="execute",
        description="The agent can run commands in this isolated workplace.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.sandbox"}),
        templateable=True,
        capabilities=(CapabilityGrantDefinition(
            kind="sandbox.execute", tool_prefix="execute_in",
            description=(
                "Execute an argv command in sandbox {target_name!r}. "
                "First use its inspect tool to obtain the runtime shell, cwd and resource paths. "
                "The configured working folder is live; edits there change real files. "
                "Attached resources are available through SANDBOX_RESOURCES."
            ),
            input_schema={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Executable and arguments as a non-empty string array; argv[0] cannot be a shell built-in."}}, "required": ["argv"], "additionalProperties": False},
        ), CapabilityGrantDefinition(
            kind="sandbox.inspect", tool_prefix="inspect_sandbox",
            description="Inspect sandbox {target_name!r} before executing: returns its operating system, shell argv prefix, cwd, read/write access, resource directory and availability.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )),
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


def create_builtin_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.install(CorePlugin())
    return registry
