"""Construction of the sandbox's deliberately small environment block."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .models import SandboxValidationError


_SAFE_OVERRIDE_NAMES = frozenset(
    {
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "TZ",
    }
)


def minimal_windows_environment(
    workspace: Path,
    overrides: Mapping[str, str] | None = None,
    *,
    windows_directory: Path | None = None,
    storage_directory: Path | None = None,
) -> dict[str, str]:
    """Return a non-secret environment without copying ``os.environ``.

    Only structural Windows values are synthesized.  In particular, API keys,
    cloud credentials, SSH configuration, proxy credentials, and the host user
    profile are never inherited.
    """

    windows = windows_directory or Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system32 = windows / "System32"
    storage = storage_directory or workspace
    temp = storage / ".tmp"
    result = {
        "COMSPEC": str(system32 / "cmd.exe"),
        "LOCALAPPDATA": str(storage),
        "PATH": f"{system32};{windows}",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "SystemRoot": str(windows),
        "TEMP": str(temp),
        "TMP": str(temp),
        "WINDIR": str(windows),
    }
    if storage_directory is not None:
        result["SANDBOX_RESOURCES"] = str(storage)
    for key, value in (overrides or {}).items():
        normalized = key.upper()
        if normalized not in _SAFE_OVERRIDE_NAMES:
            raise SandboxValidationError(
                f"environment variable {key!r} is not in the sandbox allowlist"
            )
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxValidationError("environment keys and values must be NUL-free")
        result[normalized] = value
    return result


def windows_environment_block(environment: Mapping[str, str]) -> str:
    """Encode a sorted, double-NUL-terminated CreateProcessW environment."""

    entries: list[str] = []
    for key, value in sorted(environment.items(), key=lambda item: item[0].upper()):
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxValidationError("invalid Windows environment entry")
        entries.append(f"{key}={value}")
    return "\x00".join(entries) + "\x00\x00"

