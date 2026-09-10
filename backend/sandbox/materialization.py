"""Host-owned runtime bundles, independent of mutable workspaces and resources.

Backends materialize under their private runtime root and expose only the mount
requested by the current command. This module never executes bundle content.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path, PurePath
import re
import shutil
import tempfile
import time

from .models import SandboxSecurityError, SandboxValidationError


def bundle_path(value: str) -> str:
    """One portable, unambiguous relative path on all supported runtimes."""
    if not isinstance(value, str) or not value:
        raise SandboxValidationError("Bundle paths must be non-empty relative paths")
    for part in value.split("/"):
        if (part in {"", ".", ".."} or part.endswith((".", " "))
            or any(ord(c) < 32 or c in '\\:<>"|?*' for c in part)
            or re.fullmatch(r"(?i:con|prn|aux|nul|conin\$|conout\$|clock\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", part)):
            raise SandboxValidationError(
                f"Bundle paths must be safe portable relative paths; invalid path: {value!r}. "
                "Use forward-slash relative paths such as scripts/check.py; "
                "absolute paths, empty/dot/parent segments, reserved names and "
                "non-portable characters are not allowed."
            )
    return value


@dataclass(frozen=True)
class RuntimeBundle:
    key: str
    files: tuple[tuple[str, bytes], ...]
    directories: tuple[str, ...] = ()

    def __post_init__(self):
        bundle_path(self.key)
        files = [bundle_path(path) for path, data in self.files]
        directories = [bundle_path(path) for path in self.directories]
        if any(not isinstance(data, bytes) for _, data in self.files):
            raise SandboxValidationError("Runtime bundle files must contain bytes")
        all_paths = files + directories
        # Include implicit directories when checking case aliases and file parents.
        aliases: dict[str, str] = {}
        file_keys = {path.casefold() for path in files}
        if len(file_keys) != len(files):
            raise SandboxValidationError("Duplicate bundle file paths")
        for path in all_paths:
            parts = path.split("/")
            for i in range(1, len(parts) + 1):
                parent = "/".join(parts[:i])
                key = parent.casefold()
                if key in aliases and aliases[key] != parent:
                    raise SandboxValidationError("Case-ambiguous bundle paths")
                aliases[key] = parent
                if key in file_keys and (i < len(parts) or path in directories):
                    raise SandboxValidationError("Bundle path is both a file and a directory")

    def to_wire(self):
        return {"key": self.key, "files": [[p, base64.b64encode(b).decode("ascii")] for p, b in self.files],
                "directories": list(self.directories)}

    @classmethod
    def from_wire(cls, value):
        return cls(value["key"], tuple((p, base64.b64decode(b, validate=True)) for p, b in value["files"]),
                   tuple(value["directories"]))


@dataclass(frozen=True)
class RuntimeMount:
    bundle: RuntimeBundle
    argument_index: int

    def command(self, argv, root: PurePath) -> tuple[str, ...]:
        if not 0 <= self.argument_index < len(argv):
            raise SandboxValidationError("Invalid runtime file argument")
        path = bundle_path(argv[self.argument_index])
        if path not in dict(self.bundle.files):
            raise SandboxValidationError("Requested file is not in the runtime bundle")
        command = list(argv)
        command[self.argument_index] = str(root.joinpath(*path.split("/")))
        return tuple(command)

    def to_wire(self):
        return {"bundle": self.bundle.to_wire(), "argument_index": self.argument_index}

    @classmethod
    def from_wire(cls, value):
        return cls(RuntimeBundle.from_wire(value["bundle"]), value["argument_index"])


def runtime_tree(root: Path) -> list[Path]:
    """Validate before reading, ACL changes or recursive cleanup; never follow links."""
    if not os.path.lexists(root):
        return []
    pending, result = [root], []
    while pending:
        path = pending.pop()
        stat = path.lstat()
        if path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            raise SandboxSecurityError("Runtime materialization contains a link or reparse point")
        if path.is_dir():
            pending.extend(path.iterdir())
        elif not path.is_file() or stat.st_nlink != 1:
            raise SandboxSecurityError("Runtime materialization contains a non-regular file or hard link")
        result.append(path)
    return result


def _matches_bundle(target: Path, expected: dict[str, bytes], directories: set[str]) -> bool:
    if not target.is_dir():
        return False
    tree = runtime_tree(target)
    actual_files = {p.relative_to(target).as_posix(): p for p in tree if p.is_file()}
    actual_dirs = {p.relative_to(target).as_posix() for p in tree if p.is_dir() and p != target}
    return (actual_files.keys() == expected.keys() and actual_dirs == directories
            and all(actual_files[p].read_bytes() == data for p, data in expected.items()))


def materialize_bundle(runtime_root: Path, bundle: RuntimeBundle) -> Path:
    area = runtime_root / ".oaw"
    runtime_tree(area)
    target = area.joinpath(*bundle.key.split("/"))
    expected = dict(bundle.files)
    directories = set(bundle.directories)
    for path in [*expected, *directories]:
        parts = path.split("/")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
    if _matches_bundle(target, expected, directories):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".materializing-", dir=area))
    try:
        for path in sorted(directories):
            staging.joinpath(*path.split("/")).mkdir(parents=True, exist_ok=True)
        for path, data in bundle.files:
            file = staging.joinpath(*path.split("/"))
            with file.open("xb") as output:
                output.write(data)
            file.chmod(0o755)  # Direct execution; immutability is enforced by the OS mount/ACL.
        if not _matches_bundle(staging, expected, directories):
            raise SandboxValidationError("Runtime filesystem aliases bundle paths")
        if target.exists():
            shutil.rmtree(target)
        # Windows can briefly deny a directory rename while a child file is
        # open elsewhere. Retry only that atomic publication, with a fixed
        # bound; persistent ACL failures still abort before any execution.
        for attempt in range(5):
            try:
                staging.replace(target)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32} or attempt == 4:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def cleanup_materializations(runtime_root: Path) -> None:
    area = runtime_root / ".oaw"
    if runtime_tree(area):
        shutil.rmtree(area)


def bundle_status(runtime_root: Path, bundle: RuntimeBundle) -> dict:
    target = runtime_root / ".oaw"
    # Never grant ACLs or materialize files merely because a window inspected it.
    runtime_tree(target)
    target = target.joinpath(*bundle.key.split("/"))
    directories = set(bundle.directories)
    for path in [*(p for p, _ in bundle.files), *directories]:
        parts = path.split("/")
        directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
    return {"cached": target.exists(), "current": _matches_bundle(target, dict(bundle.files), directories)}
