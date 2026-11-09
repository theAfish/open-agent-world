"""Command-scoped workspace access, reusing each runtime's native boundary."""
from pathlib import Path
from .files import parts, working_path
from .models import ResourceAccess, SandboxSecurityError


def writable_path(workspace, directory):
    """None keeps legacy/global access; empty means no workspace writes."""
    if directory is None or directory == '':
        return None
    path = working_path(workspace, directory)
    # A pre-existing hardlink could otherwise expose a read-only sibling inode
    # through the writable submount/ACL. Never follow symlinks while checking.
    import os
    import stat
    for root, directories, files in os.walk(path, followlinks=False):
        for name in files:
            metadata = (Path(root) / name).lstat()
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
                raise SandboxSecurityError('Writable workspace contains a hardlinked file; copy it to an independent file first')
    return path


def mount_access(workspace, source, access, directory):
    if directory is None or ResourceAccess(access) == ResourceAccess.READ_ONLY:
        return ResourceAccess(access)
    root = Path(workspace)
    target = Path(source)
    allowed = root.joinpath(*parts(directory)) if directory else None
    if target.is_relative_to(root) and (allowed is None or not target.is_relative_to(allowed)):
        return ResourceAccess.READ_ONLY
    if root.is_relative_to(target):
        raise SandboxSecurityError('A writable mount may not contain a restricted workspace')
    return ResourceAccess(access)
