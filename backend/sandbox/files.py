"""Bounded file operations over backend-owned roots, never client host paths.

POSIX uses no-follow directory descriptors. Windows pins every path component
with non-delete-sharing handles and rejects all reparse points. Neither path
resolution nor a symlink check followed by an unprotected open is sufficient.
"""
from __future__ import annotations

import base64
import asyncio
import os
import stat
from contextlib import contextmanager, ExitStack
from pathlib import Path

from .models import SandboxValidationError, SandboxSecurityError

PREVIEW_LIMIT = 1024 * 1024
DOWNLOAD_LIMIT = 16 * 1024 * 1024
DIRECTORY_LIMIT = 300


async def run_file_operation(function, *args, **kwargs):
    """Keep backend locks and runtime pins alive until a worker finishes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def parts(relative):
    if not isinstance(relative, str) or len(relative) > 4096 or any(c in relative for c in "\\:\0"):
        raise SandboxValidationError("Use a logical relative path with forward slashes")
    if relative:
        from .materialization import bundle_path
        bundle_path(relative)
    result = relative.split("/") if relative else []
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in result):
        raise SandboxValidationError("Path traversal and ambiguous names are forbidden")
    return result


@contextmanager
def pinned(path, *, directory=False, write=False, exclusive=False, allow_hardlink=False, runtime_pinned_root=None):
    """Yield an fd; all ancestors remain pinned until it closes."""
    path = Path(path)
    if not path.is_absolute():
        raise SandboxSecurityError("File root is not absolute")
    with ExitStack() as stack:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create.restype = wintypes.HANDLE
            close = kernel.CloseHandle
            close.argtypes = [wintypes.HANDLE]
            class AttributeTag(ctypes.Structure):
                _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]
            query = kernel.GetFileInformationByHandleEx
            query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            current = Path(path.anchor)
            sequence = [current]
            for part in path.parts[1:]:
                current /= part
                sequence.append(current)
            for index, current in enumerate(sequence):
                last = index == len(sequence) - 1
                # Share read/write but deny delete/rename, including ancestors.
                # The Windows backend already pins a running external root with
                # MAXIMUM_ALLOWED (including DELETE) and denies delete sharing.
                # A second deny-delete handle would conflict with that handle's
                # access. Keep its existing pin under the backend record lock.
                share = 7 if runtime_pinned_root is not None and current == runtime_pinned_root else 3
                handle = create(str(current), (0x40000000 if write else 0x80000000) if last else 0x80,
                    share, None, (1 if exclusive else 4) if last and write else 3, 0x02200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    error = ctypes.WinError(ctypes.get_last_error())
                    error.filename = str(current)
                    raise error
                stack.callback(close, handle)
                tag = AttributeTag()
                if not query(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if tag.attributes & 0x400:
                    raise SandboxSecurityError("Symbolic links and junctions cannot be browsed")
            # Duplicate ownership for CRT; original and ancestor handles stay pinned.
            duplicate = wintypes.HANDLE()
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            process = kernel.GetCurrentProcess()
            kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_WRONLY if write else os.O_RDONLY))
            stack.callback(os.close, fd)
        else:
            fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            for index, part in enumerate(path.parts[1:]):
                last = index == len(path.parts) - 2
                flags = os.O_NOFOLLOW | os.O_NONBLOCK
                flags |= (os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else 0)) if last and write else os.O_RDONLY
                if not last or directory:
                    flags |= os.O_DIRECTORY
                fd = os.open(part, flags, 0o600, dir_fd=fd)
                stack.callback(os.close, fd)
        info = os.fstat(fd)
        if not directory and (not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not allow_hardlink)):
            raise SandboxSecurityError("Only regular, non-hardlinked files are supported")
        yield fd


def operate(root: Path, relative: str, operation: str, *, read_only=False, data=None, overwrite=False, allow_hardlink=False, runtime_pinned_root=None):
    target = root.joinpath(*parts(relative))
    if operation == "write" and read_only:
        raise SandboxSecurityError("This root is read-only")
    if operation not in {"list", "preview", "download", "write"}:
        raise SandboxValidationError("Unsupported file operation")
    if operation == "write":
        content = base64.b64decode(data, validate=True)
        if len(content) > DOWNLOAD_LIMIT:
            raise SandboxValidationError("File exceeds 16 MiB copy limit")
    with pinned(target, directory=operation == "list", write=operation == "write", exclusive=not overwrite,
                allow_hardlink=allow_hardlink, runtime_pinned_root=runtime_pinned_root) as fd:
        if operation == "list":
            entries = []
            # Windows cannot scandir(fd), but its entire path is pinned above.
            with os.scandir(target if os.name == "nt" else fd) as iterator:
                for item in iterator:
                    if len(entries) == DIRECTORY_LIMIT:
                        return {"entries": entries, "truncated": True}
                    info = item.stat(follow_symlinks=False)
                    blocked = stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                    entries.append({"name": item.name, "directory": stat.S_ISDIR(info.st_mode) and not blocked,
                        "blocked": blocked, "size": info.st_size})
            return {"entries": sorted(entries, key=lambda e: (not e["directory"], e["name"].casefold())), "truncated": False}
        if operation == "write":
            os.ftruncate(fd, 0)
            with os.fdopen(os.dup(fd), "wb") as output:
                output.write(content)
            return {"written": len(content)}
        limit = DOWNLOAD_LIMIT if operation == "download" else PREVIEW_LIMIT
        if os.fstat(fd).st_size > limit:
            return {"state": "oversized", "limit": limit}
        with os.fdopen(os.dup(fd), "rb") as source:
            content = source.read(limit + 1)
        if len(content) > limit:
            return {"state": "oversized", "limit": limit}
        if operation == "download":
            return {"state": "ready", "data": base64.b64encode(content).decode()}
        image_type = "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if content.startswith(b"\xff\xd8\xff") else "image/gif" if content.startswith((b"GIF87a", b"GIF89a")) else None
        if image_type:
            return {"state": "image", "media_type": image_type, "data": base64.b64encode(content).decode()}
        try:
            text = content.decode("utf-8")
            if "\0" in text:
                raise ValueError()
            return {"state": "text", "text": text}
        except (UnicodeError, ValueError):
            return {"state": "unsupported"}


def file_operation(workspace, access, attachments, operation, root="workspace", path="", **options):
    if operation == "roots":
        return [{"id": "workspace", "label": "Workspace", "access": str(access), "directory": True},
            *[{"id": "resource:" + a.resource_id, "label": a.relative_path, "access": str(a.access), "directory": False} for a in attachments]]
    if root == "workspace":
        return operate(Path(workspace), path, operation, read_only=str(access) == "read_only", **options)
    for attachment in attachments:
        if root == "resource:" + attachment.resource_id and not path:
            return operate(attachment.source.parent, attachment.source.name, operation,
                read_only=str(attachment.access) == "read_only", allow_hardlink=True, **options)
    raise SandboxSecurityError("File root is not authorized for this Sandbox")
