"""Copy and verify the stopped desktop's active store before replacing the app."""
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
import json
import re
import shutil
import stat
import sys
from uuid import uuid4

from backend.config import Settings
from backend.errors import ConflictError, ResourceValidationError
from backend.storage_location import LOCK, _copy, _entries, _fingerprint, _read, _reparse, _verify_database, _write, file_lock

RECEIPT = '.oaw-update-backup.json'


def backup_preflight(settings: Settings) -> dict:
    """Estimate a full recovery copy before the desktop stops its backend."""
    config = _read(settings.storage_config_path) if settings.storage_config_path else {}
    source = Path(config.get('current_path', settings.data_root)).resolve()
    if not source.is_dir():
        raise ResourceValidationError('The active data directory is unavailable. Update cancelled.')
    required = sum(path.lstat().st_size for path in _entries(source)
                   if not _reparse(path) and stat.S_ISREG(path.lstat().st_mode))
    free = shutil.disk_usage(source.parent).free
    if free < required + 1024 * 1024:
        raise ResourceValidationError(
            f'Backup needs {required:,} bytes plus 1 MiB of free space; {free:,} bytes are available. '
            'Free space in Settings > Storage before updating.')
    return {'required_bytes': required, 'free_bytes': free}


def update_backups(source: Path) -> list[dict]:
    """Recognize complete recovery copies by both their name and receipt.

    Existing backups remain recovery data. Never expire them by age or count.
    The most recent recognized backup is always protected from UI deletion.
    """
    source = source.resolve()
    backups = []
    pattern = re.compile(re.escape(source.name) + r'\.before-update-\d{8}T\d{6}Z-[a-f0-9]{8}')
    candidates = {path.with_name(path.name.removesuffix('.deleting.json')) for path in source.parent.iterdir()}
    for path in candidates:
        journal = path.with_name(path.name + '.deleting.json')
        if not pattern.fullmatch(path.name) or _reparse(path) or _reparse(journal):
            continue
        try:
            deleting = journal.is_file()
            receipt = _read(journal if deleting else path / RECEIPT)
            if (Path(receipt['source']).resolve() != source or not receipt['fingerprint']
                    or not re.fullmatch(r'\d{8}T\d{6}Z', receipt['created_at'])):
                continue
            if deleting and receipt.get('backup_name') != path.name:
                continue
            if not deleting and (not path.is_dir() or _reparse(path / RECEIPT)):
                continue
            created = datetime.strptime(receipt['created_at'], '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
            completed = datetime.fromisoformat(receipt['completed_at']) if receipt.get('completed_at') else created
            if completed.tzinfo is None:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        backups.append({'name': path.name, 'path': str(path), 'created_at': completed.astimezone(UTC).isoformat(), 'deleting': deleting})
    backups.sort(key=lambda item: (item['created_at'], item['name']), reverse=True)
    latest = next((item['name'] for item in backups if not item['deleting']), None)
    return [{**item, 'removable': item['name'] != latest} for item in backups]


def delete_update_backup(settings: Settings, name: str) -> None:
    source = settings.data_root.resolve()
    # Coordinate with update backup creation without touching the live store lock.
    with file_lock(source.with_name(source.name + '.update-backups.lock')):
        backup = next((item for item in update_backups(source) if item['name'] == name), None)
        if backup is None:
            raise ResourceValidationError('This is not a recognized backup of the active data directory')
        if not backup['removable']:
            raise ConflictError('Keep the latest update backup for recovery')
        path = Path(backup['path'])
        if path.parent != source.parent or path == source or _reparse(path):
            raise ResourceValidationError('The backup path changed')
        journal = path.with_name(path.name + '.deleting.json')
        if not backup['deleting']:
            _write(journal, {**_read(path / RECEIPT), 'backup_name': path.name})
        # Keep the exact owned target discoverable if rmtree or the process stops
        # after removing its receipt. Never infer ownership from the folder alone.
        if path.exists():
            shutil.rmtree(path)
        journal.unlink(missing_ok=True)


def backup_for_update(settings: Settings) -> Path:
    # Honor the active storage pointer; a scheduled move must wait until normal startup.
    with ExitStack() as locks:
        if settings.storage_config_path:
            locks.enter_context(file_lock(settings.storage_config_path.with_suffix(settings.storage_config_path.suffix + '.lock')))
        config = _read(settings.storage_config_path) if settings.storage_config_path else {}
        source = Path(config.get('current_path', settings.data_root)).resolve()
        if not source.is_dir():
            raise RuntimeError('The active data directory is unavailable. Update cancelled.')
        locks.enter_context(file_lock(source / LOCK))
        locks.enter_context(file_lock(source.with_name(source.name + '.update-backups.lock')))
        print('Backup: estimating required space', file=sys.stderr, flush=True)
        estimate = backup_preflight(settings)
        stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
        target = source.with_name(f'{source.name}.before-update-{stamp}-{uuid4().hex[:8]}')
        stage = target.with_name(target.name + '.partial')
        stage.mkdir(mode=0o700)
        try:
            print(f"Backup: verifying and copying {estimate['required_bytes']:,} bytes", file=sys.stderr, flush=True)
            before = _fingerprint(source)
            _copy(source, stage)
            if _fingerprint(stage) != before or _fingerprint(source) != before:
                raise RuntimeError('Data changed during backup. Update cancelled.')
            _verify_database(stage)
            (stage / RECEIPT).write_text(json.dumps({
                'source': str(source), 'created_at': stamp, 'fingerprint': before,
                'storage_pointer': config, 'size_bytes': estimate['required_bytes'],
                'completed_at': datetime.now(UTC).isoformat(),
            }), encoding='utf-8')
            stage.rename(target)
            print('Backup: verified; ready to install', file=sys.stderr, flush=True)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return target
