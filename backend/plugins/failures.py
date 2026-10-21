"""Exceptions that must never be downgraded at an optional plugin boundary."""
import errno
import sqlite3

from backend.errors import PermissionDeniedError, UnsafePathError


class PluginIntegrityError(RuntimeError):
    """Plugin cleanup or persistence is uncertain; continuing is unsafe."""


def raise_if_unsafe(error, *, allow_revocation=False):
    # Inspect chained failures too: wrapping a database error in RuntimeError
    # must not turn corruption into an ordinary unavailable plugin.
    from backend.sandbox.models import SandboxSecurityError
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (sqlite3.Error, MemoryError, PluginIntegrityError,
                                UnsafePathError, SandboxSecurityError)):
            raise error
        if not allow_revocation and isinstance(current, PermissionDeniedError):
            raise error
        if isinstance(current, OSError) and current.errno in {errno.EIO, errno.ENOSPC, errno.EROFS}:
            raise error
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
