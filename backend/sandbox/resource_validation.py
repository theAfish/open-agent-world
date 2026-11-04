"""Managed attachment validation shared by the POSIX sandbox backends."""

from pathlib import Path

from .models import SandboxValidationError


def validate_resource_source(
    source: Path, managed_root: Path, *, must_exist: bool = True,
) -> Path:
    if source.is_symlink():
        raise SandboxValidationError("resource symlinks are not allowed")
    try:
        resolved_source = source.resolve(strict=must_exist)
    except OSError as exc:
        raise SandboxValidationError("managed resource does not exist") from exc
    if not resolved_source.is_relative_to(managed_root.resolve()):
        raise SandboxValidationError("path escapes managed storage")
    reserved_directories = ("sandbox-runtimes", "sandboxes", "sandbox-bindings", "runtime")
    if any(resolved_source.is_relative_to(managed_root / name) for name in reserved_directories):
        raise SandboxValidationError("only regular managed resource files can be attached")
    if must_exist and not resolved_source.is_file():
        raise SandboxValidationError("only regular managed resource files can be attached")
    return resolved_source
