"""Stable, bounded-memory directory pages and recursive filename search."""
import base64
import heapq
import json
import os
import re
import stat
from pathlib import Path

from .files import DIRECTORY_LIMIT, parts, pinned
from .models import SandboxValidationError


def list_directory(root, path='', *, cursor='', query='', runtime_pinned_root=None):
    root = Path(root)
    parts(path)
    if not isinstance(query, str) or len(query) > 256 or not isinstance(cursor, str) or len(cursor) > 8192:
        raise SandboxValidationError('Invalid file search or cursor')
    after = None
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if decoded[:2] != [path, query] or len(decoded) != 5 or not isinstance(decoded[2], bool) or not all(isinstance(v, str) for v in decoded[3:]):
                raise ValueError()
            after = tuple(decoded[2:])
        except (ValueError, TypeError, UnicodeError) as exc:
            raise SandboxValidationError('Invalid file page cursor') from exc
    needle = query.strip().casefold()
    def visit(relative, depth=0):
        target = root.joinpath(*parts(relative))
        with pinned(target, directory=True, runtime_pinned_root=runtime_pinned_root) as fd:
            with os.scandir(target if os.name == 'nt' else fd) as entries:
                for item in entries:
                    if re.fullmatch(r'\.oaw-upload-[a-f0-9]{32}\.part', item.name):
                        continue
                    try:
                        # Windows DirEntry.stat() omits link counts (returns 0).
                        # Use a real lstat before deciding whether a file is safe.
                        info = os.stat(item.path, follow_symlinks=False) if os.name == 'nt' else item.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    blocked = stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
                    directory = stat.S_ISDIR(info.st_mode) and not blocked
                    blocked = blocked or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
                    entry_path = f'{relative}/{item.name}' if relative else item.name
                    if not needle or needle in entry_path.casefold():
                        yield {'name': item.name, 'path': entry_path, 'directory': directory, 'blocked': blocked, 'size': info.st_size}
                    if needle and directory and depth < 64:
                        try:
                            yield from visit(entry_path, depth + 1)
                        except (PermissionError, FileNotFoundError, NotADirectoryError):
                            continue
    def key(entry):
        return (not entry['directory'], entry['path'].casefold(), entry['path'])
    page = heapq.nsmallest(DIRECTORY_LIMIT + 1, (entry for entry in visit(path) if after is None or key(entry) > after), key=key)
    more = len(page) > DIRECTORY_LIMIT
    page = page[:DIRECTORY_LIMIT]
    next_cursor = base64.urlsafe_b64encode(json.dumps([path, query, *key(page[-1])]).encode()).decode() if more else None
    return {'entries': page, 'truncated': more, 'next_cursor': next_cursor}
