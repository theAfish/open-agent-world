from __future__ import annotations

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
    capability: Any = None

    @property
    def state(self):
        def authorize():
            live = self.services.capabilities.capability_for_id(self.capability.agent_id, self.capability.id)
            if live.target_id != self.capability.target_id or live.kind != self.capability.kind:
                from backend.errors import PermissionDeniedError
                raise PermissionDeniedError("State access was revoked")
        authorize()
        return self.services.card_state.bind(self.capability.target_id, authorize=authorize)

    async def node_resource_action(self, capability, action, arguments):
        from backend.node_resources import ResourceActionRequest, invoke_resource_action
        return await invoke_resource_action(self.services, capability.target_id, action,
            ResourceActionRequest(arguments=arguments), capability=capability)

    async def capture_plugin_view(self, capability, *, capture_kind, required_capability_kind, capture_options=None):
        from backend.visual_observation import observe_plugin_view
        return await observe_plugin_view(
            self.services, capability, capture_kind=capture_kind,
            required_capability_kind=required_capability_kind, capture_options=capture_options,
        )

    async def minister_action(self, capability, arguments):
        from backend.minister import invoke
        return await invoke(self.services, capability, arguments)

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

    async def collection_members(self, capability):
        async with self.services._node_mutation():
            self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            return {"collection_id": capability.target_id, "members": [
                {"id": member.id, "name": member.name, "type": member.type}
                for member in self.services.world.list_members(capability.target_id)
            ], "permissions": "Member content and execution require independent connections."}

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

    async def node_delegation_action(self, capability, action, arguments):
        return await self.services.node_execution.delegation_action(capability.target_id, action, arguments, capability=capability)

    async def node_document_action(self, capability, action, arguments, expected_revision=None):
        from backend.node_documents import DocumentActionRequest, invoke_document_action
        return await invoke_document_action(self.services, capability.target_id, action,
            _validate_tool_request(DocumentActionRequest, dict(arguments=arguments, expected_revision=expected_revision)), capability=capability)

    async def write_sandbox_workspace_file(self, capability, sandbox_id: str, path: str, data: bytes) -> dict[str, Any]:
        """Write trusted plugin bytes through the pinned Sandbox file boundary.

        The plugin's Structure capability and the Agent's independent Sandbox
        execute grant must both remain live at the actual transfer point.
        """
        import base64
        from backend.errors import PermissionDeniedError
        from backend.sandbox.files import DOWNLOAD_LIMIT
        if not isinstance(data, bytes) or len(data) > DOWNLOAD_LIMIT:
            raise ResourceValidationError("Sandbox transfer must be bytes of at most 16 MiB")
        async with self.services._node_mutation():
            live = self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.kind != capability.kind or live.target_id != capability.target_id:
                raise PermissionDeniedError("Source capability was revoked")
            for kind in self.services.plugins.capability_definition(live.kind).target_capabilities:
                self.services.capabilities.capability_for_id(capability.agent_id, f"{kind}:{live.target_id}")
            self.services.capabilities.require_sandbox_execute(capability.agent_id, sandbox_id)
            self.services._require_card_type(sandbox_id, "sandbox")
            return await self.services._require_sandbox_backend().file_operation(
                sandbox_id, "write", root="workspace", path=path,
                data=base64.b64encode(data).decode("ascii"), overwrite=False,
            )

    async def write_sandbox_text_file(self, capability, path: str, content: str,
                                      overwrite: bool = False) -> dict[str, Any]:
        """Persist bounded UTF-8 source through the pinned Sandbox file boundary."""
        import base64
        import hashlib
        from backend.errors import PermissionDeniedError
        if not isinstance(content, str) or "\0" in content:
            raise ResourceValidationError("Sandbox text must be UTF-8 text without NUL bytes")
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ResourceValidationError("Sandbox text must be valid UTF-8") from exc
        if not raw or len(raw) > 256 * 1024:
            raise ResourceValidationError("Sandbox text must be between 1 byte and 256 KiB")
        if not isinstance(overwrite, bool):
            raise ResourceValidationError("overwrite must be a boolean")
        async with self.services._node_mutation():
            live = self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.kind != "sandbox.write_text_file" or live.target_id != capability.target_id:
                raise PermissionDeniedError("Sandbox file-write capability was revoked")
            self.services.capabilities.require_sandbox_execute(capability.agent_id, live.target_id)
            self.services._require_card_type(live.target_id, "sandbox")
            result = await self.services._require_sandbox_backend().file_operation(
                live.target_id, "write", root="workspace", path=path,
                data=base64.b64encode(raw).decode("ascii"), overwrite=overwrite,
            )
        return {"path": path, "written": result["written"],
                "sha256": hashlib.sha256(raw).hexdigest()}

    async def read_sandbox_workspace_file(self, capability, sandbox_id: str, path: str) -> bytes:
        """Read one bounded Sandbox file without echoing its body to the LLM."""
        import base64
        from backend.errors import PermissionDeniedError
        from backend.sandbox.files import DOWNLOAD_LIMIT
        async with self.services._node_mutation(read_only=True):
            live = self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.kind != capability.kind or live.target_id != capability.target_id:
                raise PermissionDeniedError("Destination capability was revoked")
            for kind in self.services.plugins.capability_definition(live.kind).target_capabilities:
                self.services.capabilities.capability_for_id(capability.agent_id, f"{kind}:{live.target_id}")
            self.services.capabilities.require_sandbox_execute(capability.agent_id, sandbox_id)
            self.services._require_card_type(sandbox_id, "sandbox")
            result = await self.services._require_sandbox_backend().file_operation(
                sandbox_id, "download", root="workspace", path=path,
            )
        if result.get("state") != "ready":
            raise ResourceValidationError("Sandbox file is unavailable or exceeds the 16 MiB transfer limit")
        encoded = result.get("data")
        if not isinstance(encoded, str) or len(encoded) > ((DOWNLOAD_LIMIT + 2) // 3) * 4:
            raise ResourceValidationError("Sandbox file exceeds the 16 MiB transfer limit")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ResourceValidationError("Sandbox returned invalid file data") from exc
        if len(data) > DOWNLOAD_LIMIT:
            raise ResourceValidationError("Sandbox file exceeds the 16 MiB transfer limit")
        return data

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

    def view_image(self, agent_id: str, resource_id: str):
        from backend.agents.media import ToolImage, VisualToolResult
        record, path = self.services.capabilities.view_image(agent_id, resource_id)
        return VisualToolResult({
            "filename": record.filename,
            "media_type": record.media_type,
            "width": record.width,
            "height": record.height,
            "size_bytes": record.size_bytes,
        }, (ToolImage(path.read_bytes(), record.media_type),))

    async def execute_sandbox(
        self, agent_id: str, sandbox_id: str, argv: list[str], *, environment_id: str | None = None, target_id: str | None = None, timeout_seconds: float | None = None, wait_seconds: float | None = None, python_environment: str = "auto"
    ) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        if self.services.sandbox_backend is None:
            raise RuntimeUnavailableError(
                "sandbox execution is not configured on this host"
            )
        return await self.services.sandbox_operations.submit(agent_id, sandbox_id, "command",
            lambda operation_id: self.services.execute_sandbox(sandbox_id, argv,
                agent_id=agent_id, environment_id=environment_id, target_id=target_id,
                timeout_seconds=timeout_seconds, python_environment=python_environment,
                _operation_id=operation_id), wait_seconds=wait_seconds)

    async def run_skill_script(self, agent_id: str, sandbox_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.skill_runtime import RunSkillScript
        request = _validate_tool_request(RunSkillScript, arguments)
        return await self.services.sandbox_operations.submit(agent_id, sandbox_id, "skill_script",
            lambda operation_id: self.services.execute_sandbox(sandbox_id,
                [*request.interpreter, request.script_path, *request.argv],
                agent_id=agent_id, _skill_request=request, environment_id=request.environment_id,
                target_id=request.target_id, timeout_seconds=request.timeout_seconds, _operation_id=operation_id),
            wait_seconds=request.wait_seconds)

    async def copy_skill_resource(self, agent_id, sandbox_id, arguments):
        from backend.sandbox_workspace import copy_skill
        return await copy_skill(self.services, sandbox_id, agent_id=agent_id, **arguments)

    async def install_python_packages(self, agent_id, sandbox_id, requirements, wait_seconds=None):
        return await self.services.sandbox_operations.submit(agent_id, sandbox_id, "python_install",
            lambda operation_id: self.services.install_python_packages(sandbox_id, requirements, agent_id=agent_id, _operation_id=operation_id),
            wait_seconds=wait_seconds)

    async def wait_sandbox_operation(self, agent_id, sandbox_id, operation_id=None, wait_seconds=30):
        return await self.services.sandbox_operations.wait(agent_id, sandbox_id, operation_id, wait_seconds)

    async def cancel_sandbox_command(self, agent_id: str, sandbox_id: str, command_id: str) -> dict[str, Any]:
        from backend.sandbox.history import stop
        return await stop(self.services, sandbox_id, agent_id=agent_id, command_id=command_id)

    async def start_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        info = await self.services.start_sandbox(sandbox_id, agent_id=agent_id)
        from backend.sandbox.manager import SandboxManager
        backend = self.services.sandbox_backend
        python_status = await backend.python_status(sandbox_id) if isinstance(backend, SandboxManager) else None
        return {"sandbox_id": sandbox_id, "state": info.state.value,
                "shared_python": python_status}

    async def stop_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        info = await self.services.stop_sandbox(sandbox_id, agent_id=agent_id)
        return {"sandbox_id": sandbox_id, "state": info.state.value}

    async def inspect_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        info = await self.services.get_sandbox(sandbox_id)
        from backend.sandbox.manager import SandboxManager
        backend = self.services.sandbox_backend
        python_status = await backend.python_status(sandbox_id) if isinstance(backend, SandboxManager) else None
        self.services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
        from backend.execution_config import configuration_summary
        active = [dict(r) for r in self.services._sandbox_commands.values() if r["sandbox_id"] == sandbox_id]
        current = active[0] if len(active) == 1 else None
        from backend.sandbox.history import recent_summaries
        # ``state`` gates commands; ``available`` only reports platform support
        # and ``configuration.ready`` only reports configured variables. Make
        # the command gate explicit so an errored sandbox is not mistaken for
        # a usable one.
        commands_accepted = info.state.value in {"ready", "running"}
        state_guidance = None
        if info.state.value == "error":
            state_guidance = ("Commands are rejected while state is \"error\". One start_sandbox "
                              "call recovers it once active commands finish; it requires an "
                              "Execute + Start/Stop connection, otherwise restart the Sandbox "
                              "from its card.")
        elif info.state.value == "stopped":
            state_guidance = ("The Sandbox is stopped: start it (start_sandbox with an Execute "
                              "+ Start/Stop connection, or its card) before executing commands.")
        return {
            "sandbox_id": sandbox_id, "state": info.state.value,
            "commands_accepted": commands_accepted, "state_guidance": state_guidance,
            "runtime_id": info.runtime_id, "platform": info.platform,
            "shell": list(info.shell), "workspace": str(info.workspace),
            "workspace_access": info.workspace_access.value,
            "resources_path": str(info.resources_path) if info.resources_path else None,
            "available": info.available, "unavailable_reason": info.unavailable_reason,
            "network_enabled": info.network_enabled,
            "supported_network_modes": list(info.supported_network_modes),
            "network_reason": info.network_reason,
            "resource_limits_available": info.resource_limits_available,
            "resource_limit_reason": info.resource_limit_reason,
            "configuration": configuration_summary(self.services, sandbox_id),
            "active_commands": [{key: item.get(key) for key in ("id", "operation_kind", "phase", "requirements", "caller", "run_id", "argv", "started_at")} for item in active],
            "current_caller": current["caller"] if current else None,
            "current_command_id": current["id"] if current else None,
            "recent_commands": recent_summaries(self.services, sandbox_id),
            "shared_python": python_status,
            "console_mode": "non-interactive; each command starts in the configured workspace; cd/export/activation do not persist",
            "command_timeout": self.services.world.get_card(sandbox_id).config.get("command_timeout", 6000),
            "installation": "Use install_python_packages for the shared read-only Python environment. HOME persists (at /sandbox/home on Linux, WSL and macOS Container VM); use $HOME/.local/bin or $HOME/bin for local CLI tools, or create a private venv in HOME/workspace and invoke its interpreter explicitly. npm -g defaults to $HOME/.local, with bins on PATH. /tmp is ephemeral.",
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

    async def read_own_document(self, agent_id: str) -> dict[str, Any]:
        context = self.services._require_run_manager().current_context
        if context is None or context.agent_id != agent_id:
            raise ResourceValidationError('Own-document access requires the current agent run')
        from backend.node_documents import read_document
        async with self.services._node_mutation(read_only=True):
            return read_document(self.services, agent_id)

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
        invocation = self.services.run_manager.current_context
        receipt_id = None
        receipts = None
        if invocation is not None:
            from backend.runs.tool_receipts import RunToolReceipts
            receipts = RunToolReceipts(self.services.database)
            definition = self.services.plugins.capability_definition(capability.kind)
            receipt_id = receipts.begin(
                run_id=invocation.run_id, agent_id=agent_id,
                capability_kind=capability.kind, target_id=capability.target_id,
                arguments=arguments, read_only=definition.read_only,
            )
        from backend.sandbox.models import SandboxValidationError, SandboxStateError, SandboxOperationError, SandboxSecurityError, SandboxNotFoundError
        try:
            from backend.runs.tool_receipts import bind_tool_receipt
            with bind_tool_receipt(receipt_id):
                result = await handler(_CapabilityContext(self.services, capability), capability, dict(arguments))
        except SandboxOperationError as exc:
            result = exc.feedback()
        except SandboxSecurityError as exc:
            from backend.errors import PermissionDeniedError
            if receipts is not None and receipt_id is not None:
                receipts.uncertain(receipt_id, exc)
            raise PermissionDeniedError(str(exc)) from exc
        except SandboxNotFoundError as exc:
            from backend.errors import NotFoundError
            if receipts is not None and receipt_id is not None:
                receipts.uncertain(receipt_id, exc)
            raise NotFoundError(str(exc)) from exc
        except SandboxValidationError as exc:
            # All Agent runtimes already return domain errors as tool feedback.
            # Validation can also fail during bundle construction/materialization,
            # after the handler has validated the initial request model.
            if receipts is not None and receipt_id is not None:
                receipts.uncertain(receipt_id, exc)
            raise ResourceValidationError(str(exc)) from exc
        except SandboxStateError as exc:
            from backend.errors import ConflictError
            if receipts is not None and receipt_id is not None:
                receipts.uncertain(receipt_id, exc)
            raise ConflictError(f"{exc}. Inspect the Sandbox activity and retry when the conflicting operation finishes.") from exc
        except BaseException as exc:
            if receipts is not None and receipt_id is not None:
                receipts.uncertain(receipt_id, exc)
            raise
        if receipts is not None and receipt_id is not None:
            receipts.finish(receipt_id, result)
        return result


def _python_type(schema_type: object) -> type[Any]:
    return {
        "boolean": bool,
        "integer": int,
        "number": float,
        "array": list,
        "object": dict,
    }.get(schema_type, str)
