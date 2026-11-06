import asyncio
import os
from pathlib import Path
import sys

import pytest

from backend.agents.execution import (
    NativeRuntimeExecutionProvider, RuntimeExecutionBroker, RuntimeExecutionPolicy, RuntimeExecutionRequest,
)
from backend.file_transfer import FILE_CHUNK_BYTES
from backend.errors import ResourceValidationError, RuntimeUnavailableError


async def execution(tmp_path):
    return await NativeRuntimeExecutionProvider().open(RuntimeExecutionRequest(
        'agent', 'run', tmp_path / 'project', tmp_path / 'home'))


@pytest.mark.asyncio
async def test_duplex_process_and_explicit_child_environment(tmp_path):
    host = await execution(tmp_path)
    script = 'import os,sys;print(os.environ.get("OAW_TEST_CHILD", "missing"), flush=True);print(sys.stdin.readline().strip(),flush=True);print("diagnostic",file=sys.stderr,flush=True)'
    try:
        process = await host.spawn([sys.executable, '-I', '-u', '-c', script, ''], env={
            **{k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR'}},
            'OAW_TEST_CHILD':'scoped'})
        assert await process.readline() == b'scoped' + os.linesep.encode()
        await process.write(b'hello\n')
        assert await process.readline() == b'hello' + os.linesep.encode()
        diagnostic = bytearray()
        while block := await process.read_stderr():
            diagnostic.extend(block)
        assert bytes(diagnostic) == b'diagnostic' + os.linesep.encode()
        assert await process.wait() == 0
    finally:
        await host.close()
    assert 'OAW_TEST_CHILD' not in os.environ
    await host.close()
    with pytest.raises(RuntimeUnavailableError, match='closed'):
        await host.spawn([sys.executable], env={})


@pytest.mark.asyncio
async def test_bounded_files_use_the_existing_no_follow_contract(tmp_path):
    host = await execution(tmp_path)
    try:
        path = await host.write_file('input/report.txt', b'report')
        assert path == str(tmp_path / 'project/input/report.txt')
        assert await host.read_file(path) == b'report'
        home = await host.write_file('config.json', b'private', root='home')
        assert Path(home).parent == tmp_path / 'home'
        with pytest.raises(ResourceValidationError, match='limit'):
            await host.read_file(path, max_bytes=3)
        with pytest.raises(ResourceValidationError, match='safe portable relative paths'):
            await host.read_file('../home/config.json')
        with pytest.raises(ResourceValidationError, match='authorized root'):
            await host.read_file(home)
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupted_operation', ['upload_begin', 'upload_chunk'])
async def test_stream_cancellation_joins_file_side_effect_before_cleanup(tmp_path, monkeypatch, interrupted_operation):
    host = await execution(tmp_path)
    original = host._file_upload
    applied, release = asyncio.Event(), asyncio.Event()

    async def upload(operation, *args, **kwargs):
        result = await original(operation, *args, **kwargs)
        if operation == interrupted_operation:
            applied.set()
            await release.wait()
        return result

    async def content():
        yield b'partial input'

    monkeypatch.setattr(host, '_file_upload', upload)
    task = asyncio.create_task(host.write_file_stream('input/test.xlsx', content()))
    try:
        await asyncio.wait_for(applied.wait(), 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list((tmp_path / 'project').rglob('*.*'))
    finally:
        release.set()
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,match', [('file_limit', 'limit'), ('invalid_type', 'bytes')])
async def test_stream_limits_abort_owned_staging_and_close_source(tmp_path, failure, match):
    host = await execution(tmp_path)
    closed = False

    async def content():
        nonlocal closed
        try:
            yield b'x'
            yield 'invalid' if failure == 'invalid_type' else b'123'
        finally:
            closed = True

    try:
        with pytest.raises(ResourceValidationError, match=match):
            await host.write_file_stream('input/test.xlsx', content(),
                max_bytes=512 * 1024 * 1024 if failure == 'invalid_type' else 2)
        assert closed
        assert not list((tmp_path / 'project').rglob('*.*'))
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_stream_abort_failure_preserves_source_error(tmp_path, monkeypatch):
    host = await execution(tmp_path)
    original = host._file_upload
    cleanup = []

    async def upload(operation, *args, **kwargs):
        result = await original(operation, *args, **kwargs)
        if operation == 'upload_abort':
            cleanup.append(True)
            # The runtime applied cleanup, but its reply was lost.
            raise RuntimeUnavailableError('private transport diagnostic')
        return result

    async def content():
        yield b'partial input'
        raise OSError('source stream interrupted')

    monkeypatch.setattr(host, '_file_upload', upload)
    try:
        with pytest.raises(OSError, match='source stream interrupted') as failure:
            await host.write_file_stream('input/test.xlsx', content())
        assert cleanup == [True]
        assert failure.value.__notes__ == ['File staging cleanup failed (RuntimeUnavailableError)']
        assert not list((tmp_path / 'project').rglob('*.*'))
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [b'', b'x' * (FILE_CHUNK_BYTES + 17)], ids=['empty', 'rechunked'])
async def test_file_streams_normalize_source_chunks_and_verify_eof(tmp_path, content):
    host = await execution(tmp_path)
    closed = False

    async def source():
        nonlocal closed
        try:
            yield content
        finally:
            closed = True

    try:
        target = await host.write_file_stream('input.bin', source())
        chunks = [chunk async for chunk in host.read_file_stream(target)]
        assert b''.join(chunks) == content and all(0 < len(chunk) <= FILE_CHUNK_BYTES for chunk in chunks)
        assert closed
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_stream_read_rechecks_file_identity_after_last_yield(tmp_path):
    host = await execution(tmp_path)
    try:
        target = await host.write_file('input.bin', b'original')
        stream = host.read_file_stream(target)
        assert await anext(stream) == b'original'
        Path(target).write_bytes(b'changed')
        with pytest.raises(ResourceValidationError, match='Source changed'):
            await anext(stream)
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_stream_read_rejects_limits_traversal_and_hardlinks(tmp_path):
    host = await execution(tmp_path)
    try:
        target = await host.write_file('input.bin', b'content')
        with pytest.raises(ResourceValidationError, match='stream limit'):
            await anext(host.read_file_stream(target, max_bytes=2))
        with pytest.raises(ResourceValidationError, match='safe portable relative paths'):
            await anext(host.read_file_stream('../outside'))
        os.link(target, tmp_path / 'project/alias.bin')
        with pytest.raises(ResourceValidationError, match='non-hardlinked'):
            await anext(host.read_file_stream('alias.bin'))
    finally:
        await host.close()


def test_runtime_policy_requires_explicit_isolation_and_is_immutable():
    from dataclasses import FrozenInstanceError

    policy = RuntimeExecutionPolicy()
    assert policy.isolation == 'none' and policy.default_permission == 'workspace-write'
    with pytest.raises(ValueError, match='dedicated VM'):
        RuntimeExecutionPolicy(default_permission='environment')
    assert RuntimeExecutionPolicy('dedicated-vm', 'environment').default_permission == 'environment'
    with pytest.raises(FrozenInstanceError):
        policy.isolation = 'dedicated-vm'


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['http://example.com/mcp','https://127.0.0.1:18081/mcp',
    'http://127.0.0.1:80/mcp','http://user@127.0.0.1:18081/mcp','http://127.0.0.1:18081/mcp?next=/'])
async def test_forwarding_does_not_accept_arbitrary_host_endpoints(tmp_path, url):
    host = await execution(tmp_path)
    try:
        assert await host.forward_http('http://127.0.0.1:18081/mcp') == 'http://127.0.0.1:18081/mcp'
        with pytest.raises(ResourceValidationError, match='loopback'):
            await host.forward_http(url)
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_hosted_missing_provider_cannot_fall_back_to_native(tmp_path):
    broker = RuntimeExecutionBroker(None)
    request = RuntimeExecutionRequest('agent', 'run', tmp_path / 'project', tmp_path / 'home')
    with pytest.raises(RuntimeUnavailableError, match='fallback is disabled'):
        async with broker.open(request):
            pytest.fail('A hosted runtime was admitted without an execution provider')
    assert not request.native_workspace.exists()


@pytest.mark.asyncio
async def test_cancellation_during_open_joins_ownership_and_cleanup(tmp_path):
    entered, proceed = asyncio.Event(), asyncio.Event()
    opened = []
    class Provider(NativeRuntimeExecutionProvider):
        async def open(self, request):
            entered.set()
            await proceed.wait()
            host = await super().open(request)
            opened.append(host)
            return host
    broker = RuntimeExecutionBroker(Provider())
    request = RuntimeExecutionRequest('agent', 'run', tmp_path / 'project', tmp_path / 'home')
    async def run():
        async with broker.open(request):
            pytest.fail('A cancelled opening was yielded to the runtime')
    task = asyncio.create_task(run())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(RuntimeUnavailableError, match='busy'):
        async with broker.open(request):
            pytest.fail('Concurrent acquisition was admitted')
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(opened) == 1 and opened[0].closed
    assert not broker.active


@pytest.mark.asyncio
async def test_close_terminates_an_active_noncooperative_child(tmp_path):
    host = await execution(tmp_path)
    process = await host.spawn([sys.executable, '-I', '-u', '-c',
        'import time;print("ready",flush=True);time.sleep(60)'], env={
            k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR'}})
    assert (await process.readline()).strip() == b'ready'
    await asyncio.wait_for(host.close(), 8)
    assert await process.wait() != 0


@pytest.mark.asyncio
async def test_failed_cleanup_keeps_ownership_until_confirmed(tmp_path):
    opened = []
    class Provider(NativeRuntimeExecutionProvider):
        async def open(self, request):
            host = await super().open(request)
            close = host.close
            attempts = 0
            async def retryable():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError('Termination unconfirmed')
                await close()
            host.close = retryable
            opened.append(host)
            return host
    broker = RuntimeExecutionBroker(Provider())
    request = RuntimeExecutionRequest('agent','run',tmp_path/'project',tmp_path/'home')
    with pytest.raises(RuntimeError, match='unconfirmed'):
        async with broker.open(request):
            pass
    assert 'agent' in broker.active
    with pytest.raises(RuntimeUnavailableError, match='busy'):
        async with broker.open(request):
            pytest.fail('Unconfirmed cleanup allowed a replacement')
    await broker.close_operation('run')
    assert opened[0].closed and not broker.active


def test_runtime_execution_is_bound_to_the_live_run(client, tmp_path):
    from dataclasses import replace
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.runs import InvocationCaller, InvocationContext, RunStore
    from backend.runs.manager import _current_invocation
    from backend.runs.models import RunStatus
    from backend.tests.conftest import create_node
    services = client.app.state.services
    agent = create_node(client, 'agent')
    provider = WorldAgentCapabilityProvider(services)
    run = RunStore(services.database).create(agent_id=agent['id'], run_id='runtime-execution',
        runtime_provider_id='core.mock', caller_kind='human')
    services.run_manager.store.update_status(run.run_id, RunStatus.RUNNING)
    context = InvocationContext(run.run_id, agent['id'], None, run.run_id,
        InvocationCaller('human'), None, None, 'core.mock')
    async def acquire(current):
        token = _current_invocation.set(current)
        try:
            async with provider.runtime_execution(agent['id'], run.run_id,
                    workspace=tmp_path/'runtime-project', home=tmp_path/'runtime-home') as host:
                return await host.write_file('result.txt', b'result')
        finally:
            _current_invocation.reset(token)
    path = client.portal.call(acquire, context)
    assert Path(path).read_bytes() == b'result'
    for invalid in (None, replace(context, agent_id='another-agent'), replace(context, run_id='another-run')):
        with pytest.raises(ResourceValidationError, match='current active agent run'):
            client.portal.call(acquire, invalid)
    services.run_manager.store.update_status(run.run_id, RunStatus.SUCCEEDED)
    with pytest.raises(ResourceValidationError, match='current active agent run'):
        client.portal.call(acquire, context)
    assert not services.runtime_execution.active


def test_async_resource_handler_receives_a_node_bound_execution(client, tmp_path):
    from pydantic import BaseModel
    from open_agent_world.plugin_api import NodeResourceAction, NodeTypeDefinition
    from backend.tests.plugin_support import install_test_plugin
    from backend.tests.conftest import create_node
    from backend.node_resources import ResourceActionRequest, invoke_resource_action
    services = client.app.state.services
    async def act(context, arguments):
        await asyncio.sleep(0)
        async with context.open_execution(workspace=tmp_path/'action-project', home=tmp_path/'action-home') as host:
            path = await host.write_file('result.txt', context.node_id.encode())
            return {'path':path}
    class ExecutionConfig(BaseModel):
        pass
    definition = NodeTypeDefinition(id='test.execution.card', label='Execution', description='test',
        icon='box', color='#123456', deck_id='test', deck_label='Test', deck_icon='box',
        default_name='Execution', default_size=(300,190), default_status='idle',
        statuses=frozenset({'idle'}), config_model=ExecutionConfig,
        resource_actions={'probe':NodeResourceAction(act)})
    install_test_plugin(services.plugins, 'test.execution', lambda registration: registration.register_node_type(definition))
    node = create_node(client, 'test.execution.card')
    async def invoke():
        return await invoke_resource_action(services, node['id'], 'probe', ResourceActionRequest())
    result = client.portal.call(invoke)
    assert Path(result['path']).read_text() == node['id']
    assert not services.runtime_execution.active


def test_execution_uses_live_approved_variables_without_host_credentials(client, tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from backend.execution_config import SecretRequirement
    from backend.security.environment_source import ExecutionEnvironmentSnapshot
    from backend.tests.conftest import create_node
    services = client.app.state.services
    services.settings = replace(services.settings, inherit_runtime_environment=False)
    monkeypatch.setenv('OAW_ALIYUN_SANDBOX_API_KEY', 'deployment-only-fixture')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'unapproved-process-fixture')
    approved = {'tool-key': 'approved-fixture'}
    services.execution_environment_source = SimpleNamespace(read=lambda:ExecutionEnvironmentSnapshot(
        {'DEEPSEEK_API_KEY':SecretRequirement(secret_ref='tool-key')}, approved))
    node = create_node(client, 'agent')
    configuration = services.runtime_execution_configuration(node['id'])
    assert configuration == {'mode':'native','configured':True}
    assert not services.runtime_execution.active
    async def snapshot():
        async with services.open_runtime_execution(node['id'],'test',workspace=tmp_path/'project',home=tmp_path/'home') as host:
            assert 'OAW_ALIYUN_SANDBOX_API_KEY' not in host.environment
            return host.environment['DEEPSEEK_API_KEY']
    assert client.portal.call(snapshot) == 'approved-fixture'
    approved['tool-key'] = 'rotated-fixture'
    assert client.portal.call(snapshot) == 'rotated-fixture'
