"""Host-owned execution of trusted Pack workers with a shared filesystem.

Uses the ordinary runtime transport and cleanup broker. A provider must opt in
to the filesystem contract before any Pack preparation runs. Remote providers
without the Pack code, environment and authorized workspace must reject it;
this API does not copy arbitrary host directories or turn them into mounts.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
import os
from pathlib import Path

from backend.agents.execution import (
    RuntimeExecutionBroker, RuntimeExecutionRequest, RuntimeProcess,
    NativeRuntimeExecution, NativeRuntimeExecutionProvider, command_arguments,
)
from backend.agents.environment import subprocess_environment
from backend.errors import RuntimeUnavailableError
from backend.file_transfer import _settle


@dataclass(frozen=True)
class PreparedPackWorker:
    """Native launch recipe. The host holds the lease through process cleanup."""

    command: tuple[str, ...]
    environment: Mapping[str, str] = field(repr=False)
    lease: AbstractContextManager = field(repr=False)


@dataclass(frozen=True)
class PackWorkerSpec:
    """Logical worker identity plus a native-only, deferred preparation hook.

    Arguments and workspace files currently use the shared filesystem layout.
    A remote adapter must provide that layout, or reject before allocation.
    No provider may call prepare_native and then silently fall back to ECS.
    """

    pack_id: str
    environment_id: str
    worker_id: str
    arguments: tuple[str, ...]
    prepare_native: Callable[[Mapping[str, str]], PreparedPackWorker] = field(repr=False, compare=False)
    # Bound by PluginContext from the selected source/archive, not card input.
    pack_version: str | None = None
    source_root: Path | None = None


@dataclass(frozen=True)
class PackWorkerExecutionRequest(RuntimeExecutionRequest):
    worker: PackWorkerSpec = field(kw_only=True, repr=False)


class _NativePackWorkerExecution(NativeRuntimeExecution):
    def __init__(self, request):
        super().__init__(request)
        self.lease = None
        self._lease_lock = asyncio.Lock()

    async def close(self):
        await super().close()
        async with self._lease_lock:
            if self.lease is not None:
                self.lease.__exit__(None, None, None)
                self.lease = None


class NativePackWorkerExecutionProvider(NativeRuntimeExecutionProvider):
    """Explicit local composition; never selected implicitly for hosted profiles."""

    def configuration(self, runtime_id):
        return {**super().configuration(runtime_id), 'pack_workers': 'shared-filesystem'}

    async def open(self, request):
        execution = _NativePackWorkerExecution(request)
        prepared, interrupted = await _settle(asyncio.to_thread(
            request.worker.prepare_native, dict(execution.environment)))
        command_arguments(prepared.command, prepared.environment)
        prepared.lease.__enter__()
        execution.lease = prepared.lease
        execution.command = prepared.command
        execution.environment = dict(prepared.environment)
        if interrupted:
            await _settle(execution.close())
            raise asyncio.CancelledError
        return execution


class PackWorker(RuntimeProcess):
    """Pack-facing process handle; no OS PID, subprocess or cloud SDK leaks."""

    def __init__(self, process, scope, log, *, operation_id, interactive, on_close):
        self.process, self.scope, self.log = process, scope, log
        self.operation_id = operation_id
        self.interactive, self.on_close = interactive, on_close
        self.closed = False
        self._close_lock = asyncio.Lock()
        self._failure = asyncio.get_running_loop().create_future()
        readers = [process.read_stderr]
        if not interactive:
            readers.append(process.read)
        self.drains = [asyncio.create_task(self._drain(read)) for read in readers]

    async def _drain(self, read):
        try:
            while chunk := await read():
                if not self._failure.done():
                    try:
                        self.log.write(chunk)
                        self.log.flush()
                    except Exception as error:
                        # Keep draining while the caller stops the process: a
                        # full pipe must not deadlock cleanup after disk failure.
                        self._failure.set_result(error)
        except Exception as error:
            if not self._failure.done():
                self._failure.set_result(error)

    async def _observe(self, operation):
        task = asyncio.ensure_future(operation)
        try:
            await asyncio.wait((task, self._failure), return_when=asyncio.FIRST_COMPLETED)
            if self._failure.done():
                raise self._failure.result()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def write(self, data):
        if not self.interactive:
            raise RuntimeUnavailableError('This Pack worker has no interactive input')
        await self._observe(self.process.write(data))

    async def readline(self):
        if not self.interactive:
            raise RuntimeUnavailableError('This Pack worker writes stdout to its log')
        return await self._observe(self.process.readline())

    async def read(self, size=16384):
        if not self.interactive:
            raise RuntimeUnavailableError('This Pack worker writes stdout to its log')
        return await self._observe(self.process.read(size))

    async def read_stderr(self, size=8192):
        raise RuntimeUnavailableError('Pack worker stderr is owned by its log')

    async def wait(self):
        return await self._observe(self.process.wait())

    async def close(self):
        async with self._close_lock:
            if self.closed:
                return
            # Failed cleanup retains the handle and environment lease for retry.
            _, interrupted = await _settle(self.process.close())
            try:
                _, cancelled = await _settle(asyncio.gather(*self.drains))
                interrupted = interrupted or cancelled
            finally:
                _, cancelled = await _settle(self.scope.__aexit__(None, None, None))
                interrupted = interrupted or cancelled
                self.log.close()
                self.closed = True
                self.on_close()
            if interrupted:
                raise asyncio.CancelledError
            if self._failure.done():
                raise self._failure.result()


class PackWorkerHost:
    """Profile-scoped composition point; unconfigured hosts fail closed."""

    def __init__(self):
        self.broker = RuntimeExecutionBroker(None)
        self.inherit_environment = False
        self.environment_variables = lambda: {}
        self.workers = set()
        self.owner = None

    def configure(self, provider, *, inherit_environment, environment_variables=None, owner=None):
        if self.broker.active or self.workers or self.owner is not None and not self.broker.closing:
            raise RuntimeUnavailableError('Cannot reconfigure active Pack workers')
        self.broker = RuntimeExecutionBroker(provider)
        self.owner = owner
        self.inherit_environment = inherit_environment
        self.environment_variables = environment_variables or (lambda: {})

    def release(self, owner):
        if self.owner is owner and not self.broker.active and not self.workers:
            self.broker.closing = True
            self.owner = None

    async def start(self, spec, *, operation_id, workspace, log_name='console.log', interactive=False):
        configuration = self.broker.configuration(spec.pack_id)
        if configuration.get('configured') is not True or configuration.get('pack_workers') != 'shared-filesystem':
            raise RuntimeUnavailableError(
                f'No compatible Pack worker executor is configured for {spec.pack_id}/{spec.worker_id}; '
                'local fallback is disabled')
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError('Worker operation identity must not be empty')
        if Path(log_name).name != log_name or log_name in {'', '.', '..'}:
            raise ValueError('Worker log must be a filename in its workspace')
        workspace = Path(workspace).resolve(strict=True)
        request = PackWorkerExecutionRequest(
            f'{spec.pack_id}:{operation_id}', operation_id, workspace, workspace,
            subprocess_environment(inherit=self.inherit_environment), self.environment_variables(),
            runtime_id=spec.pack_id, worker=spec)
        scope = self.broker.open(request)
        execution = await scope.__aenter__()
        log = None
        try:
            if self.broker.closing:
                raise RuntimeUnavailableError('Pack worker host is shutting down')
            if not execution.command:
                raise RuntimeUnavailableError('Pack worker executor did not provide a launch command')
            # The native hook must not be run by this common path. A cloud
            # provider resolves its own environment and command from spec.
            from backend.sandbox.files import pinned
            with pinned(workspace / log_name, write=True, create_parents_from=workspace) as descriptor:
                log = os.fdopen(os.dup(descriptor), 'ab')
            process = await execution.spawn(list(execution.command), env={})
            if self.broker.closing:
                raise RuntimeUnavailableError('Pack worker host is shutting down')
            worker = PackWorker(process, scope, log, operation_id=operation_id, interactive=interactive,
                                on_close=lambda: self.workers.discard(worker))
            self.workers.add(worker)
            return worker
        except BaseException:
            try:
                await _settle(scope.__aexit__(None, None, None))
            finally:
                if log is not None:
                    log.close()
            raise

    async def close_operation(self, operation_id):
        for worker in tuple(self.workers):
            if worker.operation_id == operation_id:
                await worker.close()
        await self.broker.close_operation(operation_id)

    async def shutdown(self):
        self.broker.closing = True
        failures = []
        for worker in tuple(self.workers):
            try:
                await worker.close()
            except BaseException as error:
                failures.append(error)
        try:
            await self.broker.shutdown()
        except BaseException as error:
            failures.append(error)
        if failures:
            raise BaseExceptionGroup('Pack worker cleanup failed', failures)
