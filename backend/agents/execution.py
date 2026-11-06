"""Execution facilities for trusted CLI runtimes, independent of their protocol.

Native execution is a personal host process, not a Sandbox security boundary.
Hosted compositions must supply their own execution provider explicitly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import os
from pathlib import Path
import signal
import stat
import subprocess
from typing import Literal

from backend.errors import ResourceValidationError, RuntimeUnavailableError
from backend.file_transfer import (
    INLINE_FILE_BYTES, MAX_FILE_BYTES, FileTransferEndpoint, _settle,
)

# Compatibility names for the small byte convenience API and logical file bound.
FILE_LIMIT = INLINE_FILE_BYTES
FILE_STREAM_LIMIT = MAX_FILE_BYTES


@dataclass(frozen=True)
class RuntimeExecutionPolicy:
    """Host-selected runtime defaults; location alone never grants isolation."""

    isolation: Literal['none', 'dedicated-vm'] = 'none'
    default_permission: Literal['workspace-write', 'environment'] = 'workspace-write'

    def __post_init__(self):
        if (self.isolation not in {'none', 'dedicated-vm'}
                or self.default_permission not in {'workspace-write', 'environment'}
                or self.default_permission == 'environment' and self.isolation != 'dedicated-vm'):
            raise ValueError('Environment permissions require a dedicated VM isolation policy')


@dataclass(frozen=True)
class RuntimeExecutionRequest:
    node_id: str
    operation_id: str
    native_workspace: Path
    native_home: Path
    environment: Mapping[str, str] = field(default_factory=dict, repr=False)
    variables: Mapping[str, object] = field(default_factory=dict, repr=False)
    runtime_id: str | None = None


class RuntimeProcess(ABC):
    """Duplex bytes, not a provider SDK handle or a host PID."""

    @abstractmethod
    async def write(self, data: bytes) -> None: ...

    @abstractmethod
    async def readline(self) -> bytes: ...

    @abstractmethod
    async def read(self, size: int = 16384) -> bytes: ...

    @abstractmethod
    async def read_stderr(self, size: int = 8192) -> bytes: ...

    @abstractmethod
    async def wait(self) -> int: ...

    @abstractmethod
    async def close(self) -> None:
        """Join cleanup; repeated calls may retry a failed cleanup."""


class RuntimeExecution(FileTransferEndpoint, ABC):
    """One operation's processes, private file roots and loopback tool access."""

    remote: bool = False
    policy: RuntimeExecutionPolicy = RuntimeExecutionPolicy()
    workspace: str
    home: str
    environment: Mapping[str, str]
    command: tuple[str, ...] | None = None

    @asynccontextmanager
    async def model_connection(self, reference: str):
        """Use an OAW model through a private, execution-scoped connection.

        The returned API key is a temporary proxy token, never the stored key.
        Provider/model/base URL resolve together; independent Harness settings
        remain available by not requesting this connection.
        """
        factory = getattr(self, '_model_connection', None)
        if factory is None:
            raise RuntimeUnavailableError('This host has no model connection service')
        async with factory(reference) as connection:
            yield connection

    @abstractmethod
    async def spawn(self, argv: list[str], *, env: Mapping[str, str]) -> RuntimeProcess: ...

    @abstractmethod
    async def read_file(self, path: str, *, root: str = "workspace", max_bytes: int = FILE_LIMIT) -> bytes: ...

    @abstractmethod
    async def write_file(self, path: str, data: bytes, *, root: str = "workspace") -> str: ...

    @abstractmethod
    async def forward_http(self, url: str, *, path_prefix: bool = False, stream: bool = False) -> str:
        """Make a host-issued loopback tool endpoint reachable by this execution.

        Forwarding must stay private and bounded; it must never expose arbitrary
        host services or require a public unauthenticated ingress.
        """

    @abstractmethod
    async def close(self) -> None: ...


class RuntimeExecutionProvider(ABC):
    @abstractmethod
    def configuration(self, runtime_id: str | None) -> dict[str, object]:
        """Pure discovery: no installation, process or cloud allocation."""
        ...

    @abstractmethod
    async def open(self, request: RuntimeExecutionRequest) -> RuntimeExecution: ...


class RuntimeExecutionBroker:
    """Host-owned admission and cleanup, including a runtime that forgets to close."""

    def __init__(self, provider: RuntimeExecutionProvider | None, *, resolve_model=None):
        self.provider = provider
        self.resolve_model = resolve_model
        self.active = {}
        self.closing = False

    def configuration(self, runtime_id):
        return self.provider.configuration(runtime_id) if self.provider else {"mode":"unavailable","configured":False}

    @asynccontextmanager
    async def open(self, request):
        if self.provider is None:
            raise RuntimeUnavailableError("This host has no runtime execution provider; local fallback is disabled")
        if self.closing or request.node_id in self.active:
            raise RuntimeUnavailableError("Runtime execution is busy or shutting down")
        opening = asyncio.create_task(self.provider.open(request))
        entry = {"operation_id": request.operation_id, "opening": opening, "execution": None,
                 "models": set(), "closing": False, "close_lock": asyncio.Lock()}
        self.active[request.node_id] = entry
        try:
            execution, interrupted = await _settle(opening)
            entry["execution"] = execution
            execution._model_connection = lambda reference: self._model_connection(request.node_id, entry, reference)
            if interrupted:
                raise asyncio.CancelledError
            yield execution
        finally:
            if opening.done() and (opening.cancelled() or opening.exception() is not None):
                self.active.pop(request.node_id, None)
            else:
                await self._close(request.node_id, entry)

    @asynccontextmanager
    async def _model_connection(self, node_id, entry, reference):
        from backend.agents.model_proxy import RuntimeModelProxy
        active = lambda: self.active.get(node_id) is entry and not entry['closing'] and not self.closing
        if not active() or self.resolve_model is None:
            raise RuntimeUnavailableError('Model connection requires an active hosted execution')
        proxy = RuntimeModelProxy(self.resolve_model, reference, active)
        entry['models'].add(proxy)
        try:
            connection = await proxy.open(entry['execution'])
            yield connection
        finally:
            _, interrupted = await _settle(proxy.close())
            entry['models'].discard(proxy)
            if interrupted:
                raise asyncio.CancelledError

    async def _close(self, node_id, entry):
        async with entry['close_lock']:
            entry['closing'] = True
            interrupted = False
            for proxy in tuple(entry['models']):
                _, cancelled = await _settle(proxy.close())
                interrupted = interrupted or cancelled
                entry['models'].discard(proxy)
            await self._close_execution(node_id, entry)
            if interrupted:
                raise asyncio.CancelledError

    async def _close_execution(self, node_id, entry):
        execution = entry["execution"]
        if execution is None:
            execution, _ = await _settle(entry["opening"])
            entry["execution"] = execution
        _, interrupted = await _settle(execution.close())
        if self.active.get(node_id) is entry:
            del self.active[node_id]
        if interrupted:
            raise asyncio.CancelledError

    async def close_operation(self, operation_id):
        for node_id, entry in tuple(self.active.items()):
            if entry["operation_id"] == operation_id:
                await self._close(node_id, entry)

    async def shutdown(self):
        self.closing = True
        failures = []
        for node_id, entry in tuple(self.active.items()):
            try:
                await self._close(node_id, entry)
            except BaseException as error:
                failures.append(error)
        if failures:
            raise BaseExceptionGroup("Runtime execution cleanup failed", failures)


def command_arguments(argv, env):
    if (not isinstance(argv, (list, tuple)) or not argv or not argv[0]
            or any(not isinstance(v, str) or "\0" in v for v in argv)):
        raise ResourceValidationError("Runtime command requires nonempty NUL-free arguments")
    if any(not isinstance(k, str) or not k or "=" in k or "\0" in k
           or not isinstance(v, str) or "\0" in v for k, v in env.items()):
        raise ResourceValidationError("Runtime environment requires NUL-free string names and values")


def loopback_endpoint(url):
    from urllib.parse import urlsplit
    if not isinstance(url, str) or any(ord(char) < 32 for char in url):
        raise ResourceValidationError("Runtime forwarding requires an exact loopback HTTP endpoint")
    try:
        value = urlsplit(url)
        port = value.port
    except ValueError as error:
        raise ResourceValidationError("Runtime forwarding requires an exact loopback HTTP endpoint") from error
    if (value.scheme != "http" or value.hostname not in {"127.0.0.1", "::1"}
            or value.username or value.password or value.query or value.fragment
            or port is None or not 1024 <= port <= 65535):
        raise ResourceValidationError("Runtime forwarding requires an exact loopback HTTP endpoint")
    return value


class NativeRuntimeProcess(RuntimeProcess):
    def __init__(self, process):
        self._process = process
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def write(self, data):
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def readline(self):
        return await self._process.stdout.readline()

    async def read(self, size=16384):
        return await self._process.stdout.read(size)

    async def read_stderr(self, size=8192):
        return await self._process.stderr.read(size)

    async def wait(self):
        return await self._process.wait()

    async def close(self):
        async with self._close_lock:
            if self._closed:
                return
            process = self._process
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill.exe", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    await killer.wait()
                else:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await process.wait()
            # A Unix child can exit while its descendants retain our pipes.
            if os.name != "nt":
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            self._closed = True


class NativeRuntimeExecution(RuntimeExecution):
    def __init__(self, request):
        from backend.sandbox.environment import apply_invocation_environment
        self.roots = {"workspace": request.native_workspace.resolve(), "home": request.native_home.resolve()}
        self.workspace, self.home = (str(self.roots[k]) for k in ("workspace", "home"))
        self.environment = dict(request.environment)
        apply_invocation_environment(self.environment, request.variables, windows=os.name == 'nt')
        self.processes = []
        self.closed = False

    def _root(self, root):
        if self.closed:
            raise RuntimeUnavailableError("Runtime execution is closed")
        if root not in self.roots:
            raise ResourceValidationError("Unknown runtime file root")
        return self.roots[root]

    def _path(self, raw, root):
        from backend.sandbox.files import parts
        from backend.sandbox.models import SandboxValidationError
        base = self._root(root)
        path = Path(raw)
        if path.is_absolute():
            try:
                raw = path.relative_to(base).as_posix()
            except ValueError as error:
                raise ResourceValidationError("Runtime file must stay within its authorized root") from error
        try:
            return base.joinpath(*parts(raw))
        except SandboxValidationError as error:
            raise ResourceValidationError(str(error)) from error

    async def spawn(self, argv, *, env):
        self._root("workspace")
        command_arguments(argv, env)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process, interrupted = await _settle(asyncio.create_subprocess_exec(*argv, cwd=self.workspace, env={**self.environment,**env},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024, **options))
        wrapped = NativeRuntimeProcess(process)
        self.processes.append(wrapped)
        if interrupted:
            await _settle(wrapped.close())
            raise asyncio.CancelledError
        return wrapped

    async def read_file(self, path, *, root="workspace", max_bytes=FILE_LIMIT):
        from backend.sandbox.files import pinned, run_file_operation
        from backend.sandbox.models import SandboxSecurityError
        if type(max_bytes) is not int or not 0 < max_bytes <= FILE_LIMIT:
            raise ResourceValidationError("Runtime file limit must be 1..32 MiB")
        target = self._path(path, root)
        def read():
            with pinned(target) as fd:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                    raise ResourceValidationError("Runtime file is not regular or exceeds its limit")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    data = stream.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ResourceValidationError("Runtime file exceeds its limit")
                return data
        try:
            return await run_file_operation(read)
        except SandboxSecurityError as error:
            raise ResourceValidationError(str(error)) from error

    async def write_file(self, path, data, *, root="workspace"):
        from backend.sandbox.files import pinned, run_file_operation
        from backend.sandbox.models import SandboxSecurityError
        if not isinstance(data, bytes) or len(data) > FILE_LIMIT:
            raise ResourceValidationError("Runtime file must contain at most 32 MiB of bytes")
        target = self._path(path, root)
        def write():
            with pinned(target, write=True, create_parents_from=self._root(root)) as fd:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ResourceValidationError("Runtime destination must be a regular file")
                os.ftruncate(fd, 0)
                with os.fdopen(os.dup(fd), "wb") as stream:
                    stream.write(data)
        try:
            await run_file_operation(write)
        except SandboxSecurityError as error:
            raise ResourceValidationError(str(error)) from error
        return str(target)

    async def _file_upload(self, operation, path, *, root='workspace', **options):
        from backend.sandbox.files import run_file_operation
        from backend.sandbox.models import SandboxSecurityError, SandboxValidationError
        from backend.sandbox.streaming import upload_operation
        target = self._path(path, root)
        base = self._root(root)
        try:
            result = await run_file_operation(upload_operation, base, operation,
                path=target.relative_to(base).as_posix(), **options)
        except (SandboxSecurityError, SandboxValidationError) as error:
            raise ResourceValidationError(str(error)) from error
        return {**result, 'path': str(target)}

    async def _file_download(self, operation, path, *, root='workspace', **options):
        from backend.sandbox.files import run_file_operation
        from backend.sandbox.models import SandboxSecurityError, SandboxValidationError
        from backend.sandbox.transfers import transfer
        target = self._path(path, root)
        try:
            return await run_file_operation(transfer, self._root(root), operation,
                                            path=target.relative_to(self._root(root)).as_posix(), **options)
        except (SandboxSecurityError, SandboxValidationError) as error:
            raise ResourceValidationError(str(error)) from error

    async def forward_http(self, url, *, path_prefix=False, stream=False):
        self._root("workspace")
        loopback_endpoint(url)
        return url

    async def close(self):
        self.closed = True
        failures = []
        for process in reversed(self.processes):
            try:
                await process.close()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise BaseExceptionGroup("Runtime process cleanup failed", failures)


class NativeRuntimeExecutionProvider(RuntimeExecutionProvider):
    def configuration(self, runtime_id):
        return {"mode": "native", "configured": True}

    async def open(self, request):
        execution = NativeRuntimeExecution(request)
        for path in execution.roots.values():
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return execution
