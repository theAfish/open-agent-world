from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, ValidationError

from backend.agents import ScopedToolDefinition, ToolParameter
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.resources.models import TextReplace

if TYPE_CHECKING:
    from backend.services import ApplicationServices


def _validate_tool_request(model: type[BaseModel], arguments):
    """Convert caller input errors only; internal state/output errors still propagate."""
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        details = []
        for error in exc.errors(include_url=False, include_input=False, include_context=False):
            location = '.'.join(str(part) for part in error['loc']) or 'arguments'
            details.append(f"{location}: {error['msg']}")
        raise ResourceValidationError(
            'Invalid tool arguments. Correct the listed fields and retry: ' + '; '.join(details)
        ) from exc


@dataclass(frozen=True, slots=True)
class _CapabilityContext:
    services: ApplicationServices

    async def read_file_preview(self, capability, arguments):
        from pydantic import TypeAdapter
        from backend.file_preview import FileReference, read_file
        try:
            reference = TypeAdapter(FileReference).validate_python(arguments.get("file"))
        except ValidationError as exc:
            raise ResourceValidationError("Provide a valid Sandbox or Conversation file reference") from exc
        if reference.source_id != capability.target_id:
            raise ResourceValidationError("File source must match the selected capability")
        return await read_file(self.services, capability.agent_id, reference)

    async def send_conversation_message(self, capability, arguments):
        from backend.conversations.models import ConversationPost
        from backend.conversations.attachments import agent_session, resolve
        request = _validate_tool_request(ConversationPost, arguments)
        conversation_id, agent_id = capability.target_id, capability.agent_id
        session_id = agent_session(self.services, conversation_id, agent_id)
        attachments = resolve(self.services, conversation_id, session_id, request.attachments, agent_id)
        message = self.services.conversations.add_message(conversation_id, session_id,
            sender_kind='agent', sender_id=agent_id, sender_name=self.services.world.get_card(agent_id).name,
            content=request.content, attachments=attachments,
            run_id=self.services._require_run_manager().current_context.run_id)
        await self.services._publish_conversation_message(message)
        return message.model_dump(mode='json')

    async def artifact_action(self, capability, arguments):
        from backend.resources.artifact_models import ArtifactPublish, ArtifactMaterialize
        from backend.errors import ResourceValidationError
        store = self.services.resources.artifacts
        agent, collection = capability.agent_id, capability.target_id
        args = dict(arguments)
        # Projected operations have already reauthorized independent selectors.
        if capability.kind == 'artifact.publish':
            return await store.publish(self.services, collection, _validate_tool_request(ArtifactPublish, args), agent)
        version = args.pop('version_id', None)
        if capability.kind == 'artifact.manage':
            if args.get('action', 'remove') == 'add':
                return store.add_reference(self.services, collection, version, agent, args.get('source_collection_id'))
            if args.get('action', 'remove') != 'remove':
                raise ResourceValidationError('Choose add or remove for collection references')
            return store.remove_reference(self.services, collection, version, agent)
        if capability.kind == 'artifact.materialize':
            return await store.materialize(self.services, collection, version, _validate_tool_request(ArtifactMaterialize, args), agent)
        if args.get('path') is not None:
            if not version:
                raise ResourceValidationError('Preview requires a version_id')
            return await store.preview(self.services, collection, version, args['path'], agent)
        records = store.listing(self.services, collection, agent)
        if version:
            store.authorize(self.services, collection, agent, version_id=version)
            return store.get(version)
        return records

    async def legion_state_action(self, capability, arguments):
        from backend.legions.runtime import LegionStateWrite, read_shared_state, write_shared_state
        async with self.services._node_mutation():
            self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if capability.kind == "legion.state.read":
                if arguments:
                    raise ResourceValidationError("State read takes no arguments")
                return read_shared_state(self.services.world, self.services.state, capability.target_id)
            request = _validate_tool_request(LegionStateWrite, dict(arguments))
            context = self.services._require_run_manager().current_context
            return write_shared_state(self.services.world, self.services.state, capability.target_id,
                request, actor_id=capability.agent_id, run_id=context.run_id if context else None, merge=True)

    async def summoning_action(self, capability, arguments):
        from backend.plugins.summoning import SummoningAction
        return await self.services.summoning.action(capability.target_id, _validate_tool_request(SummoningAction, arguments), capability=capability)

    async def node_execution_action(self, capability, action, arguments):
        from backend.node_execution import ExecutionRequest
        service = self.services.node_execution
        if action == "start":
            request = _validate_tool_request(ExecutionRequest, arguments)
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
            _validate_tool_request(DocumentActionRequest, dict(arguments=arguments, expected_revision=expected_revision)), capability=capability)

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
            resource_id, _validate_tool_request(TextReplace, dict(content=content)), agent_id=agent_id
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
        self, agent_id: str, sandbox_id: str, argv: list[str], *, environment_id: str | None = None, target_id: str | None = None, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        if self.services.sandbox_backend is None:
            raise RuntimeUnavailableError(
                "sandbox execution is not configured on this host"
            )
        result = await self.services.execute_sandbox(
            sandbox_id, argv, agent_id=agent_id, environment_id=environment_id, target_id=target_id, timeout_seconds=timeout_seconds
        )
        return asdict(result)

    async def run_skill_script(self, agent_id: str, sandbox_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.skill_runtime import RunSkillScript
        request = _validate_tool_request(RunSkillScript, arguments)
        result = await self.services.execute_sandbox(sandbox_id,
            [*request.interpreter, request.script_path, *request.argv],
            agent_id=agent_id, _skill_request=request, environment_id=request.environment_id, target_id=request.target_id, timeout_seconds=request.timeout_seconds)
        return asdict(result)

    async def copy_skill_resource(self, agent_id, sandbox_id, arguments):
        from backend.sandbox_workspace import copy_skill
        return await copy_skill(self.services, sandbox_id, agent_id=agent_id, **arguments)

    async def install_python_packages(self, agent_id, sandbox_id, requirements):
        return await self.services.install_python_packages(sandbox_id, requirements, agent_id=agent_id)

    async def cancel_sandbox_command(self, agent_id: str, sandbox_id: str, command_id: str) -> dict[str, Any]:
        from backend.sandbox.history import stop
        return await stop(self.services, sandbox_id, agent_id=agent_id, command_id=command_id)

    async def start_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        info = await self.services.start_sandbox(sandbox_id, agent_id=agent_id)
        return {"sandbox_id": sandbox_id, "state": info.state.value}

    async def stop_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        info = await self.services.stop_sandbox(sandbox_id, agent_id=agent_id)
        return {"sandbox_id": sandbox_id, "state": info.state.value}

    async def inspect_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        info = await self.services.get_sandbox(sandbox_id)
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        from backend.execution_config import configuration_summary
        current = self.services._sandbox_commands.get(sandbox_id)
        from backend.sandbox.history import recent_summaries
        return {
            "sandbox_id": sandbox_id, "state": info.state.value,
            "runtime_id": info.runtime_id, "platform": info.platform,
            "shell": list(info.shell), "workspace": str(info.workspace),
            "workspace_access": info.workspace_access.value,
            "resources_path": str(info.resources_path) if info.resources_path else None,
            "available": info.available, "unavailable_reason": info.unavailable_reason,
            "network_enabled": info.network_enabled,
            "supported_network_modes": list(info.supported_network_modes),
            "network_reason": info.network_reason,
            "configuration": configuration_summary(self.services, sandbox_id),
            "current_caller": current["caller"] if current else None,
            "current_command_id": current["id"] if current else None,
            "recent_commands": recent_summaries(self.services, sandbox_id),
            "console_mode": "non-interactive; each command starts in the configured workspace; cd/export/activation do not persist",
            "command_timeout": self.services.world.get_card(sandbox_id).config.get("command_timeout", 60),
            "installation": "Use install_python_packages for the shared read-only Python environment. On Linux/WSL, HOME=/sandbox/home persists; use $HOME/.local/bin or $HOME/bin for local CLI tools, or create a private venv in HOME/workspace and invoke its interpreter explicitly. npm -g defaults to $HOME/.local, with bins on PATH. /tmp is ephemeral.",
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
        from backend.sandbox.models import SandboxValidationError
        try:
            return await handler(_CapabilityContext(self.services), capability, dict(arguments))
        except SandboxValidationError as exc:
            # All Agent runtimes already return domain errors as tool feedback.
            # Validation can also fail during bundle construction/materialization,
            # after the handler has validated the initial request model.
            raise ResourceValidationError(str(exc)) from exc


def _python_type(schema_type: object) -> type[Any]:
    return {
        "boolean": bool,
        "integer": int,
        "number": float,
        "array": list,
        "object": dict,
    }.get(schema_type, str)
