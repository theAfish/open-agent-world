from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, ValidationError
from contextlib import aclosing, asynccontextmanager
from backend.file_transfer import MAX_FILE_BYTES, INLINE_FILE_BYTES, bounded_chunks, receive_file, validate_file_limit

from backend.agents import ScopedToolDefinition, ToolParameter
from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.resources.models import TextReplace
from backend.sandbox.task_workspace import task_path, binding as task_binding, ensure_directory

if TYPE_CHECKING:
    from backend.services import ApplicationServices


def _python_environment_summary(status):
    if not isinstance(status, dict):
        return None
    packages = status.get("package_versions") or {}
    dependencies = status.get("dependencies") or {}
    return {"ready": bool(status.get("ready") and dependencies.get("ready", True) and not status.get("warmup_error")),
        "interpreter_ready": status.get("ready"), "dependencies": dependencies,
        "preparing": status.get("preparing", False) or dependencies.get("state") == "preparing",
        "python_version": status.get("python_version"),
        "package_versions": {name: packages[name] for name in ("numpy", "ase", "scipy", "pymatgen", "matplotlib")
                             if name in packages},
        "versions_source": status.get("versions_source"),
        "warmup_error": status.get("warmup_error"),
        "compatibility_notes": ["Use np.ptp(array) for range; avoid array.ptp()."]
            if "numpy" in packages else []}


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
    arguments: Mapping[str, Any] | None = None
    _artifact_reads: list[tuple[str, str]] = field(default_factory=list, repr=False, compare=False)
    _sandbox_reads: list[str] = field(default_factory=list, repr=False, compare=False)

    @property
    def state(self):
        def authorize():
            live = self.services.capabilities.capability_for_id(self.capability.agent_id, self.capability.id)
            if live.target_id != self.capability.target_id or live.kind != self.capability.kind:
                from backend.errors import PermissionDeniedError
                raise PermissionDeniedError("State access was revoked")
        authorize()
        return self.services.card_state.bind(self.capability.target_id, authorize=authorize)

    async def node_resource_action(self, capability, action, arguments, *, files=None):
        from backend.capabilities.projection import authorize_invocation
        from backend.node_resources import ResourceActionRequest, invoke_resource_action
        async with self.services._node_mutation():
            authorize_invocation(self.services, capability.agent_id, capability.id, self.arguments or {})
            for collection_id, version_id in self._artifact_reads:
                self.services.resources.artifacts.authorize(
                    self.services, collection_id, capability.agent_id, version_id=version_id)
            for sandbox_id in self._sandbox_reads:
                self.services.capabilities.require_sandbox_execute(capability.agent_id, sandbox_id)
            return await invoke_resource_action(self.services, capability.target_id, action,
                ResourceActionRequest(arguments=arguments), capability=capability, files=files)

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
            async with self.services._node_mutation():
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

    async def run_history_action(self, capability, action, arguments):
        from backend.runs.tools import invoke
        return await invoke(self.services, capability, action, arguments)

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

    async def node_run_action(self, capability, action, arguments):
        """Start, stop or read a direct-run node through the caller's live grant."""
        from uuid import uuid4
        from backend.errors import PermissionDeniedError
        from backend.world.models import CardPatch
        services = self.services
        live = services.capabilities.capability_for_id(capability.agent_id, capability.id)
        if live.kind != capability.kind or live.target_id != capability.target_id:
            raise PermissionDeniedError("Run capability was revoked")
        target = services.world.get_card(capability.target_id)
        if "ui.direct-run.v1" not in services.plugins.node_type(target.type).traits:
            raise ResourceValidationError("This node cannot be run by a tool")
        manager = services._require_run_manager()
        if action == "start":
            config = arguments.get("config", {})
            if set(arguments) - {"config"} or not isinstance(config, dict):
                raise ResourceValidationError("Run start accepts only a config object")
            if config:
                await services.update_card(target.id, CardPatch(config=config))
            run = await manager.start_run(target.id, "Run", caller_kind="agent", caller_id=capability.agent_id,
                                          detached=True, context_id="tool-run:" + uuid4().hex)
            return {"run_id": run.run_id, "status": run.status.value}
        if arguments:
            raise ResourceValidationError("Only start accepts run arguments")
        if action == "stop":
            return await services.stop_agent(target.id)
        if action == "read":
            latest = max(manager.list_runs(agent_id=target.id), key=lambda run: run.created_at, default=None)
            return {"node_status": target.status,
                    "latest_run": {"run_id": latest.run_id, "status": latest.status.value, "error": latest.error} if latest else None}
        raise ResourceValidationError("Unknown run action")

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
            await ensure_directory(self.services, sandbox_id, capability.agent_id)
            return await self.services._require_sandbox_backend().file_operation(
                sandbox_id, "write", root="workspace", path=task_path(self.services, sandbox_id, path, capability.agent_id),
                data=base64.b64encode(data).decode("ascii"), overwrite=False, create_parents=True,
            )

    def _sandbox_file_transfer(self, capability, sandbox_id):
        from backend.capabilities.projection import authorize_invocation
        from backend.errors import PermissionDeniedError
        from backend.sandbox.file_streams import SandboxFileTransfer
        def authorize():
            live = authorize_invocation(self.services, capability.agent_id, capability.id, self.arguments or {})
            if live.kind != capability.kind or live.target_id != capability.target_id:
                raise PermissionDeniedError('File transfer capability was revoked')
            self.services.capabilities.require_sandbox_execute(capability.agent_id, sandbox_id)
            self.services._require_card_type(sandbox_id, 'sandbox')
        return SandboxFileTransfer(self.services, sandbox_id, authorize)

    async def write_sandbox_workspace_file_stream(self, capability, sandbox_id, path, chunks, *, max_bytes=MAX_FILE_BYTES):
        transfer = self._sandbox_file_transfer(capability, sandbox_id)
        async with aclosing(chunks):
            async with self.services.resources.artifacts.workspace(self.services, sandbox_id, capability.agent_id, allow_running=True):
                async with self.services._node_mutation():
                    transfer.authorize()
                    await ensure_directory(self.services, sandbox_id, capability.agent_id)
                    target = task_path(self.services, sandbox_id, path, capability.agent_id)
                await transfer.write_file_stream(target, chunks, max_bytes=max_bytes)
                return {**transfer.result, 'path': path}

    async def stream_sandbox_workspace_file(self, capability, sandbox_id, path, *, max_bytes=MAX_FILE_BYTES):
        transfer = self._sandbox_file_transfer(capability, sandbox_id)
        async with self.services.resources.artifacts.workspace(self.services, sandbox_id, capability.agent_id, allow_running=True):
            async with aclosing(transfer.read_file_stream(
                    task_path(self.services, sandbox_id, path, capability.agent_id), max_bytes=max_bytes)) as stream:
                async for chunk in stream:
                    yield chunk
            async with self.services._node_mutation(read_only=True):
                transfer.authorize()
                if sandbox_id not in self._sandbox_reads:
                    self._sandbox_reads.append(sandbox_id)

    @asynccontextmanager
    async def open_sandbox_workspace_file(self, capability, sandbox_id, path, *, max_bytes=MAX_FILE_BYTES):
        async with receive_file(self.stream_sandbox_workspace_file(capability, sandbox_id, path,
                max_bytes=max_bytes), name=path, max_bytes=max_bytes) as file:
            yield file

    async def write_sandbox_text_file(self, capability, path: str, content: str | None = None,
                                      overwrite: bool = False, *, edits: list | None = None) -> dict[str, Any]:
        """Persist bounded UTF-8 source through the pinned Sandbox file boundary."""
        import base64
        from backend.errors import PermissionDeniedError
        if edits is not None:
            if content is not None or overwrite is not True:
                raise ResourceValidationError("Use edits with overwrite=true, without content")
            raw = None
        elif not isinstance(content, str) or "\0" in content:
            raise ResourceValidationError("Sandbox text must be UTF-8 text without NUL bytes")
        else:
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
            await ensure_directory(self.services, live.target_id, capability.agent_id)
            if edits is not None:
                result = await self.services._require_sandbox_backend().file_operation(
                    live.target_id, "edit", root="workspace", path=task_path(self.services, live.target_id, path, capability.agent_id),
                    edits=edits)
            else:
                result = await self.services._require_sandbox_backend().file_operation(
                    live.target_id, "write", root="workspace", path=task_path(self.services, live.target_id, path, capability.agent_id),
                    data=base64.b64encode(raw).decode("ascii"), overwrite=overwrite, create_parents=True)
        return {"path": path, **result}

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
                sandbox_id, "download", root="workspace", path=task_path(self.services, sandbox_id, path, capability.agent_id),
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

    async def read_artifact_file(self, capability, collection_id: str, version_id: str,
                                 path: str, *, max_bytes: int = INLINE_FILE_BYTES) -> bytes:
        """Small-file compatibility helper; referenced files use stream/open."""
        if type(max_bytes) is not int or not 0 < max_bytes <= INLINE_FILE_BYTES:
            raise ResourceValidationError("Inline artifact reads are limited to 32 MiB; use stream_artifact_file")
        content = bytearray()
        async with aclosing(self.stream_artifact_file(capability, collection_id, version_id, path,
                                                     max_bytes=max_bytes)) as stream:
            async for chunk in stream:
                content.extend(chunk)
        return bytes(content)

    @asynccontextmanager
    async def open_artifact_file(self, capability, collection_id: str, version_id: str,
                                 path: str, *, max_bytes: int = MAX_FILE_BYTES):
        async with receive_file(self.stream_artifact_file(capability, collection_id, version_id,
                                path, max_bytes=max_bytes), name=path, max_bytes=max_bytes) as file:
            yield file

    async def stream_artifact_file(self, capability, collection_id: str, version_id: str,
                                   path: str, *, max_bytes: int = MAX_FILE_BYTES):
        """Read verified artifact bytes for trusted plugin code, never as model input.

        Destination and independently granted source access must remain live through
        the entire read. ArtifactStore owns session visibility, the version lease,
        pinned file access and the checksum check at EOF.
        """
        from contextlib import aclosing
        from backend.capabilities.projection import authorize_invocation
        from backend.errors import NotFoundError, PermissionDeniedError

        validate_file_limit(max_bytes)
        store = self.services.resources.artifacts

        def authorize():
            live = authorize_invocation(self.services, capability.agent_id, capability.id,
                                        self.arguments or {})
            if live.kind != capability.kind or live.target_id != capability.target_id:
                raise PermissionDeniedError("Destination capability was revoked")
            store.authorize(self.services, collection_id, capability.agent_id, version_id=version_id)

        async with self.services._node_mutation(read_only=True):
            authorize()
            entry = next((item for item in store.get(version_id)["manifest"]
                          if item["path"] == path and not item["directory"]), None)
            if entry is None:
                raise NotFoundError("File is not in this artifact manifest")
            if entry["size"] > max_bytes:
                raise ResourceValidationError(f"Artifact file exceeds the {max_bytes}-byte transfer limit")

        # Never enter ArtifactStore's lock while holding the graph lock: publication
        # acquires them in the opposite order. Recheck after each awaited read instead.
        async with aclosing(bounded_chunks(store.read(self.services, collection_id, version_id, path,
                                       capability.agent_id), max_bytes=max_bytes)) as stream:
            async for chunk in stream:
                async with self.services._node_mutation(read_only=True):
                    authorize()
                yield chunk
        async with self.services._node_mutation(read_only=True):
            authorize()
            reference = (collection_id, version_id)
            if reference not in self._artifact_reads:
                self._artifact_reads.append(reference)

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

    async def start_sandbox(self, agent_id: str, sandbox_id: str, *, include_shared_python=False) -> dict[str, Any]:
        info = await self.services.start_sandbox(sandbox_id, agent_id=agent_id)
        from backend.sandbox.manager import SandboxManager
        backend = self.services.sandbox_backend
        python_status = await backend.python_status(sandbox_id) if isinstance(backend, SandboxManager) else None
        return {"sandbox_id": sandbox_id, "state": info.state.value,
                "shared_python": python_status if include_shared_python else None,
                "python_environment": _python_environment_summary(python_status)}

    async def stop_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]:
        info = await self.services.stop_sandbox(sandbox_id, agent_id=agent_id)
        return {"sandbox_id": sandbox_id, "state": info.state.value}

    async def sandbox_workspace_action(self, capability, operation, options):
        from backend.sandbox_workspace import inspect_workspace
        return await inspect_workspace(self.services, capability, operation, options)

    async def inspect_sandbox(self, agent_id: str, sandbox_id: str, *, history_scope="caller", history_limit=3,
                              include_output=False, include_shared_python=False) -> dict[str, Any]:
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
        workspace_binding = task_binding(self.services, sandbox_id, agent_id)
        task_directory = workspace_binding["directory"]
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
        context = self.services.run_manager.current_context
        if history_scope == "run" and context is None:
            raise ResourceValidationError("Run history requires an active Agent Run; use caller history instead")
        return {
            "sandbox_id": sandbox_id, "state": info.state.value,
            "commands_accepted": commands_accepted, "state_guidance": state_guidance,
            "runtime_id": info.runtime_id, "platform": info.platform,
            "shell": list(info.shell), "workspace": str(info.workspace / task_directory) if task_directory else str(info.workspace),
            "sandbox_workspace": str(info.workspace),
            "task_workspace": {key: value for key, value in workspace_binding.items() if key != "shared_inputs"},
            "shared_input_count": len(workspace_binding.get("shared_inputs", [])),
            "shared_workspace": str(info.workspace / "shared"),
            "file_paths": "Relative command/Skill/file-tool paths use workspace. Use scope=all for read-only access to other workspace files, or scope=shared for explicit shared/ inputs.",
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
            "recent_commands": recent_summaries(self.services, sandbox_id,
                caller=agent_id if history_scope != "all" else None,
                run_id=context.run_id if history_scope == "run" else None,
                limit=history_limit, include_output=include_output),
            "history_scope": history_scope,
            "shared_python": python_status if include_shared_python else None,
            "python_environment": _python_environment_summary(python_status),
            "console_mode": "non-interactive; every command and relative file tool uses this assigned workspace; cd/export/activation do not persist",
            "command_timeout": self.services.world.get_card(sandbox_id).config.get("command_timeout", 6000),
            "installation": "Use install_python_packages for the shared read-only Python environment. HOME persists (at /sandbox/home on Linux, WSL and macOS Container VM); use $HOME/.local/bin or $HOME/bin for local CLI tools, or create a private venv in HOME/workspace and invoke its interpreter explicitly. npm -g defaults to $HOME/.local, with bins on PATH. /tmp is recreated for EVERY command; use HOME or workspace for files needed by subsequent commands. Shared installer logs are optional (include_shared_python=true) and do not prove a current operation is running.",
            "attachments": [
                {"resource_id": item.resource_id,
                 "path": str(info.resources_path / item.relative_path.replace("\\", "/")) if info.resources_path else None,
                 "access": item.access.value}
                for item in info.attachments
            ],
        }


class WorldAgentCapabilityProvider:
    """ADK-neutral adapter from scoped tools to live broker operations."""

    def resolve_model(self, model):
        from backend.agents.models import RuntimeModelConnection, AgentConfigurationError
        from backend.security.model_connections import ModelConnectionStore, MODEL_REF_PREFIX
        store = ModelConnectionStore(self.services.llm_settings)
        if model == "oaw:default":
            model = store.read().default_model
        if not model:
            raise AgentConfigurationError("Choose a default model in Settings / Models.")
        if model.startswith(MODEL_REF_PREFIX):
            return store.resolve_runtime(model)
        options = store.legacy_options() or {}
        return RuntimeModelConnection("legacy", model, base_url=options.get("api_base", ""), api_key=options.get("api_key", ""))

    def runtime_state_directory(self, agent_id):
        import hashlib
        self.services.world.get_card(agent_id)
        return self.services.settings.data_root / "runtime-state" / hashlib.sha256(agent_id.encode()).hexdigest()

    def managed_environment_root(self):
        return self.services.settings.data_root

    def subprocess_environment(self):
        from backend.agents.environment import subprocess_environment
        return subprocess_environment(inherit=self.services.settings.inherit_runtime_environment)

    def runtime_execution(self, agent_id, run_id, *, workspace, home):
        from contextlib import asynccontextmanager
        from backend.runs.models import TERMINAL_RUN_STATUSES
        @asynccontextmanager
        async def open_execution():
            manager = self.services._require_run_manager()
            context = manager.current_context
            if (context is None or (context.agent_id, context.run_id) != (agent_id, run_id)
                    or manager.get_run(run_id).status in TERMINAL_RUN_STATUSES):
                raise ResourceValidationError("Runtime execution requires the current active agent run")
            async with self.services.open_runtime_execution(agent_id, run_id, workspace=workspace, home=home) as execution:
                yield execution
        return open_execution()

    def runtime_execution_configuration(self, agent_id):
        return self.services.runtime_execution_configuration(agent_id)

    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    def _conversation_input_scope(self, agent_id, run_id):
        from backend.runs.models import TERMINAL_RUN_STATUSES
        manager = self.services._require_run_manager()
        context = manager.current_context
        if context is None or (context.agent_id, context.run_id) != (agent_id, run_id):
            raise ResourceValidationError('Conversation input requires the current agent run')
        run = manager.get_run(run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return None
        conversation_id, session_id = manager._conversation_scope(run)
        if not conversation_id or not session_id:
            return None
        # Parent lineage is retained for tracing/cancellation. It does not admit
        # a private helper into its parent's conversation or attachment history.
        if run.context_id != session_id:
            return None
        session = self.services.conversations.get_session(conversation_id, session_id)
        self.services._require_session_participant(session, agent_id)
        self.services._require_conversation_connection(agent_id, conversation_id)
        return conversation_id, session_id

    async def pending_conversation_input(self, agent_id, run_id):
        scope = self._conversation_input_scope(agent_id, run_id)
        if scope is None:
            return []
        result = []
        for message_id in self.services.deliveries.pending_messages(agent_id, *scope):
            message = self.services.conversations.get_message(message_id)
            text = f"{message.sender_name}: {message.content}"
            for attachment in message.attachments:
                text += (f"\n[Attachment: {attachment.name}; version_id={attachment.version_id}; "
                         f"path={attachment.path}; {attachment.size_bytes} bytes]")
            result.append({"id": message.id, "text": text, "sequence": message.sequence})
        return result

    async def acknowledge_conversation_input(self, agent_id, run_id, message_ids):
        scope = self._conversation_input_scope(agent_id, run_id)
        if scope is None:
            return
        claimed = self.services.deliveries.claim_batch(*scope, agent_id, run_id, message_ids)
        if claimed:
            manager = self.services._require_run_manager()
            lifecycle = manager.get_run(run_id).lifecycle
            manager.store.update_lifecycle(run_id,
                delivery_message_ids=list(dict.fromkeys([*lifecycle.get('delivery_message_ids', []), *claimed])),
                conversation_max_sequence=max(self.services.conversations.get_message(mid).sequence for mid in claimed))
            # Refresh delivery badges without manufacturing another chat message.
            await self.services._publish_conversation_message(self.services.conversations.get_message(claimed[-1]))

    def _conversation_file_scope(self, agent_id, run_id):
        scope = self._conversation_input_scope(agent_id, run_id)
        if scope is None:
            return None
        manager = self.services._require_run_manager()
        run = manager.get_run(run_id)
        context = manager.current_context
        if run.agent_id != agent_id or context.context_id != scope[1] or run.context_id != scope[1]:
            raise ResourceValidationError('Conversation files require this Run\'s current session')
        # Delegated runtimes inherit the admitting conversation Run's history
        # boundary; they must not see follow-ups that have not been accepted.
        boundary = run
        while boundary.caller_kind != 'conversation':
            boundary = manager.get_run(boundary.parent_run_id)
        return (*scope, boundary.lifecycle.get('conversation_max_sequence'))

    def _conversation_attachment_entries(self, agent_id, run_id):
        from backend.errors import NotFoundError, PermissionDeniedError
        scope = self._conversation_file_scope(agent_id, run_id)
        if scope is None:
            return []
        conversation_id, session_id, max_sequence = scope
        store = self.services.resources.artifacts
        store.authorize(self.services, conversation_id, agent_id)
        result = {}
        for message in self.services.conversations.context_messages(conversation_id, session_id, max_sequence):
            for attachment in message.attachments:
                key = (attachment.version_id, attachment.path)
                try:
                    store.authorize(self.services, conversation_id, agent_id, version_id=attachment.version_id)
                    if store.get(attachment.version_id)['state'] != 'ready':
                        continue
                except (NotFoundError, PermissionDeniedError):
                    # Retained messages may outlive a released file or removed
                    # collection reference. Only list currently readable files.
                    continue
                result[key] = {**attachment.model_dump(mode='json'), 'message_id': message.id,
                    'sequence': message.sequence, 'conversation_id': conversation_id, 'session_id': session_id}
        return sorted(result.values(), key=lambda entry: entry['sequence'])

    async def conversation_attachments(self, agent_id, run_id):
        async with self.services._node_mutation(read_only=True):
            return self._conversation_attachment_entries(agent_id, run_id)

    async def read_conversation_attachment(self, agent_id, run_id, version_id, path, *, max_bytes=INLINE_FILE_BYTES):
        from contextlib import aclosing
        if type(max_bytes) is not int or not 0 < max_bytes <= INLINE_FILE_BYTES:
            raise ResourceValidationError('Attachment read limit must be between 1 byte and 32 MiB')
        content = bytearray()
        async with aclosing(self.stream_conversation_attachment(agent_id, run_id, version_id, path, max_bytes=max_bytes)) as stream:
            async for chunk in stream:
                content.extend(chunk)
        return bytes(content)

    async def stream_conversation_attachment(self, agent_id, run_id, version_id, path, *, max_bytes=MAX_FILE_BYTES):
        from contextlib import aclosing
        from backend.errors import PermissionDeniedError
        validate_file_limit(max_bytes)

        def authorize():
            attachment = next((entry for entry in self._conversation_attachment_entries(agent_id, run_id)
                if entry['version_id'] == version_id and entry['path'] == path), None)
            if attachment is None:
                raise PermissionDeniedError('Attachment is not in this Run\'s visible conversation history')
            if attachment['size_bytes'] > max_bytes:
                raise ResourceValidationError(f'Attachment exceeds the {max_bytes}-byte transfer limit')
            return attachment

        async with self.services._node_mutation(read_only=True):
            attachment = authorize()
        size = 0
        store = self.services.resources.artifacts
        # ArtifactStore takes its own lock before graph access. Never hold the
        # graph lock while awaiting its stream; reauthorize after every read.
        async with aclosing(store.read(self.services, attachment['conversation_id'], version_id, path, agent_id)) as stream:
            async for chunk in stream:
                async with self.services._node_mutation(read_only=True):
                    authorize()
                if size + len(chunk) > max_bytes:
                    raise ResourceValidationError(f'Attachment exceeds the {max_bytes}-byte transfer limit')
                size += len(chunk)
                yield chunk
        async with self.services._node_mutation(read_only=True):
            authorize()

    async def publish_conversation_attachment(self, agent_id, run_id, name, data, *, request_key):
        import hashlib
        if not isinstance(data, bytes) or len(data) > INLINE_FILE_BYTES:
            raise ResourceValidationError('Runtime attachments must be bytes of at most 32 MiB')
        async def chunks():
            from backend.file_transfer import FILE_CHUNK_BYTES
            for offset in range(0, len(data), FILE_CHUNK_BYTES):
                yield data[offset:offset + FILE_CHUNK_BYTES]
        return await self.publish_conversation_attachment_stream(agent_id, run_id, name, chunks(),
            request_key=request_key, expected_sha256=hashlib.sha256(data).hexdigest())

    async def publish_conversation_attachment_stream(self, agent_id, run_id, name, chunks, *,
            request_key, expected_sha256=None, max_bytes=MAX_FILE_BYTES):
        from backend.conversations.attachments import resolve
        from backend.conversations.models import ConversationAttachmentRef
        store = self.services.resources.artifacts

        def authorize():
            scope = self._conversation_file_scope(agent_id, run_id)
            if scope is None:
                raise ResourceValidationError('Publishing attachments requires an active conversation Run')
            conversation_id, session_id, _ = scope
            store.authorize(self.services, conversation_id, agent_id, 'artifact.publish')
            return conversation_id, session_id

        # Own the source before any validation, including paths that never read
        # a chunk. Reject invalid authority before consuming or spooling bytes.
        async with aclosing(chunks):
            validate_file_limit(max_bytes)
            if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
                raise ResourceValidationError('Attachment request_key must contain 1 to 128 characters')
            async with self.services._node_mutation(read_only=True):
                conversation_id, session_id = authorize()

            async def checked_chunks():
                async with aclosing(bounded_chunks(chunks, max_bytes=max_bytes)) as source:
                    async for chunk in source:
                        async with self.services._node_mutation(read_only=True):
                            if authorize() != (conversation_id, session_id):
                                raise ResourceValidationError('Attachment publication scope changed')
                        yield chunk
                async with self.services._node_mutation(read_only=True):
                    authorize()

            if expected_sha256 is None:
                async with receive_file(checked_chunks(), name=name, max_bytes=max_bytes) as file:
                    return await self.publish_conversation_attachment_stream(agent_id, run_id, name, file.chunks(),
                        request_key=request_key, expected_sha256=file.sha256, max_bytes=max_bytes)
            async with aclosing(checked_chunks()) as source:
                record = await store.upload(self.services, conversation_id, session_id, name, source,
                    agent_id=agent_id, run_id=run_id, request_key=request_key, expected_sha256=expected_sha256)
            async with self.services._node_mutation(read_only=True):
                authorize()
                attachment = resolve(self.services, conversation_id, session_id,
                    [ConversationAttachmentRef(version_id=record['version_id'], path=name)], agent_id)[0]
                return {**attachment.model_dump(mode='json'), 'conversation_id': conversation_id, 'session_id': session_id}

    async def publish_execution(self, agent_id, run_id, snapshot):
        from backend.plugins.execution_graph import ExecutionSnapshot, MAX_EXECUTION_BYTES
        manager = self.services._require_run_manager()
        context = manager.current_context
        if context is None or context.agent_id != agent_id or context.run_id != run_id:
            raise ResourceValidationError('Execution projection requires the current agent run')
        value = ExecutionSnapshot.model_validate({**snapshot, 'run_id': run_id}).model_dump(mode='json')
        if value['mode'] != 'full' or value['next_cursor'] is not None:
            raise ResourceValidationError('Execution publications must be complete snapshots')
        conversation_id, session_id = manager._conversation_scope(manager.get_run(run_id))
        value['history_id'] = value['history_id'] or f'{agent_id}:{session_id or "default"}'
        value['total_nodes'] = len(value['nodes'])
        import json
        async with self.services._node_mutation(read_only=True):
            previous = manager.get_run(run_id).lifecycle.get('execution_graph') or {}
            if previous.get('history_id') == value['history_id']:
                previous_revision = previous.get('revision', 0)
                # Delayed workers must not roll persisted state back. Legacy
                # providers omit revision; the host sequences their full writes.
                if value['revision'] and value['revision'] <= previous_revision:
                    return
            else:
                previous_revision = 0
            value['revision'] = value['revision'] or previous_revision + 1
            if value['retention'] == 'history':
                # Only explicit history providers opt in. Keep the durable
                # activity identities even when a worker publishes a partial
                # view of earlier work, including across turns in this scope.
                prior = previous
                if prior.get('history_id') != value['history_id']:
                    prior = next((run.lifecycle['execution_graph'] for run in reversed(manager.list_runs(agent_id=agent_id))
                        if run.run_id != run_id
                        and run.runtime_provider_id == manager.get_run(run_id).runtime_provider_id
                        and manager._conversation_scope(run) == (conversation_id, session_id)
                        and run.lifecycle.get('execution_graph', {}).get('history_id') == value['history_id']
                        and run.lifecycle['execution_graph'].get('retention') == 'history'), {})
                for field in ('nodes', 'edges'):
                    records = {item['id']: item for item in prior.get(field, [])}
                    records.update({item['id']: item for item in value[field]})
                    value[field] = list(records.values())
                value['total_nodes'] = len(value['nodes'])
                # Revalidate limits after the union, never silently discard old
                # history to make room for a new publication.
                value = ExecutionSnapshot.model_validate(value).model_dump(mode='json')
            if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_EXECUTION_BYTES:
                raise ResourceValidationError('Execution projection exceeds 8 MiB')
            manager.store.update_lifecycle(run_id, execution_graph=value)
        # Invalidate only: reads still recheck the live connection and session.
        from backend.events.models import EventType
        await self.services.events.publish(EventType.EXECUTION_UPDATED,
            node_id=agent_id, agent_id=agent_id, run_id=run_id,
            conversation_id=conversation_id, session_id=session_id,
            payload={'history_id': value['history_id'], 'revision': value['revision']})

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
        context = self.services.run_manager.current_context
        if context and context.agent_id == agent_id and context.delegation_context:
            from backend.plugins.execution import ExecutionReport
            schema = ExecutionReport.model_json_schema()
            definitions.append(ScopedToolDefinition(
                capability_id="host:report_delegated_task", name="report_delegated_task",
                description="Report only this Run's assigned work before ending. Complete requires evidence; "
                    "partial/blocked require a useful next_step. Waiting on external jobs requires their references "
                    "and check_after_seconds (1-86400); OAW schedules a coordinator check. "
                    "Reports are claims awaiting verification, never task acceptance. Return paths, not file contents.",
                parameters=tuple(ToolParameter(name, _python_type(prop.get("type")),
                    prop.get("description", "Report field"), name in schema.get("required", []))
                    for name, prop in sorted(schema["properties"].items(), key=lambda item: item[0] not in schema.get("required", []))),
                input_schema=schema))
        definitions.extend(self.services.background_jobs.definitions(agent_id))
        return definitions

    async def invoke_tool(
        self,
        agent_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        from backend.background_jobs import TOOLS as JOB_TOOLS
        if capability_id.startswith("host:") and capability_id[5:] in JOB_TOOLS:
            return await self.services.background_jobs.invoke(agent_id, capability_id[5:], dict(arguments))
        if capability_id == "host:report_delegated_task":
            from backend.plugins.execution import ExecutionReport
            report = _validate_tool_request(ExecutionReport, dict(arguments))
            return await self.services.node_execution.report_delegated_task(agent_id, report.model_dump())
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
                result = await handler(_CapabilityContext(self.services, capability, dict(arguments)),
                                       capability, dict(arguments))
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
            raise ConflictError(f"{exc}. Inspect Sandbox state and activity. If stopped, start it through authorized "
                "Sandbox controls or ask the coordinator; otherwise wait for the conflicting operation before retrying.") from exc
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
