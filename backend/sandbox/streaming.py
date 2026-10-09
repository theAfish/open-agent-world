"""Browser uploads staged through bounded runtime messages, including WSL.

Only host-generated staging names and file identities cross the internal boundary.
The final destination is opened exclusively; existing files are never truncated.
"""
import base64
import os
import re
from pathlib import Path
from uuid import uuid4

from .files import parts, pinned
from .models import SandboxSecurityError, SandboxValidationError
from .transfers import CHUNK_BYTES, signature

UPLOAD_LIMIT = 1024 * 1024 * 1024


def _remove_owned(target, expected, runtime_pinned_root):
    """Remove our temporary entry only, never follow a link or remove a directory."""
    with pinned(target.parent, directory=True, runtime_pinned_root=runtime_pinned_root) as parent:
        try:
            with pinned(target, runtime_pinned_root=runtime_pinned_root, delete=True) as fd:
                if signature(os.fstat(fd))[:2] != expected[:2]:
                    raise SandboxSecurityError('Upload staging file was replaced')
                if os.name == 'nt':
                    import ctypes
                    import msvcrt
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    remove = kernel.SetFileInformationByHandle
                    remove.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
                    flag = wintypes.BOOL(True)
                    if not remove(msvcrt.get_osfhandle(fd), 4, ctypes.byref(flag), ctypes.sizeof(flag)):
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    os.unlink(target.name, dir_fd=parent)
        except FileNotFoundError:
            pass


def upload_operation(root, operation, *, path, staging=None, expected=None, offset=0, data=None,
                     read_only=False, runtime_pinned_root=None):
    if read_only:
        raise SandboxSecurityError('This workspace is read-only')
    root = Path(root)
    components = parts(path)
    if not components:
        raise SandboxValidationError('Choose an upload destination')
    target = root.joinpath(*components)
    if operation == 'upload_begin':
        with pinned(target.parent, directory=True, create_parents_from=root, create_directory=True,
                    runtime_pinned_root=runtime_pinned_root):
            try:
                target.lstat()
            except FileNotFoundError:
                pass
            else:
                raise SandboxValidationError(f'Sandbox path {path!r} already exists. Choose another destination.')
            temporary = target.with_name(f'.oaw-upload-{uuid4().hex}.part')
            with pinned(temporary, write=True, exclusive=True, runtime_pinned_root=runtime_pinned_root) as fd:
                return {'staging': temporary.relative_to(root).as_posix(), 'signature': signature(os.fstat(fd))}
    temporary = root.joinpath(*parts(staging))
    if temporary.parent != target.parent or not re.fullmatch(r'\.oaw-upload-[a-f0-9]{32}\.part', temporary.name) or len(expected or []) != 5:
        raise SandboxSecurityError('Invalid upload staging identity')
    if operation == 'upload_abort':
        _remove_owned(temporary, expected, runtime_pinned_root)
        return {'cancelled': True}
    if operation not in {'upload_chunk', 'upload_commit'}:
        raise SandboxValidationError('Unknown upload operation')
    with pinned(temporary, write=operation == 'upload_chunk', runtime_pinned_root=runtime_pinned_root) as fd:
        info = os.fstat(fd)
        if signature(info) != expected or info.st_size != offset:
            raise SandboxValidationError('Upload staging file changed; retry the upload')
        if operation == 'upload_chunk':
            content = base64.b64decode(data, validate=True)
            if len(content) > CHUNK_BYTES or offset + len(content) > UPLOAD_LIMIT:
                raise SandboxValidationError('Invalid upload chunk')
            os.lseek(fd, offset, os.SEEK_SET)
            with os.fdopen(os.dup(fd), 'wb') as output:
                output.write(content)
                output.flush()
            return {'signature': signature(os.fstat(fd))}
        destination_identity = None
        try:
            with pinned(target, write=True, exclusive=True, runtime_pinned_root=runtime_pinned_root) as destination:
                destination_identity = signature(os.fstat(destination))
                # Copy from the pinned source handle, never reopen a mutable path.
                with os.fdopen(os.dup(destination), 'wb') as output:
                    remaining = offset
                    while remaining:
                        content = os.read(fd, min(remaining, CHUNK_BYTES))
                        if not content:
                            raise SandboxValidationError('Upload staging file was truncated')
                        output.write(content)
                        remaining -= len(content)
                    output.flush()
                    os.fsync(output.fileno())
                if signature(os.fstat(fd)) != expected:
                    raise SandboxValidationError('Upload staging file changed during commit')
        except FileExistsError as exc:
            raise SandboxValidationError(f'Sandbox path {path!r} already exists. Choose another destination.') from exc
        except BaseException:
            if destination_identity:
                _remove_owned(target, destination_identity, runtime_pinned_root)
            raise
    _remove_owned(temporary, expected, runtime_pinned_root)
    return {'written': offset}
