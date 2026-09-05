"""Trusted one-request worker for the WSL bridge; never installed in a distro.

The bridge loads these application-owned modules in memory through stdin. User
commands remain structured data and reach only LinuxSandboxBackend.execute.
Closing the transport stdin terminates the active sandbox cgroup.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .linux import LinuxSandboxBackend
from .models import ResourceAccess, SandboxEvent, SandboxLimits, SandboxValidationError


def _write(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, default=str), flush=True)


def _linux_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise SandboxValidationError("invalid Windows path")
    result = subprocess.run(["/usr/bin/wslpath", "-a", "-u", raw],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    if result.returncode or not result.stdout.strip().startswith("/"):
        raise SandboxValidationError("selected Windows directory is unavailable in this WSL distribution")
    return result.stdout.rstrip("\r\n")


async def handle(request: dict[str, Any], *, stdin_pending: bool = False) -> Any:
    operation = request["operation"]
    if operation == "probe":
        version = Path("/proc/sys/kernel/osrelease").read_text().lower()
        if "microsoft" not in version or "wsl2" not in version:
            return (False, "This runtime requires WSL2; WSL1 is not a supported security boundary.")
        return await LinuxSandboxBackend.probe()
    if operation == "kill_unit":
        await LinuxSandboxBackend.kill_unit(request["unit"])
        return None
    managed = _linux_path(request["managed_root"])
    backend = LinuxSandboxBackend(Path(managed), limits=SandboxLimits(**request["limits"]),
        runtime_id=request["runtime_id"], event_sink=lambda event: _write({"event": asdict(event)}))
    sandbox_id = request["sandbox_id"]
    if operation == "create":
        return await backend.create(sandbox_id)
    if operation == "configure":
        workspace = request.get("workspace_path")
        return await backend.configure(sandbox_id,
            workspace_path=_linux_path(workspace) if workspace else None,
            workspace_access=ResourceAccess(request["workspace_access"]))
    if operation == "start":
        return await backend.start(sandbox_id)
    if operation == "execute":
        await backend.start(sandbox_id)
        execution = asyncio.create_task(backend.execute(sandbox_id, request["argv"],
            timeout_seconds=request.get("timeout_seconds"), env=request.get("env"),
            _unit_name=request["unit"]))
        disconnected = asyncio.Event()
        if stdin_pending:
            disconnected.set()
        loop = asyncio.get_running_loop()
        # stdin belongs only to the trusted bridge. Commands get /dev/null, so
        # user programs cannot forge protocol messages or hold this pipe open.
        def readable() -> None:
            try:
                os.read(sys.stdin.fileno(), 4096)
            except BlockingIOError:
                return
            disconnected.set()
            loop.remove_reader(sys.stdin.fileno())

        loop.add_reader(sys.stdin.fileno(), readable)

        async def cancel_on_disconnect() -> None:
            await disconnected.wait()
            await backend.terminate(sandbox_id)

        # execute was scheduled first and establishes its running record before
        # this task can observe a disconnect, including a pre-spawn disconnect.
        monitor = asyncio.create_task(cancel_on_disconnect())
        try:
            return await execution
        finally:
            loop.remove_reader(sys.stdin.fileno())
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
    if operation == "terminate":
        return await backend.terminate(sandbox_id)
    if operation == "attach_resource":
        return await backend.attach_resource(sandbox_id, request["resource_id"],
            Path(_linux_path(request["source"])), request["relative_path"],
            ResourceAccess(request["access"]))
    if operation == "detach_resource":
        return await backend.detach_resource(sandbox_id, request["resource_id"])
    if operation == "destroy":
        return await backend.destroy(sandbox_id)
    if operation == "get":
        return await backend.get(sandbox_id)
    raise SandboxValidationError("unknown WSL sandbox operation")


def main(request: dict[str, Any], *, stdin_pending: bool = False) -> None:
    try:
        result = asyncio.run(handle(request, stdin_pending=stdin_pending))
        if hasattr(result, "__dataclass_fields__"):
            result = asdict(result)
            # Return host paths for user-facing metadata. Paths visible to the
            # command itself (/workspace and /sandbox) remain POSIX paths.
            paths: list[tuple[dict[str, Any], str]] = []
            if result.get("workspace_path"):
                paths.append((result, "workspace_path"))
            if "source" in result:
                paths.append((result, "source"))
            paths.extend((item, "source") for item in result.get("attachments", []))
            for item, key in paths:
                converted = subprocess.run(["/usr/bin/wslpath", "-a", "-w", str(item[key])],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
                if converted.returncode:
                    raise SandboxValidationError("sandbox path is no longer available from Windows")
                item[key] = converted.stdout.rstrip("\r\n")
        _write({"result": result})
    except BaseException as exc:
        _write({"error": {"type": type(exc).__name__, "message": str(exc)}})
