"""Startup-owned, copy/verify/switch relocation; never delete the source store."""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from dataclasses import replace
import hashlib
import json
import logging
import time
import os
from pathlib import Path, PureWindowsPath
import shutil
import sqlite3
import stat
from uuid import uuid4

from backend.config import Settings
from backend.errors import ConflictError, ResourceValidationError

LOCK = ".oaw-storage.lock"
RECEIPT = ".oaw-migration.json"
_log = logging.getLogger("uvicorn.error.storage")
_last_progress = 0.0


def _progress(message: str, *, force: bool = False):
    global _last_progress
    now = time.monotonic()
    if force or now - _last_progress >= 5:
        _log.info("Storage migration: %s", message)
        _last_progress = now


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ConflictError("The storage location is in use by another backend. Stop it before restarting.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ResourceValidationError("Invalid storage startup configuration")
    return value


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
            _copy_security(path, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


LX_SYMLINK = 0xA000001D


def _reparse(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400) or path.is_symlink()
    except FileNotFoundError:
        return False


def _lx_link(path: Path) -> bool:
    return getattr(path.lstat(), "st_reparse_tag", 0) == LX_SYMLINK


def _lx_data(path: Path, payload: bytes | None = None) -> bytes:
    """Read/write a WSL link itself, without asking Windows to resolve its target."""
    import ctypes
    from ctypes import wintypes as w
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    api.DeviceIoControl.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    handle = api.CreateFileW(str(path), 0x40000000 if payload is not None else 0, 7, None, 3, 0x02200000, None)
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        output = ctypes.create_string_buffer(16384)
        source = ctypes.create_string_buffer(payload) if payload is not None else None
        count = w.DWORD()
        if not api.DeviceIoControl(handle, 0x900A4 if payload is not None else 0x900A8,
                                   source, len(payload) if payload is not None else 0,
                                   None if payload is not None else output, 0 if payload is not None else len(output), ctypes.byref(count), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return output.raw[:count.value]
    finally:
        api.CloseHandle(handle)


def _copy_lx_link(source: Path, target: Path):
    if getattr(source.lstat(), "st_file_attributes", 0) & 0x10:
        target.mkdir()
    else:
        target.touch(exist_ok=False)
    _lx_data(target, _lx_data(source))
    _copy_security(source, target)


def _target(source: Path, raw: str, config: Path) -> Path:
    path = Path(raw).expanduser()
    if not raw.strip() or not path.is_absolute() or raw.startswith(("\\\\", "//")):
        raise ResourceValidationError("Choose an absolute local folder path")
    for part in (path, *path.parents):
        if _reparse(part):
            raise ResourceValidationError("The destination must not use symbolic links or junctions")
    path = path.resolve()
    if path == Path(path.anchor) or path == source or path.is_relative_to(source) or source.is_relative_to(path):
        raise ResourceValidationError("Choose a separate folder outside the current data directory")
    if config.is_relative_to(path):
        raise ResourceValidationError("The destination must not contain the startup settings file")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ResourceValidationError("Choose an empty folder or a new folder")
    return path


def storage_status(settings: Settings) -> dict:
    config = settings.storage_config_path
    value = _read(config) if config else {}
    return {"current_path": str(settings.data_root), "pending_path": value.get("pending_path"),
            "previous_path": value.get("previous_path"), "last_error": value.get("last_error"),
            "revision": value.get("revision", 0), "editable": config is not None,
            "managed_by": "settings" if config else "startup configuration"}


def schedule_storage(settings: Settings, target: str | None, revision: int) -> dict:
    config = settings.storage_config_path
    if config is None:
        raise ConflictError("Storage is controlled by startup configuration (including OPEN_AGENT_WORLD_DATA_ROOT).")
    with file_lock(config.with_suffix(config.suffix + ".lock")):
        value = _read(config)
        if value.get("revision", 0) != revision:
            raise ConflictError("Storage settings changed. Reload them before saving.")
        destination = _target(settings.data_root, target, config) if target else None
        value.update(current_path=str(settings.data_root), pending_path=str(destination) if destination else None,
                     migration_id=uuid4().hex if destination else None, last_error=None, revision=revision + 1)
        _write(config, value)
    return storage_status(settings)


def _hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
            _progress(f"verifying {path.name}")
    return result.hexdigest()


def _entries(root: Path):
    def fail(error):
        raise error
    for parent, directories, files in os.walk(root, followlinks=False, onerror=fail):
        # os.walk does not recognize WSL reparse points as links on Windows.
        names = sorted(directories + files)
        directories[:] = [name for name in directories if not _reparse(Path(parent) / name)]
        for name in names:
            path = Path(parent) / name
            if path.parent == root and name in (LOCK, RECEIPT):
                continue
            if _reparse(path) and not path.is_symlink() and not _lx_link(path):
                raise ResourceValidationError(f"Cannot relocate a junction or special reparse point: {path}")
            yield path


def _fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(_entries(root)):
        info = path.lstat()
        if _lx_link(path):
            content = "wsl-link:" + _lx_data(path).hex()
        elif stat.S_ISLNK(info.st_mode):
            content = "link:" + os.readlink(path)
        elif stat.S_ISDIR(info.st_mode):
            content = "directory"
        elif stat.S_ISREG(info.st_mode):
            content = _hash(path)
        else:
            raise ResourceValidationError(f"Unsupported special file: {path.relative_to(root)}")
        digest.update(json.dumps([path.relative_to(root).as_posix(), content]).encode())
    return digest.hexdigest()


def _copy_security(source: Path, target: Path):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes as w
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    api.GetFileSecurityW.restype = w.BOOL
    api.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.c_void_p]
    api.SetFileSecurityW.restype = w.BOOL
    api.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)]
    api.GetSecurityDescriptorControl.restype = w.BOOL
    size = w.DWORD()
    api.GetFileSecurityW(str(source), 4, None, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not api.GetFileSecurityW(str(source), 4, buffer, size, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    control, revision = w.WORD(), w.DWORD()
    if not api.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = 4 | (0x80000000 if control.value & 0x1000 else 0x20000000)
    if not api.SetFileSecurityW(str(target), flags, buffer):
        raise ctypes.WinError(ctypes.get_last_error())


def _copy(source: Path, stage: Path):
    links = {}
    directories = []
    for path in _entries(source):
        _progress(f"copying {path.relative_to(source)}")
        target = stage / path.relative_to(source)
        info = path.lstat()
        if _lx_link(path):
            _copy_lx_link(path, target)
            continue
        if getattr(info, "st_file_attributes", 0) & 0x400 and not path.is_symlink():
            raise ResourceValidationError(f"Cannot relocate a junction or special reparse point: {path}")
        if path.is_symlink():
            target.symlink_to(os.readlink(path), target_is_directory=path.is_dir())
        elif path.is_dir():
            target.mkdir()
            directories.append((path, target))
        elif stat.S_ISREG(info.st_mode):
            identity = (info.st_dev, info.st_ino)
            if info.st_nlink > 1 and identity in links:
                os.link(links[identity], target)
            else:
                shutil.copy2(path, target)
                links[identity] = target
                _copy_security(path, target)
        else:
            raise ResourceValidationError(f"Cannot relocate special file: {path}")
    for source_dir, target_dir in reversed(directories):
        shutil.copystat(source_dir, target_dir)
        _copy_security(source_dir, target_dir)
    _copy_security(source, stage)


def _rebase_managed_metadata(stage: Path, source: Path, target: Path):
    replacements = [(str(source), str(target)), (source.as_posix(), target.as_posix())]
    if os.name == "nt":
        def wsl(path):
            value = PureWindowsPath(path)
            return '/mnt/' + value.drive[0].lower() + '/' + '/'.join(value.parts[1:])
        replacements.append((wsl(source), wsl(target)))
    def text(value):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    # Only backend-owned manifests/configuration. User documents and messages remain byte-for-byte unchanged.
    manifests = [*stage.glob('sandbox-bindings/*.json'), *stage.glob('sandboxes/*/sandbox.json'),
                 *stage.glob('sandbox-runtimes/*/sandboxes/*/sandbox.json')]
    def ensure_owned(path):
        for part in (path, *path.parents):
            if part == stage:
                break
            if _reparse(part):
                raise ResourceValidationError(f"Managed configuration must not traverse links: {path.relative_to(stage)}")
    for path in manifests:
        ensure_owned(path)
        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str):
                for old, new in replacements:
                    if value == old or value.startswith(old + '/') or value.startswith(old + chr(92)):
                        return new + value[len(old):]
            return value
        _write(path, relocate(_read(path)))
    for path in stage.glob('runtime/**/python/venv/pyvenv.cfg'):
        ensure_owned(path)
        path.write_text(text(path.read_text(encoding='utf-8')), encoding='utf-8')
    for path in stage.glob('runtime/**/python/ready.json'):
        ensure_owned(path)
        value = _read(path)
        value['python'] = text(value.get('python', ''))
        value['launcher_version'] = 0  # Existing runtime repair rebuilds scripts without dropping installed packages.
        _write(path, value)
    for path in list(_entries(stage)):
        if _lx_link(path):
            import struct
            payload = _lx_data(path)
            previous = payload[12:].decode("utf-8")
            for old, new in replacements:
                if previous == old or previous.startswith(old + '/'):
                    data = payload[8:12] + (new + previous[len(old):]).encode("utf-8")
                    _lx_data(path, struct.pack("<IHH", LX_SYMLINK, len(data), 0) + data)
                    break
        elif path.is_symlink():
            previous = os.readlink(path)
            updated = previous
            for old, new in replacements:
                if previous == old or previous.startswith(old + '/') or previous.startswith(old + chr(92)):
                    updated = new + previous[len(old):]
                    break
            if updated != previous:
                is_directory = path.is_dir()
                path.unlink()
                path.symlink_to(updated, target_is_directory=is_directory)


def _checkpoint(source: Path):
    for path in (source / 'database/world.sqlite3', source / 'runtime/plugins.sqlite3'):
        if path.is_file():
            with closing(sqlite3.connect(path, timeout=1)) as database:
                if database.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                    raise ConflictError("A database is still in use; migration was not started")


def _verify_database(root: Path):
    path = root / 'database/world.sqlite3'
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as database:
            if database.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ResourceValidationError("The copied database did not pass verification")


def _migrate(source: Path, target: Path, config: Path, value: dict):
    for part in (target, *target.parents):
        if _reparse(part):
            raise ResourceValidationError('The destination must not use symbolic links or junctions')
    _progress('inspecting source and destination', force=True)
    migration_id = value['migration_id']
    # Recover a crash between directory promotion and committing the startup pointer.
    receipt = _read(target / RECEIPT) if target.is_dir() else {}
    if receipt.get('migration_id') == migration_id and receipt.get('phase') == 'ready':
        if _fingerprint(source) == receipt['source_digest'] and _fingerprint(target) == receipt['target_digest']:
            _verify_database(target)
            return
        raise ConflictError("The prepared migration changed; the original location is still active")
    _target(source, str(target), config)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.name + '.oaw-migration-' + migration_id)
    if stage.exists():
        # This exact sibling is reserved by this plan, never the source or an arbitrary destination.
        if _reparse(stage) or stage.resolve() != target.parent / (target.name + '.oaw-migration-' + migration_id):
            raise ConflictError("The migration staging path changed")
        if _read(stage / RECEIPT).get('migration_id') != migration_id:
            raise ConflictError("The staging directory does not belong to this migration")
        _progress('removing incomplete staging copy before retry', force=True)
        shutil.rmtree(stage)
    required = sum(path.stat().st_size for path in _entries(source) if not _reparse(path) and path.is_file())
    if shutil.disk_usage(target.parent).free < required + 1024 * 1024:
        raise ResourceValidationError("The destination does not have enough free space")
    stage.mkdir(mode=0o700)
    _write(stage / RECEIPT, {'migration_id': migration_id, 'phase': 'copying'})
    _progress('checkpointing databases and verifying source', force=True)
    _checkpoint(source)
    original = _fingerprint(source)
    _progress('copying data to staging directory', force=True)
    _copy(source, stage)
    if _fingerprint(stage) != original or _fingerprint(source) != original:
        raise ConflictError("Storage changed while copying; the original location is still active")
    _progress('updating managed paths and checking database', force=True)
    _rebase_managed_metadata(stage, source, target)
    _verify_database(stage)
    _write(stage / RECEIPT, {'migration_id': migration_id, 'phase': 'ready',
                           'source_digest': original, 'target_digest': _fingerprint(stage)})
    if target.exists():
        target.rmdir()  # Only an empty directory can be removed.
    stage.rename(target)


def prepare_storage(settings: Settings, stack: ExitStack) -> Settings:
    config = settings.storage_config_path
    if config is None:
        stack.enter_context(file_lock(settings.data_root / LOCK))
        return settings
    with file_lock(config.with_suffix(config.suffix + '.lock')):
        value = _read(config)
        source = Path(value.get('current_path', settings.data_root)).resolve()
        if value.get('current_path') and not source.is_dir():
            raise ResourceValidationError("The saved data directory is unavailable; refusing to create an empty replacement")
        stack.enter_context(file_lock(source / LOCK))
        pending = value.get('pending_path')
        if pending:
            target = Path(pending)
            try:
                _migrate(source, target, config, value)
                stack.enter_context(file_lock(target / LOCK))
                committed = {**value, 'current_path': str(target), 'previous_path': str(source),
                             'pending_path': None, 'last_error': None, 'revision': value.get('revision', 0) + 1}
                _write(config, committed)
                source = target
                _progress('complete; starting application services', force=True)
            except (OSError, ValueError, sqlite3.Error, ConflictError, ResourceValidationError) as exc:
                _log.warning('Storage migration failed; using original location: %s', exc)
                value.update(last_error=str(exc), revision=value.get('revision', 0) + 1)
                _write(config, value)
        return replace(settings, data_root=source, database_path=source / 'database/world.sqlite3')
