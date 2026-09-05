from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.agents import ScopedToolDefinition, ToolParameter
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.resources.models import TextReplace

if TYPE_CHECKING:
    from backend.services import ApplicationServices


@dataclass(frozen=True, slots=True)
class _CapabilityContext:
    services: ApplicationServices

    async def communicate(
        self, source_agent_id: str, target_agent_id: str, message: str
    ) -> Any:
        return await self.services.communicate_with_agent(
            source_agent_id, target_agent_id, message
        )

    async def request_conversation_turn(
        self,
        source_agent_id: str,
        conversation_id: str,
        participant_agent_id: str,
        message: str,
    ) -> Any:
        run_context = self.services._require_run_manager().current_context
        if run_context is None or not run_context.context_id:
            raise ResourceValidationError(
                "conversation turn capability is only available during a conversation run"
            )
        return await self.services.request_conversation_turn(
            source_agent_id,
            conversation_id,
            run_context.context_id,
            participant_agent_id,
            message,
        )

    def read_text(self, agent_id: str, resource_id: str) -> dict[str, Any]:
        document = self.services.capabilities.read_text(agent_id, resource_id)
        return document.model_dump(mode="json")

    async def replace_text(
        self, agent_id: str, resource_id: str, content: str
    ) -> dict[str, Any]:
        document = await self.services.replace_text(
            resource_id, TextReplace(content=content), agent_id=agent_id
        )
        return document.model_dump(mode="json")

    def view_image(self, agent_id: str, resource_id: str) -> dict[str, Any]:
        record, path = self.services.capabilities.view_image(agent_id, resource_id)
        return {
            "filename": record.filename,
            "media_type": record.media_type,
            "width": record.width,
            "height": record.height,
            "size_bytes": record.size_bytes,
            "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    async def execute_sandbox(
        self, agent_id: str, sandbox_id: str, argv: list[str]
    ) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        if self.services.sandbox_backend is None:
            raise RuntimeUnavailableError(
                "sandbox execution is not configured on this host"
            )
        result = await self.services.execute_sandbox(
            sandbox_id, argv, agent_id=agent_id
        )
        return asdict(result)

    async def inspect_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        info = await self.services.get_sandbox(sandbox_id)
        return {
            "sandbox_id": sandbox_id, "state": info.state.value,
            "runtime_id": info.runtime_id, "platform": info.platform,
            "shell": list(info.shell), "workspace": str(info.workspace),
            "workspace_access": info.workspace_access.value,
            "resources_path": str(info.resources_path) if info.resources_path else None,
            "available": info.available, "unavailable_reason": info.unavailable_reason,
            "network_enabled": info.network_enabled,
            "attachments": [
                {"resource_id": item.resource_id,
                 "path": str(info.resources_path / item.relative_path.replace("\\", "/")) if info.resources_path else None,
                 "access": item.access.value}
                for item in info.attachments
            ],
        }


class WorldAgentCapabilityProvider:
    """ADK-neutral adapter from scoped tools to live broker operations."""

    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def list_tools(self, agent_id: str) -> Sequence[ScopedToolDefinition]:
        definitions: list[ScopedToolDefinition] = []
        for capability in self.services.capabilities.derive(agent_id).capabilities:
            properties = capability.input_schema.get("properties", {})
            required = set(capability.input_schema.get("required", []))
            parameters = tuple(
                ToolParameter(
                    name,
                    _python_type(schema.get("type")),
                    str(schema.get("description", "Tool argument.")),
                    name in required,
                )
                for name, schema in properties.items()
                if isinstance(name, str) and isinstance(schema, dict)
            )
            definitions.append(
                ScopedToolDefinition(
                    capability_id=capability.id,
                    name=capability.tool_name,
                    description=capability.description,
                    parameters=parameters,
                )
            )
        return definitions

    async def invoke_tool(
        self,
        agent_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        # The id locates a concrete scope; capability_for_id re-derives the
        # current graph and never treats the id as a durable authorization token.
        capability = self.services.capabilities.capability_for_id(
            agent_id, capability_id
        )
        handler = self.services.plugins.capability_handler(capability.kind)
        return await handler(_CapabilityContext(self.services), capability, dict(arguments))


def _python_type(schema_type: object) -> type[Any]:
    return {
        "boolean": bool,
        "integer": int,
        "number": float,
        "array": list,
        "object": dict,
    }.get(schema_type, str)
