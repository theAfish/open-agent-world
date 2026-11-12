import asyncio
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys
import threading

import pytest

from backend.errors import RuntimeUnavailableError
from backend.packs.workers import (
    PackWorkerHost, PackWorkerSpec, PreparedPackWorker, NativePackWorkerExecutionProvider,
)


def recipe(script, events, *, entered=None, release=None):
    @contextmanager
    def lease():
        events.append('lease acquired')
        try:
            yield
        finally:
            events.append('lease released')

    def prepare(environment):
        events.append('prepare')
        if entered is not None:
            entered.set()
            assert release.wait(10)
        return PreparedPackWorker((sys.executable, '-I', '-u', '-c', script), environment, lease())

    return PackWorkerSpec('test.worker', 'python', 'fixture', (), prepare)


def native_host():
    host = PackWorkerHost()
    host.configure(NativePackWorkerExecutionProvider(), inherit_environment=False)
    return host


@pytest.mark.asyncio
async def test_real_batch_worker_logs_files_environment_and_lease(tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_DEPLOYMENT_SECRET', 'must-not-inherit')
    events = []
    script = ('import os,sys; from pathlib import Path; '
              'assert "OAW_DEPLOYMENT_SECRET" not in os.environ; '
              'Path("result.txt").write_text(Path("input.txt").read_text().upper()); '
              'print("output"); print("diagnostic", file=sys.stderr)')
    (tmp_path / 'input.txt').write_text('sample')
    host = native_host()
    worker = await host.start(recipe(script, events), operation_id='run', workspace=tmp_path)
    try:
        assert await worker.wait() == 0
        assert events == ['prepare', 'lease acquired']
    finally:
        await worker.close()
    await worker.close()
    assert events == ['prepare', 'lease acquired', 'lease released']
    assert (tmp_path / 'result.txt').read_text() == 'SAMPLE'
    log = (tmp_path / 'console.log').read_text()
    assert 'output' in log and 'diagnostic' in log
    assert not host.broker.active and not host.workers


@pytest.mark.asyncio
async def test_interactive_worker_is_closed_by_host_shutdown(tmp_path):
    events = []
    host = native_host()
    worker = await host.start(recipe('import sys; print(sys.stdin.readline().strip().upper(), flush=True); '
                                    'sys.stdin.read()', events),
                              operation_id='dialog', workspace=tmp_path, interactive=True)
    await worker.write(b'hello\n')
    assert (await worker.readline()).strip() == b'HELLO'
    await host.shutdown()
    assert worker.closed and events[-1] == 'lease released'
    assert not host.broker.active
    with pytest.raises(RuntimeUnavailableError, match='shutting down'):
        await host.start(recipe('', []), operation_id='later', workspace=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['missing', 'cli-only', 'unconfigured'])
async def test_unconfigured_executor_rejects_before_native_preparation_or_allocation(tmp_path, mode):
    class CLIOnly(NativePackWorkerExecutionProvider):
        def configuration(self, runtime_id):
            if mode == 'unconfigured':
                return {'mode': 'cloud', 'configured': False, 'pack_workers': 'shared-filesystem'}
            return {'mode': 'cloud', 'configured': True}

        async def open(self, request):
            pytest.fail('unsupported worker allocated an execution')

    host = PackWorkerHost()
    if mode != 'missing':
        host.configure(CLIOnly(), inherit_environment=False)
    events = []
    with pytest.raises(RuntimeUnavailableError, match='local fallback is disabled'):
        await host.start(recipe('', events), operation_id='run', workspace=tmp_path)
    assert events == [] and list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_cancellation_during_native_preparation_joins_lease_cleanup(tmp_path):
    entered, release = threading.Event(), threading.Event()
    events = []
    host = native_host()
    opening = asyncio.create_task(host.start(recipe('', events, entered=entered, release=release),
                                            operation_id='run', workspace=tmp_path))
    assert await asyncio.to_thread(entered.wait, 10)
    opening.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert events == ['prepare', 'lease acquired', 'lease released']
    assert not host.broker.active and not host.workers


@pytest.mark.asyncio
async def test_failed_spawn_releases_environment_and_allows_retry(tmp_path):
    events = []
    base = recipe('', events)
    original = base.prepare_native

    def broken(environment):
        prepared = original(environment)
        return replace(prepared, command=(str(tmp_path / 'missing-python'),))

    host = native_host()
    with pytest.raises(FileNotFoundError):
        await host.start(replace(base, prepare_native=broken), operation_id='run', workspace=tmp_path)
    assert events[-1] == 'lease released'
    worker = await host.start(base, operation_id='run', workspace=tmp_path)
    await worker.close()
    assert not host.broker.active


@pytest.mark.asyncio
async def test_log_disk_failure_is_reported_without_hanging_process_cleanup(tmp_path):
    events = []
    host = native_host()
    worker = await host.start(recipe('import sys; print("x" * 200000); sys.stdin.read()', events),
                              operation_id='run', workspace=tmp_path)
    original = worker.log

    class FullDisk:
        def write(self, data):
            raise OSError('disk full')

        def close(self):
            original.close()

    worker.log = FullDisk()
    with pytest.raises(OSError, match='disk full'):
        await asyncio.wait_for(worker.wait(), 10)
    with pytest.raises(OSError, match='disk full'):
        await asyncio.wait_for(worker.close(), 10)
    assert worker.closed and events[-1] == 'lease released'
    assert not host.broker.active


@pytest.mark.asyncio
async def test_same_operation_is_not_launched_twice(tmp_path):
    host = native_host()
    events = []
    spec = recipe('import sys; sys.stdin.read()', events)
    first = await host.start(spec, operation_id='run', workspace=tmp_path)
    try:
        with pytest.raises(RuntimeUnavailableError, match='busy'):
            await host.start(spec, operation_id='run', workspace=tmp_path)
        assert events.count('prepare') == 1
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_failed_stop_retains_environment_until_cleanup_retry(tmp_path, monkeypatch):
    host, events = native_host(), []
    worker = await host.start(recipe('import sys; sys.stdin.read()', events), operation_id='run', workspace=tmp_path)
    original = worker.process.close
    attempts = 0

    async def transient_failure():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError('termination not confirmed')
        await original()

    monkeypatch.setattr(worker.process, 'close', transient_failure)
    with pytest.raises(OSError, match='termination not confirmed'):
        await worker.close()
    assert not worker.closed and 'lease released' not in events
    assert host.broker.active
    await worker.close()
    assert worker.closed and events[-1] == 'lease released'
    assert not host.broker.active


@pytest.mark.asyncio
async def test_context_uses_declared_owner_and_environment(tmp_path):
    from backend.packs.spec import read_source_pack
    from open_agent_world.plugin_api import PluginContext
    source = Path(__file__).resolve().parents[2] / 'plugins/xrd'
    context = PluginContext(tmp_path, source, read_source_pack(source), native_host())
    spec = recipe('', [])
    with pytest.raises(ValueError, match='belong'):
        await context.start_worker(spec, operation_id='run', workspace=tmp_path)
    with pytest.raises(ValueError, match='declared'):
        await context.start_worker(replace(spec, pack_id='research.xrd'), operation_id='run', workspace=tmp_path)


@pytest.mark.asyncio
async def test_alternative_executor_receives_logical_worker_without_native_preparation(tmp_path):
    from backend.agents.execution import NativeRuntimeExecution
    observed = []

    class SharedFilesystemExecutor(NativePackWorkerExecutionProvider):
        async def open(self, request):
            observed.append(request.worker)
            execution = NativeRuntimeExecution(request)
            execution.command = (sys.executable, '-I', '-c', 'print("adapter selected")')
            return execution

    host = PackWorkerHost()
    host.configure(SharedFilesystemExecutor(), inherit_environment=False)
    events = []
    spec = recipe('', events)
    worker = await host.start(spec, operation_id='run', workspace=tmp_path)
    try:
        assert await worker.wait() == 0
    finally:
        await worker.close()
    assert observed == [spec] and events == []
    assert 'adapter selected' in (tmp_path / 'console.log').read_text()


@pytest.mark.asyncio
async def test_service_composition_disables_implicit_hosted_workers(tmp_path):
    from backend.config import Settings
    from backend.plugins.builtin import create_builtin_registry
    from backend.request_context import TenantScope
    from backend.services import create_services

    for hosted in (False, True):
        settings = Settings.for_data_root(tmp_path / str(hosted))
        if hosted:
            settings = replace(settings, tenant_scope=TenantScope('team', 'user'))
        services = create_services(settings, plugins=create_builtin_registry())
        try:
            configuration = services.plugins.worker_host.broker.configuration('test.worker')
            assert configuration['configured'] is not hosted
            if hosted:
                with pytest.raises(RuntimeUnavailableError, match='fallback is disabled'):
                    await services.plugins.worker_host.start(recipe('', []),
                        operation_id='run', workspace=settings.data_root)
            else:
                worker = await services.plugins.worker_host.start(recipe('import sys; sys.stdin.read()', []),
                    operation_id='run', workspace=settings.data_root)
                await services._cleanup_run_execution('run')
                assert worker.closed
        finally:
            await services.shutdown()
            services.close()


@pytest.mark.asyncio
async def test_hosted_explicit_executor_reads_live_shared_variables_without_process_secrets(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.config import Settings
    from backend.execution_config import SecretRequirement
    from backend.plugins.builtin import create_builtin_registry
    from backend.request_context import TenantScope
    from backend.security.environment_source import ExecutionEnvironmentSnapshot
    from backend.services import create_services

    monkeypatch.setenv('OAW_DEPLOYMENT_SECRET', 'deployment-only')
    settings = replace(Settings.for_data_root(tmp_path), tenant_scope=TenantScope('team', 'user'),
                       inherit_runtime_environment=False)
    services = create_services(settings, plugins=create_builtin_registry(),
                               pack_worker_execution_provider=NativePackWorkerExecutionProvider())
    approved = {'tool-key': 'first'}
    services.execution_environment_source = SimpleNamespace(read=lambda: ExecutionEnvironmentSnapshot(
        {'TOOL_KEY': SecretRequirement(secret_ref='tool-key')}, approved))
    try:
        for value in ('first', 'rotated'):
            approved['tool-key'] = value
            spec = recipe('import os; assert "OAW_DEPLOYMENT_SECRET" not in os.environ; print(os.environ["TOOL_KEY"])', [])
            worker = await services.plugins.worker_host.start(spec, operation_id=value, workspace=tmp_path,
                                                               log_name=value + '.log')
            try:
                assert await worker.wait() == 0
            finally:
                await worker.close()
            assert (tmp_path / (value + '.log')).read_text().strip() == value
    finally:
        await services.shutdown()
        services.close()
