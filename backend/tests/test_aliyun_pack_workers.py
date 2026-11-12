import copy
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from backend.cloud.aliyun import pack_workers
from backend.cloud.aliyun.pack_workers import AliyunPackWorkerExecutionProvider
from backend.cloud.aliyun.sandbox import AliyunSandboxSettings
from backend.errors import RuntimeUnavailableError
from backend.packs.workers import PackWorkerExecutionRequest, PackWorkerHost, PackWorkerSpec
from backend.tests.test_aliyun_execution import DuplexSDK
from backend.tests.test_aliyun_workspaces import operator_file_metadata


PROFILE = '7aa842fc-22ec-4163-8d9e-211f5c86eb42'


def assignment():
    return {'version': 1, 'profile_id': PROFILE, 'volume': 'oaw-' + PROFILE,
            'mount_path': '/mnt/oaw-workspaces/' + PROFILE, 'verified': True,
            'deployments': [{'pack_id': 'example.science', 'pack_version': '1.0.0',
                'environment_id': 'science', 'template': 'reviewed-science-v1',
                'commands': {'worker': ['/usr/bin/python3', '-I', '-u', '-c',
                    'import os,sys; from pathlib import Path; '
                    'assert "HOST_SECRET" not in os.environ; '
                    'assert os.environ["FIXED"] == "recipe"; '
                    'assert os.environ["TOOL_KEY"] == "approved"; '
                    'Path("result.txt").write_text(Path("input.txt").read_text().upper()); '
                    'print("ready", flush=True); print(sys.stdin.readline().strip().upper(), flush=True); '
                    'print("diagnostic", file=sys.stderr)']},
                'environment': {'FIXED': 'recipe'}}]}


def settings():
    return AliyunSandboxSettings('cn-hangzhou', 'private-api-key', 'cli-template', 'oaw-' + PROFILE, PROFILE)


def spec():
    def forbidden(environment):
        raise AssertionError('Cloud executor must never prepare a native environment')
    return PackWorkerSpec('example.science', 'science', 'worker', (), forbidden, pack_version='1.0.0')


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    """Model the mount/SDK locally; all worker and relay processes are real."""
    sdk = DuplexSDK(tmp_path, shared_workspace=True)
    provider = AliyunPackWorkerExecutionProvider(tmp_path / 'receipts', settings(), assignment(), sdk=sdk)
    provider.mount = tmp_path / 'shared'
    provider.workspace_root = provider.mount / 'packs'
    workspace = provider.workspace_root / 'example.science' / 'job'
    workspace.mkdir(parents=True)
    (workspace / 'input.txt').write_text('diffraction')
    monkeypatch.setattr(provider, '_mounted', lambda: True)
    initialize = pack_workers._DeploymentExecution._initialize_execution

    async def local_interpreter(self, execution, request, key):
        # The fake VM has the current interpreter in place of its template Python.
        self.command = (sys.executable, *self.command[1:])
        await initialize(self, execution, request, key)
    monkeypatch.setattr(pack_workers._DeploymentExecution, '_initialize_execution', local_interpreter)
    return provider, sdk, workspace


def request(workspace, worker=None):
    return PackWorkerExecutionRequest('example.science:job', 'job', workspace, workspace,
        {'HOST_SECRET': 'must-not-leak'}, {'FIXED': 'untrusted override', 'TOOL_KEY': 'approved'},
        'example.science', worker=worker or spec())


@pytest.mark.asyncio
async def test_shared_worker_interactive_io_results_and_confirmed_vm_cleanup(mounted):
    provider, sdk, workspace = mounted
    host = PackWorkerHost()
    host.configure(provider, inherit_environment=False,
        environment_variables=lambda: {'TOOL_KEY': 'approved', 'FIXED': 'override'})
    worker = await host.start(spec(), operation_id='job', workspace=workspace, interactive=True)
    try:
        assert (await worker.readline()).strip() == b'ready'
        await worker.write(b'continue\n')
        assert (await worker.readline()).strip() == b'CONTINUE'
        assert await worker.wait() == 0
    finally:
        await worker.close()
    assert (workspace / 'result.txt').read_text() == 'DIFFRACTION'
    assert 'diagnostic' in (workspace / 'console.log').read_text()
    assert not list(workspace.glob('.oaw-worker-*'))
    assert sdk.created[0]['volume_mounts'] == {'/mnt/oaw-workspaces/' + PROFILE: 'oaw-' + PROFILE}
    assert sdk.created[0]['template'] == 'reviewed-science-v1'
    assert sdk.commands[0][1]['user'] == 'user'
    assert not sdk.instances and len(sdk.killed) == 1
    assert not host.broker.active and not host.workers


@pytest.mark.parametrize('change', ['version', 'worker', 'environment', 'other-pack', 'missing-mount'])
@pytest.mark.asyncio
async def test_rejects_incompatible_or_unmounted_work_before_allocation(mounted, change, monkeypatch):
    provider, sdk, workspace = mounted
    worker = spec()
    if change == 'version':
        worker = replace(worker, pack_version='2.0.0')
    elif change == 'worker':
        worker = replace(worker, worker_id='unreviewed')
    elif change == 'environment':
        worker = replace(worker, environment_id='unreviewed')
    elif change == 'other-pack':
        workspace = workspace.parent.parent / 'someone-else'
        workspace.mkdir()
    else:
        monkeypatch.setattr(provider, '_mounted', lambda: False)
    with pytest.raises(RuntimeUnavailableError):
        await provider.open(request(workspace, worker))
    assert not sdk.created


@pytest.mark.asyncio
async def test_missing_shared_files_kills_vm_before_worker_launch(mounted):
    provider, sdk, workspace = mounted
    sdk.shared_workspace = False
    with pytest.raises((FileNotFoundError, RuntimeUnavailableError)):
        await provider.open(request(workspace))
    assert not sdk.instances and len(sdk.killed) == 1
    assert not (workspace / 'result.txt').exists()
    assert not list(workspace.glob('.oaw-worker-*'))


@pytest.mark.asyncio
async def test_host_file_permissions_are_checked_before_vm_allocation(mounted, monkeypatch):
    from contextlib import contextmanager
    from backend.sandbox import files
    provider, sdk, workspace = mounted
    @contextmanager
    def denied(*args, **kwargs):
        raise PermissionError('Ancestor cannot be opened by the diagnostic identity')
        yield
    monkeypatch.setattr(files, 'pinned', denied)
    with pytest.raises(PermissionError, match='Ancestor'):
        await provider.open(request(workspace))
    assert not sdk.created


@pytest.mark.asyncio
async def test_failed_cleanup_retains_receipt_and_can_be_retried(mounted):
    provider, sdk, workspace = mounted
    execution = await provider.open(request(workspace))
    sdk.fail_kill = True
    try:
        with pytest.raises(Exception, match='could not be confirmed'):
            await execution.close()
        assert sdk.instances
        assert list(provider.root.rglob('aliyun-sandbox.json'))
    finally:
        sdk.fail_kill = False
        await execution.close()
    assert not sdk.instances


@pytest.mark.asyncio
async def test_output_visibility_is_checked_before_worker_launch(mounted, monkeypatch):
    from backend.cloud.aliyun.execution import AliyunRuntimeExecution
    provider, sdk, workspace = mounted
    async def lost_write(self, *args, **kwargs):
        return 'acknowledged-without-visible-write'
    monkeypatch.setattr(AliyunRuntimeExecution, 'write_file', lost_write)
    with pytest.raises(RuntimeUnavailableError, match='output is not visible'):
        await provider.open(request(workspace))
    assert not sdk.instances and not (workspace / 'result.txt').exists()


@pytest.mark.parametrize('change', ['profile', 'volume', 'mount', 'unverified', 'relative-command', 'duplicate'])
def test_assignment_rejects_wrong_identity_and_unreviewed_launches(tmp_path, change):
    value = assignment()
    if change in {'profile', 'volume', 'mount'}:
        value[{'profile': 'profile_id', 'volume': 'volume', 'mount': 'mount_path'}[change]] = 'another-profile'
    elif change == 'unverified':
        value['verified'] = False
    elif change == 'relative-command':
        value['deployments'][0]['commands']['worker'] = ['python']
    else:
        value['deployments'].append(copy.deepcopy(value['deployments'][0]))
    with pytest.raises(ValueError):
        AliyunPackWorkerExecutionProvider(tmp_path, settings(), value)


def test_discovery_never_allocates_or_accepts_an_ordinary_directory(tmp_path):
    sdk = DuplexSDK(tmp_path)
    provider = AliyunPackWorkerExecutionProvider(tmp_path, settings(), assignment(), sdk=sdk)
    provider.mount = tmp_path
    assert provider.configuration('example.science')['configured'] is False
    assert not sdk.created


@pytest.mark.asyncio
async def test_approved_probe_uses_two_vms_and_keeps_unrelated_files(mounted):
    from deploy.aliyun import pack_worker_probe as probe
    provider, sdk, _ = mounted
    deployment = probe.assignment(provider.settings)['deployments'][0]
    provider.deployments = {(probe.PACK, '1.0.0', 'stdlib'): deployment}
    unrelated = provider.mount / 'unrelated.txt'
    unrelated.write_text('preserve')
    result = await probe.run_probe(provider)
    assert result == {'sessions': 2, 'persistent_file': True, 'interactive_io': True, 'cleanup_confirmed': True}
    assert len(sdk.created) == len(sdk.killed) == 2 and not sdk.instances
    assert unrelated.read_text() == 'preserve'
    assert not list((provider.workspace_root / probe.PACK).iterdir())


def test_operator_assignment_is_explicit_and_null_is_not_missing(tmp_path, operator_file_metadata):
    import json
    assert pack_workers.load_worker_provider(tmp_path / 'data', settings()) is None
    path = tmp_path / 'aliyun-pack-workers.json'
    path.write_text(json.dumps(assignment()))
    path.chmod(0o600)
    provider = pack_workers.load_worker_provider(tmp_path / 'data', settings())
    assert provider.settings.volume_mount_path == '/mnt/oaw-workspaces/' + PROFILE
    assert provider.workspace_root == Path('/mnt/oaw-workspaces') / PROFILE / 'packs'
    path.write_text('null')
    with pytest.raises(ValueError, match='JSON object'):
        pack_workers.load_worker_provider(tmp_path / 'data', settings())


@pytest.mark.parametrize('path', ['/', '/mnt', '/etc', '//workspace', '/workspace/../etc', '/mnt//private', '/mnt/x\x00'])
def test_volume_mount_rejects_noncanonical_or_system_paths(path):
    with pytest.raises(ValueError, match='canonical path'):
        replace(settings(), volume_mount_path=path)


@pytest.mark.asyncio
async def test_mount_path_is_part_of_durable_sandbox_identity(tmp_path):
    from backend.cloud.aliyun.sandbox import AliyunSandboxBackend
    from backend.sandbox.models import SandboxSecurityError
    sdk = DuplexSDK(tmp_path)
    backend = AliyunSandboxBackend(tmp_path, settings(), sdk=sdk)
    await backend.create('bound')
    # Old default receipts stay compatible; changing a mount needs reconciliation.
    assert 'volume_mount_path' not in backend._read('bound')['configuration']
    changed = AliyunSandboxBackend(tmp_path, replace(settings(), volume_mount_path='/mnt/new'), sdk=sdk)
    with pytest.raises(SandboxSecurityError, match='binding differs'):
        changed._read('bound')
    assert not sdk.created
