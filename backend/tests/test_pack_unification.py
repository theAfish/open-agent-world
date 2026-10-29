"""Distribution/owner identity and preparation transaction integration."""
from copy import deepcopy
import hashlib
import json

import pytest

from backend.packs.archive import inspect_archive
from backend.packs.environments import installation_lock
from backend.packs.installation import PackInstallationManager
from backend.packs.planning import environment_status, inventory, manifest_spec
from backend.packs.manifest import PackManifest
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.loader import load_installed_packs
from backend.tests.test_pack_installation import MANIFEST, artifact, isolate_modules, zipped
from backend.tests.test_store import store


def unified(version='0.1.0', *, environment=None, script=None, owner='test.runtime'):
    manifest = deepcopy(MANIFEST)
    manifest.update(schema_version=3, id='test.greeter-release', owner=owner,
                    packs=[owner + '.cards', owner + '.tools'],
                    backend_modules=['oaw_pack_test_schema'], version=version)
    if environment is not None:
        manifest['environments'] = {'worker': environment}
    module, prefix = 'oaw_pack_test_schema', f'oaw_pack_test_schema-{version}.dist-info'
    source = f'''
from open_agent_world.plugin_api import PluginDefinition, PluginDescriptor, PackDefinition, NodeTypeDefinition
from pydantic import BaseModel
class Config(BaseModel):
    pass
def register(registry):
    for name in ('cards', 'tools'):
        node = 'test.runtime.' + name
        registry.register_node_type(NodeTypeDefinition(id=node, label=name, description='Test', icon='sparkles', color='#123456', deck_id='test', deck_label='Test', deck_icon='sparkles', default_name=name, default_size=(300,200), default_status='idle', statuses=frozenset({{'idle'}}), config_model=Config, frontend={{'body':'greeting'}}))
        registry.register_pack(PackDefinition(id=node, name=name, cards=(node,)))
def create_plugin():
    return PluginDefinition(PluginDescriptor(id='test.runtime', version='{version}', plugin_api_version='1.23'), register)
'''.replace('test.runtime', owner)
    wheel = zipped({f'{module}/__init__.py': source,
        f'{prefix}/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        f'{prefix}/METADATA': f'Metadata-Version: 2.1\nName: {module}\nVersion: {version}\n',
        f'{prefix}/entry_points.txt': f'[open_agent_world.plugins]\n{owner} = {module}:create_plugin\n'})
    files = {'manifest.json': json.dumps(manifest).encode(), 'backend/runtime.whl': wheel,
             'frontend/index.js': b'export default {apiVersion:1,views:{}}'}
    if script:
        files['runtime/verify.py'] = script.encode()
    return artifact(files=files)


def test_schema3_install_restart_owner_frontend_and_inventory(tmp_path):
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    data = unified(environment={'target': 'host-worker', 'prepare': 'install', 'verify_imports': ['json']})
    assert manager.plan(data)['blockers'] == []
    manager.install(data)
    assert manager.operations()[0]['state'] == 'completed'
    load_installed_packs(registry, tmp_path)
    assert registry.has_plugin('test.runtime')
    assert registry.owner_id('pack', 'test.runtime.tools') == 'test.runtime'
    assert registry.frontend_modules['test.runtime']['distribution_id'] == 'test.greeter-release'
    assert manager.frontend_asset('test.greeter-release', '0.1.0', 'frontend/index.js').is_file()
    row = next(row for row in inventory(manager) if row['owner'] == 'test.runtime')
    assert row['source'] == 'installed' and row['environments'][0]['state'] == 'ready'
    assert row['id'] == 'test.greeter-release'


def test_schema3_presentation_cannot_advertise_an_unprovided_pack():
    files = dict(inspect_archive(unified()).files)
    files.pop('checksums.json')
    manifest = json.loads(files['manifest.json'])
    manifest['presentation'] = {'id': 'other.publisher.pack', 'name': 'Other Pack', 'packaging': 'premium'}
    files['manifest.json'] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match='provided Schema 3 Pack'):
        inspect_archive(artifact(files=files))


def test_failed_worker_preparation_preserves_previous_selection(tmp_path):
    manager = PackInstallationManager(tmp_path)
    manager.install(unified())
    broken = unified('0.2.0', environment={'target': 'host-worker', 'prepare': 'install',
        'verify_script': 'runtime/verify.py'}, script="raise RuntimeError('failed verification')")
    with pytest.raises(Exception):
        manager.install(broken)
    assert manager.selected()['test.greeter-release'].version == '0.1.0'
    assert not manager.version_path('test.greeter-release', '0.2.0').exists()
    assert manager.operations()[0]['state'] == 'failed'


def test_expected_store_identity_is_checked_before_preparation(tmp_path, monkeypatch):
    manager = PackInstallationManager(tmp_path)
    def unexpected(*args):
        pytest.fail('no preparation may run before expected identity validation')
    monkeypatch.setattr(manager, '_prepare', unexpected)
    with pytest.raises(ValueError, match='identity differs'):
        manager.prepare(unified(), expected_id='other.pack')
    assert manager.operations() == []
    assert not (tmp_path / 'runtime').exists()


def test_operation_recovery_preserves_active_lock_and_reuses_retry_identity(tmp_path):
    manager = PackInstallationManager(tmp_path)
    data = unified()
    pack = inspect_archive(data)
    operation_id = manager._operation(pack, 'preparing')
    with installation_lock(manager._operation_lock(pack.digest)):
        active = PackInstallationManager(tmp_path)
        assert active.operations()[0]['state'] == 'preparing'
    recovered = PackInstallationManager(tmp_path)
    assert recovered.operations()[0]['state'] == 'failed'
    recovered.install(data)
    assert recovered.operations()[0]['id'] == operation_id
    assert recovered.operations()[0]['state'] == 'completed'


def test_recovery_releases_publication_left_before_sqlite_commit(tmp_path):
    manager = PackInstallationManager(tmp_path)
    data = unified()
    pack = inspect_archive(data)
    manager._operation(pack, 'selecting')
    target = manager.version_path(pack.manifest.id, pack.manifest.version)
    target.mkdir(parents=True)
    (target / 'manifest.json').write_bytes(pack.files['manifest.json'])
    recovered = PackInstallationManager(tmp_path)
    assert not target.exists()
    assert list((manager.root / 'staging').glob('interrupted-*/manifest.json'))
    recovered.install(data)
    assert recovered.selected()[pack.manifest.id].version == '0.1.0'


def test_explicit_legacy_profile_cannot_escape_legacy_bootstrap_contract():
    with pytest.raises(ValueError, match='must equal runtime'):
        inspect_archive(unified(environment={'target': 'sandbox', 'management': 'legacy-shared', 'python': ['numpy>=2']}))


def test_legacy_adapter_preserves_explicit_environment_with_same_name():
    manifest = inspect_archive(unified()).manifest.model_dump(mode='json')
    manifest['runtime']['sandbox']['python'] = ['numpy>=2']
    manifest['environments']['legacy-sandbox'] = {'target': 'host-worker', 'python': ['requests==2.32.0']}
    spec = manifest_spec(PackManifest.model_validate(manifest))
    assert spec.environments['legacy-sandbox'].target == 'host-worker'
    assert spec.environments['legacy-sandbox-legacy'].python == ('numpy>=2',)


def test_distribution_owner_is_stable_even_after_version_removal(tmp_path):
    manager = PackInstallationManager(tmp_path)
    manager.install(unified())
    with pytest.raises(ValueError, match='preserve its runtime owner'):
        manager.install(unified('0.2.0', owner='test.next'))
    manager.uninstall('test.greeter-release')
    manager.remove_version('test.greeter-release', '0.1.0')
    with pytest.raises(ValueError, match='preserve its runtime owner'):
        PackInstallationManager(tmp_path).install(unified('0.2.0', owner='test.next'))


@pytest.mark.parametrize('field', ['entrypoint', 'lock', 'verify_script'])
def test_every_declared_runtime_file_must_exist(field):
    environment = {'target': 'host-worker', field: 'runtime/missing.py'}
    with pytest.raises(ValueError, match='runtime resource'):
        inspect_archive(unified(environment=environment))


def test_declared_artifact_digest_is_verified_independently():
    with pytest.raises(ValueError, match='artifact digest'):
        inspect_archive(unified(environment={'target': 'host-worker', 'resources': ['runtime/verify.py'],
            'artifact_hashes': {'runtime/verify.py': hashlib.sha256(b'other').hexdigest()}}, script='pass'))


@pytest.mark.parametrize('declaration', ['lock', 'executable'])
def test_runtime_declaration_cannot_override_verified_file_checksum(declaration):
    environment = {'target': 'host-worker'}
    if declaration == 'lock':
        environment.update(lock='runtime/verify.py', lock_digest='0' * 64)
    else:
        environment.update(provider='executable', artifacts=[{
            'platform': 'linux-x86_64', 'path': 'runtime/verify.py',
            'sha256': '0' * 64, 'executable': 'verify.py',
        }])
    with pytest.raises(ValueError, match='digest|checksum'):
        inspect_archive(unified(environment=environment, script='pass'))


def test_unsupported_host_environment_blocks_plan_before_install(tmp_path):
    manager = PackInstallationManager(tmp_path)
    data = unified(environment={'target': 'host-worker', 'provider': 'node', 'prepare': 'install'})
    assert manager.plan(data)['environments'][0]['state'] == 'unsupported'
    assert manager.plan(data)['blockers']
    assert manager.operations() == []


def test_inventory_recognizes_verified_desktop_bundle_without_creating_profile(tmp_path, monkeypatch):
    from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec
    from backend.packs.spec import EnvironmentSpec
    import sys
    monkeypatch.setattr(sys, 'prefix', str(tmp_path / 'desktop-python'))
    declaration = EnvironmentSpec(target='host-worker', verify_imports=('json',))
    spec = PythonEnvironmentSpec.from_declaration('test.runtime', 'worker', declaration, tmp_path)
    PackEnvironmentManager(tmp_path / 'desktop-python' / 'oaw-runtime-bundle').prepare_python(spec)
    profile = tmp_path / 'user-profile'
    assert environment_status('test.runtime', 'worker', declaration, tmp_path, profile)['state'] == 'ready'
    assert not profile.exists()


def test_store_exposes_same_environment_plan_and_checks_expected_identity(store, monkeypatch):
    client, fake = store
    fake.data = unified(environment={'target': 'sandbox', 'python': ['numpy==2.0.0']})
    fake.manifest = inspect_archive(fake.data).manifest.model_dump(mode='json')
    metadata = client.get('/api/store/packs/test.greeter-release/versions/0.1.0').json()
    assert metadata['plan']['environments'][0]['state'] == 'on_use'
    assert metadata['plan']['environments'][0]['python'] == ['numpy==2.0.0']
    manager = client.app.state.services.pack_installations
    fake.manifest['id'] = 'test.greeter'
    monkeypatch.setattr(manager, '_prepare', lambda *args: pytest.fail('Store prepared the wrong artifact'))
    response = client.post('/api/store/packs/test.greeter/versions/0.1.0/install', headers={'X-OAW-Pack-Install': '1'})
    assert response.status_code == 422
    assert manager.operations() == []


@pytest.mark.asyncio
async def test_cancelled_request_after_preparation_is_retryable(tmp_path):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from backend.api.packs import prepare_and_install
    manager = PackInstallationManager(tmp_path)
    @asynccontextmanager
    async def cancelled_gate():
        raise asyncio.CancelledError()
        yield
    services = SimpleNamespace(pack_installations=manager, _node_mutation=cancelled_gate)
    with pytest.raises(asyncio.CancelledError):
        await prepare_and_install(services, unified())
    assert manager.operations()[0]['state'] == 'failed'
    assert not manager.selected()
