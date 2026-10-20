"""Agent Sandbox adapter: one logical Sandbox per profile.

Commands are exclusive and the VM is released after each command, including
cancellation, so detached descendants cannot survive command completion. Files
live on an operator-provisioned dedicated volume, or on ECS through the snapshot
subclass. This intentionally does not
claim support for concurrent commands, host mounts or private runtime bundles.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time
from uuid import uuid4

from backend.sandbox.base import SandboxBackend
from backend.sandbox.environment import validate_command_environment
from backend.sandbox.models import (
    CommandResult, ResourceAccess, SandboxEvent, SandboxEventType, SandboxInfo,
    SandboxNotFoundError, SandboxSecurityError, SandboxState, SandboxStateError,
    SandboxValidationError, execution_command_id,
)
from backend.sandbox.registry import SandboxRuntime, SandboxRuntimeRegistration, SandboxRuntimeRegistry


@dataclass(frozen=True)
class AliyunSandboxSettings:
    region: str
    api_key: str = field(repr=False)
    template: str
    volume: str
    profile_id: str
    lifetime_seconds: int = 600
    metadata: dict[str, str] = field(default_factory=dict, repr=False)
    unavailable_reason: str | None = None
    workspace_store: str = "volume"

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,48}", self.region):
            raise ValueError("Invalid Alibaba Sandbox region")
        if self.workspace_store not in {"volume", "ecs"}:
            raise ValueError("Unsupported workspace store")
        if not self.api_key or not self.template or not self.profile_id or (self.workspace_store == "volume" and not self.volume):
            raise ValueError("Sandbox API key, template, dedicated volume and profile ID are required")
        if not 60 <= self.lifetime_seconds <= 3600:
            raise ValueError("Sandbox lifetime must be 60..3600 seconds in this POC")
        if set(self.metadata) - {"fc.sandbox.network.vpc", "fc.sandbox.auth.role"}:
            raise ValueError("Unsupported platform metadata")
        if any(not isinstance(v, str) for v in self.metadata.values()):
            raise ValueError("Sandbox metadata values must be strings")

    @property
    def connection(self):
        return {"api_key": self.api_key, "api_url": f"https://api.{self.region}.sandbox.aliyuncs.com",
                "domain": f"{self.region}.sandbox.aliyuncs.com", "request_timeout": 20}


class AliyunSandboxBackend(SandboxBackend):
    supports_invocation_environment = True

    def __init__(self, root: Path, settings: AliyunSandboxSettings, event_sink=None, *, sdk=None):
        if sdk is None:
            from e2b import AsyncSandbox
            sdk = AsyncSandbox
        self.sdk, self.settings, self.event_sink = sdk, settings, event_sink
        self.path = root / "aliyun-sandbox.json"
        self.lock = asyncio.Lock()
        self.execution = None

    def _read(self, sandbox_id):
        if not self.path.exists():
            raise SandboxNotFoundError("Sandbox has not been bound")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("sandbox_id") != sandbox_id:
            raise SandboxNotFoundError("This POC supports one logical Sandbox per profile")
        if value.get("configuration") != self._configuration():
            raise SandboxSecurityError("Sandbox binding differs from the configured profile, region, template or volume")
        return value

    def _configuration(self):
        value = {"profile": self.settings.profile_id, "region": self.settings.region,
                "template": self.settings.template, "volume": self.settings.volume,
                "metadata": self.settings.metadata}
        if self.settings.workspace_store != "volume":
            value["workspace_store"] = self.settings.workspace_store
        return value

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(value))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    async def create(self, sandbox_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", sandbox_id):
            raise SandboxValidationError("Invalid Sandbox ID")
        async with self.lock:
            if self.path.exists():
                raise SandboxStateError("This POC allows one Sandbox Card per user profile")
            self._write({"version": 1, "sandbox_id": sandbox_id, "configuration": self._configuration(),
                         "remote_id": None, "request_key": None, "command": None, "state": "stopped"})
        return await self.get(sandbox_id)

    async def managed_workspace(self, sandbox_id):
        self._read(sandbox_id)
        raise SandboxValidationError("Remote workspace has no host path; use scoped file operations")

    async def configure(self, sandbox_id, *, workspace_path, workspace_access):
        if workspace_path is not None or workspace_access != ResourceAccess.READ_WRITE:
            raise SandboxValidationError("This adapter uses only its dedicated remote read/write volume")
        return await self.get(sandbox_id)

    def _info(self, value):
        state = {"stopped": SandboxState.STOPPED, "ready": SandboxState.READY,
                 "running": SandboxState.RUNNING}.get(value["state"], SandboxState.ERROR)
        if value["command"] is not None and self.execution is None:
            state = SandboxState.ERROR
        return SandboxInfo(value["sandbox_id"], state, PurePosixPath("/workspace"),
            security_boundary="aliyun-agent-sandbox", runtime_id="aliyun.agent-sandbox", platform="linux",
            shell=("/bin/sh", "-c"), available=state != SandboxState.ERROR and not self.settings.unavailable_reason,
            unavailable_reason=self.settings.unavailable_reason or (
                "Remote state requires reconciliation; no command will be resubmitted" if state == SandboxState.ERROR else None),
            network_reason="This POC disables internet access; network grants are not implemented")

    async def get(self, sandbox_id):
        # A poll reports our durable receipt; it never creates/resumes a VM.
        return self._info(self._read(sandbox_id))

    async def _connect(self, value):
        from e2b import NotFoundException
        try:
            sandbox = await self.sdk.connect(value["remote_id"], **self.settings.connection)
            info = await sandbox.get_info()
        except NotFoundException:
            # Only a confirmed expired idle VM can be replaced. _start rejects
            # every unfinished command before reaching this branch.
            value.update(remote_id=None, state="stopped")
            self._write(value)
            return None
        if (info.metadata.get("oaw.profile") != self.settings.profile_id
                or info.metadata.get("oaw.request") != value["request_key"]):
            raise SandboxSecurityError("Remote Sandbox ownership metadata does not match its binding")
        await sandbox.set_timeout(self.settings.lifetime_seconds)
        return sandbox

    async def _reconcile(self, value):
        from e2b import SandboxQuery
        paginator = self.sdk.list(query=SandboxQuery(metadata={"oaw.profile": self.settings.profile_id,
                                  "oaw.request": value["request_key"]}), **self.settings.connection)
        matches = []
        for _ in range(10):
            if not paginator.has_next:
                break
            matches.extend(await paginator.next_items())
        if paginator.has_next or len(matches) != 1:
            raise SandboxStateError("Sandbox creation outcome is unknown; inspect the recorded request key in the cloud console")
        value["remote_id"] = matches[0].sandbox_id
        value["state"] = "ready"
        self._write(value)

    async def _start(self, value):
        if self.settings.unavailable_reason:
            raise SandboxStateError(self.settings.unavailable_reason)
        if value["command"] is not None:
            raise SandboxStateError("A previous command has an uncertain outcome; inspect or explicitly stop the Sandbox, never resubmit")
        if value["state"] == "uncertain":
            raise SandboxStateError("Remote cleanup outcome is uncertain; explicitly stop the Sandbox before admitting more work")
        if value["state"] == "creating":
            await self._reconcile(value)
        if value["remote_id"]:
            sandbox = await self._connect(value)
            if sandbox is not None:
                return sandbox
        # Reserve before the cloud side effect. A failed create is reconciled,
        # never retried just because the HTTP response was lost.
        value.update(state="creating", request_key=uuid4().hex)
        self._write(value)
        sandbox = await self.sdk.create(template=self.settings.template, timeout=self.settings.lifetime_seconds,
            metadata={**self.settings.metadata, "oaw.profile": self.settings.profile_id, "oaw.request": value["request_key"]},
            volume_mounts={"/workspace": self.settings.volume} if self.settings.workspace_store == "volume" else {},
            allow_internet_access=False,
            network={"allow_public_traffic": False},
            **self.settings.connection)
        value.update(remote_id=sandbox.sandbox_id, state="ready")
        self._write(value)
        return sandbox

    async def start(self, sandbox_id):
        if self.lock.locked():
            raise SandboxStateError("This Sandbox is busy; this POC serializes remote operations")
        async with self.lock:
            value = self._read(sandbox_id)
            try:
                await self._start(value)
            except (SandboxStateError, SandboxSecurityError):
                raise
            except Exception:
                raise SandboxStateError("Alibaba Sandbox is unavailable; inspect the durable binding before retrying") from None
            return self._info(value)

    async def _dispose(self, value):
        if value["remote_id"]:
            await self.sdk.kill(value["remote_id"], **self.settings.connection)
        value.update(remote_id=None, state="stopped", command=None)
        self._write(value)

    async def _join_dispose(self, value):
        task = asyncio.create_task(self._dispose(value))
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            task.result()
        except Exception:
            value["state"] = "uncertain"
            self._write(value)
            raise SandboxStateError("Cloud cleanup could not be confirmed; the remote binding was retained") from None

    async def _ensure_remote(self, value):
        try:
            return await self._start(value)
        except (SandboxStateError, SandboxSecurityError):
            raise
        except Exception:
            raise SandboxStateError("Alibaba Sandbox is unavailable; inspect its binding before retrying") from None

    async def execute(self, sandbox_id, argv, *, timeout_seconds=None, env=None, invocation_env=None,
                      runtime_mount=None, execution_policy=None, folder_mounts=()):
        if runtime_mount is not None or folder_mounts or execution_policy:
            raise SandboxValidationError("Remote runtime bundles, host folders and per-command resource policies are not implemented")
        if not argv or any(not isinstance(v, str) or "\0" in v for v in argv):
            raise SandboxValidationError("Expected nonempty NUL-free argv")
        environment = validate_command_environment(env or {})
        invocation = validate_command_environment(invocation_env or {})
        if {k.upper() for k in environment} & {k.upper() for k in invocation}:
            raise SandboxValidationError("Command and invocation environment overlap")
        environment.update(invocation)
        timeout = timeout_seconds if timeout_seconds is not None else 60
        if not 0 < timeout < self.settings.lifetime_seconds - 10:
            raise SandboxValidationError("Command timeout must be shorter than the configured Sandbox lifetime minus 10 seconds")
        if self.lock.locked():
            raise SandboxStateError("This POC permits only one remote operation at a time")
        async with self.lock:
            value = self._read(sandbox_id)
            sandbox = await self._ensure_remote(value)
            command_id = execution_command_id.get() or uuid4().hex
            value.update(state="running", command={"id": command_id, "state": "admitted"})
            self._write(value)
            self.execution = asyncio.current_task()
            started = time.monotonic()
            from e2b.sandbox.commands.command_handle import CommandExitException
            output_bytes = 0

            class OutputLimitExceeded(Exception):
                pass

            async def output(kind, text):
                nonlocal output_bytes
                output_bytes += len(text.encode("utf-8"))
                if output_bytes > 1024 * 1024:
                    raise OutputLimitExceeded()
                if self.event_sink:
                    await self.event_sink(SandboxEvent(sandbox_id, kind, {"command_id": command_id, "text": text}))
            try:
                async with asyncio.timeout(timeout):
                    handle = await sandbox.commands.run(shlex.join(argv), background=True, envs=environment,
                        cwd="/workspace", user="user", timeout=timeout,
                        on_stdout=lambda text: output(SandboxEventType.STDOUT, text),
                        on_stderr=lambda text: output(SandboxEventType.STDERR, text))
                    value["command"].update(pid=handle.pid, state="running")
                    self._write(value)
                    try:
                        result = await handle.wait()
                    except CommandExitException as exc:
                        result = exc
                answer = CommandResult(sandbox_id, tuple(argv), result.exit_code, result.stdout, result.stderr,
                                       time.monotonic() - started, command_id=command_id)
                value["command"]["state"] = "completed"
                self._write(value)
                await self._join_dispose(value)
                return answer
            except OutputLimitExceeded:
                await self._join_dispose(value)
                return CommandResult(sandbox_id, tuple(argv), 125, "", "Command output exceeded 1 MiB; write large results to workspace files",
                                     time.monotonic() - started, command_id=command_id)
            except (asyncio.CancelledError, TimeoutError) as exc:
                await self._join_dispose(value)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return CommandResult(sandbox_id, tuple(argv), 124, "", "Command timed out", time.monotonic() - started,
                                     timed_out=True, command_id=command_id)
            except Exception:
                # Keep the receipt and remote identity. Do not report a stopped
                # process or retry a command when the transport result is unknown.
                value["state"] = "uncertain"
                self._write(value)
                raise SandboxStateError("Remote command outcome is uncertain; inspect the binding or explicitly stop it") from None
            finally:
                self.execution = None

    async def cancel(self, sandbox_id):
        self._read(sandbox_id)
        task = self.execution
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.terminate(sandbox_id)

    async def terminate(self, sandbox_id):
        task = self.execution
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self.lock:
            value = self._read(sandbox_id)
            if value["state"] == "creating":
                await self._reconcile(value)
            await self._join_dispose(value)

    async def destroy(self, sandbox_id):
        await self.terminate(sandbox_id)
        # The persistent volume belongs to the user, not this Card. Never delete it.
        self.path.unlink()

    async def attach_resource(self, sandbox_id, resource_id, source, relative_path, access):
        raise SandboxValidationError("Live resource mounts are unavailable; copy files explicitly to the remote workspace")

    async def detach_resource(self, sandbox_id, resource_id):
        raise SandboxValidationError("This adapter has no live resource mounts")

    async def file_operation(self, sandbox_id, operation, **options):
        from .file_protocol import worker_command
        if options.get("root", "workspace") != "workspace":
            raise SandboxValidationError("Only the private workspace root is available")
        if self.lock.locked():
            raise SandboxStateError("Remote workspace is busy; retry after the active operation")
        async with self.lock:
            value = self._read(sandbox_id)
            sandbox = await self._ensure_remote(value)
            payload = json.dumps({"operation": operation, **options}) + "\n"
            if len(payload.encode()) > 32 * 1024 * 1024:
                raise SandboxValidationError("File request exceeds size limit")
            try:
                async with asyncio.timeout(60):
                    handle = await sandbox.commands.run(worker_command(), background=True, stdin=True,
                                                        user="user", cwd="/", timeout=60)
                    await sandbox.commands.send_stdin(handle.pid, payload)
                    result = await handle.wait()
                    response = json.loads(result.stdout)
            except (asyncio.CancelledError, TimeoutError):
                await self._join_dispose(value)
                raise
            except Exception:
                raise SandboxStateError("Remote file operation failed; its outcome must be checked before retrying a write") from None
            if "error" in response:
                error = response["error"]
                kind = {"FileNotFoundError": FileNotFoundError, "FileExistsError": FileExistsError,
                        "PermissionError": PermissionError, "SandboxSecurityError": SandboxSecurityError}.get(
                            error["type"], SandboxValidationError)
                raise kind(error["message"])
            return response["result"]


def registry_factory(settings: AliyunSandboxSettings):
    def build(root, event_sink):
        registry = SandboxRuntimeRegistry()
        if settings.workspace_store == "ecs":
            from .ecs_workspace import EcsWorkspaceSandboxBackend
            backend = EcsWorkspaceSandboxBackend(root, settings, event_sink)
        else:
            backend = AliyunSandboxBackend(root, settings, event_sink)

        async def probe():
            # Discovery is local configuration validation, never a billable create.
            return not settings.unavailable_reason, settings.unavailable_reason

        registry.register(SandboxRuntimeRegistration(
            SandboxRuntime("aliyun.agent-sandbox", "Alibaba Agent Sandbox (POC)", "linux", ("/bin/sh", "-c"),
                           supports_workspace=False), lambda: backend, probe,
        ))
        return registry
    return build
