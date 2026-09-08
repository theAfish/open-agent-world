"""Internal bounded transfer operations through the existing pinned file boundary.

Chunk payloads are only an internal runtime transport (including WSL workers).
They are never Run state, browser snapshots or provider event payloads.
"""
import base64
import os
import stat
from pathlib import Path

from .files import parts, pinned
from .models import SandboxSecurityError, SandboxValidationError

CHUNK_BYTES = 256 * 1024
MAX_ENTRIES = 10000


def signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def transfer(root, operation, *, path='', paths=None, expected=None, offset=0,
             data=None, read_only=False, runtime_pinned_root=None):
    root = Path(root)
    target = root.joinpath(*parts(path))
    if operation == 'capture_manifest':
        result = {}
        def visit(relative):
            if relative in result:
                return
            if len(result) >= MAX_ENTRIES:
                raise SandboxValidationError('Artifact bundle exceeds 10000 entries')
            components = parts(relative)
            target = root.joinpath(*components)
            # Try regular-file pin first; directory type is probed without following links.
            info = target.lstat()
            directory = stat.S_ISDIR(info.st_mode)
            with pinned(target, directory=directory, runtime_pinned_root=runtime_pinned_root) as fd:
                info = os.fstat(fd)
                result[relative] = {'path': relative, 'directory': directory,
                                    'size': 0 if directory else info.st_size, 'signature': signature(info)}
                if directory:
                    with os.scandir(target if os.name == 'nt' else fd) as entries:
                        for entry in entries:
                            visit(relative + '/' + entry.name)
        for relative in paths or []:
            if not relative or len(parts(relative)) == 0:
                raise SandboxValidationError('Select explicit files or directories, not the workspace root')
            visit(relative)
        return sorted(result.values(), key=lambda item: item['path'])
    if operation == 'read_chunk':
        with pinned(target, runtime_pinned_root=runtime_pinned_root) as fd:
            before = signature(os.fstat(fd))
            if before != expected:
                raise SandboxValidationError('Source changed during publication; finalize inputs and use a new request key')
            os.lseek(fd, offset, os.SEEK_SET)
            content = os.read(fd, CHUNK_BYTES)
            if signature(os.fstat(fd)) != before:
                raise SandboxValidationError('Source changed during publication')
            return {'data': base64.b64encode(content).decode(), 'size': len(content)}
    if read_only:
        raise SandboxSecurityError('This workspace is read-only')
    if operation == 'transfer_mkdir':
        # Pin parent before creating; the destination cannot be redirected.
        with pinned(target.parent, directory=True, runtime_pinned_root=runtime_pinned_root) as fd:
            if os.name == 'nt':
                target.mkdir()
            else:
                os.mkdir(target.name, mode=0o700, dir_fd=fd)
        return {'created': True}
    if operation == 'write_chunk':
        content = base64.b64decode(data, validate=True)
        if len(content) > CHUNK_BYTES or offset < 0:
            raise SandboxValidationError('Invalid transfer chunk')
        with pinned(target, write=True, exclusive=offset == 0, runtime_pinned_root=runtime_pinned_root) as fd:
            if os.fstat(fd).st_size != offset:
                raise SandboxValidationError('Destination changed during materialization')
            os.lseek(fd, offset, os.SEEK_SET)
            with os.fdopen(os.dup(fd), 'wb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        return {'written': len(content)}
    raise SandboxValidationError('Unknown internal transfer operation')
