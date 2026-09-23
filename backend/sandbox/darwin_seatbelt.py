"""macOS Seatbelt sandbox using compiled profiles and process groups.

Runs commands through the built-in ``sandbox-exec`` tool with a deny-default
profile that only permits reading distribution tool trees and writing to
sandbox-managed paths.  Process trees are tracked through session/process
group IDs for whole-tree SIGKILL termination.  No image, daemon, virtual
machine, or additional software installation is required. Networking defaults
to denied; enabled mode admits only a command-scoped authenticated proxy.
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
import signal
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from .base import SandboxBackend, SandboxEventSink
from .materialization import RuntimeMount, materialize_bundle, cleanup_materializations
from .darwin_network_proxy import SeatbeltProxySession
from .models import (
    CommandResult, ResourceAccess, ResourceAttachment, SandboxEvent,
    SandboxEventType, SandboxInfo, SandboxLimits, SandboxNotFoundError,
    SandboxNetworkError, SandboxSecurityError, SandboxState, SandboxStateError, SandboxValidationError,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OUTPUT_LIMIT = 2 * 1024 * 1024
_MANIFEST_NAME = "sandbox.json"
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Distribution-owned tool trees, matched read-only.  /System is additionally
# protected by SIP on modern macOS.  Paths that only exist on some installs
# are included unconditionally; Seatbelt ignores missing allow targets.
_SYSTEM_READ_SUBPATHS = (
    "/bin", "/sbin",
    "/usr/bin", "/usr/lib", "/usr/libexec", "/usr/sbin", "/usr/share",
    "/usr/local/bin", "/usr/local/lib", "/usr/local/libexec", "/usr/local/share",
    "/opt/homebrew/bin", "/opt/homebrew/lib", "/opt/homebrew/opt", "/opt/homebrew/share",
    "/System", "/Library",
    "/private/etc", "/private/var/db/timezone", "/private/var/db/dyld",
)

# The directory node itself must be readable for a deny-default process to
# start on macOS.  This is intentionally a ``literal`` rule, not
# ``(subpath \"/\")``: it grants no access to files or child directories.
_SYSTEM_READ_LITERALS = ("/",)

# The functional probe must exercise a path which its own profile denies.
# Command profiles need /private/etc for normal macOS runtime configuration,
# and /etc is a firmlink to it, so /etc/hosts is not a valid denied canary
# when testing the full command allow-list.
_PROBE_SYSTEM_READ_SUBPATHS = tuple(
    path for path in _SYSTEM_READ_SUBPATHS if path != "/private/etc"
)

_DEVICE_READ_LITERALS = (
    "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
    # Explicitly permit the standard streams as paths as well as inherited
    # descriptors.  Model-authored shell snippets commonly redirect through
    # /dev/stdout, /dev/stderr, or /dev/fd/{0,1,2}.
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
)

_DEVICE_WRITE_LITERALS = (
    "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/fd/1", "/dev/fd/2",
)

# Trusted host-side supervisor.  Its stdin is a liveness lease held by the OAW
# backend.  If the backend crashes, the kernel closes the pipe and the
# supervisor kills the complete sandbox-exec process group.  User code itself
# still runs only below sandbox-exec and never inside this Python process.
_WATCHDOG_SOURCE = r'''
import json, os, select, signal, subprocess, sys, time

command = json.loads(sys.argv[1])
child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
while child.poll() is None:
    readable, _, _ = select.select([sys.stdin.buffer], [], [], 0.2)
    if readable and os.read(sys.stdin.fileno(), 1) == b"":
        os.killpg(os.getpgrp(), signal.SIGKILL)
    time.sleep(0.01)
code = child.wait()
raise SystemExit(code if code >= 0 else 128 - code)
'''


def _sbpl_quote(value: str) -> str:
    """Quote a path for a Seatbelt profile filter expression."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sbpl_subpath_filters(paths: Sequence[str]) -> str:
    return "\n".join(f"        (subpath {_sbpl_quote(path)})" for path in sorted(set(paths)))


def _sbpl_literal_filters(paths: Sequence[str]) -> str:
    return "\n".join(f"        (literal {_sbpl_quote(path)})" for path in sorted(set(paths)))


def seatbelt_profile(
    sandbox_root: Path,
    workspace: Path,
    home: Path,
    tmpdir: Path,
    attachments: Sequence[ResourceAttachment],
    runtime_mount: tuple[Path, str] | None = None,
    python_runtime: Path | Sequence[Path] | None = None,
    *,
    workspace_access: ResourceAccess = ResourceAccess.READ_WRITE,
    proxy_ports: Sequence[int] = (),
) -> str:
    """Generate a deny-default Seatbelt profile for one command execution.

    Only system tool trees are readable.  Writable access is limited to the
    sandbox-managed workspace, home, and tmp directories.  Attachments receive
    read or read-write matching their declared access.  All network operations,
    Mach service lookups, and unlisted filesystem paths stay denied. Optional
    network permission covers only the command's proxy loopback ports.
    """
    read_subpaths = list(_SYSTEM_READ_SUBPATHS)
    write_subpaths: list[str] = []

    # Seatbelt has no mount namespace.  OAW exposes stable attachment-relative
    # paths through a host-owned, read-only symlink tree instead.
    read_subpaths.append(str(sandbox_root / "resources"))

    # The selected workspace follows the card's access policy.  Home and tmp
    # remain private command scratch space and therefore stay writable.
    read_subpaths.append(str(workspace))
    if workspace_access == ResourceAccess.READ_WRITE:
        write_subpaths.append(str(workspace))
    for managed in (home, tmpdir):
        read_subpaths.append(str(managed))
        write_subpaths.append(str(managed))

    for attachment in attachments:
        source = str(attachment.source)
        read_subpaths.append(source)
        if attachment.access == ResourceAccess.READ_WRITE:
            write_subpaths.append(source)

    if runtime_mount is not None:
        read_subpaths.append(str(runtime_mount[0]))
    if python_runtime is not None:
        runtimes = ((python_runtime,) if isinstance(python_runtime, Path)
            else tuple(python_runtime))
        read_subpaths.extend(str(path) for path in runtimes)

    lines = [
        "(version 1)",
        "(deny default)",
        "",
        "    ;; Metadata inspection (stat/lstat) is globally permitted; only",
        "    ;; data reads remain path-restricted below.  Without this, basic",
        "    ;; shell and dynamic-linker startup fail during path resolution.",
        "(allow file-read-metadata)",
        "(allow file-ioctl)",
        "(allow sysctl-read)",
        "",
        "    ;; Distribution tool trees (read-only; /System is SIP-protected).",
        "(allow file-read*",
        _sbpl_subpath_filters(read_subpaths),
        _sbpl_literal_filters(_SYSTEM_READ_LITERALS),
        _sbpl_literal_filters(_DEVICE_READ_LITERALS),
        ")",
        "",
        "    ;; Sandbox-managed writable storage and read-write attachments.",
        "(allow file-write*",
        _sbpl_subpath_filters(write_subpaths),
        _sbpl_literal_filters(_DEVICE_WRITE_LITERALS),
        ")",
        "",
        "    ;; Process execution is bounded by the file-read* allow list above.",
        "(allow process-exec)",
        "(allow process-fork)",
        "",
    ]
    if proxy_ports:
        if any(type(port) is not int or not 1 <= port <= 65535 for port in proxy_ports):
            raise SandboxValidationError("Seatbelt proxy ports must be valid TCP ports")
        # The proxy owns each port on both loopback families before this
        # profile is used. No other network-outbound, network-bind, direct DNS
        # or host-service permission is granted.
        lines.extend(
            f'(allow network-outbound (remote ip "localhost:{port}"))'
            for port in sorted(set(proxy_ports)))
    return "\n".join(lines)


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


def minimal_darwin_environment(
    home: Path, tmpdir: Path,
    extra: Mapping[str, str] | None = None, *,
    resources: Path | None = None,
    invocation_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    from .environment import validate_command_environment, apply_invocation_environment
    environment = {
        "PATH": (f"{home}/.local/bin:{home}/bin:/opt/homebrew/bin:/usr/local/bin"
                 ":/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(home), "TMPDIR": str(tmpdir),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "SHELL": "/bin/sh",
        "SANDBOX_RESOURCES": str(resources) if resources is not None else "",
        "NPM_CONFIG_PREFIX": f"{home}/.local",
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


@dataclass(slots=True)
class _Execution:
    command_id: str
    command: tuple[str, ...] = ()
    process: asyncio.subprocess.Process | None = None
    proxy: SeatbeltProxySession | None = None
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


class DarwinSeatbeltBackend(SandboxBackend):
    """Native macOS Seatbelt sandbox; zero-install, no daemon or VM required.

    ``sandbox_exec`` is private test injection.  Production always resolves
    ``/usr/bin/sandbox-exec``, which raises on non-macOS systems.  There is
    intentionally no subprocess or path-only fallback.
    """

    supports_invocation_environment = True
    supports_execution_policy = True
    supports_optional_python_runtime = True

    def __init__(
        self, managed_root: Path, *,
        limits: SandboxLimits = SandboxLimits(),
        event_sink: SandboxEventSink | None = None,
        runtime_id: str = "darwin",
        sandbox_exec: str | None = None,
        python_runtime=None,
    ) -> None:
        self._managed_root = Path(managed_root).resolve()
        self.python_runtime = python_runtime
        self._root = self._managed_root / "sandbox-runtimes" / hashlib.sha256(runtime_id.encode()).hexdigest()[:16] / "sandboxes"
        self._limits = limits
        self._event_sink = event_sink
        self._runtime_id = runtime_id
        self._sandbox_exec = sandbox_exec or _SANDBOX_EXEC
        self._records: dict[str, _Record] = {}
        self._records_lock = asyncio.Lock()

    def validate_execution_policy(self, policy: Mapping[str, object]) -> None:
        if type(policy.get("network_enabled", False)) is not bool:
            raise SandboxValidationError("network_enabled must be a boolean")
        unsupported = {
            "memory_bytes": self._limits.memory_bytes,
            "active_process_limit": self._limits.active_process_limit,
        }
        changed = [key for key, default in unsupported.items()
            if key in policy and policy[key] != default]
        if changed:
            raise SandboxValidationError(
                "macOS Seatbelt cannot safely enforce configurable whole-tree "
                f"{', '.join(changed)} limits; use macOS Container VM instead")

    @staticmethod
    def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        """Kill the watchdog process group as the final cleanup fallback."""
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    def _request_process_stop(process: asyncio.subprocess.Process) -> None:
        """Release the watchdog lease; it owns descendant-tree cleanup."""
        if process.returncode is not None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            return
        DarwinSeatbeltBackend._kill_process_group(process)

    def _watchdog_command(self, profile: str, command: Sequence[str]) -> tuple[str, ...]:
        sandboxed = (self._sandbox_exec, "-p", profile, *command)
        return (sys.executable, "-I", "-c", _WATCHDOG_SOURCE,
            json.dumps(sandboxed, ensure_ascii=True))

    @staticmethod
    def _probe_profile() -> str:
        """Return the smallest profile that can launch a normal macOS tool.

        This deliberately mirrors the system-library portion of command
        profiles.  A probe which permits only ``/bin`` and ``/usr/bin`` can
        reject ``/bin/sh`` before it starts because the dynamic linker and
        framework libraries live below ``/usr/lib`` and ``/System``.
        ``/private/etc`` is intentionally absent so the command below proves
        that data outside the allow list remains inaccessible.
        """
        return "\n".join((
            "(version 1)",
            "(deny default)",
            "(allow file-read-metadata)",
            "(allow file-ioctl)",
            "(allow sysctl-read)",
            "(allow file-read*",
            _sbpl_subpath_filters(_PROBE_SYSTEM_READ_SUBPATHS),
            _sbpl_literal_filters(_SYSTEM_READ_LITERALS),
            _sbpl_literal_filters(_DEVICE_READ_LITERALS),
            ")",
            "(allow file-write*",
            _sbpl_literal_filters(_DEVICE_WRITE_LITERALS),
            ")",
            "(allow process-exec)",
            "(allow process-fork)",
        ))

    @classmethod
    async def probe(cls) -> tuple[bool, str | None]:
        if sys.platform != "darwin":
            return False, "macOS Seatbelt requires macOS."
        if not Path(_SANDBOX_EXEC).is_file():
            return False, f"Missing {_SANDBOX_EXEC}; a stock macOS installation is required"
        # Fail-closed functional probe: the profile must run a command and
        # also deny opening a data file outside its allow list.  Metadata
        # (stat) is globally permitted, matching the real command profiles.
        profile = cls._probe_profile()
        try:
            process = await asyncio.create_subprocess_exec(
                _SANDBOX_EXEC, "-p", profile,
                "/bin/sh", "-c",
                'if cat /etc/hosts >/dev/null 2>&1; then printf oaw-probe-leak; else printf oaw-probe-ok; fi',
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            except BaseException:
                cls._kill_process_group(process)
                if process.returncode is None:
                    await process.wait()
                raise
            output = stdout.strip()
            if output == b"oaw-probe-leak":
                return False, "Seatbelt containment failed: the probe read a denied host path"
            if process.returncode != 0 or output != b"oaw-probe-ok":
                detail = stderr.decode("utf-8", "replace").strip()[:1200]
                if not detail:
                    rendered_output = output.decode("utf-8", "replace")[:300]
                    detail = f"exit code {process.returncode}; stdout={rendered_output!r}"
                return False, f"Seatbelt sandbox probe failed: {detail}"
            return True, None
        except (OSError, TimeoutError) as exc:
            return False, f"Seatbelt sandbox probe failed: {exc}"

    @staticmethod
    async def probe_network() -> tuple[bool, str | None]:
        """Prove exact-port proxy access without contacting an external host."""
        if sys.platform != "darwin":
            return False, "Seatbelt proxy networking requires macOS"
        if not Path(_SANDBOX_EXEC).is_file() or not Path("/usr/bin/curl").is_file():
            return False, "Seatbelt proxy networking requires sandbox-exec and /usr/bin/curl"
        proxy = SeatbeltProxySession()
        try:
            await proxy.start()
            with tempfile.TemporaryDirectory(prefix="oaw-seatbelt-network-") as temporary:
                root = Path(temporary).resolve()
                for name in ("workspace", "home", "tmp"):
                    (root / name).mkdir()
                profile = seatbelt_profile(root, root / "workspace", root / "home",
                    root / "tmp", (), proxy_ports=proxy.ports)
                environment = minimal_darwin_environment(root / "home", root / "tmp")
                environment.update(proxy.environment())

                async def invoke(args: list[str]) -> tuple[int, bytes, bytes]:
                    process = await asyncio.create_subprocess_exec(
                        _SANDBOX_EXEC, "-p", profile, "/usr/bin/curl", *args,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=environment, cwd=str(root / "workspace"))
                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
                    except BaseException:
                        if process.returncode is None:
                            process.kill()
                            await process.communicate()
                        raise
                    return process.returncode or 0, stdout, stderr

                blocked = ["--disable", "--noproxy", "", "--silent", "--output",
                    "/dev/null", "--max-time", "5", "--write-out", "%{http_code}",
                    "http://127.0.0.1:1/"]
                code, output, error = await invoke(blocked)
                if code != 0 or output.strip() != b"403":
                    raise SandboxNetworkError("Seatbelt proxy was not reachable through its exact-port rule: "
                        + error.decode("utf-8", "replace")[:500])

                reached = asyncio.Event()

                async def unexpected(_reader, writer):
                    reached.set()
                    writer.close()
                    await writer.wait_closed()

                server = await asyncio.start_server(unexpected, "127.0.0.1", 0)
                try:
                    port = server.sockets[0].getsockname()[1]
                    direct = ["--disable", "--noproxy", "*", "--silent", "--output",
                        "/dev/null", "--max-time", "5", f"http://127.0.0.1:{port}/"]
                    direct_code, _, _ = await invoke(direct)
                    if direct_code == 0 or reached.is_set():
                        raise SandboxNetworkError(
                            "Seatbelt permitted a direct connection to another host service")
                finally:
                    server.close()
                    await server.wait_closed()
            return True, None
        except SandboxNetworkError:
            raise
        except (OSError, TimeoutError, ValueError) as exc:
            raise SandboxNetworkError(f"Seatbelt proxy setup probe failed: {exc}") from exc
        finally:
            await proxy.close()

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
                (root / "tmp").mkdir(mode=0o700)
                (root / "resources").mkdir(mode=0o700)
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
                raise SandboxSecurityError("Seatbelt execution requires macOS")
            if not Path(self._sandbox_exec).is_file():
                raise SandboxSecurityError(f"Missing {self._sandbox_exec}")
            await asyncio.to_thread(self._validate_record_paths, record)
            await asyncio.to_thread(self._sync_resource_links, record)
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
            try:
                if execution.proxy is not None:
                    await execution.proxy.close()
                    execution.proxy = None
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
        managed_python: bool = True,
    ) -> CommandResult:
        policy = execution_policy or {}
        self.validate_execution_policy(policy)
        if type(managed_python) is not bool:
            raise SandboxValidationError("managed_python must be a boolean")
        network_enabled = bool(policy.get("network_enabled", False))
        # macOS exposes no native equivalent of a cgroup or Job Object that can
        # hard-limit the aggregate RSS/PID count of just this process tree.
        # RLIMIT_AS is per process and RLIMIT_NPROC is per login UID, so using
        # either would be both misleading and capable of affecting the OAW
        # host.  Preserve the fixed compatibility defaults, but fail closed if
        # a caller asks Seatbelt to enforce a different value.
        limits = SandboxLimits(memory_bytes=policy.get("memory_bytes", self._limits.memory_bytes),
            active_process_limit=policy.get("active_process_limit", self._limits.active_process_limit),
            default_timeout_seconds=policy.get("command_timeout", self._limits.default_timeout_seconds))
        command = validate_argv(argv)
        if managed_python and self.python_runtime is not None:
            await self.python_runtime.prepare()
        if execution.cancelled or execution.stop_requested:
            return CommandResult(sandbox_id, command, -9, "", "", 0, cancelled=True,
                command_id=execution.command_id)
        workspace = owner.host_workspace
        home = owner.root / "home"
        tmpdir = owner.root / "tmp"
        environment = minimal_darwin_environment(
            home, tmpdir, env, resources=owner.root / "resources",
            invocation_env=invocation_env)
        if network_enabled and any(key.upper() in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"
        } for key in environment):
            raise SandboxValidationError(
                "Seatbelt proxy settings are controlled by the Sandbox runtime")
        if managed_python and self.python_runtime is not None:
            self.python_runtime.environment(environment)
            command = self.python_runtime.command(command)
        timeout = limits.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise SandboxValidationError("timeout_seconds must be finite and positive")

        if network_enabled:
            execution.proxy = SeatbeltProxySession()
            await execution.proxy.start()
            environment.update(execution.proxy.environment())
            if execution.cancelled or execution.stop_requested:
                return CommandResult(sandbox_id, command, -9, "", "", 0,
                    cancelled=True, command_id=execution.command_id)

        async with owner.lock:
            if owner.deleted or owner.state != SandboxState.RUNNING:
                raise SandboxStateError("sandbox must be running before executing a command")
            await asyncio.to_thread(self._validate_record_paths, owner)
            mount = None
            if runtime_mount is not None:
                command = runtime_mount.command(command, owner.root / ".oaw" / runtime_mount.bundle.key)
                source = await asyncio.to_thread(materialize_bundle, owner.root, runtime_mount.bundle.versioned())
                mount = (source, runtime_mount.bundle.key)
            owner.active_command = command

        profile = seatbelt_profile(
            owner.root, workspace, home, tmpdir,
            tuple(owner.attachments.values()), mount,
            ((self.python_runtime.base, self.python_runtime.venv)
                if managed_python and self.python_runtime else None),
            workspace_access=owner.workspace_access,
            proxy_ports=execution.proxy.ports if execution.proxy else (),
        )

        await self._emit_state(owner)
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.COMMAND_STARTED,
            {"argv": list(command), "timeout_seconds": timeout}))

        started = time.monotonic()
        timed_out = False
        process: asyncio.subprocess.Process | None = None
        stdout_data = bytearray()
        stderr_data = bytearray()

        async def _read_stream(reader: asyncio.StreamReader, sink: bytearray) -> None:
            while chunk := await reader.read(65536):
                if len(sink) < _OUTPUT_LIMIT:
                    sink.extend(chunk[:_OUTPUT_LIMIT - len(sink)])

        try:
            process = await asyncio.create_subprocess_exec(
                *self._watchdog_command(profile, command),
                # Keeping this pipe open leases the descendant tree.  Backend
                # exit, timeout, cancellation and explicit terminate all close
                # it and trigger watchdog cleanup.
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=environment,
                cwd=str(workspace),
                limit=32 * 1024 * 1024)
            execution.process = process
            assert process.stdout and process.stderr
            readers = [
                asyncio.create_task(_read_stream(process.stdout, stdout_data)),
                asyncio.create_task(_read_stream(process.stderr, stderr_data)),
            ]
            try:
                await asyncio.wait_for(process.wait(), timeout)
            except asyncio.TimeoutError:
                timed_out = True
                self._request_process_stop(process)
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    self._kill_process_group(process)
                    await process.wait()
            await asyncio.gather(*readers, return_exceptions=True)
        except BaseException:
            if process is not None:
                self._request_process_stop(process)
                if process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        self._kill_process_group(process)
                        await process.wait()
            raise
        finally:
            execution.process = None
            async with owner.lock:
                owner.active_command = None

        duration = time.monotonic() - started
        cancelled = execution.cancelled or execution.stop_requested
        result = CommandResult(
            sandbox_id=sandbox_id, argv=command,
            exit_code=(process.returncode if process and process.returncode is not None else -9),
            stdout=stdout_data.decode("utf-8", "replace"),
            stderr=stderr_data.decode("utf-8", "replace"),
            duration_seconds=duration,
            timed_out=timed_out,
            cancelled=cancelled,
            command_id=execution.command_id,
        )
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.COMMAND_FINISHED, {
            "argv": list(command), "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out, "cancelled": result.cancelled,
        }))
        await self._emit_state(owner)
        return result

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
                if execution.process is not None:
                    self._request_process_stop(execution.process)
        await asyncio.gather(*(execution.finished.wait() for execution in active))

    async def terminate(self, sandbox_id: str) -> None:
        owner = await self._record(sandbox_id)
        async with owner.lock:
            owner.stop_requested = True
            active = list(owner.executions.values())
            for execution in active:
                execution.stop_requested = True
                execution.cancelled = True
                if execution.process is not None:
                    self._request_process_stop(execution.process)
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
            workspace=record.host_workspace, attachments=tuple(record.attachments.values()),
            security_boundary="macos-seatbelt", network_enabled=False,
            network_transport="proxy_tcp",
            supported_network_modes=("disabled", "enabled"),
            network_reason="Proxy-mediated public IPv4 TCP only; direct sockets, UDP, private networks, host services and IPv6 are blocked.",
            active_command=(record.active_command if len(record.executions) <= 1 else None),
            runtime_id=self._runtime_id, platform="macos",
            shell=("/bin/zsh", "-c"), workspace_path=record.workspace_path,
            workspace_access=record.workspace_access,
            resources_path=record.root / "resources",
            runtime_locked=True,
            resource_limits_available=False,
            resource_limit_reason=("Seatbelt cannot hard-limit aggregate command-tree memory or "
                "process count; use macOS Container VM for those controls."))

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
        for protected in ("/System", "/private/etc", "/usr", "/bin", "/sbin", "/Library", "/Applications"):
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
        for name in ("home", "tmp"):
            path = record.root / name
            if path.is_symlink():
                raise SandboxSecurityError(f"sandbox {name} must not be a symlink")
            path.mkdir(mode=0o700, exist_ok=True)
            if path.resolve(strict=True) != record.root.resolve() / name:
                raise SandboxSecurityError(f"sandbox {name} target changed")
        for attachment in record.attachments.values():
            if self._validate_source(attachment.source) != attachment.source:
                raise SandboxSecurityError("resource target changed")

    def _sync_resource_links(self, record: _Record) -> None:
        """Rebuild the host-owned attachment view without exposing its tree."""
        resources = record.root / "resources"
        if resources.is_symlink():
            raise SandboxSecurityError("sandbox resources must not be a symlink")
        temporary = record.root / f"resources-{uuid.uuid4().hex}.tmp"
        backup = record.root / f"resources-{uuid.uuid4().hex}.old"
        temporary.mkdir(mode=0o700)
        try:
            for attachment in record.attachments.values():
                target = temporary.joinpath(*PurePosixPath(
                    attachment.relative_path).parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.symlink_to(attachment.source, target_is_directory=False)
            if resources.exists():
                resources.rename(backup)
            temporary.rename(resources)
            if backup.exists():
                shutil.rmtree(backup)
        except BaseException:
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            if backup.exists() and not resources.exists():
                backup.rename(resources)
            raise

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
