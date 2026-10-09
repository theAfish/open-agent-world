"""Workspace-only deletion and atomic, non-overwriting moves through pinned roots."""
import ctypes
import errno
import os
import re
from pathlib import Path

from .files import parts, pinned
from .models import SandboxSecurityError, SandboxValidationError
from .transfers import transfer


def _windows_information(fd, kind, value):
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    change = kernel.SetFileInformationByHandle
    change.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    change.restype = wintypes.BOOL
    if not change(msvcrt.get_osfhandle(fd), kind, ctypes.byref(value), ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())


def _rename(source, destination, source_fd, source_parent, destination_parent):
    if os.name == 'nt':
        from ctypes import wintypes
        class RenameInfo(ctypes.Structure):
            _fields_ = [('replace', wintypes.BOOL), ('root', wintypes.HANDLE),
                        ('length', wintypes.DWORD), ('name', wintypes.WCHAR * 1)]
        name = str(destination).encode('utf-16-le')
        buffer = ctypes.create_string_buffer(RenameInfo.name.offset + len(name) + 2)
        info = RenameInfo.from_buffer(buffer)
        info.replace, info.root, info.length = False, None, len(name)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, name, len(name))
        _windows_information(source_fd, 3, buffer)
    else:
        # Linux/WSL must fail atomically if a destination appears after validation.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise SandboxValidationError('This runtime does not support safe file moves')
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(source_parent, os.fsencode(source.name), destination_parent, os.fsencode(destination.name), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))


def _same_entry(target, fd, parent, expected):
    info = os.fstat(fd)
    current = os.stat(target if os.name == 'nt' else target.name,
                      **({} if os.name == 'nt' else {'dir_fd': parent}), follow_symlinks=False)
    if (info.st_dev, info.st_ino) != tuple(expected[:2]) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
        raise SandboxSecurityError('File changed during the operation; refresh and retry')


def _remove(root, relative, manifest, runtime_pinned_root):
    target = root.joinpath(*parts(relative))
    entry = manifest[relative]
    options = {'runtime_pinned_root': runtime_pinned_root}
    with pinned(target.parent, directory=True, **options) as parent:
        if entry['directory']:
            # Keep ancestors pinned while visiting children. A Windows DELETE
            # handle is acquired only after descendant pins have been released.
            with pinned(target, directory=True, **options) as fd:
                _same_entry(target, fd, parent, entry['signature'])
                with os.scandir(target if os.name == 'nt' else fd) as children:
                    names = [child.name for child in children]
                if any(f'{relative}/{name}' not in manifest for name in names):
                    raise SandboxValidationError('Folder changed during deletion; refresh and retry')
                for name in names:
                    _remove(root, f'{relative}/{name}', manifest, runtime_pinned_root)
        with pinned(target, directory=entry['directory'], delete=os.name == 'nt', **options) as fd:
            _same_entry(target, fd, parent, entry['signature'])
            if os.name == 'nt':
                from ctypes import wintypes
                _windows_information(fd, 4, wintypes.BOOL(True))
            elif entry['directory']:
                os.rmdir(target.name, dir_fd=parent)
            else:
                os.unlink(target.name, dir_fd=parent)


def mutate(root, operation, *, path, destination=None, read_only=False, runtime_pinned_root=None):
    if read_only:
        raise SandboxSecurityError('This workspace is read-only')
    root = Path(root)
    source_parts = parts(path)
    if not source_parts:
        raise SandboxValidationError('Choose a file or folder within the workspace')
    source = root.joinpath(*source_parts)
    if operation == 'move':
        destination_parts = parts(destination)
        if not destination_parts:
            raise SandboxValidationError('Choose a destination within the workspace')
        target = root.joinpath(*destination_parts)
        if target == source or target.is_relative_to(source):
            raise SandboxValidationError('A folder cannot be moved into itself')
    elif operation != 'delete':
        raise SandboxValidationError('Unsupported file mutation')
    try:
        # Validate the whole subtree before deleting any children. Links,
        # reparse points, hardlinks and special files retain the read boundary.
        entries = transfer(root, 'capture_manifest', paths=[path], runtime_pinned_root=runtime_pinned_root)
        manifest = {entry['path']: entry for entry in entries}
        if any(re.fullmatch(r'\.oaw-upload-[a-f0-9]{32}\.part', Path(entry['path']).name) for entry in entries):
            raise SandboxValidationError('An upload is in progress in this folder. Wait for it to finish.')
        if operation == 'delete':
            _remove(root, path, manifest, runtime_pinned_root)
            return {'deleted': path}
        with pinned(source.parent, directory=True, runtime_pinned_root=runtime_pinned_root) as source_parent:
            with pinned(target.parent, directory=True, runtime_pinned_root=runtime_pinned_root) as destination_parent:
                with pinned(source, directory=manifest[path]['directory'], delete=os.name == 'nt', runtime_pinned_root=runtime_pinned_root) as fd:
                    _same_entry(source, fd, source_parent, manifest[path]['signature'])
                    _rename(source, target, fd, source_parent, destination_parent)
        return {'moved': path, 'destination': destination}
    except FileExistsError as exc:
        raise SandboxValidationError('Destination already exists. Choose another folder.') from exc
    except FileNotFoundError as exc:
        raise SandboxValidationError('File or folder no longer exists. Refresh and retry.') from exc
    except OSError as exc:
        if exc.errno == errno.ENOTEMPTY:
            raise SandboxValidationError('Folder changed during deletion. Refresh and retry.') from exc
        raise SandboxValidationError('File operation was denied. Check permissions and whether the file is in use.') from exc
