from contextlib import ExitStack, closing
from dataclasses import replace
import json
import os
import sqlite3

import pytest
from backend.config import Settings
from backend.errors import ConflictError, ResourceValidationError
from backend import storage_location as storage


def configured(tmp_path):
    root = tmp_path / 'original'
    (root / 'database').mkdir(parents=True)
    with closing(sqlite3.connect(root / 'database/world.sqlite3')) as db:
        db.execute('CREATE TABLE messages(body TEXT)')
        db.execute("INSERT INTO messages VALUES ('intermediate tool message')")
        db.commit()
    (root / 'secrets').mkdir()
    (root / 'secrets/settings.key').write_bytes(b'unchanged-secret-key')
    os.link(root / 'secrets/settings.key', root / 'key-link')
    return replace(Settings.for_data_root(root), storage_config_path=tmp_path / 'startup.json')


def test_restart_relocates_and_preserves_original(tmp_path):
    settings = configured(tmp_path)
    target = tmp_path / 'new-store'
    with ExitStack() as stack:
        running = storage.prepare_storage(settings, stack)
        status = storage.schedule_storage(running, str(target), 0)
        assert status['pending_path'] == str(target)
        assert not target.exists()
        with pytest.raises(ConflictError):
            with ExitStack() as other:
                storage.prepare_storage(settings, other)
    with ExitStack() as stack:
        moved = storage.prepare_storage(settings, stack)
        assert moved.data_root == target
        assert (target / 'secrets/settings.key').read_bytes() == b'unchanged-secret-key'
        assert (settings.data_root / 'secrets/settings.key').exists()
        assert (target / 'key-link').stat().st_ino == (target / 'secrets/settings.key').stat().st_ino
        assert (target / 'key-link').stat().st_ino != (settings.data_root / 'key-link').stat().st_ino
        with closing(sqlite3.connect(moved.database_path)) as db:
            assert db.execute('SELECT body FROM messages').fetchone() == ('intermediate tool message',)
        assert storage.storage_status(moved)['pending_path'] is None
    with ExitStack() as stack:
        assert storage.prepare_storage(settings, stack).data_root == target


def test_failed_copy_falls_back_and_retries(tmp_path, monkeypatch):
    settings = configured(tmp_path)
    target = tmp_path / 'new-store'
    storage.schedule_storage(settings, str(target), 0)
    original = storage._copy
    def fail(*args):
        raise OSError('simulated copy failure')
    monkeypatch.setattr(storage, '_copy', fail)
    with ExitStack() as stack:
        running = storage.prepare_storage(settings, stack)
        assert running.data_root == settings.data_root
        assert 'simulated copy failure' in storage.storage_status(running)['last_error']
    monkeypatch.setattr(storage, '_copy', original)
    with ExitStack() as stack:
        assert storage.prepare_storage(settings, stack).data_root == target


def test_cancel_revision_and_validation(tmp_path):
    settings = configured(tmp_path)
    for target in ['relative', str(settings.data_root), str(tmp_path), str(settings.data_root / 'nested')]:
        with pytest.raises(ResourceValidationError):
            storage.schedule_storage(settings, target, 0)
    occupied = tmp_path / 'occupied'
    occupied.mkdir()
    (occupied / 'user-file').write_text('keep')
    with pytest.raises(ResourceValidationError):
        storage.schedule_storage(settings, str(occupied), 0)
    storage.schedule_storage(settings, str(tmp_path / 'new'), 0)
    with pytest.raises(ConflictError):
        storage.schedule_storage(settings, None, 0)
    assert storage.schedule_storage(settings, None, 1)['pending_path'] is None
    with pytest.raises(ConflictError):
        storage.schedule_storage(Settings.for_data_root(settings.data_root), str(tmp_path / 'new'), 0)


def test_crash_after_promotion_recovers(tmp_path):
    settings = configured(tmp_path)
    target = tmp_path / 'new'
    storage.schedule_storage(settings, str(target), 0)
    value = storage._read(settings.storage_config_path)
    storage._migrate(settings.data_root, target, settings.storage_config_path, value)
    with ExitStack() as stack:
        assert storage.prepare_storage(settings, stack).data_root == target


def test_only_managed_paths_are_rebased(tmp_path):
    settings = configured(tmp_path)
    root = settings.data_root
    (root / 'sandbox-bindings').mkdir()
    external = str(tmp_path / 'external')
    (root / 'sandbox-bindings/test.json').write_text(json.dumps({'workspace_path': str(root / 'sandboxes/a'), 'external': external}))
    (root / 'document.txt').write_text(str(root))
    target = tmp_path / 'new'
    storage.schedule_storage(settings, str(target), 0)
    with ExitStack() as stack:
        assert storage.prepare_storage(settings, stack).data_root == target
    data = json.loads((target / 'sandbox-bindings/test.json').read_text())
    assert data['workspace_path'] == str(target / 'sandboxes/a')
    assert data['external'] == external
    assert (target / 'document.txt').read_text() == str(root)

def test_api_and_application_restart(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    settings = replace(Settings.for_data_root(tmp_path / 'app'), storage_config_path=tmp_path / 'startup.json')
    target = tmp_path / 'moved-app'
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/settings/storage').json()['editable']
        response = client.put('/api/settings/storage', json={'target_path': str(target), 'expected_revision': 0})
        assert response.status_code == 200, response.text
        assert response.json()['current_path'] == str(settings.data_root)
    with TestClient(create_app(settings)) as client:
        status = client.get('/api/settings/storage').json()
        assert status['current_path'] == str(target), status
        assert status['last_error'] is None
        assert client.get('/api/nodes').status_code == 200

@pytest.mark.skipif(os.name != 'nt', reason='Windows WSL reparse points')
def test_wsl_links_preserved_without_resolving_and_internal_paths_rebased(tmp_path):
    import struct
    settings = configured(tmp_path)
    root = settings.data_root
    def link(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        data = struct.pack('<I', 2) + text.encode('utf-8')
        storage._lx_data(path, struct.pack('<IHH', storage.LX_SYMLINK, len(data), 0) + data)
    relative = root / 'runtime/python/cache/wheels/package'
    link(relative, '../../../archive-v0/missing-target')
    absolute = root / 'runtime/python/venv/bin/python'
    def wsl(path):
        return '/mnt/' + path.drive[0].lower() + '/' + '/'.join(path.parts[1:])
    link(absolute, wsl(root / 'runtime/python/base/bin/python'))
    target = tmp_path / 'new'
    storage.schedule_storage(settings, str(target), 0)
    with ExitStack() as stack:
        moved = storage.prepare_storage(settings, stack)
        assert moved.data_root == target, storage.storage_status(moved)
    assert storage._lx_data(relative) == storage._lx_data(target / relative.relative_to(root))
    assert storage._lx_data(target / absolute.relative_to(root))[12:].decode() == wsl(target / 'runtime/python/base/bin/python')
    assert storage._lx_data(absolute)[12:].decode() == wsl(root / 'runtime/python/base/bin/python')


def test_walk_errors_are_not_silently_ignored(tmp_path, monkeypatch):
    def denied(root, *, followlinks, onerror):
        onerror(PermissionError('unreadable directory'))
        yield
    monkeypatch.setattr(storage.os, 'walk', denied)
    with pytest.raises(PermissionError, match='unreadable directory'):
        storage._fingerprint(tmp_path)
