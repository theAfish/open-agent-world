import asyncio
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.sandbox import relocation
from backend.sandbox.settings import SandboxSettings, SandboxSettingsStore
from backend.services import create_services
from backend.tests.test_sandbox_manager import make_manager
from backend.world.models import CardCreate


@pytest.fixture
def relocation_client(tmp_path):
    manager, runtime = make_manager(tmp_path)
    services = create_services(Settings.for_data_root(manager.root), sandbox_backend=manager)
    with TestClient(create_app(services.settings, services=services)) as client:
        yield client, services, manager, runtime
    services.close()


def create(client):
    response = client.post('/api/nodes', json={'type': 'sandbox'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def location(client, path):
    return client.put('/api/settings/sandbox', json={'workspace_root': str(path) if path else None})


def test_global_environment_save_does_not_migrate_running_custom_workspace(relocation_client, tmp_path):
    client, services, manager, runtime = relocation_client
    root = tmp_path / 'default'
    root.mkdir()
    assert location(client, root).status_code == 200
    node = create(client)
    custom = tmp_path / 'custom'
    custom.mkdir()
    response = client.patch(f'/api/nodes/{node}', json={'config': {'workspace_path': str(custom)}})
    assert response.status_code == 200, response.text
    assert client.post(f'/api/sandboxes/{node}/start').status_code == 200
    response = client.put('/api/settings/sandbox', json={'workspace_root': str(root), 'environment_variables': {'REGION': 'global'}})
    assert response.status_code == 200, response.text
    assert services.world.get_card(node).config['workspace_path'] == str(custom)


def test_provisioned_managed_files_migrate_and_binding_survives_reload(relocation_client, tmp_path):
    client, services, manager, runtime = relocation_client
    node = create(client)
    assert client.post(f'/api/sandboxes/{node}/start').status_code == 200
    assert client.post(f'/api/sandboxes/{node}/stop').status_code == 200
    source = runtime.root / node
    (source / '.hidden').mkdir(parents=True)
    (source / '.hidden/data.bin').write_bytes(bytes(range(256)))
    (source / 'empty').mkdir()
    # Names reserved by application-storage relocation are ordinary workspace data.
    (source / '.oaw-migration.json').write_text('user document')
    destination = tmp_path / 'destination'
    destination.mkdir()
    response = location(client, destination)
    assert response.status_code == 200, response.text
    target = destination / node
    assert response.json()['backup_paths'] == [str(source)]
    assert client.get('/api/settings/sandbox').json()['backup_paths'] == [str(source)]
    # A fresh store reads the durable receipt; an unchanged save does not lose it.
    assert SandboxSettingsStore(services.database, services.settings.data_root).public().backup_paths == [str(source)]
    assert location(client, destination).json()['backup_paths'] == [str(source)]
    assert (target / '.hidden/data.bin').read_bytes() == bytes(range(256))
    assert (target / 'empty').is_dir()
    assert (target / '.oaw-migration.json').read_text() == 'user document'
    assert (source / '.hidden/data.bin').exists()
    assert runtime.records[node].workspace == target
    from backend.sandbox.manager import SandboxManager
    restored = SandboxManager(manager.root, manager.registry)
    assert client.portal.call(restored.get, node).workspace_path == str(target)


def test_saving_already_selected_root_backfills_old_sandboxes(relocation_client, tmp_path):
    client, services, _, _ = relocation_client
    node = create(client)
    destination = tmp_path / 'selected'
    destination.mkdir()
    SandboxSettingsStore(services.database, services.settings.data_root).save(SandboxSettings(workspace_root=str(destination)))
    response = location(client, destination)
    assert response.status_code == 200, response.text
    assert client.get(f'/api/nodes/{node}').json()['config']['workspace_path'] == str(destination / node)
    assert (destination / node).is_dir()


def test_ready_sandbox_blocks_save_before_any_copy(relocation_client, tmp_path):
    client, services, _, _ = relocation_client
    node = create(client)
    client.post(f'/api/sandboxes/{node}/start')
    destination = tmp_path / 'busy-destination'
    destination.mkdir()
    response = location(client, destination)
    assert response.status_code == 409, response.text
    assert 'Stop Sandbox' in response.text
    assert list(destination.iterdir()) == []
    assert client.get('/api/settings/sandbox').json()['workspace_root'] is None


def test_conflicting_destination_keeps_sources_settings_and_bindings(relocation_client, tmp_path):
    client, services, _, _ = relocation_client
    node = create(client)
    destination = tmp_path / 'conflict'
    target = destination / node
    target.mkdir(parents=True)
    (target / 'important.txt').write_text('do not overwrite')
    response = location(client, destination)
    assert response.status_code == 409, response.text
    assert (target / 'important.txt').read_text() == 'do not overwrite'
    assert services.world.get_card(node).config['workspace_path'] is None
    assert client.get('/api/settings/sandbox').json()['workspace_root'] is None


def test_binding_failure_rolls_back_batch_and_verified_copy_can_be_retried(relocation_client, tmp_path, monkeypatch):
    client, services, manager, _ = relocation_client
    first, second = create(client), create(client)
    destination = tmp_path / 'rollback'
    destination.mkdir()
    original = manager.configure_options
    calls = []

    async def fail_second(node, config):
        if config.get('workspace_path'):
            calls.append(node)
            if len(calls) == 2:
                from backend.sandbox.models import SandboxValidationError
                raise SandboxValidationError('injected binding failure')
        return await original(node, config)

    monkeypatch.setattr(manager, 'configure_options', fail_second)
    response = location(client, destination)
    assert response.status_code == 422, response.text
    for node in (first, second):
        assert services.world.get_card(node).config['workspace_path'] is None
        assert client.portal.call(manager.get, node).workspace_path is None
    assert client.get('/api/settings/sandbox').json()['workspace_root'] is None
    monkeypatch.setattr(manager, 'configure_options', original)
    assert location(client, destination).status_code == 200


def test_settings_commit_failure_rolls_back_cards_and_runtime(relocation_client, tmp_path, monkeypatch):
    client, services, manager, _ = relocation_client
    node = create(client)
    destination = tmp_path / 'db-failure'
    destination.mkdir()
    SandboxSettingsStore(services.database, services.settings.data_root).record_backups(['previous-backup'])

    def fail_save(*args):
        raise OSError('injected persistence failure')

    monkeypatch.setattr(SandboxSettingsStore, 'save', fail_save)
    response = location(client, destination)
    assert response.status_code == 422, response.text
    assert services.world.get_card(node).config['workspace_path'] is None
    assert client.portal.call(manager.get, node).workspace_path is None
    assert client.get('/api/settings/sandbox').json()['workspace_root'] is None
    assert client.get('/api/settings/sandbox').json()['backup_paths'] == ['previous-backup']


def test_backup_receipt_is_read_only(relocation_client):
    client, _, _, _ = relocation_client
    response = client.put('/api/settings/sandbox', json={
        'workspace_root': None, 'runtime': 'auto', 'backup_paths': ['invented-path']})
    assert response.status_code == 422
    assert client.get('/api/settings/sandbox').json()['backup_paths'] == []


def test_copy_verification_detects_concurrent_source_changes(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir()
    (source / 'data').write_text('before')
    original = relocation._copy

    def mutate(source, stage, **kwargs):
        original(source, stage, **kwargs)
        (source / 'data').write_text('after')

    monkeypatch.setattr(relocation, '_copy', mutate)
    from backend.errors import ConflictError
    with pytest.raises(ConflictError, match='changed during migration'):
        relocation._copy_workspace(source, target)
    assert not target.exists()
    assert (source / 'data').read_text() == 'after'


def test_nested_destination_is_rejected_without_copying(tmp_path):
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'data').write_text('keep')
    from backend.errors import ResourceValidationError
    with pytest.raises(ResourceValidationError, match='separate'):
        relocation._copy_workspace(source, source / 'nested')
    assert sorted(p.name for p in source.iterdir()) == ['data']


def test_copy_failure_keeps_default_and_original_files(relocation_client, tmp_path, monkeypatch):
    client, services, _, _ = relocation_client
    source = tmp_path / 'original'
    source.mkdir()
    (source / 'data').write_text('keep')
    node = client.post('/api/nodes', json={'type': 'sandbox', 'config': {'workspace_path': str(source)}}).json()['id']
    destination = tmp_path / 'no-space'
    destination.mkdir()

    def full_disk(*args, **kwargs):
        raise OSError('not enough space')

    monkeypatch.setattr(relocation, '_copy', full_disk)
    response = location(client, destination)
    assert response.status_code == 422, response.text
    assert (source / 'data').read_text() == 'keep'
    assert services.world.get_card(node).config['workspace_path'] == str(source)
    assert client.get('/api/settings/sandbox').json()['workspace_root'] is None


def test_read_only_workspace_preserves_access_on_migration(relocation_client, tmp_path):
    client, services, _, _ = relocation_client
    source = tmp_path / 'read-only'
    source.mkdir()
    (source / 'data').write_text('keep')
    node = client.post('/api/nodes', json={'type': 'sandbox', 'config': {
        'workspace_path': str(source), 'workspace_access': 'read_only'}}).json()['id']
    destination = tmp_path / 'read-only-destination'
    destination.mkdir()
    assert location(client, destination).status_code == 200
    assert services.world.get_card(node).config['workspace_access'] == 'read_only'
    assert (destination / node / 'data').read_text() == 'keep'
    assert location(client, None).status_code == 422
    assert services.world.get_card(node).config['workspace_access'] == 'read_only'


@pytest.mark.asyncio
async def test_native_runtime_managed_paths_are_host_paths(tmp_path, monkeypatch):
    import hashlib
    from backend.sandbox.linux import LinuxSandboxBackend
    from backend.sandbox.windows import WindowsSandboxBackend
    from backend.sandbox.wsl import WslSandboxBackend
    from backend.tests.test_sandbox_runtime import FakeWindowsNativeApi
    native = FakeWindowsNativeApi()
    monkeypatch.setattr(native, 'validate_workspace_volume', lambda path: None, raising=False)
    windows = WindowsSandboxBackend(tmp_path / 'windows', native_api=native)
    linux = LinuxSandboxBackend(tmp_path / 'linux')
    external = tmp_path / 'external-path'
    external.mkdir()
    from backend.sandbox.models import ResourceAccess
    for runtime in (windows, linux):
        await runtime.create('probe')
        managed = (tmp_path / 'windows/sandboxes/probe/workspace' if runtime is windows else
                   tmp_path / 'linux/sandbox-runtimes' / hashlib.sha256(b'linux').hexdigest()[:16] / 'sandboxes/probe/workspace')
        await runtime.configure('probe', workspace_path=str(external), workspace_access=ResourceAccess.READ_WRITE)
        assert await runtime.managed_workspace('probe') == managed
    wsl = WslSandboxBackend(tmp_path / 'wsl', distribution='Ubuntu')

    async def get(_):
        return None

    monkeypatch.setattr(wsl, 'get', get)
    path = await wsl.managed_workspace('probe')
    assert path == tmp_path / 'wsl' / 'sandbox-runtimes' / hashlib.sha256(b'wsl:Ubuntu').hexdigest()[:16] / 'sandboxes/probe/workspace'


def test_clear_default_restores_unprovisioned_workspace_files(relocation_client, tmp_path):
    client, services, manager, runtime = relocation_client
    destination = tmp_path / 'external'
    destination.mkdir()
    assert location(client, destination).status_code == 200
    node = create(client)
    source = Path(services.world.get_card(node).config['workspace_path'])
    (source / 'keep.txt').write_text('never started')
    response = location(client, None)
    assert response.status_code == 200, response.text
    assert services.world.get_card(node).config['workspace_path'] is None
    assert (runtime.root / node / 'keep.txt').read_text() == 'never started'
    assert client.post(f'/api/sandboxes/{node}/start').status_code == 200
    assert (runtime.records[node].workspace / 'keep.txt').read_text() == 'never started'


@pytest.mark.asyncio
async def test_disconnect_keeps_migration_locked_until_worker_finishes(tmp_path, monkeypatch):
    manager, _ = make_manager(tmp_path)
    services = create_services(Settings.for_data_root(manager.root), sandbox_backend=manager)
    entered, release = threading.Event(), threading.Event()
    destination = tmp_path / 'disconnect'
    destination.mkdir()
    original = relocation._copy_workspace

    def delayed(source, target):
        entered.set()
        assert release.wait(10)
        original(source, target)

    monkeypatch.setattr(relocation, '_copy_workspace', delayed)
    try:
        card = await services.create_card(CardCreate(type='sandbox'))
        task = asyncio.create_task(services._complete_committed(relocation.save_settings(
            services, SandboxSettings(workspace_root=str(destination)))))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        create_task = asyncio.create_task(services.create_card(CardCreate(type='sandbox')))
        await asyncio.sleep(0)
        assert not create_task.done()
        release.set()
        await asyncio.wait_for(task, 10)
        fresh = await asyncio.wait_for(create_task, 5)
        assert services.world.get_card(card.id).config['workspace_path'] == str(destination / card.id)
        assert Path(fresh.config['workspace_path']).parent == destination
    finally:
        release.set()
        services.close()


@pytest.mark.asyncio
async def test_agent_admission_is_blocked_during_workspace_maintenance(tmp_path):
    manager, _ = make_manager(tmp_path)
    services = create_services(Settings.for_data_root(manager.root), sandbox_backend=manager)
    try:
        run_manager = services.run_manager
        async with run_manager.workspace_maintenance(['agent']):
            lock = run_manager._start_locks['agent']
            assert lock.locked()
        assert not lock.locked()
        run_manager._occupied_runs['active'] = 'agent'
        from backend.errors import ConflictError
        with pytest.raises(ConflictError, match='Stop running Agents'):
            async with run_manager.workspace_maintenance(['agent']):
                pytest.fail('must reject active execution')
        assert not lock.locked()
    finally:
        services.close()
