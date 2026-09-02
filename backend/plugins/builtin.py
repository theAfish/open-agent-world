from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Any

from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.plugins.registry import (
    CapabilityGrantDefinition,
    NodeTypeDefinition,
    PluginRegistry,
    RelationshipDefinition,
)
from backend.resources.models import TextReplace
from backend.world.models import AgentConfig, ImageConfig, SandboxConfig, TextConfig


async def _communicate(services: Any, capability: Any, values: dict[str, Any]) -> Any:
    message = values.get("message")
    if set(values) != {"message"} or not isinstance(message, str) or not message.strip():
        raise ResourceValidationError(
            "agent communication capability requires one non-empty message"
        )
    return await services.communicate_with_agent(
        capability.agent_id, capability.target_id, message
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
    registry = PluginRegistry()
    registry.register_capability_handler("agent.communicate", _communicate)
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
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="text", label="Text file", description="Managed knowledge", icon="file-text",
        color="#7c7267", deck_id="objects", deck_label="Objects", deck_icon="boxes",
        default_name="Untitled Text", default_size=(300, 220), default_status="available",
        statuses=frozenset({"available", "modified"}), config_model=TextConfig,
        traits=frozenset({"core.resource", "core.text"}),
        creation_fields=frozenset({"content"}),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="image", label="Image file", description="Visual resource", icon="image",
        color="#8a7560", deck_id="objects", deck_label="Objects", deck_icon="boxes",
        default_name="Untitled Image", default_size=(280, 240), default_status="available",
        statuses=frozenset({"available", "modified"}), config_model=ImageConfig,
        traits=frozenset({"core.resource", "core.image"}),
        creation_fields=frozenset({"data_base64"}),
    ))
    registry.register_node_type(NodeTypeDefinition(
        id="sandbox", label="Sandbox", description="Secure work field", icon="workflow",
        color="#696c66", deck_id="fields", deck_label="Fields", deck_icon="workflow",
        default_name="New Sandbox", default_size=(340, 220), default_status="stopped",
        statuses=frozenset({"stopped", "ready", "running", "error"}),
        config_model=SandboxConfig, traits=frozenset({"core.sandbox"}),
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
            description="Execute an argv command in sandbox {target_name!r}.",
            input_schema={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Command and arguments as a non-empty string array."}}, "required": ["argv"], "additionalProperties": False},
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
