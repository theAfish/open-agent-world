"""Linux namespaces, seccomp and cgroup-v2 sandbox, without an image or daemon.

The trusted systemd scope guard verifies the *actual* cgroup limits before
executing bubblewrap. Failure to establish any boundary never runs the command.
Only distribution tools, explicitly selected workspace and managed attachments
are mounted. This module is also used unchanged by the WSL transport.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .base import SandboxBackend, SandboxEventSink
from .models import (
    CommandResult, ResourceAccess, ResourceAttachment, SandboxEvent,
    SandboxEventType, SandboxInfo, SandboxLimits, SandboxNotFoundError,
    SandboxSecurityError, SandboxState, SandboxStateError, SandboxValidationError,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_UNIT = re.compile(r"^oaw-sandbox-[a-f0-9]{32}\.scope$")
_OUTPUT_LIMIT = 2 * 1024 * 1024
_ERROR_MARKER = "OAW_SANDBOX_SECURITY:"

# This code runs as a trusted service launcher, before any user code. All variable
# arguments are base64 JSON, avoiding systemd's ExecStart dollar expansion.
# The BPF descriptor is created inside the service, so no descriptor forwarding
# through systemd-run (or through WSL) is necessary.
_SERVICE_GUARD = r'''
import base64, ctypes, ctypes.util, errno, json, os, pathlib, platform, resource, sys
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    data = json.loads(base64.b64decode(sys.argv[1]))
    entries = pathlib.Path('/proc/self/cgroup').read_text().splitlines()
    relative = next(line[3:] for line in entries if line.startswith('0::'))
    group = pathlib.Path('/sys/fs/cgroup') / relative.lstrip('/')
    for name, maximum in [('memory.max', data['memory']), ('pids.max', data['pids'])]:
        value = (group / name).read_text().strip()
        if value == 'max' or not 0 < int(value) <= maximum:
            raise RuntimeError(name + ' is not enforced')
    if (group / 'memory.swap.max').read_text().strip() != '0':
        raise RuntimeError('memory.swap.max is not enforced')
    library = ctypes.util.find_library('seccomp')
    if not library:
        raise RuntimeError('libseccomp is required')
    lib = ctypes.CDLL(library)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    class ArgCompare(ctypes.Structure):
        _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_uint),
                    ('mask', ctypes.c_uint64), ('value', ctypes.c_uint64)]
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ArgCompare)]
    context = lib.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError('seccomp allocation failed')
    try:
        # Denying socket/connect also closes the WSL Windows-interop socket
        # transport, including AF_UNIX sockets placed in a selected workspace.
        for name in ('socket', 'socketcall', 'connect', 'mount', 'umount2', 'pivot_root',
                     'setns', 'unshare', 'ptrace', 'process_vm_readv',
                     'process_vm_writev', 'bpf', 'perf_event_open', 'keyctl',
                     'add_key', 'request_key', 'userfaultfd', 'io_uring_setup',
                     'open_by_handle_at', 'init_module', 'finit_module',
                     'delete_module', 'kexec_load', 'reboot', 'swapon', 'swapoff'):
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and lib.seccomp_rule_add(context, 0x50000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('cannot restrict ' + name)
        # Prevent further namespaces without requiring recent bubblewrap
        # --disable-userns support. clone3 has pointer-based flags, so return
        # ENOSYS (allowing libc to use clone for ordinary threads/processes).
        number = lib.seccomp_syscall_resolve_name(b'clone3')
        if number >= 0 and lib.seccomp_rule_add(context, 0x50000 | errno.ENOSYS, number, 0) != 0:
            raise RuntimeError('cannot restrict clone3')
        clone = lib.seccomp_syscall_resolve_name(b'clone')
        if clone < 0:
            raise RuntimeError('cannot resolve clone syscall')
        flag_arg = 1 if platform.machine() in ('s390', 's390x') else 0
        for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                     0x10000000, 0x20000000, 0x40000000):
            comparison = ArgCompare(flag_arg, 7, flag, flag)
            if lib.seccomp_rule_add_array(context, 0x50000 | errno.EPERM,
                    clone, 1, ctypes.byref(comparison)) != 0:
                raise RuntimeError('cannot restrict clone namespace flags')
        descriptor = os.memfd_create('oaw-sandbox-seccomp', 0)
        if lib.seccomp_export_bpf(context, descriptor) != 0:
            raise RuntimeError('seccomp export failed')
    finally:
        lib.seccomp_release(context)
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.set_inheritable(descriptor, True)
    command = data['command']
    command[1:1] = ['--seccomp', str(descriptor)]
    os.execve(command[0], command, {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
except BaseException as exc:
    print('OAW_SANDBOX_SECURITY: ' + str(exc), file=sys.stderr, flush=True)
    sys.exit(125)
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
    if ":" in path.parts[0]:
        raise SandboxValidationError("attachment path must be relative")
    return str(path)


def minimal_linux_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": "/tmp/home", "TMPDIR": "/tmp",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "SHELL": "/bin/sh",
        "SANDBOX_RESOURCES": "/sandbox",
    }
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise SandboxValidationError("env must be an object")
        for key, value in extra.items():
            if key not in {"LANG", "LC_ALL", "TZ", "TERM"}:
                raise SandboxValidationError(f"environment key is outside the allowlist: {key}")
            if not isinstance(value, str) or "\0" in value:
                raise SandboxValidationError("environment values must be NUL-free strings")
            environment[key] = value
    return environment


def _host_control_environment() -> dict[str, str]:
    runtime = f"/run/user/{os.getuid()}"
    return {
        "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": runtime,
    }


def new_unit_name() -> str:
    return f"oaw-sandbox-{uuid.uuid4().hex}.scope"


def service_command(
    command: Sequence[str], unit: str, limits: SandboxLimits, timeout: float,
) -> list[str]:
    if not _SAFE_UNIT.fullmatch(unit):
        raise SandboxValidationError("invalid sandbox service identity")
    payload = base64.b64encode(json.dumps({
        "memory": limits.memory_bytes, "pids": limits.active_process_limit,
        "command": list(command),
    }, ensure_ascii=True).encode()).decode()
    return [
        "/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}",
        f"--property=MemoryMax={limits.memory_bytes}", "--property=MemorySwapMax=0",
        f"--property=TasksMax={limits.active_process_limit}",
        # The application enforces the requested timeout. This independent upper
        # bound also kills orphaned services after an application/transport crash.
        f"--property=RuntimeMaxSec={timeout + 3:.3f}",
        "--property=KillMode=control-group", "--property=KillSignal=SIGKILL",
        "--property=TimeoutStopSec=3", "--",
        sys.executable, "-I", "-c", _SERVICE_GUARD, payload,
    ]


def bubblewrap_command(
    workspace: Path, access: ResourceAccess,
    attachments: Sequence[ResourceAttachment], argv: Sequence[str],
    environment: Mapping[str, str],
) -> list[str]:
    result = [
        "/usr/bin/bwrap", "--unshare-all", "--unshare-user",
        "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
        "--dir", "/etc", "--dir", "/usr",
    ]
    # Distribution-owned tool trees only. No /home, /root, /run, /mnt, complete
    # /etc, /usr/local, or host sockets. Libraries remain shared and read-only.
    for name in ("/usr/bin", "/usr/lib", "/usr/lib64", "/usr/share"):
        if Path(name).exists():
            result.extend(("--ro-bind", name, name))
    for name in ("/bin", "/lib", "/lib64", "/sbin"):
        path = Path(name)
        if path.is_symlink():
            result.extend(("--symlink", os.readlink(path), name))
        elif path.exists():
            result.extend(("--ro-bind", name, name))
    for name in ("/etc/ld.so.cache", "/etc/alternatives"):
        if Path(name).exists():
            result.extend(("--ro-bind", name, name))
    result.extend((
        "--ro-bind" if access == ResourceAccess.READ_ONLY else "--bind",
        str(workspace), "/workspace",
    ))
    # Attachments occupy an independent ephemeral tree: mounting one never
    # creates files in, shadows files in, or changes the selected real folder.
    result.extend(("--dir", "/sandbox", "--dir", "/sandbox/resources"))
    for item in attachments:
        result.extend((
            "--ro-bind" if item.access == ResourceAccess.READ_ONLY else "--bind",
            str(item.source), f"/sandbox/{item.relative_path}",
        ))
    for key, value in environment.items():
        result.extend(("--setenv", key, value))
    result.extend(("--chdir", "/workspace", "--remount-ro", "/", "--", *argv))
    return result


@dataclass(slots=True)
class _Record:
    sandbox_id: str
    root: Path
    workspace_path: str | None = None
    workspace_access: ResourceAccess = ResourceAccess.READ_WRITE
    state: SandboxState = SandboxState.STOPPED
    attachments: dict[str, ResourceAttachment] = field(default_factory=dict)
    active_command: tuple[str, ...] | None = None
    unit: str | None = None
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    deleted: bool = False

    def __post_init__(self) -> None:
        self.done.set()

    @property
    def host_workspace(self) -> Path:
        return Path(self.workspace_path) if self.workspace_path else self.root / "workspace"


class LinuxSandboxBackend(SandboxBackend):
    def __init__(
        self, managed_root: Path, *, limits: SandboxLimits = SandboxLimits(),
        event_sink: SandboxEventSink | None = None, runtime_id: str = "linux",
    ) -> None:
        self._managed_root = Path(managed_root).resolve()
        self._root = self._managed_root / "sandbox-runtimes" / hashlib.sha256(runtime_id.encode()).hexdigest()[:16] / "sandboxes"
        self._limits = limits
        self._event_sink = event_sink
        self._runtime_id = runtime_id
        self._records: dict[str, _Record] = {}
        self._records_lock = asyncio.Lock()

    @classmethod
    async def probe(cls) -> tuple[bool, str | None]:
        if sys.platform != "linux":
            return False, "Linux namespaces require a Linux kernel (native or WSL2)."
        missing = [name for name in ("bwrap", "systemd-run", "systemctl") if not Path(f"/usr/bin/{name}").is_file()]
        if missing:
            return False, "Missing Linux sandbox dependencies: " + ", ".join(missing)
        unit = new_unit_name()
        try:
            with tempfile.TemporaryDirectory(prefix="oaw-sandbox-probe-") as directory:
                command = bubblewrap_command(
                    Path(directory), ResourceAccess.READ_ONLY, (),
                    ("/bin/sh", "-c", "test -d /workspace && test ! -e /run/WSL && test ! -e /mnt/c && printf oaw-probe-ok"),
                    minimal_linux_environment(),
                )
                process = await asyncio.create_subprocess_exec(
                    *service_command(command, unit, SandboxLimits(), 5),
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, env=_host_control_environment(),
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
                except BaseException:
                    await cls.kill_unit(unit)
                    if process.returncode is None:
                        process.kill()
                    await process.communicate()
                    raise
                if process.returncode != 0 or stdout.strip() != b"oaw-probe-ok":
                    detail = stderr.decode("utf-8", "replace").strip()[:1200]
                    return False, "Linux sandbox needs bubblewrap, libseccomp and a systemd user session with cgroup-v2 memory/pids delegation. " + detail
            return True, None
        except (OSError, TimeoutError, SandboxSecurityError) as exc:
            return False, f"Linux sandbox security probe failed: {exc}"

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
                record = _Record(sandbox_id, root)
                self._save(record)
            except BaseException:
                shutil.rmtree(root)
                raise
            self._records[sandbox_id] = record
        await self._emit_state(record)
        return self._info(record)

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
            self._assert_idle(record)
            if sys.platform != "linux":
                raise SandboxSecurityError("Linux sandbox execution requires Linux")
            await asyncio.to_thread(self._validate_record_paths, record)
            record.cancelled.clear()
            record.state = SandboxState.READY
            self._save(record)
        await self._emit_state(record)
        return self._info(record)

    async def execute(
        self, sandbox_id: str, argv: Sequence[str], *,
        timeout_seconds: float | None = None, env: Mapping[str, str] | None = None,
        _unit_name: str | None = None,
    ) -> CommandResult:
        command = validate_argv(argv)
        environment = minimal_linux_environment(env)
        timeout = self._limits.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise SandboxValidationError("timeout_seconds must be finite and positive")
        record = await self._record(sandbox_id)
        unit = _unit_name or new_unit_name()
        if not _SAFE_UNIT.fullmatch(unit):
            raise SandboxValidationError("invalid sandbox service identity")
        async with record.lock:
            if record.deleted or record.state != SandboxState.READY:
                raise SandboxStateError("sandbox must be ready before executing a command")
            await asyncio.to_thread(self._validate_record_paths, record)
            isolated = bubblewrap_command(record.host_workspace, record.workspace_access,
                tuple(record.attachments.values()), command, environment)
            invocation = service_command(isolated, unit, self._limits, timeout)
            record.state = SandboxState.RUNNING
            record.active_command, record.unit = command, unit
            record.cancelled.clear()
            record.done.clear()
            try:
                self._save(record)
            except BaseException:
                record.state = SandboxState.ERROR
                record.active_command = record.unit = None
                record.done.set()
                raise
        started = time.monotonic()
        process: asyncio.subprocess.Process | None = None
        streams: list[asyncio.Task[None]] = []
        waiters: list[asyncio.Task[Any]] = []
        outputs: dict[str, list[str]] = {"stdout": [], "stderr": []}
        timed_out = False
        failure: BaseException | None = None
        cleanup_confirmed = True
        try:
            await self._emit_state(record)
            await self._emit(SandboxEvent(sandbox_id, SandboxEventType.COMMAND_STARTED,
                {"argv": list(command), "timeout_seconds": timeout}))
            process = await asyncio.create_subprocess_exec(*invocation,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=_host_control_environment())

            async def consume(stream: asyncio.StreamReader, label: str) -> None:
                decoder = codecs.getincrementaldecoder("utf-8")("replace")
                total = 0
                truncated = False
                event_type = SandboxEventType.STDOUT if label == "stdout" else SandboxEventType.STDERR

                async def append(value: str) -> None:
                    if value:
                        outputs[label].append(value)
                        await self._emit(SandboxEvent(sandbox_id, event_type, {"text": value}))

                while chunk := await stream.read(8192):
                    value = decoder.decode(chunk)
                    remaining = max(0, _OUTPUT_LIMIT - total)
                    encoded = value.encode("utf-8")
                    total += len(encoded)
                    if remaining:
                        kept = encoded[:remaining].decode("utf-8", "ignore")
                        await append(kept)
                    if total > _OUTPUT_LIMIT and not truncated:
                        await append("\n[output truncated at 2 MiB]\n")
                        truncated = True
                tail = decoder.decode(b"", final=True)
                if total < _OUTPUT_LIMIT and tail:
                    encoded = tail.encode("utf-8")
                    await append(encoded[:_OUTPUT_LIMIT - total].decode("utf-8", "ignore"))
                    total += len(encoded)
                if total > _OUTPUT_LIMIT and not truncated:
                    await append("\n[output truncated at 2 MiB]\n")

            assert process.stdout is not None and process.stderr is not None
            streams = [asyncio.create_task(consume(process.stdout, "stdout")),
                       asyncio.create_task(consume(process.stderr, "stderr"))]
            exited = asyncio.create_task(process.wait())
            cancelled = asyncio.create_task(record.cancelled.wait())
            waiters = [exited, cancelled]
            finished, _ = await asyncio.wait(waiters, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
            timed_out = not finished
            if timed_out or record.cancelled.is_set():
                # Stop the local launcher before stopping its registered scope:
                # cancellation can arrive while systemd is still registering
                # it. This prevents a late registration from starting user code
                # after an initially absent scope was treated as already gone.
                if process.returncode is None:
                    process.kill()
                await self.kill_unit(unit)
            await asyncio.wait_for(asyncio.shield(exited), 5)
            await asyncio.wait_for(asyncio.gather(*streams), 5)
            stderr = "".join(outputs["stderr"])
            if _ERROR_MARKER in stderr:
                raise SandboxSecurityError(stderr.strip())
            result = CommandResult(sandbox_id, command, process.returncode or 0,
                "".join(outputs["stdout"]), stderr, time.monotonic() - started,
                timed_out, record.cancelled.is_set())
        except BaseException as exc:
            failure = exc
            if process is not None and process.returncode is None:
                process.kill()
            try:
                await asyncio.shield(self.kill_unit(unit))
            except BaseException:
                cleanup_confirmed = False
                raise
            raise
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            for task in (*waiters, *streams):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiters, *streams, return_exceptions=True)
            async with record.lock:
                record.state = (SandboxState.ERROR if failure is not None
                    else SandboxState.STOPPED if record.cancelled.is_set() else SandboxState.READY)
                record.active_command = None
                if cleanup_confirmed:
                    record.unit = None
                try:
                    self._save(record)
                finally:
                    record.done.set()
        await self._emit(SandboxEvent(sandbox_id, SandboxEventType.COMMAND_FINISHED, {
            "argv": list(command), "exit_code": result.exit_code,
            "duration_seconds": result.duration_seconds, "timed_out": result.timed_out,
            "cancelled": result.cancelled,
        }))
        await self._emit_state(record)
        return result

    @staticmethod
    async def kill_unit(unit: str) -> None:
        if not _SAFE_UNIT.fullmatch(unit):
            raise SandboxSecurityError("invalid sandbox service identity")
        # stop uses KillMode=control-group and SIGKILL, including daemonized
        # children. An already-collected unit is a successful no-op.
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/systemctl", "--user", "stop", "--", unit,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, env=_host_control_environment())
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode and b"not loaded" not in stderr and b"not found" not in stderr:
            raise SandboxSecurityError("Cannot stop sandbox cgroup: " + stderr.decode("utf-8", "replace")[:1000])

    async def terminate(self, sandbox_id: str) -> None:
        record = await self._record(sandbox_id)
        async with record.lock:
            record.cancelled.set()
        # execute owns stopping its service. Waiting closes spawn-versus-stop
        # races: a stop requested before service creation still kills that unit.
        await record.done.wait()
        async with record.lock:
            self._assert_idle(record)
            if record.unit is not None:
                # A failed earlier cleanup is an outstanding obligation, not
                # evidence of a stopped sandbox. Preserve it on disk until the
                # exact cgroup is confirmed gone, including after restarts.
                await self.kill_unit(record.unit)
                record.unit = None
            record.state = SandboxState.STOPPED
            self._save(record)
        await self._emit_state(record)

    async def attach_resource(self, sandbox_id: str, resource_id: str, source: Path,
        relative_path: str, access: ResourceAccess) -> ResourceAttachment:
        self._validate_id(resource_id)
        source = self._validate_source(Path(source))
        relative = validate_relative_path(relative_path)
        attachment = ResourceAttachment(sandbox_id, resource_id, source, relative, ResourceAccess(access))
        record = await self._record(sandbox_id)
        async with record.lock:
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
            # External workspace and attachment sources are never descendants
            # that we own. Only this card's managed metadata/storage is removed.
            shutil.rmtree(record.root)
            record.deleted = True
        async with self._records_lock:
            self._records.pop(sandbox_id, None)

    async def get(self, sandbox_id: str) -> SandboxInfo:
        return self._info(await self._record(sandbox_id))

    async def _record(self, sandbox_id: str) -> _Record:
        self._validate_id(sandbox_id)
        async with self._records_lock:
            if sandbox_id in self._records:
                return self._records[sandbox_id]
            root = self._root / sandbox_id
            self._assert_within(root.resolve(), self._root)
            manifest = root / "sandbox.json"
            if not manifest.is_file():
                raise SandboxNotFoundError(f"sandbox not found: {sandbox_id}")
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
                if raw["sandbox_id"] != sandbox_id or raw["runtime_id"] != self._runtime_id:
                    raise ValueError("manifest runtime or identity mismatch")
                # Reclaim a surviving command before inspecting user-mutable
                # folders/resources: cleanup must work even if they vanished.
                if raw.get("unit"):
                    await self.kill_unit(raw["unit"])
                record = _Record(sandbox_id, root,
                    raw.get("workspace_path"), ResourceAccess(raw["workspace_access"]))
                for item in raw["attachments"]:
                    self._validate_id(item["resource_id"])
                    attachment = ResourceAttachment(sandbox_id, item["resource_id"],
                        self._validate_source(Path(item["source"]), must_exist=False),
                        validate_relative_path(item["relative_path"]), ResourceAccess(item["access"]))
                    record.attachments[attachment.resource_id] = attachment
                if raw.get("unit"):
                    self._save(record)
            except (KeyError, TypeError, ValueError, OSError) as exc:
                raise SandboxSecurityError(f"invalid sandbox manifest: {exc}") from exc
            self._records[sandbox_id] = record
            return record

    def _save(self, record: _Record) -> None:
        payload = {
            "version": 1, "sandbox_id": record.sandbox_id, "runtime_id": self._runtime_id,
            "workspace_path": record.workspace_path, "workspace_access": record.workspace_access.value,
            "state": record.state.value, "unit": record.unit,
            "attachments": [{"resource_id": item.resource_id, "source": str(item.source),
                "relative_path": item.relative_path, "access": item.access.value}
                for item in record.attachments.values()],
        }
        target = record.root / "sandbox.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _info(self, record: _Record) -> SandboxInfo:
        return SandboxInfo(sandbox_id=record.sandbox_id, state=record.state,
            workspace=Path("/workspace"), attachments=tuple(record.attachments.values()),
            security_boundary="linux-bubblewrap+seccomp+cgroup-v2", network_enabled=False,
            active_command=record.active_command, runtime_id=self._runtime_id, platform="linux",
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
        # Prevent authorizing a host control tree which would contain runtime
        # sockets/credentials rather than a user's project folder.
        for protected in ("/proc", "/sys", "/dev", "/run", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64"):
            if resolved.is_relative_to(Path(protected).resolve()):
                raise SandboxValidationError("workspace_path may not expose a system directory")
        self._validate_workspace_tree(resolved)
        return str(resolved)

    @staticmethod
    def _validate_workspace_tree(root: Path) -> None:
        # Symlinks are interpreted inside the new mount namespace, where host
        # paths do not exist. Hardlinks, however, share an already-openable host
        # inode and could modify a sibling directory despite namespace isolation.
        pending = [root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    metadata = os.stat(entry.path, follow_symlinks=False)
                    if stat.S_ISLNK(metadata.st_mode):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(Path(entry.path))
                    elif not stat.S_ISREG(metadata.st_mode):
                        raise SandboxValidationError(f"workspace contains a special file: {entry.path}")
                    elif metadata.st_nlink > 1:
                        raise SandboxValidationError(f"workspace contains a hard-linked file: {entry.path}")

    def _validate_source(self, source: Path, *, must_exist: bool = True) -> Path:
        if source.is_symlink():
            raise SandboxValidationError("resource symlinks are not allowed")
        try:
            resolved = source.resolve(strict=must_exist)
        except OSError as exc:
            raise SandboxValidationError("managed resource does not exist") from exc
        self._assert_within(resolved, self._managed_root)
        if (any(resolved.is_relative_to(self._managed_root / name)
                for name in ("sandbox-runtimes", "sandboxes", "sandbox-bindings"))
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
        if record.state == SandboxState.RUNNING:
            raise SandboxStateError("stop the sandbox command before changing its configuration or resources")

    async def _emit_state(self, record: _Record) -> None:
        await self._emit(SandboxEvent(record.sandbox_id, SandboxEventType.STATE_CHANGED,
            {"state": record.state.value}))

    async def _emit(self, event: SandboxEvent) -> None:
        if self._event_sink is not None:
            result = self._event_sink(event)
            if inspect.isawaitable(result):
                await result
