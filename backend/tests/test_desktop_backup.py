from dataclasses import replace
import json
import sqlite3

import pytest

from backend.config import Settings
from backend.desktop_backup import backup_for_update
from backend.storage_location import LOCK, file_lock


def test_update_backup_uses_active_store_keeps_secrets_and_pending_move(tmp_path):
    source = tmp_path / 'active'
    source.mkdir()
    (source / 'database').mkdir()
    with sqlite3.connect(source / 'database/world.sqlite3') as db:
        db.execute('create table data (value text)')
        db.execute("insert into data values ('saved work')")
    (source / 'secret.key').write_text('key')
    pointer = tmp_path / 'storage.json'
    settings = replace(Settings.for_data_root(tmp_path / 'default'), storage_config_path=pointer)
    config = {'current_path': str(source), 'pending_path': str(tmp_path / 'future')}
    pointer.write_text(json.dumps(config))
    backup = backup_for_update(settings)
    assert (backup / 'secret.key').read_text() == 'key'
    assert not (backup / LOCK).exists()
    assert json.loads(pointer.read_text()) == config
    assert not (tmp_path / 'future').exists()
    with sqlite3.connect(backup / 'database/world.sqlite3') as db:
        assert db.execute('select value from data').fetchone()[0] == 'saved work'
    assert (source / 'secret.key').exists()


def test_update_backup_refuses_active_backend(tmp_path):
    with file_lock(tmp_path / LOCK), pytest.raises(Exception, match='in use'):
        backup_for_update(Settings.for_data_root(tmp_path))
    assert not list(tmp_path.parent.glob(tmp_path.name + '.before-update-*'))


def test_update_backup_removes_incomplete_copy(tmp_path, monkeypatch):
    from backend import desktop_backup
    (tmp_path / 'work').write_text('original')
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(desktop_backup, '_copy', fail)
    with pytest.raises(OSError, match='disk full'):
        backup_for_update(Settings.for_data_root(tmp_path))
    assert (tmp_path / 'work').read_text() == 'original'
    assert not list(tmp_path.parent.glob(tmp_path.name + '.before-update-*'))
