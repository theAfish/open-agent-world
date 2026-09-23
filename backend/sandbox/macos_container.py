"""macOS Container VM sandbox using Apple's Containerization framework.

Each command executes inside its own lightweight Linux virtual machine through
the ``container`` CLI, providing a hypervisor-level security boundary without
bubblewrap, systemd, or other Linux-specific tooling inside the guest.  Host
state management mirrors the Linux backend; only command execution crosses
into the VM.  Only the managed workspace, its home directory, and explicitly
attached resources are mounted.  The host filesystem remains invisible.
Networking is disabled by default. Opt-in public egress uses a separate image
and a trusted in-guest firewall bootstrap before a command is admitted.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from .base import SandboxBackend, SandboxEventSink
from .materialization import RuntimeMount, materialize_bundle, cleanup_materializations
from .models import (
    CommandResult, ResourceAccess, ResourceAttachment, SandboxEvent,
    SandboxEventType, SandboxInfo, SandboxLimits, SandboxNotFoundError,
    SandboxNetworkError, SandboxPreparationError, SandboxSecurityError, SandboxState,
    SandboxStateError, SandboxValidationError,
)
from .macos_container_network import (
    NETWORK_BOOTSTRAP_SOURCE, NETWORK_IMAGE, image_available,
    network_bootstrap_arguments, probe_network_image,
)
from .python_runtime import (
    PREPARATION_TIMEOUT, finish_thread, mutation_lock, validate_requirements,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OUTPUT_LIMIT = 2 * 1024 * 1024
_MANIFEST_NAME = "sandbox.json"
_CONTAINER_IMAGE = "docker.io/library/python:3.12-slim"
_PROBE_IMAGE = "docker.io/library/alpine:latest"
_PYTHON_RUNTIME_TARGET = "/opt/oaw-python"
_PYTHON_RUNTIME_VERSION = 1
_NETWORK_PROBE = ('set -- /sys/class/net/*; '
    '[ "$#" -eq 1 ] && [ "${1##*/}" = lo ] && printf oaw-probe-ok')

_PREPARE_PYTHON_SOURCE = r'''
import json, os, subprocess, sys, venv

root = "/opt/oaw-python"
environment = {"HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PIP_NO_INPUT": "1", "PIP_NO_CACHE_DIR": "1", "PYTHONNOUSERSITE": "1"}
python = root + "/venv/bin/python"
if not os.path.isfile(python):
    venv.EnvBuilder(with_pip=True, clear=True).create(root + "/venv")
requirements = json.loads(sys.argv[1])
if requirements:
    command = [sys.executable, "-I", "-m", "pip", "--python", python,
        "install", "--only-binary", ":all:"]
    subprocess.run(command + ["--dry-run", *requirements], check=True, env=environment)
    subprocess.run(command + requirements, check=True, env=environment)
'''


# This code runs inside the container VM as a trusted worker before any user
# code.  It reads one JSON request from stdin, executes it, and writes one JSON
# response to stdout.  The VM boundary itself provides filesystem, network and
# process isolation; the worker only enforces command-level timeouts and
# output limits.
_WORKER_SOURCE = r'''
import json, os, subprocess, sys, time

_OUTPUT_LIMIT = 2 * 1024 * 1024

def _run(argv, request):
    timeout = request.get("timeout_seconds")
    environment = dict(request.get("environment") or {})
    cwd = request.get("cwd") or "/workspace"
    started = time.monotonic()
    try:
        result = subprocess.run(argv, capture_output=True,
            timeout=timeout, env=environment, cwd=cwd,
            stdin=subprocess.DEVNULL)
        duration = time.monotonic() - started
        return {"exit_code": result.returncode,
            "stdout": result.stdout[:_OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stderr": result.stderr[:_OUTPUT_LIMIT].decode("utf-8", "replace"),
            "duration_seconds": duration, "timed_out": False}
    except subprocess.TimeoutExpired as error:
        duration = time.monotonic() - started
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        return {"exit_code": -9,
            "stdout": stdout[:_OUTPUT_LIMIT].decode("utf-8", "replace"),
            "stderr": stderr[:_OUTPUT_LIMIT].decode("utf-8", "replace"),
            "duration_seconds": duration, "timed_out": True}
    except OSError as error:
        return {"exit_code": 127, "stdout": "",
            "stderr": "Cannot execute command: " + str(error)[:1200],
            "duration_seconds": time.monotonic() - started, "timed_out": False}

def _probe():
    if sys.platform != "linux":
        return {"ok": False, "reason": "Container did not boot a Linux kernel"}
    if not os.path.isdir("/workspace"):
        return {"ok": False, "reason": "Workspace mount is missing"}
    return {"ok": True}

try:
    line = sys.stdin.buffer.readline()
    if not line:
        raise RuntimeError("no request received on stdin")
    request = json.loads(line)
    operation = request.get("operation")
    if operation == "execute":
        result = _run(request["argv"], request)
    elif operation == "probe":
        result = _probe()
    else:
        result = {"error": {"type": "SandboxValidationError",
            "message": "unsupported operation: " + str(operation)[:200]}}
except BaseException as exc:
    result = {"error": {"type": "SandboxSecurityError", "message": str(exc)[:1200]}}
sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode() + b"\n")
sys.stdout.buffer.flush()
'''


def validate_argv(argv: Any) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
        raise SandboxValidationError("argv must be a sequence, never a shell string")
    result = tuple(argv)
    if not result or any(not isinstance(value, str) or "\0" in value for value in result):
        raise SandboxValidationError("argv must contain NUL-free strings")
    if not result[0]:
        raise SandboxValidationError("argv executable must not be empty")
    return result


def validate_relative_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\0" in raw or "\\" in raw:
        raise SandboxValidationError("attachment path must be a POSIX relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise SandboxValidationError("attachment path may not traverse directories")
    return str(path)


def minimal_container_environment(extra: Mapping[str, str] | None = None, *,
    invocation_env: Mapping[str, str] | None = None) -> dict[str, str]:
    from .environment import validate_command_environment, apply_invocation_environment
    environment = {
        "PATH": "/sandbox/home/.local/bin:/sandbox/home/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": "/sandbox/home", "TMPDIR": "/tmp",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "SHELL": "/bin/sh",
        "SANDBOX_RESOURCES": "/sandbox",
        "NPM_CONFIG_PREFIX": "/sandbox/home/.local",
    }
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise SandboxValidationError("env must be an object")
        for key, value in extra.items():
            if key not in {"LANG", "LC_ALL", "TZ", "TERM"}:
                raise SandboxValidationError(f"environment key is outside the allowlist: {key}")
            validate_command_environment({key: value})
            environment[key] = value
    apply_invocation_environment(environment, invocation_env, extra)
    return environment


def _container_memory(value: int) -> str:
    """Format bytes for the container CLI's --memory flag."""
    mebibytes = max(16, value // (1024 * 1024))
    return f"{mebibytes}M"


def _host_identity() -> tuple[int, int]:
    """Numeric owner used for bind mounts (fallbacks only serve unit tests)."""
    uid = getattr(os, "getuid", lambda: 1000)()
    gid = getattr(os, "getgid", lambda: 1000)()
    return int(uid), int(gid)


class _ContainerPythonRuntime:
    """Linux venv prepared inside Apple Container, never on the macOS host."""

    kind = "python"

    def __init__(self, managed_root: Path, container_path: str) -> None:
        self.root = Path(managed_root).resolve() / "runtime" / "macos-container-python"
        self.venv = self.root / "venv"
        self.python = self.venv / "bin" / "python"
        self._container_path = container_path
        identity = hashlib.sha256(str(self.root).encode()).hexdigest()[:20]
        self._container_id = f"oaw-python-{identity}"

    def _ready_metadata(self) -> dict[str, Any]:
        try:
            value = json.loads((self.root / "ready.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _has_runtime(self) -> bool:
        # Linux venv interpreters are commonly absolute symlinks into the
        # container image.  Following that link on macOS would test the host's
        # /usr/local instead of the VM, so validate the link node plus cfg.
        return ((self.python.is_symlink() or self.python.is_file())
            and (self.venv / "pyvenv.cfg").is_file())

    def _delete_preparation_container(self) -> None:
        try:
            subprocess.run(
                [self._container_path, "delete", "--force", self._container_id],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def prepare_sync(self, requirements=(), bootstrap_key=None) -> dict[str, Any]:
        requirements = validate_requirements(requirements)
        self.root.mkdir(parents=True, exist_ok=True)
        with mutation_lock(self.root):
            metadata = self._ready_metadata()
            receipts = metadata.get("receipts", {}) if isinstance(metadata.get("receipts"), dict) else {}
            current = (metadata.get("version") == _PYTHON_RUNTIME_VERSION
                and self._has_runtime()
                and (not requirements or (bootstrap_key is not None
                    and receipts.get(bootstrap_key) == requirements)))
            if current:
                return {"kind": self.kind, "python": f"{_PYTHON_RUNTIME_TARGET}/venv/bin/python",
                    "requirements": requirements}

            uid, gid = _host_identity()
            self._delete_preparation_container()
            command = [
                self._container_path, "run", "--rm", "--name", self._container_id,
                "--network", "default",
                "--memory", "2G", "--cap-drop", "ALL",
                "--uid", str(uid), "--gid", str(gid),
                "--read-only", "--tmpfs", "/tmp",
                "--volume", f"{self.root}:{_PYTHON_RUNTIME_TARGET}",
                _CONTAINER_IMAGE, "python3", "-I", "-c", _PREPARE_PYTHON_SOURCE,
                json.dumps(requirements, ensure_ascii=True),
            ]
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL,
                    capture_output=True, timeout=PREPARATION_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._delete_preparation_container()
                raise SandboxPreparationError(
                    f"Container Python preparation failed: {exc}") from exc
            self._delete_preparation_container()
            if result.returncode:
                output = (result.stdout + result.stderr)[-16000:].decode("utf-8", "replace")
                raise SandboxPreparationError(
                    f"Container Python preparation failed: {output[-2000:]}")
            if not self._has_runtime():
                raise SandboxPreparationError(
                    "Container Python preparation completed without creating the venv")
            if bootstrap_key is not None:
                receipts[bootstrap_key] = requirements
            payload = {"version": _PYTHON_RUNTIME_VERSION,
                "image": _CONTAINER_IMAGE, "receipts": receipts}
            temporary = self.root / "ready.tmp"
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")
            temporary.replace(self.root / "ready.json")
        return {"kind": self.kind, "python": f"{_PYTHON_RUNTIME_TARGET}/venv/bin/python",
            "requirements": requirements}

    async def prepare(self, requirements=(), bootstrap_key=None) -> dict[str, Any]:
        try:
            # Do not release the cross-process mutation lock while the VM is
            # still modifying the shared environment after caller cancellation.
            return await finish_thread(self.prepare_sync, requirements, bootstrap_key)
        except SandboxPreparationError:
            raise
        except ValueError as exc:
            raise SandboxValidationError(str(exc)) from exc
        except OSError as exc:
            raise SandboxPreparationError(str(exc)) from exc

    def snapshot(self) -> dict[str, Any]:
        metadata = self._ready_metadata()
        return {"kind": self.kind, "ready": bool(
            metadata.get("version") == _PYTHON_RUNTIME_VERSION and self._has_runtime()),
            "python": f"{_PYTHON_RUNTIME_TARGET}/venv/bin/python",
            "image": metadata.get("image") or _CONTAINER_IMAGE}

    @staticmethod
    def environment(environment: dict[str, str]) -> None:
        environment.update(
            PATH=f"{_PYTHON_RUNTIME_TARGET}/venv/bin:" + environment["PATH"],
            VIRTUAL_ENV=f"{_PYTHON_RUNTIME_TARGET}/venv",
            PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")

    @staticmethod
    def command(command: Sequence[str]) -> tuple[str, ...]:
        if command[0].lower() in {
            "python", "python3", "python.exe", "python3.exe", "/usr/bin/python3",
        }:
            return (f"{_PYTHON_RUNTIME_TARGET}/venv/bin/python", *command[1:])
        return tuple(command)


@dataclass(slots=True)
class _Execution:
    command_id: str
    command: tuple[str, ...] = ()
    process: asyncio.subprocess.Process | None = None
    container_id: str | None = None
    cancelled: bool = False
    stop_requested: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        self.finished.set()


@dataclass(slots=True)
class _Record:
    sandbox_id: str
    root: Path
    workspace_path: str | None = None
    workspace_access: ResourceAccess = ResourceAccess.READ_WRITE
    state: SandboxState = SandboxState.STOPPED
    attachments: dict[str, ResourceAttachment] = field(default_factory=dict)
    executions: dict[str, _Execution] = field(default_factory=dict)
    active_command: tuple[str, ...] | None = None
    stop_requested: bool = False
    deleted: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def host_workspace(self) -> Path:
        return Path(self.workspace_path) if self.workspace_path else self.root / "workspace"


class MacosContainerSandboxBackend(SandboxBackend):
    """Container VM sandbox using Apple's Containerization framework.

    ``container_path`` is private test injection.  Production discovery always
    resolves ``container`` from PATH, which raises on non-macOS systems or
    when the Containerization system service is unavailable.  There is
    intentionally no subprocess or path-only fallback.
    """

    supports_invocation_environment = True
    supports_execution_policy = True

    def __init__(
        self, managed_root: Path, *,
        limits: SandboxLimits = SandboxLimits(),
        event_sink: SandboxEventSink | None = None,
        runtime_id: str = "macos-container",
        container_path: str | None = None,
        python_runtime=None,
    ) -> None:
        self._managed_root = Path(managed_root).resolve()
        self._root = self._managed_root / "sandbox-runtimes" / hashlib.sha256(runtime_id.encode()).hexdigest()[:16] / "sandboxes"
        self._limits = limits
        self._event_sink = event_sink
        self._runtime_id = runtime_id
        self._container_path = container_path or shutil.which("container") or "container"
        # A Darwin venv cannot be mounted into a Linux VM: native wheels and
        # interpreter launchers target different kernels.  Always provision a
        # container-native venv instead of using the shared host runtime.
        self.python_runtime = _ContainerPythonRuntime(
            self._managed_root, self._container_path)
        self._records: dict[str, _Record] = {}
        self._records_lock = asyncio.Lock()

    @classmethod
    async def probe(cls) -> tuple[bool, str | None]:
        if sys.platform != "darwin":
            return False, "Apple container requires macOS 26 or newer on Apple Silicon."
        executable = shutil.which("container")
        if not executable:
            return False, ("Missing container CLI. Install from https://github.com/apple/container/releases "
                "and start the system service with: container system start")
        try:
            process = await asyncio.create_subprocess_exec(
                executable, "run", "--rm", "--network", "none",
                _PROBE_IMAGE, "/bin/sh", "-c", _NETWORK_PROBE,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                raise
            if process.returncode != 0 or stdout.strip() != b"oaw-probe-ok":
                detail = stderr.decode("utf-8", "replace").strip()[:1200]
                return False, ("Apple container could not prove network-disabled isolation. "
                    "Use Apple container 0.6.0 or newer, verify macOS 26 on Apple Silicon, "
                    "and run: container system start. " + detail)
            return True, None
        except (OSError, TimeoutError) as exc:
            return False, f"Apple container probe failed: {exc}"

    @staticmethod
    async def probe_network() -> tuple[bool, str | None]:
        return await probe_network_image()

    async def create(self, sandbox_id: str) -> SandboxInfo:
        self._validate_id(sandbox_id)
        async with self._records_lock:
            self._root.mkdir(parents=True, exist_ok=True)
            root = self._root / sandbox_id
            if sandbox_id in self._records or root.exists():
                raise SandboxStateError(f"sandbox already exists: {sandbox_id}")
            root.mkdir(mode=0o700)
            try:
                (root / "workspace").mkdir(mode=0o700)
                (root / "home").mkdir(mode=0o700)
                record = _Record(sandbox_id, root)
                self._save(record)
            except BaseException:
                shutil.rmtree(root)
                raise
            self._records[sandbox_id] = record
        await self._emit_state(record)
        return self._info(record)

    async def managed_workspace(self, sandbox_id: str) -> Path:
        return (await self._record(sandbox_id)).root / "workspace"

    async def configure(
        self, sandbox_id: str, *, workspace_path: str | None,
        workspace_access: ResourceAccess,
    ) -> SandboxInfo:
        access = ResourceAccess(workspace_access)
        record = await self._record(sandbox_id)
        path = await asyncio.to_thread(self._validate_workspace, workspace_path) if workspace_path else None
        async with record.lock:
            self._assert_idle(record)
            previous = (record.workspace_path, record.workspace_access)
            record.workspace_path, record.workspace_access = path, access
            try:
                self._save(record)
            except BaseException:
                record.workspace_path, record.workspace_access = previous
                raise
        return self._info(record)

    async def start(self, sandbox_id: str) -> SandboxInfo:
        record = await self._record(sandbox_id)
        async with record.lock:
            if not record.stop_requested and record.state in {SandboxState.READY, SandboxState.RUNNING}:
                return self._info(record)
            self._assert_idle(record)
            if sys.platform != "darwin":
                raise SandboxSecurityError("Apple container execution requires macOS")
            await asyncio.to_thread(self._validate_record_paths, record)
            await self._cleanup_orphaned_containers(record.sandbox_id)
            record.stop_requested = False
            record.state = SandboxState.READY
            self._save(record)
        await self._emit_state(record)
        return self._info(record)

    async def execute(self, sandbox_id, argv, **options):
        from .models import execution_command_id
        from uuid import uuid4
        if type((options.get("execution_policy") or {}).get("network_enabled", False)) is not bool:
            raise SandboxValidationError("network_enabled must be a boolean")
        owner = await self._record(sandbox_id)
        command_id = execution_command_id.get() or uuid4().hex
        self._validate_id(command_id)
        async with owner.lock:
            if owner.stop_requested or owner.state not in {SandboxState.READY, SandboxState.RUNNING}:
                raise SandboxStateError("sandbox must be ready before executing a command")
            execution = _Execution(command_id)
            owner.executions[command_id] = execution
            owner.state = SandboxState.RUNNING
        token = execution_command_id.set(command_id)
        failed = False
        try:
            return await self._execute_record(owner, execution, sandbox_id, argv, **options)
        except BaseException:
            failed = True
            raise
        finally:
            async with owner.lock:
                owner.executions.pop(command_id, None)
                owner.state = (SandboxState.RUNNING if owner.executions else
                    SandboxState.STOPPED if owner.stop_requested else SandboxState.READY)
                if failed and not owner.executions:
                    owner.state = SandboxState.ERROR
            execution.finished.set()
            execution_command_id.reset(token)
            await self._emit_state(owner)

    async def _execute_record(
        self, owner: _Record, execution: _Execution, sandbox_id: str, argv: Sequence[str], *,
        timeout_seconds: float | None = None, env: Mapping[str, str] | None = None,
        invocation_env: Mapping[str, str] | None = None,
        runtime_mount: RuntimeMount | None = None,
        execution_policy: Mapping[str, Any] | None = None,
    ) -> CommandResult:
        policy = execution_policy or {}
        if type(policy.get("network_enabled", False)) is not bool:
            raise SandboxValidationError("network_enabled must be a boolean")
        network_enabled = policy.get("network_enabled", False)
        limits = SandboxLimits(memory_bytes=policy.get("memory_bytes", self._limits.memory_bytes),
            active_process_limit=policy.get("active_process_limit", self._limits.active_process_limit),
            default_timeout_seconds=policy.get("command_timeout", self._limits.default_timeout_seconds))
        command = validate_argv(argv)
        environment = minimal_container_environment(env, invocation_env=invocation_env)
        await self.python_runtime.prepare()
        if execution.cancelled or execution.stop_requested:
            return CommandResult(sandbox_id, command, -9, "", "", 0,
                cancelled=True, command_id=execution.command_id)
        self.python_runtime.environment(environment)
        command = self.python_runtime.command(command)
        timeout = limits.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise SandboxValidationError("timeout_seconds must be finite and positive")
        network_settings = None
        if network_enabled:
            if not await asyncio.to_thread(image_available, self._container_path):
                raise SandboxNetworkError("The macOS public-egress image is missing; prepare it and refresh runtimes")
            uid, gid = _host_identity()
            network_settings = await asyncio.to_thread(network_bootstrap_arguments, uid, gid)

        async with owner.lock:
            if owner.deleted or owner.state != SandboxState.RUNNING:
                raise SandboxStateError("sandbox must be running before executing a command")
            await asyncio.to_thread(self._validate_record_paths, owner)
            mount = None
            if runtime_mount is not None:
                command = runtime_mount.command(command, Path("/.oaw") / runtime_mount.bundle.key)
                source = await asyncio.to_thread(materialize_bundle, owner.root, runtime_mount.bundle.versioned())
                mount = (source, runtime_mount.bundle.key)
            owner.active_command = command

        await self._emit_state(owner)
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.COMMAND_STARTED,
            {"argv": list(command), "timeout_seconds": timeout}))

        container_id = self._execution_container_id(sandbox_id, execution.command_id)
        execution.container_id = container_id
        container_argv = self._container_command(
            owner, mount, limits, container_id=container_id,
            network_settings=network_settings)
        request = json.dumps({
            "operation": "execute",
            "argv": list(command),
            "environment": environment,
            "timeout_seconds": timeout,
            "cwd": "/workspace",
        }, ensure_ascii=True).encode() + b"\n"

        started = time.monotonic()
        timed_out = False
        cancelled = False
        result: dict[str, Any] | None = None
        process: asyncio.subprocess.Process | None = None

        try:
            process = await asyncio.create_subprocess_exec(
                *container_argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=32 * 1024 * 1024)
            execution.process = process
            assert process.stdin and process.stdout
            process.stdin.write(request)
            await process.stdin.drain()
            process.stdin.close()
            # The worker enforces the command timeout inside the VM.  This
            # outer bound also reaps a hung container after a transport crash.
            line = await asyncio.wait_for(process.stdout.readline(), timeout + 60)
            if not line:
                if execution.cancelled or execution.stop_requested:
                    cancelled = True
                else:
                    try:
                        stderr = (await asyncio.wait_for(process.stderr.read(8192), 10)
                            if process.stderr else b"")
                    except asyncio.TimeoutError as exc:
                        error = "Container worker closed stdout but did not exit"
                        if network_enabled:
                            raise SandboxNetworkError(error) from exc
                        raise SandboxSecurityError(error) from exc
                    detail = stderr.decode("utf-8", "replace")
                    if "OAW_SANDBOX_NETWORK_SETUP:" in detail:
                        raise SandboxNetworkError(detail.split("OAW_SANDBOX_NETWORK_SETUP:", 1)[1].strip()[:1200])
                    raise SandboxSecurityError("Container worker produced no response: " + detail[:1200])
            else:
                raw = json.loads(line)
                if isinstance(raw, dict) and "error" in raw:
                    error = raw["error"]
                    raise SandboxSecurityError(f"Container worker failed: {error.get('message', 'unknown')}")
                result = raw
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await self._delete_container(container_id)
            execution.process = None
            execution.container_id = None
            async with owner.lock:
                owner.active_command = None

        duration = time.monotonic() - started

        command_result = CommandResult(
            sandbox_id=sandbox_id, argv=command,
            exit_code=(result or {}).get("exit_code", -9),
            stdout=(result or {}).get("stdout", ""),
            stderr=(result or {}).get("stderr", ""),
            duration_seconds=(result or {}).get("duration_seconds", duration),
            timed_out=timed_out or (result or {}).get("timed_out", False),
            cancelled=cancelled or execution.cancelled,
            command_id=execution.command_id,
        )
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.COMMAND_FINISHED, {
            "argv": list(command), "exit_code": command_result.exit_code,
            "duration_seconds": command_result.duration_seconds,
            "timed_out": command_result.timed_out, "cancelled": command_result.cancelled,
        }))
        await self._emit_state(owner)
        return command_result

    def _container_command(
        self, record: _Record, runtime_mount: tuple[Path, str] | None,
        limits: SandboxLimits, *, container_id: str | None = None,
        network_settings: str | None = None,
    ) -> list[str]:
        uid, gid = _host_identity()
        result = [
            self._container_path, "run", "--rm", "--interactive",
            "--network", "default" if network_settings is not None else "none",
            "--memory", _container_memory(limits.memory_bytes),
            # The trusted worker shares this UID, so reserve one process for it
            # while bounding the complete untrusted descendant tree.
            "--ulimit", (f"nproc={limits.active_process_limit + 1}:"
                f"{limits.active_process_limit + 1}"),
            "--uid", "0" if network_settings is not None else str(uid),
            "--gid", "0" if network_settings is not None else str(gid),
            "--cap-drop", "ALL",
            "--read-only", "--tmpfs", "/tmp",
            "--volume", (f"{record.host_workspace}:/workspace:ro"
                if record.workspace_access == ResourceAccess.READ_ONLY
                else f"{record.host_workspace}:/workspace"),
            "--volume", f"{record.root / 'home'}:/sandbox/home",
            "--volume", f"{self.python_runtime.root}:{_PYTHON_RUNTIME_TARGET}:ro",
            "--workdir", "/workspace",
        ]
        if network_settings is not None:
            from .linux_network import PUBLIC_RESOLVERS
            # These are granted only to the trusted bootstrap. It installs
            # nftables and drops its entire capability bounding set before
            # starting the command worker or reading the command request.
            for capability in ("NET_ADMIN", "SETPCAP", "SETUID", "SETGID"):
                result.extend(["--cap-add", capability])
            for resolver in PUBLIC_RESOLVERS:
                result.extend(["--dns", resolver])
        if container_id is not None:
            result[3:3] = ["--name", container_id]
        for attachment in record.attachments.values():
            target = f"/sandbox/{attachment.relative_path}"
            suffix = ":ro" if attachment.access == ResourceAccess.READ_ONLY else ""
            result.extend(["--volume", f"{attachment.source}:{target}{suffix}"])
        if runtime_mount is not None:
            source, key = runtime_mount
            result.extend(["--volume", f"{source}:/.oaw/{key}:ro"])
        if network_settings is None:
            result.extend([_CONTAINER_IMAGE, "python3", "-I", "-u", "-c", _WORKER_SOURCE])
        else:
            result.extend([NETWORK_IMAGE, "python3", "-I", "-u", "-c",
                NETWORK_BOOTSTRAP_SOURCE, network_settings, _WORKER_SOURCE])
        return result

    async def prepare_python(self, requirements=(), bootstrap_key=None):
        return await self.python_runtime.prepare(requirements, bootstrap_key)

    async def python_status(self):
        return await asyncio.to_thread(self.python_runtime.snapshot)

    def _container_prefix(self, sandbox_id: str) -> str:
        identity = hashlib.sha256(
            f"{self._managed_root}:{self._runtime_id}:{sandbox_id}".encode()).hexdigest()[:16]
        return f"oaw-{identity}-"

    def _execution_container_id(self, sandbox_id: str, command_id: str) -> str:
        suffix = hashlib.sha256(command_id.encode()).hexdigest()[:16]
        return self._container_prefix(sandbox_id) + suffix

    async def _delete_container(self, container_id: str) -> None:
        """Idempotently remove a named VM after normal or abnormal transport exit."""
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                self._container_path, "delete", "--force", container_id,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(process.wait(), 15)
        except (OSError, TimeoutError):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            # The command path already has a bounded in-guest timeout and --rm.
            # A later start performs discovery-based recovery as a second line.
            return

    async def _cleanup_orphaned_containers(self, sandbox_id: str) -> None:
        """Remove VMs left by a backend crash before admitting new work."""
        prefix = self._container_prefix(sandbox_id)
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                self._container_path, "list", "--all", "--quiet",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(process.communicate(), 15)
        except (OSError, TimeoutError):
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
            return
        if process.returncode:
            return
        for value in stdout.decode("utf-8", "replace").splitlines():
            container_id = value.strip()
            if container_id.startswith(prefix):
                await self._delete_container(container_id)

    async def bundle_status(self, sandbox_id, bundle):
        from .materialization import bundle_status
        record = await self._record(sandbox_id)
        async with record.lock:
            return await asyncio.to_thread(bundle_status, record.root, bundle.versioned())

    async def reset_cache(self, sandbox_id):
        record = await self._record(sandbox_id)
        async with record.lock:
            if record.state != SandboxState.STOPPED:
                raise SandboxStateError("Stop the Sandbox before clearing its runtime cache")
            cleanup_materializations(record.root)

    async def cancel(self, sandbox_id):
        owner = await self._record(sandbox_id)
        async with owner.lock:
            active = list(owner.executions.values())
            for execution in active:
                execution.cancelled = True
                if execution.process is not None and execution.process.returncode is None:
                    execution.process.kill()
        await asyncio.gather(*(execution.finished.wait() for execution in active))

    async def terminate(self, sandbox_id: str) -> None:
        owner = await self._record(sandbox_id)
        async with owner.lock:
            owner.stop_requested = True
            active = list(owner.executions.values())
            for execution in active:
                execution.stop_requested = True
                execution.cancelled = True
                if execution.process is not None and execution.process.returncode is None:
                    execution.process.kill()
        await asyncio.gather(*(execution.finished.wait() for execution in active))
        async with owner.lock:
            owner.state = SandboxState.STOPPED
            owner.stop_requested = False
            self._save(owner)
        await self._emit_state(owner)

    async def attach_resource(self, sandbox_id: str, resource_id: str, source: Path,
        relative_path: str, access: ResourceAccess) -> ResourceAttachment:
        self._validate_id(resource_id)
        source = await asyncio.to_thread(self._validate_source, Path(source))
        relative = validate_relative_path(relative_path)
        attachment = ResourceAttachment(sandbox_id, resource_id, source, relative, ResourceAccess(access))
        record = await self._record(sandbox_id)
        async with record.lock:
            if not record.stop_requested and record.attachments.get(resource_id) == attachment:
                return attachment
            self._assert_idle(record)
            for key, item in record.attachments.items():
                if key == resource_id:
                    continue
                old, new = PurePosixPath(item.relative_path), PurePosixPath(relative)
                if source == item.source or old == new or old in new.parents or new in old.parents:
                    raise SandboxValidationError("resource attachment conflicts with an existing mount")
            previous = record.attachments.get(resource_id)
            record.attachments[resource_id] = attachment
            try:
                self._save(record)
            except BaseException:
                if previous is None:
                    del record.attachments[resource_id]
                else:
                    record.attachments[resource_id] = previous
                raise
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.RESOURCE_ATTACHED,
            {"resource_id": resource_id, "relative_path": relative, "access": attachment.access.value}))
        return attachment

    async def detach_resource(self, sandbox_id: str, resource_id: str) -> None:
        record = await self._record(sandbox_id)
        async with record.lock:
            self._assert_idle(record)
            if resource_id not in record.attachments:
                raise SandboxNotFoundError(f"resource is not attached: {resource_id}")
            attachment = record.attachments.pop(resource_id)
            try:
                self._save(record)
            except BaseException:
                record.attachments[resource_id] = attachment
                raise
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.RESOURCE_DETACHED,
            {"resource_id": resource_id}))

    async def destroy(self, sandbox_id: str) -> None:
        await self.terminate(sandbox_id)
        record = await self._record(sandbox_id)
        async with record.lock:
            self._assert_idle(record)
            self._assert_within(record.root.resolve(), self._root)
            cleanup_materializations(record.root)
            shutil.rmtree(record.root)
            record.deleted = True
        async with self._records_lock:
            self._records.pop(sandbox_id, None)

    async def file_operation(self, sandbox_id, operation, **options):
        from .files import file_operation, run_file_operation
        record = await self._record(sandbox_id)
        async with record.lock:
            return await run_file_operation(file_operation, record.host_workspace, record.workspace_access,
                tuple(record.attachments.values()), operation, **options)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        return self._info(await self._record(sandbox_id))

    async def _record(self, sandbox_id: str) -> _Record:
        self._validate_id(sandbox_id)
        async with self._records_lock:
            if sandbox_id in self._records:
                return self._records[sandbox_id]
            root = self._root / sandbox_id
            self._assert_within(root.resolve(), self._root)
            manifest = root / _MANIFEST_NAME
            if not manifest.is_file():
                raise SandboxNotFoundError(f"sandbox not found: {sandbox_id}")
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
                if raw["sandbox_id"] != sandbox_id or raw["runtime_id"] != self._runtime_id:
                    raise ValueError("manifest runtime or identity mismatch")
                record = _Record(sandbox_id, root,
                    raw.get("workspace_path"), ResourceAccess(raw["workspace_access"]))
                for item in raw["attachments"]:
                    self._validate_id(item["resource_id"])
                    attachment = ResourceAttachment(sandbox_id, item["resource_id"],
                        self._validate_source(Path(item["source"]), must_exist=False),
                        validate_relative_path(item["relative_path"]), ResourceAccess(item["access"]))
                    record.attachments[attachment.resource_id] = attachment
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise SandboxSecurityError(f"invalid sandbox manifest: {exc}") from exc
            self._records[sandbox_id] = record
            return record

    def _save(self, record: _Record) -> None:
        payload = {
            "version": 1, "sandbox_id": record.sandbox_id, "runtime_id": self._runtime_id,
            "workspace_path": record.workspace_path, "workspace_access": record.workspace_access.value,
            "state": record.state.value,
            "attachments": [{"resource_id": item.resource_id, "source": str(item.source),
                "relative_path": item.relative_path, "access": item.access.value}
                for item in record.attachments.values()],
        }
        target = record.root / _MANIFEST_NAME
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _info(self, record: _Record) -> SandboxInfo:
        return SandboxInfo(sandbox_id=record.sandbox_id, state=record.state,
            workspace=Path("/workspace"), attachments=tuple(record.attachments.values()),
            security_boundary="macos-containerization-vm", network_enabled=False,
            supported_network_modes=("disabled", "enabled"),
            network_reason="Public IPv4 TCP/UDP only; private networks, host services and IPv6 are blocked when the public-egress image is installed.",
            active_command=(record.active_command if len(record.executions) <= 1 else None),
            runtime_id=self._runtime_id, platform="linux",
            shell=("/bin/sh", "-c"), workspace_path=record.workspace_path,
            workspace_access=record.workspace_access, resources_path=Path("/sandbox"),
            runtime_locked=True)

    def _validate_workspace(self, raw: str) -> str:
        if not isinstance(raw, str) or "\0" in raw:
            raise SandboxValidationError("workspace_path must be an absolute directory")
        path = Path(raw)
        if not path.is_absolute():
            raise SandboxValidationError("workspace_path must be absolute")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir() or resolved == Path(resolved.anchor):
            raise SandboxValidationError("workspace_path must be a directory, not a filesystem root")
        if resolved.is_relative_to(self._managed_root) or self._managed_root.is_relative_to(resolved):
            raise SandboxValidationError("workspace_path may not overlap application managed storage")
        for protected in ("/proc", "/sys", "/dev", "/run", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
                "/System", "/private", "/Library", "/Applications"):
            if resolved.is_relative_to(Path(protected).resolve()):
                raise SandboxValidationError("workspace_path may not expose a system directory")
        return str(resolved)

    def _validate_source(self, source: Path, *, must_exist: bool = True) -> Path:
        if source.is_symlink():
            raise SandboxValidationError("resource symlinks are not allowed")
        try:
            resolved = source.resolve(strict=must_exist)
        except OSError as exc:
            raise SandboxValidationError("managed resource does not exist") from exc
        self._assert_within(resolved, self._managed_root)
        if (any(resolved.is_relative_to(self._managed_root / name)
                for name in ("sandbox-runtimes", "sandboxes", "sandbox-bindings", "runtime"))
                or (must_exist and not resolved.is_file())):
            raise SandboxValidationError("only regular managed resource files can be attached")
        return resolved

    def _validate_record_paths(self, record: _Record) -> None:
        self._assert_within(record.root.resolve(), self._root)
        if record.workspace_path:
            if self._validate_workspace(record.workspace_path) != record.workspace_path:
                raise SandboxSecurityError("workspace target changed; configure the selected directory again")
        else:
            self._assert_within(record.host_workspace.resolve(strict=True), record.root)
        home = record.root / "home"
        if home.is_symlink():
            raise SandboxSecurityError("sandbox home must not be a symlink")
        home.mkdir(mode=0o700, exist_ok=True)
        if home.resolve(strict=True) != record.root.resolve() / "home":
            raise SandboxSecurityError("sandbox home target changed")
        for attachment in record.attachments.values():
            if self._validate_source(attachment.source) != attachment.source:
                raise SandboxSecurityError("resource target changed")

    @staticmethod
    def _validate_id(value: str) -> None:
        if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
            raise SandboxValidationError("invalid sandbox/resource identity")

    @staticmethod
    def _assert_within(path: Path, parent: Path) -> None:
        if not path.is_relative_to(parent.resolve()):
            raise SandboxValidationError("path escapes managed storage")

    @staticmethod
    def _assert_idle(record: _Record) -> None:
        if record.deleted:
            raise SandboxNotFoundError("sandbox was deleted")
        if record.executions or record.state == SandboxState.RUNNING:
            raise SandboxStateError("stop the sandbox command before changing its configuration or resources")

    async def _emit_state(self, record: _Record) -> None:
        await self._emit(SandboxEvent(record.sandbox_id, SandboxEventType.STATE_CHANGED,
            {"state": record.state.value}))

    async def _emit(self, event: SandboxEvent) -> None:
        from .models import execution_command_id
        if command_id := execution_command_id.get():
            event = replace(event, payload={**event.payload, "command_id": command_id})
        if self._event_sink is not None:
            result = self._event_sink(event)
            if inspect.isawaitable(result):
                await result
