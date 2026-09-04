from __future__ import annotations

import base64
from contextlib import suppress
from dataclasses import asdict
from typing import Any

from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.plugins.registry import (
    CapabilityGrantDefinition,
    NodeTypeDefinition,
    PluginRegistry,
    RelationshipDefinition,
)
from backend.plugins.lifecycle import NodeLifecycleContext, NodeLifecycleHandler
from backend.resources.models import TextReplace
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

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        del request
        if context.agents is not None:
            await context.agents.create(node)

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        del request, error
        if context.agents is not None:
            await context.agents.delete(node.id, missing_ok=True)

    async def on_update(
        self, context: NodeLifecycleContext, node: Card, request: CardPatch
    ) -> None:
        if context.agents is None or (
            request.name is None and request.config is None
        ):
            return
        merged = node.model_copy(
            update={
                "name": request.name or node.name,
                "config": {**node.config, **(request.config or {})},
            }
        )
        await context.agents.update(merged)

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.agents is not None:
            await context.agents.delete(node.id)


class SandboxNodeBehavior(NodeLifecycleHandler):
    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.sandboxes is None:
            return
        status = await context.sandboxes.ensure(node.id)
        if node.status != status:
            context.nodes.update_status(node.id, status)

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.sandboxes is not None:
            await context.sandboxes.terminate(node.id, missing_ok=True)

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        del request
        if context.sandboxes is not None:
            await context.sandboxes.create(node.id)

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        del request, error
        if context.sandboxes is not None:
            await context.sandboxes.destroy(node.id, missing_ok=True)

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.sandboxes is not None:
            await context.sandboxes.destroy(node.id, missing_ok=True)


class ConversationNodeBehavior(NodeLifecycleHandler):
    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        del request
        context.conversations.create_initial_session(node.id, "General")

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        del request, error
        context.conversations.delete_session_state(node.id)

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        context.conversations.delete_session_state(node.id)


class ManagedResourceNodeBehavior(NodeLifecycleHandler):
    _mount_relationships = frozenset({"mount_read_only", "mount_read_write"})

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        del request, error
        context.resources.remove_file(node.id)

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        if context.sandboxes is not None:
            for edge in context.nodes.list_edges_from(node.id):
                if edge.relationship in self._mount_relationships:
                    await context.sandboxes.detach_resource(
                        edge.target, node.id, missing_ok=True
                    )
        context.resources.remove_file(node.id)


class TextNodeBehavior(ManagedResourceNodeBehavior):
    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        filename = str(node.config.get("filename", "untitled.txt"))
        initial_content = request.content
        if initial_content is None:
            configured_content = request.config.get("content", "")
            initial_content = configured_content if isinstance(configured_content, str) else ""
        context.resources.create_text(node.id, filename, initial_content)


class ImageNodeBehavior(ManagedResourceNodeBehavior):
    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        if request.data_base64 is None:
            return
        filename = str(node.config.get("filename", "image.png"))
        context.resources.create_image(
            node.id, filename, request.media_type or "", request.data_base64
        )


async def _communicate(services: Any, capability: Any, values: dict[str, Any]) -> Any:
    message = values.get("message")
    if set(values) != {"message"} or not isinstance(message, str) or not message.strip():
        raise ResourceValidationError(
            "agent communication capability requires one non-empty message"
        )
    return await services.communicate_with_agent(
        capability.agent_id, capability.target_id, message
    )


async def _request_conversation_turn(
    services: Any, capability: Any, values: dict[str, Any]
) -> Any:
    if set(values) != {"session_id", "agent_id", "message"}:
        raise ResourceValidationError(
            "conversation turn capability requires session_id, agent_id, and message"
        )
    if not all(isinstance(values[key], str) and values[key].strip() for key in values):
        raise ResourceValidationError("conversation turn arguments must be non-empty strings")
    return await services.request_conversation_turn(
        capability.agent_id,
        capability.target_id,
        values["session_id"],
        values["agent_id"],
        values["message"],
    )


async def _read_text(services: Any, capability: Any, values: dict[str, Any]) -> Any:
    if values:
        raise ResourceValidationError("text read capability takes no arguments")
    document = services.capabilities.read_text(
        capability.agent_id, capability.target_id
    )
    return document.model_dump(mode="json")


async def _edit_text(services: Any, capability: Any, values: dict[str, Any]) -> Any:
    if set(values) != {"content"} or not isinstance(values["content"], str):
        raise ResourceValidationError(
            "text edit capability requires one string content argument"
        )
    document = await services.replace_text(
        capability.target_id,
        TextReplace(content=values["content"]),
        agent_id=capability.agent_id,
    )
    return document.model_dump(mode="json")


async def _view_image(services: Any, capability: Any, values: dict[str, Any]) -> Any:
    if values:
        raise ResourceValidationError("image view capability takes no arguments")
    record, path = services.capabilities.view_image(
        capability.agent_id, capability.target_id
    )
    return {
        "filename": record.filename,
        "media_type": record.media_type,
        "width": record.width,
        "height": record.height,
        "size_bytes": record.size_bytes,
        "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


async def _execute_sandbox(
    services: Any, capability: Any, values: dict[str, Any]
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
    services.capabilities.require_sandbox_execute(
        capability.agent_id, capability.target_id
    )
    if services.sandbox_backend is None:
        raise RuntimeUnavailableError("the native Windows sandbox backend is unavailable")
    result = await services.execute_sandbox(
        capability.target_id, argv, agent_id=capability.agent_id
    )
    return asdict(result)


def create_builtin_registry() -> PluginRegistry:
    from backend.agents import GoogleAdkAgentRuntime, MockAgentRuntime
    from backend.state import MergePolicy, StateFieldDefinition, StateSchema

    registry = PluginRegistry()

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
        "execution_graph": StateFieldDefinition(
            value_type=dict[str, Any],
            allowed_scope_kinds=run_only,
            merge_policy=MergePolicy.MERGE_DICT,
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
        "completed": StateFieldDefinition(
            value_type=bool, allowed_scope_kinds=run_only, default=False
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
    registry.register_node_type(NodeTypeDefinition(
        id="agent", label="Agent", description="Reasoning worker", icon="bot",
        color="#75736c", deck_id="agents", deck_label="Agents", deck_icon="bot",
        default_name="New Agent", default_size=(300, 190), default_status="idle",
        statuses=frozenset({"idle", "running", "waiting", "error"}),
        config_model=AgentConfig, traits=frozenset({"core.agent"}),
        surfaces={"preview": True, "inspector": True, "workspace": True},
        lifecycle=AgentNodeBehavior(),
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
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="text", label="Text file", description="Managed knowledge", icon="file-text",
        color="#7c7267", deck_id="objects", deck_label="Objects", deck_icon="boxes",
        default_name="Untitled Text", default_size=(300, 220), default_status="available",
        statuses=frozenset({"available", "modified"}), config_model=TextConfig,
        traits=frozenset({"core.resource", "core.text"}),
        creation_fields=frozenset({"content"}),
        lifecycle=TextNodeBehavior(),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="image", label="Image file", description="Visual resource", icon="image",
        color="#8a7560", deck_id="objects", deck_label="Objects", deck_icon="boxes",
        default_name="Untitled Image", default_size=(280, 240), default_status="available",
        statuses=frozenset({"available", "modified"}), config_model=ImageConfig,
        traits=frozenset({"core.resource", "core.image"}),
        creation_fields=frozenset({"data_base64"}),
        lifecycle=ImageNodeBehavior(),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="sandbox", label="Sandbox", description="Secure work field", icon="workflow",
        color="#696c66", deck_id="fields", deck_label="Fields", deck_icon="workflow",
        default_name="New Sandbox", default_size=(340, 220), default_status="stopped",
        statuses=frozenset({"stopped", "ready", "running", "error"}),
        config_model=SandboxConfig, traits=frozenset({"core.sandbox"}),
        lifecycle=SandboxNodeBehavior(),
    ))

    registry.register_relationship(RelationshipDefinition(
        id="communicate", label="Communicate", short_label="message",
        description="The agent can send a scoped message to this agent and receive its response.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.agent"}),
        directions=frozenset({"forward", "bidirectional"}),
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
        capabilities=(CapabilityGrantDefinition(
            kind="conversation.request_turn", tool_prefix="request_turn_in",
            description=(
                "Ask a different participant to speak in Conversation {target_name!r}. "
                "Use the current session id and another participant's agent id. "
                "Never use your own agent id; if no other participant is available, "
                "answer directly without this tool."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current conversation session id."},
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "Agent id of a different participant to address. "
                            "This must never be your own agent id."
                        ),
                    },
                    "message": {"type": "string", "description": "Message or question for that participant."},
                },
                "required": ["session_id", "agent_id", "message"],
                "additionalProperties": False,
            },
        ),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="read", label="Read", short_label="read",
        description="The agent can inspect this text through a scoped tool.",
        source_traits=frozenset({"core.agent"}), target_traits=frozenset({"core.text"}),
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
        capabilities=(CapabilityGrantDefinition(
            kind="sandbox.execute", tool_prefix="execute_in",
            description=(
                "Execute an argv command in Windows sandbox {target_name!r}. "
                "argv[0] must be an executable on PATH; run shell built-ins such as "
                "echo through ['cmd.exe', '/d', '/c', ...]."
            ),
            input_schema={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Executable and arguments as a non-empty string array; argv[0] cannot be a shell built-in."}}, "required": ["argv"], "additionalProperties": False},
        ),),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="mount_read_only", label="Mount read-only", short_label="read-only",
        description="The resource is visible in the sandbox but cannot be changed there.",
        source_traits=frozenset({"core.resource"}), target_traits=frozenset({"core.sandbox"}),
    ))
    registry.register_relationship(RelationshipDefinition(
        id="mount_read_write", label="Mount read/write", short_label="read/write",
        description="The text resource can be read and changed inside the sandbox.",
        source_traits=frozenset({"core.text"}), target_traits=frozenset({"core.sandbox"}),
    ))
    return registry
