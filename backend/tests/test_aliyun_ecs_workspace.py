import base64
import json
from types import SimpleNamespace

import pytest

from backend.cloud.aliyun.ecs_workspace import EcsWorkspaceSandboxBackend, SnapshotStore, read_snapshot, remote_command
from backend.cloud.aliyun.sandbox import AliyunSandboxSettings
from backend.sandbox.models import SandboxStateError


def snapshot(name='results/result.txt', content=b'result'):
    return {'directories':['results'], 'files':{name:{'data':base64.b64encode(content).decode(), 'executable':False}}}


@pytest.mark.parametrize('name', ['../outside','/etc/passwd','C:/secret','a/../../outside','a\\b','CON','a.','a//b'])
def test_snapshot_rejects_unsafe_cloud_paths_without_changing_saved_data(tmp_path, name):
    store = SnapshotStore(tmp_path/'store')
    store.commit(snapshot())
    before = store.current()
    with pytest.raises(ValueError):
        store.commit(snapshot(name))
    assert store.current() == before
    assert (before/'results/result.txt').read_bytes() == b'result'


def test_invalid_cloud_snapshot_keeps_previous_generation(tmp_path):
    store = SnapshotStore(tmp_path/'store')
    store.commit(snapshot(content=b'first'))
    before = store.current()
    with pytest.raises(ValueError):
        store.commit({'directories':[], 'files':{'a':{'data':'not base64!', 'executable':False}}})
    assert store.current() == before
    store.commit(snapshot(content=b'second'))
    restored = SnapshotStore(tmp_path/'store')
    assert (restored.current()/'results/result.txt').read_bytes() == b'second'
    assert (before/'results/result.txt').read_bytes() == b'first'


@pytest.mark.parametrize('value', [
    {'directories':['A','a'], 'files':{}},
    {'directories':['a/b'], 'files':{'a':{'data':'', 'executable':False}}},
    {'directories':[f'd{i}' for i in range(2049)], 'files':{}},
])
def test_conflicting_or_excessive_entries_preserve_checkpoint(tmp_path, value):
    store = SnapshotStore(tmp_path/'store')
    store.commit(snapshot())
    before = store.current()
    with pytest.raises(ValueError):
        store.commit(value)
    assert store.current() == before


def test_cloud_file_size_limit_preserves_checkpoint(tmp_path, monkeypatch):
    from backend.cloud.aliyun import ecs_workspace
    store = SnapshotStore(tmp_path/'store')
    store.commit(snapshot())
    before = store.current()
    monkeypatch.setattr(ecs_workspace, 'MAX_BYTES', 8)
    with pytest.raises(ValueError, match='32 MiB'):
        store.commit(snapshot(content=b'123456789'))
    assert store.current() == before


def test_linked_workspace_files_cannot_cross_snapshot_boundary(tmp_path):
    import os
    (tmp_path/'original').write_bytes(b'private file')
    os.link(tmp_path/'original', tmp_path/'hardlink')
    with pytest.raises(ValueError, match='regular files'):
        read_snapshot(tmp_path)


class SnapshotSDK:
    def __init__(self, root):
        self.root, self.created, self.instances, self.killed = root, [], {}, []
        self.fail_collection = False
        self.fail_command = False
        self.business_calls = 0

    async def create(self, **kwargs):
        self.created.append(kwargs)
        identifier = 'remote-' + str(len(self.created))
        store = SnapshotStore(self.root/identifier)
        sdk = self

        class Remote:
            sandbox_id = identifier
            payload = None
            commands = None

            async def get_info(self):
                return SimpleNamespace(metadata=kwargs['metadata'])

            async def set_timeout(self, seconds): pass

            async def send_stdin(self, pid, payload):
                self.payload = payload

            async def run(self, command, **options):
                if command == remote_command('restore'):
                    assert options['user'] == 'root' and options['stdin']
                    remote = self
                    class Handle:
                        pid = 91
                        async def wait(self):
                            store.commit(json.loads(remote.payload))
                            return SimpleNamespace(exit_code=0,stdout='OAW_WORKSPACE_RESTORED\n',stderr='')
                    return Handle()
                if command == remote_command('snapshot'):
                    assert options['user'] == 'root'
                    if sdk.fail_collection:
                        raise ConnectionError('collection result lost')
                    return SimpleNamespace(exit_code=0,stdout=json.dumps(store.snapshot()),stderr='')
                assert options['user'] == 'user'
                sdk.business_calls += 1
                if sdk.fail_command:
                    raise ConnectionError('command result unknown')
                current = store.current()
                old = (current/'counter.txt').read_text() if (current/'counter.txt').exists() else '0'
                (current/'counter.txt').write_text(str(int(old)+1))
                class Command:
                    pid = 92
                    async def wait(self):
                        return SimpleNamespace(exit_code=0,stdout=str(int(old)+1),stderr='')
                return Command()
        remote = Remote()
        remote.commands = remote
        self.instances[identifier] = remote
        return remote

    async def connect(self, identifier, **kwargs):
        return self.instances[identifier]

    async def kill(self, identifier, **kwargs):
        self.killed.append(identifier)
        self.instances.pop(identifier)
        return True


def backend(root):
    sdk = SnapshotSDK(root/'remote')
    settings = AliyunSandboxSettings('cn-hangzhou','private-api-key','base','', 'profile-a', workspace_store='ecs')
    return EcsWorkspaceSandboxBackend(root/'profile',settings,sdk=sdk),sdk


@pytest.mark.asyncio
async def test_ecs_files_survive_cloud_vm_and_backend_recreation(tmp_path):
    worker,sdk = backend(tmp_path)
    await worker.create('sandbox')
    await worker.file_operation('sandbox','write',path='input.txt',data=base64.b64encode(b'input').decode())
    assert not sdk.created
    assert (await worker.execute('sandbox',['python3','count.py'])).stdout == '1'
    assert sdk.killed == ['remote-1']
    assert worker.store.snapshot()['files']['input.txt']['data'] == base64.b64encode(b'input').decode()
    restarted = EcsWorkspaceSandboxBackend(worker.path.parent,worker.settings,sdk=sdk)
    assert (await restarted.execute('sandbox',['python3','count.py'])).stdout == '2'
    assert sdk.killed == ['remote-1','remote-2']
    assert (await restarted.file_operation('sandbox','preview',path='counter.txt'))['text'] == '2'
    assert all(item['volume_mounts'] == {} and not item['allow_internet_access'] for item in sdk.created)
    assert 'private-api-key' not in worker.path.read_text()


@pytest.mark.asyncio
async def test_collection_failure_retains_ecs_snapshot_and_never_replays_command(tmp_path):
    worker,sdk = backend(tmp_path)
    await worker.create('sandbox')
    worker.store.commit(snapshot(content=b'original'))
    sdk.fail_collection = True
    with pytest.raises(SandboxStateError):
        await worker.execute('sandbox',['python3','count.py'])
    assert sdk.killed == []
    assert 'counter.txt' not in worker.store.snapshot()['files']
    with pytest.raises(SandboxStateError):
        await worker.execute('sandbox',['python3','count.py'])
    assert sdk.business_calls == 1
    sdk.fail_collection = False
    await worker.terminate('sandbox')
    assert sdk.killed == ['remote-1']
    assert (await worker.file_operation('sandbox','preview',path='counter.txt'))['text'] == '1'


@pytest.mark.asyncio
async def test_unknown_command_does_not_read_or_overwrite_local_workspace(tmp_path):
    worker,sdk = backend(tmp_path)
    await worker.create('sandbox')
    sdk.fail_command = True
    with pytest.raises(SandboxStateError):
        await worker.execute('sandbox',['python3','count.py'])
    with pytest.raises(SandboxStateError):
        await worker.file_operation('sandbox','write',path='input',data='')
    assert sdk.business_calls == 1 and not sdk.killed
