from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from backend.agents import ScopedToolDefinition, ToolParameter
from backend.capabilities.models import CapabilityKind
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.resources.models import TextReplace

if TYPE_CHECKING:
    from backend.services import ApplicationServices


class WorldAgentCapabilityProvider:
    """ADK-neutral adapter from scoped tools to live broker operations."""

    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def list_tools(self, agent_id: str) -> Sequence[ScopedToolDefinition]:
        definitions: list[ScopedToolDefinition] = []
        for capability in self.services.capabilities.derive(agent_id).capabilities:
            parameters: tuple[ToolParameter, ...] = ()
            if capability.kind is CapabilityKind.TEXT_EDIT:
                parameters = (
                    ToolParameter(
                        "content", str, "Complete replacement text for this resource."
                    ),
                )
            elif capability.kind is CapabilityKind.SANDBOX_EXECUTE:
                parameters = (
                    ToolParameter(
                        "argv", list, "Command and arguments as a non-empty string array."
                    ),
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
        values = dict(arguments)
        if capability.kind is CapabilityKind.TEXT_READ:
            if values:
                raise ResourceValidationError("text read capability takes no arguments")
            document = self.services.capabilities.read_text(agent_id, capability.target_id)
            return document.model_dump(mode="json")
        if capability.kind is CapabilityKind.TEXT_EDIT:
            if set(values) != {"content"} or not isinstance(values["content"], str):
                raise ResourceValidationError(
                    "text edit capability requires one string content argument"
                )
            document = await self.services.replace_text(
                capability.target_id,
                TextReplace(content=values["content"]),
                agent_id=agent_id,
            )
            return document.model_dump(mode="json")
        if capability.kind is CapabilityKind.IMAGE_VIEW:
            if values:
                raise ResourceValidationError("image view capability takes no arguments")
            record, path = self.services.capabilities.view_image(
                agent_id, capability.target_id
            )
            return {
                "filename": record.filename,
                "media_type": record.media_type,
                "width": record.width,
                "height": record.height,
                "size_bytes": record.size_bytes,
                "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        if capability.kind is CapabilityKind.SANDBOX_EXECUTE:
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
            self.services.capabilities.require_sandbox_execute(
                agent_id, capability.target_id
            )
            if self.services.sandbox_backend is None:
                raise RuntimeUnavailableError(
                    "the native Windows sandbox backend is unavailable"
                )
            result = await self.services.execute_sandbox(
                capability.target_id, argv, agent_id=agent_id
            )
            return asdict(result)
        raise AssertionError(f"unhandled capability kind {capability.kind}")
