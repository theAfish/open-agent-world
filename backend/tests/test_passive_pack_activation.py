"""New passive Packs become usable through the running host, without code reload."""
import io
import json
import sys
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.packs.build import build_project, _zip
from backend.packs.content import export_archive
from backend.plugins.builtin import create_builtin_registry
from backend.services import create_services
from backend.tests.test_content_packs import creation, HEADERS
from backend.tests.test_declarative_packs import project
from backend.tests.test_pack_installation import artifact as advanced_artifact


def test_first_declarative_install_is_live_upgrade_and_rollback_keep_loaded_version(tmp_path):
    source = project(tmp_path)
    first = build_project(source, tmp_path / 'first.oawpack').read_bytes()
    settings = Settings.for_data_root(tmp_path / 'profile')
    services = create_services(settings, plugins=create_builtin_registry())
    modules = {name for name in sys.modules if name.startswith('oaw_declarative_')}
    with TestClient(create_app(settings, services=services)) as client:
        plan = client.post('/api/packs/inspect', content=first, headers=HEADERS).json()
        assert not plan['restart_required'] and not plan['plan']['restart_required']
        response = client.post('/api/packs/install', content=first, headers=HEADERS)
        assert response.status_code == 201, response.text
        assert not response.json()['restart_required']
        assert response.json()['versions'][0]['loaded']
        assert {name for name in sys.modules if name.startswith('oaw_declarative_')} == modules
        card = client.post('/api/nodes', json={'type': 'test.notes', 'config': {'message': 'Before upgrade'}}).json()
        assert card['config']['message'] == 'Before upgrade'
        assert client.get('/api/packs/test.notes/versions/1.0.0/frontend/index.js').status_code == 200
        path = source / 'pyproject.toml'
        path.write_text(path.read_text().replace('1.0.0', '1.1.0'))
        newer = build_project(source, tmp_path / 'new.oawpack').read_bytes()
        assert client.post('/api/packs/inspect', content=newer, headers=HEADERS).json()['restart_required']
        updated = client.post('/api/packs/install', content=newer, headers=HEADERS).json()
        assert updated['restart_required']
        assert next(row for row in updated['versions'] if row['loaded'])['version'] == '1.0.0'
        assert client.get('/api/packs/test.notes/versions/1.0.0/frontend/index.js').status_code == 200
        reverted = client.post('/api/packs/test.notes/activate', json={'version': '1.0.0'},
            headers={'X-OAW-Pack-Install': '1'}).json()
        assert not reverted['restart_required']
        assert client.get('/api/nodes/' + card['id']).json()['config']['message'] == 'Before upgrade'
    services.close()


def test_content_pack_is_collectable_and_instantiable_without_restart(tmp_path):
    registry, files = creation()
    services = create_services(Settings.for_data_root(tmp_path), plugins=registry)
    with TestClient(create_app(services.settings, services=services)) as client:
        response = client.post('/api/packs/install', content=export_archive(files), headers=HEADERS)
        assert response.status_code == 201, response.text
        assert not response.json()['restart_required']
        library = client.get('/api/card-library').json()
        assert client.post('/api/card-library/actions', json={'action': 'open_pack', 'id': 'local.research',
            'expected_revision': library['revision']}).status_code == 200
        placed = client.post('/api/legions/presets/local.research.legion/instances', json={'position': {'x': 100, 'y': 200}})
        assert placed.status_code == 201, placed.text
    services.close()


def test_arbitrary_python_and_modified_generated_wheel_are_never_loaded_live(tmp_path):
    source = project(tmp_path)
    data = build_project(source, tmp_path / 'note.oawpack').read_bytes()
    with ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    with ZipFile(io.BytesIO(files['backend/adapter.whl'])) as archive:
        wheel = {name: archive.read(name) for name in archive.namelist()}
    module = next(name for name in wheel if name.endswith('/__init__.py'))
    wheel[module] += b'\nraise RuntimeError("must not execute during install")\n'
    # The ordinary wheel validator requires truthful RECORD hashes too.
    import base64, csv, hashlib
    record = next(name for name in wheel if name.endswith('/RECORD'))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    for name, value in sorted(wheel.items()):
        if name != record:
            writer.writerow((name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(value).digest()).rstrip(b'=').decode(), len(value)))
    writer.writerow((record, '', ''))
    wheel[record] = buffer.getvalue().encode()
    files['backend/adapter.whl'] = _zip(wheel)
    files['checksums.json'] = json.dumps({name: hashlib.sha256(value).hexdigest() for name, value in files.items() if name != 'checksums.json'}).encode()
    services = create_services(Settings.for_data_root(tmp_path / 'profile'), plugins=create_builtin_registry())
    with TestClient(create_app(services.settings, services=services)) as client:
        for data in (advanced_artifact(), _zip(files)):
            result = client.post('/api/packs/install', content=data, headers=HEADERS)
            assert result.status_code == 201, result.text
            assert result.json()['restart_required']
        assert not services.plugins.has_plugin('test.notes')
        assert not services.plugins.has_plugin('test.greeter')
    services.close()
