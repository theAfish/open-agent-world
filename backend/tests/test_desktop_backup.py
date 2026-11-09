from dataclasses import replace
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from backend.config import Settings
from backend.desktop_backup import backup_for_update, backup_preflight, delete_update_backup, update_backups
from backend.errors import ConflictError, ResourceValidationError
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
    with file_lock(tmp_path / LOCK), pytest.raises(ConflictError, match='in use'):
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


@pytest.mark.parametrize('changed', ['source', 'copy'])
def test_update_backup_rejects_changes_and_releases_lock(tmp_path, monkeypatch, changed):
    from backend import desktop_backup
    (tmp_path / 'work').write_text('original')
    copy = desktop_backup._copy

    def change_after_copy(source, stage):
        copy(source, stage)
        ((source if changed == 'source' else stage) / 'work').write_text('changed')

    monkeypatch.setattr(desktop_backup, '_copy', change_after_copy)
    with pytest.raises(RuntimeError, match='Data changed'):
        backup_for_update(Settings.for_data_root(tmp_path))
    assert (tmp_path / 'work').read_text() == ('changed' if changed == 'source' else 'original')
    assert not list(tmp_path.parent.glob(tmp_path.name + '.before-update-*'))
    with file_lock(tmp_path / LOCK):
        pass


def test_update_backup_rejects_corrupt_database(tmp_path):
    (tmp_path / 'database').mkdir()
    database = tmp_path / 'database/world.sqlite3'
    database.write_bytes(b'not a SQLite database')
    with pytest.raises(sqlite3.DatabaseError):
        backup_for_update(Settings.for_data_root(tmp_path))
    assert database.read_bytes() == b'not a SQLite database'
    assert not list(tmp_path.parent.glob(tmp_path.name + '.before-update-*'))


def test_update_backup_refuses_missing_active_store_without_creating_replacement(tmp_path):
    missing = tmp_path / 'missing'
    pointer = tmp_path / 'storage.json'
    pointer.write_text(json.dumps({'current_path': str(missing)}))
    settings = replace(Settings.for_data_root(tmp_path / 'default'), storage_config_path=pointer)
    with pytest.raises(RuntimeError, match='unavailable'):
        backup_for_update(settings)
    assert not missing.exists()
    assert not settings.data_root.exists()


def test_update_backup_can_restore_database_documents_and_encrypted_credentials(tmp_path):
    from backend.persistence.database import Database
    from backend.security.llm_settings import LlmSettingsStore
    source = tmp_path / 'active'
    source.mkdir()
    (source / 'document.txt').write_text('saved document')
    with closing(Database(source / 'database/world.sqlite3')) as db:
        LlmSettingsStore(db, source).save(base_url='https://example.invalid', api_key='test-credential')
    backup = backup_for_update(Settings.for_data_root(source))
    receipt = json.loads((backup / '.oaw-update-backup.json').read_text())
    assert receipt['source'] == str(source.resolve())
    # Recover this test-owned data using the documented whole-directory restore.
    failed = tmp_path / 'failed-upgrade'
    source.rename(failed)
    (failed / 'document.txt').write_text('changed after upgrade')
    shutil.copytree(backup, source)
    assert (source / 'document.txt').read_text() == 'saved document'
    assert (failed / 'document.txt').read_text() == 'changed after upgrade'
    with closing(Database(source / 'database/world.sqlite3')) as db:
        assert LlmSettingsStore(db, source).read().api_key == 'test-credential'
    assert backup.is_dir()


def test_space_preflight_fails_before_copy_and_keeps_source_usable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend import desktop_backup
    source = tmp_path / 'active'
    source.mkdir()
    (source / 'document').write_text('original')
    settings = Settings.for_data_root(source)
    monkeypatch.setattr(desktop_backup.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    monkeypatch.setattr(desktop_backup, '_copy', lambda *_: pytest.fail('must check space before copying'))
    for action in (backup_preflight, backup_for_update):
        with pytest.raises(ResourceValidationError, match='Backup needs'):
            action(settings)
    assert (source / 'document').read_text() == 'original'
    assert not list(tmp_path.glob('active.before-update-*'))
    with file_lock(source / LOCK):
        pass


def test_backup_management_preserves_latest_unrecognized_and_active_data(tmp_path):
    source = tmp_path / 'active'
    source.mkdir()
    (source / 'document').write_text('original')
    settings = Settings.for_data_root(source)
    backup_for_update(settings)
    backup_for_update(settings)
    rows = update_backups(source)
    latest, older = rows
    assert not latest['removable'] and older['removable']
    with pytest.raises(ConflictError, match='latest'):
        delete_update_backup(settings, latest['name'])
    with pytest.raises(ResourceValidationError):
        delete_update_backup(settings, '../active')
    unknown = tmp_path / 'active.before-update-20200101T000000Z-aaaaaaaa'
    unknown.mkdir()
    (unknown / 'valuable').write_text('keep')
    with pytest.raises(ResourceValidationError):
        delete_update_backup(settings, unknown.name)
    delete_update_backup(settings, older['name'])
    assert not Path(older['path']).exists()
    assert Path(latest['path']).is_dir()
    assert (unknown / 'valuable').read_text() == 'keep'
    assert (source / 'document').read_text() == 'original'


def test_interrupted_backup_deletion_keeps_exact_target_retryable(tmp_path, monkeypatch):
    from backend import desktop_backup
    source = tmp_path / 'active'
    source.mkdir()
    (source / 'document').write_text('original')
    settings = Settings.for_data_root(source)
    backup_for_update(settings)
    backup_for_update(settings)
    older = update_backups(source)[1]
    path = Path(older['path'])
    remove = desktop_backup.shutil.rmtree
    def interrupted(target):
        (target / desktop_backup.RECEIPT).unlink()
        raise OSError('interrupted deletion')
    monkeypatch.setattr(desktop_backup.shutil, 'rmtree', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        delete_update_backup(settings, older['name'])
    assert next(item for item in update_backups(source) if item['name'] == older['name'])['deleting']
    monkeypatch.setattr(desktop_backup.shutil, 'rmtree', remove)
    delete_update_backup(settings, older['name'])
    assert not path.exists()
    assert not path.with_name(path.name + '.deleting.json').exists()
    assert len(update_backups(source)) == 1


@pytest.mark.parametrize('mode,function', [('--backup-preflight', 'backup_preflight'), ('--backup-for-update', 'backup_for_update')])
def test_desktop_backup_cli_reports_concise_failure(tmp_path, monkeypatch, capsys, mode, function):
    import runpy
    import sys
    from backend import desktop_backup
    monkeypatch.setattr(sys, 'argv', ['launch.py', mode])
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setattr(Settings, 'from_environment', lambda: Settings.for_data_root(tmp_path))
    def fail(_):
        raise ResourceValidationError('Not enough free space')
    monkeypatch.setattr(desktop_backup, function, fail)
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts/desktop-entry.py'), run_name='__main__')
    assert error.value.code == 1
    assert capsys.readouterr().err == 'Backup cancelled: Not enough free space\n'
