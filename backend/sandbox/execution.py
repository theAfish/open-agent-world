"""Authorize, execute and record a command against the live application state."""

from __future__ import annotations

import asyncio
import math
from contextvars import Token
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from backend.capabilities.projection import authorize_invocation
from backend.errors import DomainError, ResourceValidationError
from backend.execution_config import effective_variables, resolve_sandbox_configuration
from backend.security.redaction import redact
from backend.world.models import CardType

from .base import SandboxBackend
from .commands import needs_managed_python, require_noninteractive
from .history import key as command_history_key, save
from .manager import SandboxManager
from .models import (
    CommandResult, SandboxError, SandboxEvent, SandboxEventType,
    SandboxStateError, SandboxValidationError, execution_command_id,
)
from .pack_profiles import bind_command_profiles, execution_profile_references

if TYPE_CHECKING:
    from backend.services import ApplicationServices
    from backend.skill_runtime import RunSkillScript


def _validate_execution_options(python_environment: str, timeout_seconds: float | None) -> None:
    if not isinstance(python_environment, str) or python_environment not in {"auto", "managed", "none"}:
        raise SandboxValidationError("python_environment must be auto, managed, or none")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 36000
    ):
        raise SandboxValidationError("timeout_seconds must be finite and between 0 (exclusive) and 36000 seconds")


async def execute_sandbox(
    services: ApplicationServices,
    sandbox_id: str,
    argv: list[str] | None = None,
    *,
    command: str | None = None,
    timeout_seconds: float | None = None,
    agent_id: str | None = None,
    skill_request: RunSkillScript | None = None,
    background_job_id: str | None = None,
    keep_on_disconnect: bool = False,
    operation_id: str | None = None,
    environment_id: str | None = None,
    target_id: str | None = None,
    python_environment: str = "auto",
    working_directory: str | None = None,
) -> CommandResult:
    _validate_execution_options(python_environment, timeout_seconds)
    async with services._portable_state_gate.execution():
        # Validate graph authority without holding the barrier during execution.
        async with services._node_mutation():
            services._require_card_type(sandbox_id, CardType.SANDBOX)
            if agent_id is not None:
                services.capabilities.require_sandbox_execute(agent_id, sandbox_id)
            from backend.card_state import active_state_session
            if services.run_manager.current_context is not None or active_state_session.get() is not None:
                from backend.workspace_context import session_context
                session_context(services, sandbox_id)
        backend = services._require_sandbox_backend()
        if (argv is None) == (command is None):
            raise SandboxValidationError("provide exactly one of command or argv")
        require_noninteractive(argv if skill_request is None else None, command)
        await services.ensure_sandbox_running(sandbox_id, agent_id=agent_id)
        if command is not None:
            sandbox_info = await backend.get(sandbox_id)
            if not sandbox_info.shell:
                raise SandboxStateError("No execution shell is available for this Sandbox")
            argv = [*sandbox_info.shell, command]
        assert argv is not None
        execution = _CommandExecution(
            services=services, backend=backend, sandbox_id=sandbox_id, argv=argv,
            timeout_seconds=timeout_seconds, agent_id=agent_id, skill_request=skill_request,
            background_job_id=background_job_id, operation_id=operation_id,
            environment_id=environment_id, target_id=target_id, python_environment=python_environment,
            requested_directory=working_directory,
        )
        execution_task = asyncio.create_task(execution.run())
        try:
            return await asyncio.shield(execution_task)
        except asyncio.CancelledError:
            await _finish_disconnected_execution(services, execution_task, keep_on_disconnect)
            raise


async def _finish_disconnected_execution(services, execution_task, keep_on_disconnect) -> None:
    if not keep_on_disconnect:
        for command_id, task in services._sandbox_tasks.items():
            if task is execution_task:
                receipt = services._sandbox_commands[command_id]
                receipt.update(cancellation_requested=True, cancellation_reason="caller_cancelled", cleanup="pending")
                save(services, receipt["sandbox_id"], receipt)
        if not execution_task.cancelling():
            execution_task.cancel()

    async def finish():
        await asyncio.gather(execution_task, return_exceptions=True)

    await services._complete_committed(finish())


@dataclass(kw_only=True)
class _CommandExecution:
    services: ApplicationServices
    backend: SandboxBackend
    sandbox_id: str
    argv: list[str]
    timeout_seconds: float | None
    agent_id: str | None
    skill_request: RunSkillScript | None
    background_job_id: str | None
    operation_id: str | None
    environment_id: str | None
    target_id: str | None
    python_environment: str
    requested_directory: str | None
    directory: str = field(default="", init=False)
    workspace_binding: dict = field(default_factory=dict, init=False)
    command_id: str = field(init=False)
    execution_argv: list[str] = field(init=False)
    options: dict[str, Any] = field(default_factory=dict, init=False)
    receipt: dict | None = field(default=None, init=False)
    concurrent_commands: tuple[dict, ...] = field(default=(), init=False)
    secret_token: Token | None = field(default=None, init=False)

    async def run(self) -> CommandResult:
        self.command_id = self.operation_id or uuid4().hex
        command_token = execution_command_id.set(self.command_id)
        try:
            await self._prepare()
            if isinstance(self.backend, SandboxManager):
                await self.backend.wait_for_python_preparation(
                    self.sandbox_id, self.execution_argv, self.python_environment,
                    skill=self.skill_request is not None,
                )
                await self._recheck_admission()
                self.options["on_command_start"] = self._mark_command_started
            else:
                await self._mark_command_started()
            result = await self.backend.execute(
                self.sandbox_id, self.execution_argv,
                timeout_seconds=self.timeout_seconds or self.services.world.get_card(self.sandbox_id).config.get("command_timeout", 6000),
                **self.options,
            )
            return await self._record_result(result)
        except asyncio.CancelledError:
            if self.receipt is not None:
                self.receipt.update(state="cancelled", cancelled=True, timed_out=False, termination_reason="cancelled")
                if self.receipt.get("cancellation_requested"):
                    self.receipt.update(cleanup="complete", termination_confirmed=True)
                    return CommandResult(
                        self.sandbox_id, tuple(self.receipt["argv"]), -9,
                        self.receipt.get("stdout", ""), self.receipt.get("stderr", ""),
                        (datetime.now(UTC) - datetime.fromisoformat(self.receipt["started_at"])).total_seconds(),
                        cancelled=True, command_id=self.command_id, concurrent_commands=self.concurrent_commands,
                    )
            raise
        except Exception as error:
            self._record_error(error)
            secrets = self.services._execution_secrets.get()
            if secrets:
                error_type = type(error) if isinstance(error, (DomainError, SandboxError)) else SandboxError
                raise error_type(redact(str(error), secrets)) from None
            raise
        finally:
            try:
                await self._finish_receipt()
            finally:
                if self.secret_token is not None:
                    self.services._execution_secrets.reset(self.secret_token)
                execution_command_id.reset(command_token)
                await self.services._refresh_sandbox_write_mounts(self.sandbox_id, agent_id=self.agent_id)

    def _authorize(self) -> None:
        if self.agent_id is None:
            return
        selectors = {
            "environment_id": self.environment_id,
            "target_id": self.target_id,
            "skill_id": self.skill_request.skill_id if self.skill_request else None,
        }
        capability_kind = "sandbox.run_skill_script" if self.skill_request else "sandbox.execute"
        authorize_invocation(self.services, self.agent_id, f"{capability_kind}:{self.sandbox_id}",
            {name: value for name, value in selectors.items() if value is not None})

    def _resolve_runtime(self):
        requirements = execution_profile_references(
            self.services, self.agent_id, self.sandbox_id, background_job_id=self.background_job_id,
        )
        if self.skill_request is not None:
            from backend.skill_runtime import resolve_skill_mount
            if self.agent_id is None:
                raise ResourceValidationError("Skill execution requires an Agent")
            return resolve_skill_mount(self.services, self.agent_id, self.sandbox_id, self.skill_request,
                runtime_requirements=requirements)
        if requirements:
            return bind_command_profiles(self.services.plugins, requirements, self.argv)
        return self.argv, None

    def _configure_python(self) -> None:
        if self.python_environment == "none" and self.execution_argv[:4] == ["python3", "-I", "-S", "_oaw_profile/runner.py"]:
            raise SandboxValidationError("Declared Pack environments require Sandbox Python; use auto or managed")
        if getattr(self.backend, "supports_optional_python_runtime", False):
            if isinstance(self.backend, SandboxManager):
                self.options["python_environment"] = self.python_environment
            else:
                self.options["managed_python"] = needs_managed_python(
                    self.execution_argv, self.python_environment, skill=self.skill_request is not None,
                )
        elif self.python_environment == "none":
            raise SandboxValidationError("This Sandbox runtime cannot omit its managed Python environment")

    def _configure_environment(self) -> tuple[str, ...]:
        _, pending_variables = effective_variables(self.services, self.sandbox_id, self.environment_id)
        if (pending_variables or self.target_id or self.environment_id) and not self.backend.supports_invocation_environment:
            raise SandboxValidationError("This Sandbox backend does not support invocation configuration")
        folder_mounts = []
        environment, secrets = resolve_sandbox_configuration(
            self.services, self.sandbox_id, self.environment_id, self.target_id, folder_mounts=folder_mounts,
        )
        if folder_mounts:
            if not self.backend.supports_folder_mounts:
                raise SandboxValidationError("This Sandbox runtime does not support folder variables")
            self.options["folder_mounts"] = tuple(folder_mounts)
        if environment:
            if not self.backend.supports_invocation_environment:
                raise SandboxValidationError("This Sandbox backend does not support invocation configuration")
            self.options["invocation_env"] = environment
            self.secret_token = self.services._execution_secrets.set(secrets)
        return secrets

    def _assert_admission(self) -> None:
        self.services.resources.artifacts.assert_source_idle(self.sandbox_id)
        self.services.summoning.assert_admission(self.sandbox_id)
        if self.sandbox_id in self.services._sandbox_stopping:
            raise SandboxStateError("Sandbox admission is closed while termination cleanup is pending")

    async def _configure_workspace(self) -> None:
        from .task_workspace import binding, ensure_directory, path_for
        if self.requested_directory is not None:
            if self.agent_id is not None:
                raise SandboxValidationError("Agent cwd is assigned by the host")
            self.directory = path_for("", self.requested_directory)
            previous = binding(self.services, self.sandbox_id)['directory']
            self.workspace_binding = await ensure_directory(self.services, self.sandbox_id)
            if self.directory == previous:
                self.directory = self.workspace_binding['directory']
        else:
            self.workspace_binding = await ensure_directory(self.services, self.sandbox_id, self.agent_id)
            self.directory = self.workspace_binding['directory']
        writable = self.workspace_binding.get('writable_directory', self.workspace_binding.get('directory') or None)
        if writable is not None:
            if not getattr(self.backend, 'supports_workspace_scopes', False):
                raise SandboxValidationError('This runtime cannot enforce session/group workspace write restrictions')
            self.options['workspace_write_directory'] = writable
        if self.directory:
            self.options['working_directory'] = self.directory

    async def _prepare(self) -> None:
        if getattr(self.backend, "supports_execution_policy", False):
            config = self.services.world.get_card(self.sandbox_id).config
            self.options["execution_policy"] = {
                key: config[key] for key in ("network_enabled", "memory_bytes", "active_process_limit", "command_timeout")
                if key in config
            }
        # Re-check after scheduling, immediately before preparing the command.
        async with self.services._node_mutation():
            self._authorize()
            self.execution_argv, runtime_mount = self._resolve_runtime()
            if runtime_mount is not None:
                self.options["runtime_mount"] = runtime_mount
            self._configure_python()
            secrets = self._configure_environment()
            self._assert_admission()
            await self._configure_workspace()
            self.services._require_card_type(self.sandbox_id, CardType.SANDBOX)
            self._start_receipt(secrets)

    def _start_receipt(self, secrets: tuple[str, ...]) -> None:
        # Build the entire journal entry before exposing it to history readers.
        receipt_data = {
            "id": self.command_id, "caller": self.agent_id or "user", "state": "running",
            "sandbox_id": self.sandbox_id, "python_environment": self.python_environment,
            "working_directory": self.directory, "session_id": self.workspace_binding.get('session_id'),
            "group_id": self.workspace_binding.get('group_id'),
            "history_key": command_history_key(self.services, self.sandbox_id),
            "run_id": self.services.run_manager.current_context.run_id if self.services.run_manager.current_context else None,
            "started_at": datetime.now(UTC).isoformat(), "argv": redact(list(self.execution_argv), secrets),
            "phase": "preparing_python" if isinstance(self.backend, SandboxManager)
                and needs_managed_python(self.execution_argv, self.python_environment, skill=self.skill_request is not None)
                else "dispatching",
            "skill_id": self.skill_request.skill_id if self.skill_request else None,
        }
        self.receipt = self.services._sandbox_commands.get(self.command_id, {}) if self.operation_id else {}
        self.receipt.update(receipt_data)
        self.concurrent_commands = tuple(
            {key: receipt.get(key) for key in ("id", "caller", "run_id", "argv", "started_at")}
            for receipt in self.services._sandbox_commands.values()
            if receipt["sandbox_id"] == self.sandbox_id and receipt["id"] != self.command_id
        )
        self.services._sandbox_commands[self.command_id] = self.receipt
        self.services._sandbox_tasks[self.command_id] = asyncio.current_task()
        save(self.services, self.sandbox_id, self.receipt)

    async def _recheck_admission(self) -> None:
        # Queue waits and interpreter preparation can outlive grants and bindings.
        async with self.services._node_mutation(read_only=True):
            self.services._require_card_type(self.sandbox_id, CardType.SANDBOX)
            self._authorize()
            current_argv, current_mount = self._resolve_runtime()
            if current_argv != self.execution_argv or current_mount != self.options.get("runtime_mount"):
                raise SandboxValidationError("Pack environment or Skill changed while this command was preparing; retry the command")
            self._assert_admission()

    async def _mark_command_started(self) -> None:
        # The manager can prepare another interpreter after the initial queue wait.
        await self._recheck_admission()
        self.receipt["phase"] = "executing"
        save(self.services, self.sandbox_id, self.receipt)
        await self.services._emit_sandbox_event(SandboxEvent(
            self.sandbox_id, SandboxEventType.COMMAND_STARTED,
            {"command_id": self.command_id, "argv": self.receipt["argv"], "working_directory": self.directory, "concurrent_commands": self.concurrent_commands},
        ))

    async def _record_result(self, result: CommandResult) -> CommandResult:
        secrets = self.services._execution_secrets.get()
        if secrets:
            result = replace(result, stdout=redact(result.stdout, secrets), stderr=redact(result.stderr, secrets),
                argv=tuple(redact(list(result.argv), secrets)))
            for event_type, output in ((SandboxEventType.STDOUT, result.stdout), (SandboxEventType.STDERR, result.stderr)):
                if output:
                    await self.services._emit_sandbox_event(SandboxEvent(self.sandbox_id, event_type, {"text": output}))
        self.receipt.update(
            state="timed_out" if result.timed_out else "cancelled" if result.cancelled else "finished",
            stdout=result.stdout[-65536:], stderr=result.stderr[-65536:], exit_code=result.exit_code,
            duration_seconds=result.duration_seconds, timed_out=result.timed_out, cancelled=result.cancelled,
            termination_reason="timeout" if result.timed_out else "cancelled" if result.cancelled else "exit",
        )
        if self.receipt.get("cancellation_requested"):
            self.receipt.update(cleanup="complete", termination_confirmed=True)
        return replace(result, command_id=self.command_id, concurrent_commands=self.concurrent_commands, working_directory=self.directory)

    def _record_error(self, error: Exception) -> None:
        if self.receipt is None:
            return
        self.receipt.update(state="error", error=redact(str(error), self.services._execution_secrets.get())[:4096])
        if self.receipt.get("cancellation_requested"):
            self.receipt.update(cleanup="failed", termination_confirmed=False, cleanup_error=self.receipt["error"])
            self.services._sandbox_stopping.add(self.sandbox_id)

    async def _finish_receipt(self) -> None:
        try:
            if self.receipt is not None:
                if self.receipt["state"] == "running":
                    self.receipt.update(state="interrupted", error="Command interrupted")
                save(self.services, self.sandbox_id, self.receipt)
        finally:
            if self.receipt is not None and self.operation_id is None:
                self.services._sandbox_commands.pop(self.command_id, None)
                self.services._sandbox_tasks.pop(self.command_id, None)
            if self.receipt is not None:
                await self.services._emit_sandbox_event(SandboxEvent(
                    self.sandbox_id, SandboxEventType.COMMAND_FINISHED,
                    {key: self.receipt.get(key) for key in (
                        "argv", "caller", "run_id", "state", "exit_code", "duration_seconds", "cancelled", "timed_out",
                    )} | {"command_id": self.command_id},
                ))
