"""Small WSL2 transport for the same Linux security implementation.

No distro/image installation, permanent worker, shell interpolation or WSL
configuration changes. The application ships its trusted worker through stdin
on each operation. All project bytes remain in the user's Windows folder.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .base import SandboxBackend, SandboxEventSink
from .linux import minimal_linux_environment, new_unit_name, validate_argv, validate_relative_path
from .models import (
    CommandResult, ResourceAccess, ResourceAttachment, SandboxEvent,
    SandboxEventType, SandboxInfo, SandboxLimits, SandboxNotFoundError,
    SandboxSecurityError, SandboxState, SandboxStateError, SandboxValidationError,
)

_BOOTSTRAP = """import json,os,sys,types
if sys.version_info < (3,10):
 print(json.dumps({'error':{'type':'SandboxSecurityError','message':'WSL sandbox requires Python 3.10 or newer at /usr/bin/python3.'}}),flush=True)
 sys.exit(1)
frame=bytearray()
while b'\\n' not in frame:
 chunk=os.read(sys.stdin.fileno(),65536)
 if not chunk: raise RuntimeError('incomplete WSL sandbox request')
 frame.extend(chunk)
line,pending=bytes(frame).split(b'\\n',1)
payload=json.loads(line)
package=types.ModuleType('oaw_sandbox');package.__path__=[]
sys.modules['oaw_sandbox']=package
for name,source in payload['modules']:
 module=types.ModuleType('oaw_sandbox.'+name)
 module.__package__='oaw_sandbox'
 sys.modules[module.__name__]=module
 exec(compile(source,'<oaw_sandbox/'+name+'.py>','exec'),module.__dict__)
sys.modules['oaw_sandbox.linux_worker'].main(payload['request'],stdin_pending=bool(pending))
"""

# Capture trusted code before a sandbox can edit any project folder. Re-reading
# these files per operation would let a workspace containing the application's
# source change the next unrestricted transport helper.
_WORKER_MODULES = tuple(
    (name, (Path(__file__).parent / f"{name}.py").read_text(encoding="utf-8"))
    for name in ("models", "base", "linux", "linux_worker")
)


def wsl_command(distribution: str) -> list[str]:
    if (not isinstance(distribution, str) or not distribution or len(distribution) > 128
        or any(character in distribution for character in "\0\r\n") or distribution.startswith("-")):
        raise SandboxValidationError("invalid WSL distribution name")
    executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe")
    return [executable, "--distribution", distribution, "--exec",
        "/usr/bin/python3", "-I", "-u", "-c", _BOOTSTRAP]


def _transport_environment() -> dict[str, str]:
    # In particular, do not propagate WSLENV or application provider credentials.
    return {key: value for key in ("SystemRoot", "WINDIR", "USERPROFILE", "LOCALAPPDATA", "TEMP", "TMP")
        if (value := os.environ.get(key)) is not None}


def _envelope(request: dict[str, Any]) -> bytes:
    return (json.dumps({"modules": _WORKER_MODULES, "request": request}, ensure_ascii=True) + "\n").encode()


def _error(raw: dict[str, str]) -> Exception:
    kind = {
        "SandboxNotFoundError": SandboxNotFoundError,
        "SandboxValidationError": SandboxValidationError,
        "SandboxStateError": SandboxStateError,
        "SandboxSecurityError": SandboxSecurityError,
    }.get(raw.get("type", ""), SandboxSecurityError)
    return kind(raw.get("message", "WSL sandbox operation failed"))


@dataclass(slots=True)
class _Active:
    unit: str
    process: asyncio.subprocess.Process | None = None
    cancelled: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


class WslSandboxBackend(SandboxBackend):
    def __init__(self, managed_root: Path, *, distribution: str,
        runtime_id: str | None = None, limits: SandboxLimits = SandboxLimits(),
        event_sink: SandboxEventSink | None = None) -> None:
        self._command = wsl_command(distribution)
        self._distribution = distribution
        self._managed_root = Path(managed_root).resolve()
        self._runtime_id = runtime_id or f"wsl:{distribution}"
        self._limits = limits
        self._event_sink = event_sink
        self._infos: dict[str, SandboxInfo] = {}
        self._active: dict[str, _Active] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    async def probe(cls, distribution: str) -> tuple[bool, str | None]:
        try:
            backend = cls(Path.cwd(), distribution=distribution)
            result = await backend._request({"operation": "probe"}, timeout=12)
            if not isinstance(result, list) or len(result) != 2 or not isinstance(result[0], bool):
                return False, "WSL security probe returned an invalid response"
            return result[0], result[1]
        except (OSError, TimeoutError, SandboxSecurityError, SandboxValidationError) as exc:
            return False, f"WSL2 sandbox unavailable: {exc}"

    def _lock(self, sandbox_id: str) -> asyncio.Lock:
        return self._locks.setdefault(sandbox_id, asyncio.Lock())

    def _payload(self, operation: str, sandbox_id: str, **values: Any) -> dict[str, Any]:
        return {"operation": operation, "sandbox_id": sandbox_id,
            "managed_root": str(self._managed_root), "runtime_id": self._runtime_id,
            "limits": asdict(self._limits), **values}

    async def _request(self, request: dict[str, Any], *, timeout: float = 15,
        active: _Active | None = None) -> Any:
        process = await asyncio.create_subprocess_exec(*self._command,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=_transport_environment(),
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
            # Two 2 MiB output buffers can require 24 MiB when JSON escapes
            # control characters. Keep the protocol bounded above that maximum.
            limit=32 * 1024 * 1024)
        if active is not None:
            active.process = process
        assert process.stdin and process.stdout and process.stderr
        stderr_task = asyncio.create_task(self._bounded_stderr(process.stderr))

        async def exchange() -> Any:
            process.stdin.write(_envelope(request))
            await process.stdin.drain()
            if active is None:
                process.stdin.close()
            elif active.cancelled:
                process.stdin.write(b'{"cancel":true}\n')
                await process.stdin.drain()
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise SandboxSecurityError("invalid WSL worker response") from exc
                if "event" in message:
                    await self._receive_event(message["event"])
                elif "error" in message:
                    raise _error(message["error"])
                elif "result" in message:
                    return message["result"]
                else:
                    raise SandboxSecurityError("unknown WSL worker response")
            detail = await stderr_task
            raise SandboxSecurityError("WSL worker exited without a result. " + detail)

        failed = False
        try:
            return await asyncio.wait_for(exchange(), timeout)
        except BaseException:
            failed = True
            raise
        finally:
            # EOF triggers the Linux worker's cgroup cancellation, then wait for
            # its cleanup. Killing wsl.exe alone does not prove Linux children
            # have stopped, so failure also uses the exact independent unit.
            process.stdin.close()
            shutdown_timed_out = False
            cleanup_error: Exception | None = None
            try:
                await asyncio.wait_for(process.wait(), 7)
            except TimeoutError:
                failed = True
                shutdown_timed_out = True
                process.kill()
                await process.wait()
            if failed and active is not None:
                try:
                    await asyncio.shield(self._request({"operation": "kill_unit", "unit": active.unit}, timeout=8))
                except (OSError, TimeoutError, SandboxSecurityError) as exc:
                    # The service's independent RuntimeMaxSec remains enforced;
                    # expose failure rather than claiming cancellation succeeded.
                    self._infos.pop(request.get("sandbox_id", ""), None)
                    cleanup_error = SandboxSecurityError(
                        f"WSL transport failed and sandbox process cleanup could not be confirmed: {exc}")
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            if cleanup_error is not None:
                raise cleanup_error
            if shutdown_timed_out and active is None:
                raise SandboxSecurityError("WSL worker did not shut down after its response")

    @staticmethod
    async def _bounded_stderr(stream: asyncio.StreamReader) -> str:
        pieces: list[bytes] = []
        total = 0
        while chunk := await stream.read(8192):
            if total < 8192:
                pieces.append(chunk[:8192 - total])
            total += len(chunk)
        return b"".join(pieces).decode("utf-8", "replace")

    async def _receive_event(self, raw: dict[str, Any]) -> None:
        event = SandboxEvent(sandbox_id=raw["sandbox_id"], type=SandboxEventType(raw["type"]),
            payload=raw["payload"], timestamp=datetime.fromisoformat(raw["timestamp"]))
        if self._event_sink is not None:
            result = self._event_sink(event)
            if inspect.isawaitable(result):
                await result

    def _info(self, raw: dict[str, Any], *, workspace_path: str | None = None,
        preserve_workspace: bool = True) -> SandboxInfo:
        previous = self._infos.get(raw["sandbox_id"])
        path = previous.workspace_path if preserve_workspace and previous else (
            raw.get("workspace_path") if preserve_workspace else workspace_path)
        # The API reports host paths to users; sandbox cwd and resource paths
        # remain Linux paths. Backend-owned manifests keep the resolved Linux
        # counterpart and are never exposed as a Windows filesystem path.
        info = SandboxInfo(sandbox_id=raw["sandbox_id"], state=SandboxState(raw["state"]),
            workspace=PurePosixPath(raw["workspace"]), attachments=tuple(
                ResourceAttachment(item["sandbox_id"], item["resource_id"], Path(item["source"]),
                    item["relative_path"], ResourceAccess(item["access"])) for item in raw["attachments"]),
            security_boundary=raw["security_boundary"], network_enabled=False,
            active_command=tuple(raw["active_command"]) if raw["active_command"] else None,
            runtime_id=self._runtime_id, platform="linux", shell=("/bin/sh", "-c"),
            workspace_path=path, workspace_access=ResourceAccess(raw["workspace_access"]),
            resources_path=PurePosixPath(raw["resources_path"]), runtime_locked=True)
        self._infos[info.sandbox_id] = info
        return info

    def _assert_idle(self, sandbox_id: str) -> None:
        if sandbox_id in self._active:
            raise SandboxStateError("stop the sandbox command before changing configuration or resources")

    async def create(self, sandbox_id: str) -> SandboxInfo:
        async with self._lock(sandbox_id):
            raw = await self._request(self._payload("create", sandbox_id))
            return self._info(raw)

    async def configure(self, sandbox_id: str, *, workspace_path: str | None,
        workspace_access: ResourceAccess) -> SandboxInfo:
        if workspace_path is not None and (not PureWindowsPath(workspace_path).is_absolute() or "\0" in workspace_path):
            raise SandboxValidationError("WSL workspace_path must be an absolute Windows directory")
        async with self._lock(sandbox_id):
            self._assert_idle(sandbox_id)
            raw = await self._request(self._payload("configure", sandbox_id,
                workspace_path=workspace_path, workspace_access=ResourceAccess(workspace_access).value))
            return self._info(raw, workspace_path=workspace_path, preserve_workspace=False)

    async def start(self, sandbox_id: str) -> SandboxInfo:
        async with self._lock(sandbox_id):
            self._assert_idle(sandbox_id)
            return self._info(await self._request(self._payload("start", sandbox_id)))

    async def execute(self, sandbox_id: str, argv: Sequence[str], *,
        timeout_seconds: float | None = None, env: Mapping[str, str] | None = None) -> CommandResult:
        command = validate_argv(argv)
        minimal_linux_environment(env)
        timeout = self._limits.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise SandboxValidationError("timeout_seconds must be finite and positive")
        async with self._lock(sandbox_id):
            self._assert_idle(sandbox_id)
            info = await self.get(sandbox_id)
            if info.state != SandboxState.READY:
                raise SandboxStateError("sandbox must be ready before executing a command")
            active = _Active(new_unit_name())
            self._active[sandbox_id] = active
            self._infos[sandbox_id] = replace(info, state=SandboxState.RUNNING, active_command=command)
        try:
            raw = await self._request(self._payload("execute", sandbox_id, argv=list(command),
                timeout_seconds=timeout, env=dict(env) if env is not None else None,
                unit=active.unit), timeout=timeout + 20, active=active)
            result = CommandResult(sandbox_id=raw["sandbox_id"], argv=tuple(raw["argv"]),
                exit_code=raw["exit_code"], stdout=raw["stdout"], stderr=raw["stderr"],
                duration_seconds=raw["duration_seconds"], timed_out=raw["timed_out"],
                cancelled=raw["cancelled"] or active.cancelled)
            self._infos[sandbox_id] = replace(info,
                state=SandboxState.STOPPED if result.cancelled else SandboxState.READY)
            return result
        except BaseException:
            self._infos[sandbox_id] = replace(info, state=SandboxState.ERROR)
            raise
        finally:
            self._active.pop(sandbox_id, None)
            active.done.set()

    async def terminate(self, sandbox_id: str) -> None:
        async with self._lock(sandbox_id):
            active = self._active.get(sandbox_id)
            if active is not None:
                active.cancelled = True
                if active.process is not None and active.process.stdin is not None:
                    try:
                        active.process.stdin.write(b'{"cancel":true}\n')
                        await active.process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
        if active is not None:
            await active.done.wait()
            info = self._infos.get(sandbox_id)
            if info is not None and info.state == SandboxState.ERROR:
                raise SandboxSecurityError("WSL command failed while stopping; inspect sandbox status")
        else:
            await self._request(self._payload("terminate", sandbox_id))
        if sandbox_id in self._infos:
            self._infos[sandbox_id] = replace(self._infos[sandbox_id], state=SandboxState.STOPPED, active_command=None)

    async def attach_resource(self, sandbox_id: str, resource_id: str, source: Path,
        relative_path: str, access: ResourceAccess) -> ResourceAttachment:
        relative = validate_relative_path(relative_path)
        async with self._lock(sandbox_id):
            self._assert_idle(sandbox_id)
            raw = await self._request(self._payload("attach_resource", sandbox_id,
                resource_id=resource_id, source=str(source), relative_path=relative,
                access=ResourceAccess(access).value))
            attachment = ResourceAttachment(sandbox_id, resource_id, Path(source),
                raw["relative_path"], ResourceAccess(raw["access"]))
            if sandbox_id in self._infos:
                info = self._infos[sandbox_id]
                self._infos[sandbox_id] = replace(info,
                    attachments=tuple(item for item in info.attachments if item.resource_id != resource_id) + (attachment,))
            return attachment

    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        async with self._lock(sandbox_id):
            self._assert_idle(sandbox_id)
            await self._request(self._payload("detach_resource", sandbox_id, resource_id=resource_id))
            if sandbox_id in self._infos:
                info = self._infos[sandbox_id]
                self._infos[sandbox_id] = replace(info,
                    attachments=tuple(item for item in info.attachments if item.resource_id != resource_id))

    async def destroy(self, sandbox_id: str) -> None:
        await self.terminate(sandbox_id)
        async with self._lock(sandbox_id):
            self._assert_idle(sandbox_id)
            await self._request(self._payload("destroy", sandbox_id))
            self._infos.pop(sandbox_id, None)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        if sandbox_id in self._infos:
            return self._infos[sandbox_id]
        return self._info(await self._request(self._payload("get", sandbox_id)))
