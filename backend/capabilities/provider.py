from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from backend.agents import ScopedToolDefinition, ToolParameter

if TYPE_CHECKING:
    from backend.services import ApplicationServices


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
        return await handler(self.services, capability, dict(arguments))


def _python_type(schema_type: object) -> type[Any]:
    return {
        "boolean": bool,
        "integer": int,
        "number": float,
        "array": list,
        "object": dict,
    }.get(schema_type, str)
