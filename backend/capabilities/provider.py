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

    async def legion_state_action(self, capability, arguments):
        from backend.legions.runtime import LegionStateWrite, read_shared_state, write_shared_state
        from pydantic import ValidationError
        async with self.services._node_mutation():
            self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if capability.kind == "legion.state.read":
                if arguments:
                    raise ResourceValidationError("State read takes no arguments")
                return read_shared_state(self.services.world, self.services.state, capability.target_id)
            try:
                request = LegionStateWrite.model_validate(dict(arguments))
            except ValidationError as exc:
                raise ResourceValidationError(str(exc)) from exc
            context = self.services._require_run_manager().current_context
            return write_shared_state(self.services.world, self.services.state, capability.target_id,
                request, actor_id=capability.agent_id, run_id=context.run_id if context else None, merge=True)

    async def summoning_action(self, capability, arguments):
        from backend.plugins.summoning import SummoningAction
        return await self.services.summoning.action(capability.target_id, SummoningAction.model_validate(arguments), capability=capability)

    async def node_execution_action(self, capability, action, arguments):
        from backend.node_execution import ExecutionRequest
        from backend.node_documents import validation_message
        from pydantic import ValidationError
        service = self.services.node_execution
        if action == "start":
            try:
                request = ExecutionRequest.model_validate(arguments)
            except ValidationError as error:
                raise ResourceValidationError(validation_message(error)) from error
            return await service.start(capability.target_id, request, capability=capability)
        if arguments:
            raise ResourceValidationError("Only start accepts execution arguments")
        if action == "stop":
            return await service.stop(capability.target_id, capability=capability)
        if action == "read":
            service.authorize(capability.target_id, capability)
            return service.snapshot(capability.target_id)
        raise ResourceValidationError("Unknown execution action")

    async def node_document_action(self, capability, action, arguments, expected_revision=None):
        from backend.node_documents import DocumentActionRequest, invoke_document_action
        return await invoke_document_action(self.services, capability.target_id, action,
            DocumentActionRequest(arguments=arguments, expected_revision=expected_revision), capability=capability)

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
        self, agent_id: str, sandbox_id: str, argv: list[str], *, environment_id: str | None = None, target_id: str | None = None
    ) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        if self.services.sandbox_backend is None:
            raise RuntimeUnavailableError(
                "sandbox execution is not configured on this host"
            )
        result = await self.services.execute_sandbox(
            sandbox_id, argv, agent_id=agent_id, environment_id=environment_id, target_id=target_id
        )
        return asdict(result)

    async def run_skill_script(self, agent_id: str, sandbox_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.skill_runtime import RunSkillScript
        from backend.node_documents import validation_message
        try:
            request = RunSkillScript.model_validate(arguments)
        except ValueError as exc:
            raise ResourceValidationError(validation_message(exc)) from exc
        result = await self.services.execute_sandbox(sandbox_id,
            [*request.interpreter, request.script_path, *request.argv],
            agent_id=agent_id, _skill_request=request, environment_id=request.environment_id, target_id=request.target_id)
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
        from backend.capabilities.projection import project_operations
        async with self.services._node_mutation(read_only=True):
            operations = project_operations(self.services, agent_id)
        for operation in operations:
            schema = operation.schema()
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            parameters = tuple(
                ToolParameter(
                    name,
                    _python_type(schema.get("type")),
                    str(schema.get("description", "Tool argument.")),
                    name in required,
                )
                for name, schema in sorted(properties.items(), key=lambda item: item[0] not in required)
                if isinstance(name, str) and isinstance(schema, dict)
            )
            definitions.append(
                ScopedToolDefinition(
                    capability_id=operation.id,
                    name=operation.definition.tool_name,
                    description=operation.definition.description,
                    parameters=parameters,
                    input_schema=schema,
                )
            )
        return definitions

    async def invoke_tool(
        self,
        agent_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        async with self.services._node_mutation(read_only=True):
            if capability_id.startswith("operation:"):
                from backend.capabilities.projection import resolve_operation
                capability, arguments = resolve_operation(self.services, agent_id, capability_id, arguments)
            else:
                # Existing internal callers may still address a concrete scope.
                # This route is never advertised as a per-target Agent tool.
                from backend.capabilities.projection import authorize_invocation
                capability = authorize_invocation(self.services, agent_id, capability_id, arguments)
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
