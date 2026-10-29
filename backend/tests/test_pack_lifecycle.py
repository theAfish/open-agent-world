"""Resource sharing, removal boundaries, process leases and source enrollment."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec, installation_lock
from backend.packs.installation import PackInstallationManager
from backend.packs.lifecycle import ManagedResourceLedger
from backend.packs.spec import EnvironmentSpec
from backend.tests.test_pack_source_ownership import source_registry
from backend.tests.test_pack_unification import unified


def test_identical_recipes_share_bytes_but_references_have_independent_lifetimes(tmp_path):
    manager = PackEnvironmentManager(tmp_path)
    first = PythonEnvironmentSpec('first', 'worker', verify_imports=('json',))
    second = replace(first, owner_id='second', environment_id='different-name')
    assert first.fingerprint == second.fingerprint
    handle = manager.prepare_python(first)
    assert manager.prepare_python(second).root == handle.root
    ledger = ManagedResourceLedger(tmp_path)
    resource = 'environment:' + first.fingerprint
    ledger.claim('selected:first', resource)
    ledger.claim('selected:second', resource)
    ledger.release_consumer('selected:first')
    assert ledger.collect() == []
    assert handle.root.is_dir()
    assert ledger.preview()[0]['action'] == 'retain'
    ledger.release_consumer('selected:second')
    with manager.lease(handle):
        assert ledger.preview()[0]['action'] == 'defer'
        assert ledger.collect() == []
    assert ledger.collect() == [{'id': resource, 'state': 'removed'}]
    assert not handle.root.exists()


def test_crashed_worker_lease_is_released_by_os_without_pid_heuristics(tmp_path):
    manager = PackEnvironmentManager(tmp_path)
    handle = manager.prepare_python(PythonEnvironmentSpec('fixture', 'worker'))
    script = '''
import sys,time
from pathlib import Path
from backend.packs.lifecycle import ManagedResourceLedger
with ManagedResourceLedger(Path(sys.argv[1])).lease(sys.argv[2]):
    print('leased',flush=True)
    time.sleep(60)
'''
    # Windows venv python.exe is a launcher; killing it alone can leave its real
    # interpreter alive, correctly retaining the lease. Launch the actual VM.
    process = subprocess.Popen([sys._base_executable, '-c', script, str(tmp_path), 'environment:' + handle.fingerprint],
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'leased'
        ledger = ManagedResourceLedger(tmp_path)
        assert ledger.collect() == []
    finally:
        process.kill()
        process.wait(timeout=10)
    assert ledger.collect()[0]['state'] == 'removed'


def test_preparation_lock_and_unknown_data_are_preserved(tmp_path):
    manager = PackEnvironmentManager(tmp_path)
    handle = manager.prepare_python(PythonEnvironmentSpec('fixture', 'worker'))
    data = tmp_path / 'runtime/user-data'
    data.mkdir()
    (data / 'valuable.txt').write_text('retain')
    ledger = ManagedResourceLedger(tmp_path)
    with pytest.raises(ValueError, match='outside'):
        ledger.register('environment:' + handle.fingerprint, data, 'environment')
    with installation_lock(manager.root / 'locks' / (handle.fingerprint + '.lock')):
        assert ledger.collect()[0]['state'] == 'deferred'
    ledger.collect()
    assert (data / 'valuable.txt').read_text() == 'retain'


def test_uninstall_releases_archive_dependencies_and_retained_activation_prepares_again(tmp_path):
    manager = PackInstallationManager(tmp_path)
    manager.install(unified(environment={'target': 'host-worker', 'prepare': 'install', 'verify_imports': ['json']}))
    resources = manager.uninstall_plan('test.greeter-release')['resources']
    assert {item['kind'] for item in resources} == {'environment', 'modules'}
    assert all(item['action'] == 'remove' for item in resources)
    module_path = manager.root / 'cache/modules' / next(item['id'].removeprefix('modules:') for item in resources if item['kind'] == 'modules')
    assert module_path.exists()
    manager.uninstall('test.greeter-release')
    assert list((tmp_path / 'runtime/environments').iterdir()) == []
    assert not module_path.exists()
    assert manager.version_path('test.greeter-release', '0.1.0').exists()
    manager.prepare_activation('test.greeter-release', '0.1.0')
    manager.activate('test.greeter-release', '0.1.0')
    assert manager.selected()['test.greeter-release'].version == '0.1.0'
    assert module_path.exists()


def test_source_uninstall_persists_enrollment_without_deleting_checkout(tmp_path):
    registry = source_registry(tmp_path)
    root = registry.pack_roots['source.owner']
    manager = PackInstallationManager(tmp_path / 'profile', registry)
    assert manager.uninstall_plan('source.release')['source'] == 'development'
    manager.uninstall('source.release')
    assert not PackInstallationManager(tmp_path / 'profile').source_enrolled('source.release')
    assert (root / 'pyproject.toml').is_file()
    assert manager.status()['restart_required']
    manager.restore_source('source.release')
    assert manager.source_enrolled('source.release')


def test_source_uninstall_keeps_shared_declared_environment(tmp_path):
    registry = source_registry(tmp_path)
    spec = registry.pack_specs['source.owner'].model_copy(update={'environments': {
        'worker': EnvironmentSpec(target='host-worker', prepare='install')}})
    registry.pack_specs['source.owner'] = spec
    root = tmp_path / 'profile'
    handle = PackEnvironmentManager(root).prepare_python(PythonEnvironmentSpec('source.owner', 'worker'))
    manager = PackInstallationManager(root, registry)
    manager.reconcile_resources()
    manager.install(unified(environment={'target': 'host-worker', 'prepare': 'install'}))
    plan = manager.uninstall_plan('test.greeter-release')
    assert plan['resources'][0]['action'] == 'retain'
    manager.uninstall('test.greeter-release')
    assert handle.root.exists()


def test_source_unenrollment_skips_factory_before_import_and_remains_visible(tmp_path, monkeypatch):
    from backend.plugins import loader
    from backend.packs.planning import inventory
    from backend.tests.test_pack_spec import source
    root = source(tmp_path)
    data = tmp_path / 'profile'
    manager = PackInstallationManager(data)
    with manager.connect(write=True) as db:
        db.execute('INSERT INTO source_states VALUES (?,0)', ('example.release',))
    monkeypatch.setattr(loader, 'entry_points', lambda: {})
    # The entry point deliberately names a nonexistent module. It must never be
    # imported when only its source declaration is retained after uninstall.
    registry = loader.load_plugin_registry(root.parent, data_root=data)
    assert not registry.has_plugin('example.runtime')
    row = next(item for item in inventory(PackInstallationManager(data, registry)) if item['id'] == 'example.release')
    assert row['enrolled'] is False and row['loaded'] is False


def test_prepared_request_survives_another_host_reconciliation_until_cancelled(tmp_path):
    manager = PackInstallationManager(tmp_path)
    data = unified(environment={'target': 'host-worker', 'prepare': 'install'})
    manager.prepare(data)
    other = PackInstallationManager(tmp_path)
    assert other.operations()[0]['state'] == 'prepared'
    assert other.reconcile_resources(collect=True) == []
    manager.interrupt_preparation(data)
    assert other.operations()[0]['state'] == 'failed'
    assert other.reconcile_resources(collect=True)[0]['state'] == 'removed'


def test_source_api_uninstall_restart_restore_preserves_saved_enabled_intent(tmp_path):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    # Codex is the source Pack being made optional by this migration. Keep the
    # complete uninstall/restart/restore contract on its explicit source path.
    settings = replace(Settings.for_data_root(tmp_path),
                       plugin_directories=(Path(__file__).resolve().parents[2] / 'packs/codex',))
    headers = {'X-OAW-Pack-Install': '1'}
    def row(client):
        return next(item for item in client.get('/api/packs').json()['inventory'] if item['id'] == 'theafish.codex')
    with TestClient(create_app(settings)) as client:
        preview = client.get('/api/packs/theafish.codex/uninstall-plan')
        assert preview.status_code == 200 and preview.json()['can_uninstall'], preview.text
        removed = client.delete('/api/packs/theafish.codex', headers=headers)
        assert removed.status_code == 200, removed.text
        assert row(client)['enrolled'] is False
    with TestClient(create_app(settings)) as client:
        assert row(client)['loaded'] is False
        restored = client.post('/api/packs/theafish.codex/restore', headers=headers)
        assert restored.status_code == 200, restored.text
        assert row(client)['enrolled'] is True and row(client)['loaded'] is False
    with TestClient(create_app(settings)) as client:
        assert row(client)['loaded'] is True and row(client)['enabled'] is True
