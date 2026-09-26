"""Copy and verify the stopped desktop's active store before replacing the app."""
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
import json
import shutil
from uuid import uuid4

from backend.config import Settings
from backend.storage_location import LOCK, _copy, _fingerprint, _read, _verify_database, file_lock


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
        stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
        target = source.with_name(f'{source.name}.before-update-{stamp}-{uuid4().hex[:8]}')
        stage = target.with_name(target.name + '.partial')
        stage.mkdir(mode=0o700)
        try:
            before = _fingerprint(source)
            _copy(source, stage)
            if _fingerprint(stage) != before or _fingerprint(source) != before:
                raise RuntimeError('Data changed during backup. Update cancelled.')
            _verify_database(stage)
            (stage / '.oaw-update-backup.json').write_text(json.dumps({
                'source': str(source), 'created_at': stamp, 'fingerprint': before,
                'storage_pointer': config,
            }), encoding='utf-8')
            stage.rename(target)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return target
