from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import sqlite3
from pathlib import Path
import stat
import subprocess
import sys
from zipfile import ZipFile, ZipInfo

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.packs.archive import inspect_archive
from backend.packs.installation import PackInstallationManager
from backend.packs.manifest import PackManifest
from backend.packs.requirements import aggregate_requirements
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.loader import load_installed_packs

MANIFEST = {
    'schema_version': 1, 'id': 'test.greeter', 'name': 'Greeter', 'version': '0.1.0',
    'compatibility': {'oaw': '>=0.1.0,<1', 'plugin_api': '1.23', 'frontend_api': 1},
    'dependencies': {'packs': []}, 'runtime': {'sandbox': {'python': []}},
    'entrypoints': {'backend': 'backend/runtime.whl', 'frontend': 'frontend/index.js'},
}


def zipped(files):
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def pack_files(manifest=None, source=None):
    manifest = deepcopy(manifest or MANIFEST)
    module = 'oaw_pack_' + manifest['id'].replace('.', '_')
    prefix = f"{module}-{manifest['version']}.dist-info"
    code = source or f'''
from open_agent_world.plugin_api import PluginDescriptor, PluginDefinition, NodeTypeDefinition, PackDefinition
from pydantic import BaseModel
class Config(BaseModel):
    pass
def register(r):
    r.register_node_type(NodeTypeDefinition(id='{manifest['id']}.card', label='Greeter', description='Test', icon='sparkles', color='#123456', deck_id='test', deck_label='Test', deck_icon='sparkles', default_name='Greeter', default_size=(300,200), default_status='idle', statuses=frozenset({{'idle'}}), config_model=Config, frontend={{'body':'greeting'}}))
    r.register_pack(PackDefinition(id='{manifest['id']}', name='Greeter', cards=('{manifest['id']}.card',)))
def create_plugin():
    return PluginDefinition(PluginDescriptor(id='{manifest['id']}',version='{manifest['version']}',plugin_api_version='1.23'),register)
'''
    wheel = zipped({f'{module}/__init__.py': code,
        f'{prefix}/WHEEL': 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        f'{prefix}/METADATA': f"Metadata-Version: 2.1\nName: {module}\nVersion: {manifest['version']}\n",
        f'{prefix}/entry_points.txt': f"[open_agent_world.plugins]\n{manifest['id']} = {module}:create_plugin\n"})
    return {'manifest.json': json.dumps(manifest).encode(), manifest['entrypoints']['backend']: wheel,
            manifest['entrypoints']['frontend']: b'export default {apiVersion:1,views:{greeting:()=>null}}'}


def artifact(manifest=None, *, files=None, source=None):
    files = dict(files if files is not None else pack_files(manifest, source))
    files['checksums.json'] = json.dumps({name: hashlib.sha256(content).hexdigest() for name, content in files.items()}).encode()
    return zipped(files)


@pytest.fixture(autouse=True)
def isolate_modules(monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    yield
    for name in list(sys.modules):
        if name.startswith('oaw_pack_test_'):
            del sys.modules[name]


def test_inspection_never_executes_backend_and_emits_schema(tmp_path):
    marker = tmp_path / 'executed'
    payload = artifact(source=f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    assert inspect_archive(payload).manifest.id == 'test.greeter'
    assert not marker.exists()
    assert PackManifest.model_json_schema()['additionalProperties'] is False


@pytest.mark.parametrize('change', [
    {'schema_version': 2}, {'id': '../core'}, {'version': 'v1.0'}, {'post_install': 'curl | bash'},
    {'compatibility': {'oaw': '>=99', 'plugin_api': '1.23', 'frontend_api': 1}},
    {'compatibility': {'oaw': '>=0.1', 'plugin_api': '2.0', 'frontend_api': 1}},
    {'compatibility': {'oaw': '>=0.1', 'plugin_api': '1.24', 'frontend_api': 1}},
    {'compatibility': {'oaw': '>=0.1', 'plugin_api': '1.23', 'frontend_api': 2}},
    {'runtime': {'sandbox': {'python': ['https://evil/wheel.whl']}}},
    {'runtime': {'sandbox': {'python': ['git+https://evil/repo']}}},
    {'runtime': {'sandbox': {'python': ['../package']}}},
    {'runtime': {'sandbox': {'python': ['--index-url=evil']}}},
    {'runtime': {'sandbox': {'python': ['numpy; os_name == "nt"']}}},
    {'runtime': {'sandbox': {'node': ['arbitrary']}}},
])
def test_invalid_or_incompatible_manifest(change):
    manifest = {**deepcopy(MANIFEST), **change}
    with pytest.raises(ValueError):
        inspect_archive(artifact(manifest))


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/escape', 'frontend/../escape', 'frontend\\escape',
    'frontend/a:stream', 'frontend/CON.js', 'frontend/trailing.', 'frontend//empty'])
def test_traversal_rejected(name):
    files = pack_files()
    files[name] = b'bad'
    with pytest.raises(ValueError, match='path'):
        inspect_archive(artifact(files=files))


def test_checksums_missing_tampered_duplicate_case_and_symlink():
    files = pack_files()
    with pytest.raises(ValueError, match='checksums'):
        inspect_archive(zipped(files))
    payload = artifact()
    with ZipFile(io.BytesIO(payload)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files['frontend/index.js'] = b'changed'
    with pytest.raises(ValueError, match='Checksum mismatch'):
        inspect_archive(zipped(files))
    files['frontend/INDEX.js'] = b'case collision'
    with pytest.raises(ValueError, match='Duplicate'):
        inspect_archive(zipped(files))
    stream = io.BytesIO(payload)
    with ZipFile(stream, 'a') as archive:
        info = ZipInfo('assets/link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, '/outside')
    with pytest.raises(ValueError, match='non-regular'):
        inspect_archive(stream.getvalue())


@pytest.mark.parametrize('missing', ['backend/runtime.whl', 'frontend/index.js'])
def test_missing_artifact(missing):
    files = pack_files()
    del files[missing]
    with pytest.raises(ValueError, match='Missing Pack artifact'):
        inspect_archive(artifact(files=files))


def test_backend_wheel_cannot_shadow_host():
    files = pack_files()
    with ZipFile(io.BytesIO(files['backend/runtime.whl'])) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    contents['backend/__init__.py'] = b'bad'
    files['backend/runtime.whl'] = zipped(contents)
    with pytest.raises(ValueError, match='only oaw_pack_test_greeter'):
        inspect_archive(artifact(files=files))


def test_install_upgrade_rollback_atomic_immutable_and_restart(tmp_path):
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    first = artifact()
    assert manager.install(first)['restart_required']
    assert not registry.has_plugin('test.greeter')
    assert manager.version_path('test.greeter', '0.1.0').is_dir()
    with pytest.raises(ValueError, match='immutable'):
        manager.install(first)
    newer = {**deepcopy(MANIFEST), 'version': '0.2.0'}
    manager.install(artifact(newer))
    assert manager.selected()['test.greeter'].version == '0.2.0'
    manager.activate('test.greeter', '0.1.0')
    load_installed_packs(registry, tmp_path)
    assert registry.has_plugin('test.greeter')
    assert registry.runtime_requirements['test.greeter'] == ()
    assert not manager.status()['restart_required']
    assert {p.source for p in registry.catalog().packs} == {'installed', 'bundled'}
    assert registry.frontend_modules['test.greeter']['url'].endswith('/0.1.0/frontend/index.js')
    manager.remove_version('test.greeter', '0.2.0')
    manager.uninstall('test.greeter')
    assert manager.status()['restart_required']
    assert registry.has_plugin('test.greeter')
    with pytest.raises(ValueError, match='loaded'):
        manager.remove_version('test.greeter', '0.1.0')


def test_duplicate_bundled_id_and_missing_pack_dependency(tmp_path):
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    # Use a bundled test definition whose external-safe identity can collide.
    from backend.plugins.registry import PluginDescriptor, PluginDefinition
    registry.install(PluginDefinition(PluginDescriptor(id='test.greeter', version='1', plugin_api_version='1.0'), lambda r: None))
    with pytest.raises(ValueError, match='ownership'):
        manager.install(artifact())
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    manifest = deepcopy(MANIFEST)
    manifest['dependencies']['packs'] = [{'id': 'missing.pack', 'version': '>=1'}]
    with pytest.raises(ValueError, match='missing/incompatible'):
        manager.install(artifact(manifest))
    assert manager.status()['versions'] == []


def test_removed_version_cannot_be_replaced_with_different_content(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    original = artifact()
    manager.install(original)
    manager.uninstall('test.greeter')
    manager.remove_version('test.greeter', '0.1.0')
    changed = pack_files()
    changed['frontend/index.js'] = b'changed release'
    with pytest.raises(ValueError, match='immutable'):
        manager.install(artifact(files=changed))
    assert manager.install(original)['versions'][0]['selected']


def test_dependency_version_upgrade_rejected_without_switching(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manager.install(artifact())
    consumer = {**deepcopy(MANIFEST), 'id': 'test.consumer', 'dependencies': {'packs': [{'id': 'test.greeter', 'version': '<0.2'}]}}
    manager.install(artifact(consumer))
    with pytest.raises(ValueError, match='missing/incompatible'):
        manager.install(artifact({**deepcopy(MANIFEST), 'version': '0.2.0'}))
    assert manager.selected()['test.greeter'].version == '0.1.0'


@pytest.mark.parametrize('requirements', [(['numpy<2'], ['numpy>=2']), (['numpy==1.26'], ['numpy>=2']),
    (['numpy~=1.26'], ['numpy==2']), (['numpy==1.*'], ['numpy>=2'])])
def test_obvious_conflicts_name_owners(requirements):
    with pytest.raises(ValueError, match='Pack A.*Pack B'):
        aggregate_requirements({'Pack A': requirements[0], 'Pack B': requirements[1]})


def test_compatible_requirements_are_aggregated():
    assert aggregate_requirements({'A': ['numpy>=1.26', 'ase>=3'], 'B': ['numpy<3', 'ase>=3']}) == ['ase>=3', 'numpy<3', 'numpy>=1.26']


def test_conflicting_new_pack_never_becomes_selected(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    first = {**deepcopy(MANIFEST), 'runtime': {'sandbox': {'python': ['numpy<2']}}}
    manager.install(artifact(first))
    second = {**deepcopy(MANIFEST), 'id': 'test.second', 'runtime': {'sandbox': {'python': ['numpy>=2']}}}
    with pytest.raises(ValueError, match='dependency conflict'):
        manager.install(artifact(second))
    assert set(manager.selected()) == {'test.greeter'}
    assert len(manager.status()['versions']) == 1


def test_installed_tampering_and_private_frontend_paths(tmp_path):
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    manager.install(artifact())
    load_installed_packs(registry, tmp_path)
    for name in ('backend/runtime.whl', 'manifest.json', '../escape', 'frontend/../manifest.json'):
        with pytest.raises(ValueError):
            manager.frontend_asset('test.greeter', '0.1.0', name)
    path = manager.frontend_asset('test.greeter', '0.1.0', 'frontend/index.js')
    path.write_text('changed')
    checksums = path.parent.parent / 'checksums.json'
    value = json.loads(checksums.read_text())
    value['frontend/index.js'] = hashlib.sha256(b'changed').hexdigest()
    checksums.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='checksum'):
        manager.frontend_asset('test.greeter', '0.1.0', 'frontend/index.js')
    with pytest.raises(ValueError, match='checksum'):
        manager.verify_installed(MANIFEST_MODEL)


MANIFEST_MODEL = PackManifest.model_validate(MANIFEST)


def test_api_install_restart_collect_place_and_uninstall_in_use(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    headers = {'Content-Type': 'application/vnd.oaw.pack', 'X-OAW-Pack-Install': '1'}
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/packs/install', content=artifact()).status_code == 403
        inspected = client.post('/api/packs/inspect', content=artifact(), headers=headers)
        assert inspected.status_code == 200, inspected.text
        installed = client.post('/api/packs/install', content=artifact(), headers=headers)
        assert installed.status_code == 201, installed.text
        assert installed.json()['restart_required']
        assert 'test.greeter' not in client.get('/api/catalog').json()['frontend_modules']
    with TestClient(create_app(settings)) as client:
        catalog = client.get('/api/catalog').json()
        url = catalog['frontend_modules']['test.greeter']['url']
        assert client.get(url).status_code == 200
        assert 'javascript' in client.get(url).headers['content-type']
        assert client.get(url.replace('frontend/index.js', 'backend/runtime.whl')).status_code == 404
        library = client.get('/api/card-library').json()
        library = client.post('/api/card-library/actions', json={'action': 'open_pack', 'id': 'test.greeter', 'expected_revision': library['revision']}).json()
        assert 'test.greeter.card' in library['collection']
        card = client.post('/api/card-library/nodes', json={'type': 'test.greeter.card'})
        assert card.status_code == 201, card.text
        assert client.delete('/api/packs/test.greeter', headers=headers).status_code == 409
        assert client.delete('/api/nodes/' + card.json()['id']).is_success
        assert client.delete('/api/packs/test.greeter', headers=headers).status_code == 200
        assert client.post('/api/nodes', json={'type': 'test.greeter.card'}).status_code != 201
        state = client.get('/api/card-library').json()
        assert client.post('/api/card-library/actions', json={'action': 'set_plugin_enabled', 'id': 'test.greeter',
            'enabled': True, 'expected_revision': state['revision']}).status_code == 409


def test_runtime_rejects_distribution_ownership_mismatch(tmp_path):
    files = pack_files()
    with ZipFile(io.BytesIO(files['backend/runtime.whl'])) as archive:
        wheel = {name: archive.read(name) for name in archive.namelist()}
    name = 'oaw_pack_test_greeter/__init__.py'
    wheel[name] = wheel[name].replace(b"id='test.greeter', name='Greeter'", b"id='test.other', name='Greeter'")
    files['backend/runtime.whl'] = zipped(wheel)
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manager.install(artifact(files=files))
    with pytest.raises(RuntimeError, match='exactly one Pack'):
        load_installed_packs(manager.registry, tmp_path)
    assert not manager.registry.has_plugin('test.greeter')


def test_missing_runtime_frontend_does_not_prevent_backend_discovery(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manager.install(artifact())
    (manager.version_path('test.greeter', '0.1.0') / 'frontend/index.js').unlink()
    load_installed_packs(manager.registry, tmp_path)
    assert manager.registry.has_plugin('test.greeter')
    with pytest.raises(FileNotFoundError):
        manager.frontend_asset('test.greeter', '0.1.0', 'frontend/index.js')


def test_local_version_frontend_url_is_encoded(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manifest = {**deepcopy(MANIFEST), 'version': '1!0.1.0+local.1'}
    manager.install(artifact(manifest))
    load_installed_packs(manager.registry, tmp_path)
    assert manager.registry.frontend_modules['test.greeter']['url'] == (
        '/api/packs/test.greeter/versions/1!0.1.0%2Blocal.1/frontend/index.js')


def test_failed_database_write_restores_files_and_allows_retry(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    with manager.connect() as db:
        db.execute("CREATE TRIGGER fail_install BEFORE INSERT ON versions BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='disk failure'):
        manager.install(artifact())
    assert not manager.selected()
    assert not manager.version_path('test.greeter', '0.1.0').exists()
    with manager.connect() as db:
        db.execute('DROP TRIGGER fail_install')
    manager.install(artifact())
    manager.uninstall('test.greeter')
    with manager.connect() as db:
        db.execute("CREATE TRIGGER fail_remove BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='disk failure'):
        manager.remove_version('test.greeter', '0.1.0')
    manager.verify_installed(MANIFEST_MODEL)
    with manager.connect() as db:
        db.execute('DROP TRIGGER fail_remove')
    manager.remove_version('test.greeter', '0.1.0')
    assert not manager.status()['versions']
