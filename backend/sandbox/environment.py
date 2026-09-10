"""Construction of the sandbox's deliberately small environment block."""

from __future__ import annotations

import os
import re
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

# Application variables are allowed, but startup, loader, transport and isolation
# controls remain host-owned on every backend. Compare case-insensitively so a
# portable profile cannot acquire different authority on Windows.
_RESERVED = frozenset("PATH HOME USER USERNAME USERPROFILE SHELL COMSPEC PATHEXT SYSTEMROOT WINDIR TEMP TMP TMPDIR LOCALAPPDATA APPDATA SANDBOX_RESOURCES ENV BASH_ENV BASHOPTS SHELLOPTS CDPATH IFS GCONV_PATH LOCPATH NLSPATH GETCONF_DIR HOSTALIASES RES_OPTIONS LOCALDOMAIN NODE_OPTIONS NODE_PATH RUBYOPT RUBYLIB PERL5OPT PERL5LIB PERLLIB JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS CLASSPATH R_ENVIRON R_PROFILE R_ENVIRON_USER R_PROFILE_USER ZDOTDIR FPATH FPATHEXT PROMPT_COMMAND WSLENV WSL_INTEROP WSL_DISTRO_NAME DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS XAUTHORITY".split())
_RESERVED_PREFIXES = ("LD_", "DYLD_", "_RLD", "LDR_", "PYTHON", "BASH_FUNC_", "XDG_", "OAW_", "SANDBOX_", "DOTNET_", "COMPLUS_", "COR_", "VIRTUAL_ENV", "CONDA_", "PIP_", "UV_")


def validate_command_environment(values: Mapping[str, str], *, allow_target: bool = True) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise SandboxValidationError("env must be an object")
    seen = set()
    for key, value in values.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SandboxValidationError("Environment names must be portable identifiers")
        normalized = key.upper()
        if normalized in seen:
            raise SandboxValidationError("Environment names must be unique ignoring case")
        seen.add(normalized)
        allowed = normalized in _SAFE_OVERRIDE_NAMES or (allow_target and key == "OAW_TARGET_CONFIG_JSON")
        if not allowed and (normalized in _RESERVED or normalized.startswith(_RESERVED_PREFIXES)):
            raise SandboxValidationError(f"Environment variable {key!r} is reserved by the sandbox")
        if not isinstance(value, str) or "\0" in value:
            raise SandboxValidationError("Environment values must be NUL-free strings")
    if sum(len(key.encode('utf-8')) + len(value.encode('utf-8')) + 2 for key, value in values.items()) > 24000:
        raise SandboxValidationError("Command environment exceeds the portable 24 KB limit")
    return dict(values)


def minimal_windows_environment(
    workspace: Path,
    overrides: Mapping[str, str] | None = None,
    *,
    windows_directory: Path | None = None,
    storage_directory: Path | None = None,
    invocation_env: Mapping[str, str] | None = None,
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
    for key, value in validate_command_environment({} if overrides is None else overrides).items():
        normalized = key.upper()
        if normalized not in _SAFE_OVERRIDE_NAMES:
            raise SandboxValidationError(f"environment variable {key!r} is not in the sandbox allowlist")
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxValidationError("environment keys and values must be NUL-free")
        result[normalized if normalized in _SAFE_OVERRIDE_NAMES else key] = value
    apply_invocation_environment(result, invocation_env, overrides)
    return result


def apply_invocation_environment(environment, invocation_env, overrides=None):
    values = validate_command_environment({} if invocation_env is None else invocation_env)
    if {key.upper() for key in values} & {key.upper() for key in (overrides or {})}:
        raise SandboxValidationError("Invocation environment conflicts with backend overrides")
    environment.update(values)


def windows_environment_block(environment: Mapping[str, str]) -> str:
    """Encode a sorted, double-NUL-terminated CreateProcessW environment."""

    entries: list[str] = []
    for key, value in sorted(environment.items(), key=lambda item: item[0].upper()):
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxValidationError("invalid Windows environment entry")
        entries.append(f"{key}={value}")
    return "\x00".join(entries) + "\x00\x00"

