"""Construction of the sandbox's deliberately small environment block."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

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

# Explicit installer options are ordinary configurable command variables.
# Keep interpreter, install-target and other UV_/PIP_ controls reserved. This
# allowlist does not change the host's separate shared-Python preparation flow.
_INSTALL_OPTION_NAMES = frozenset({
    "UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL",
    "UV_HTTP_TIMEOUT", "UV_REQUEST_TIMEOUT", "PIP_TIMEOUT", "PIP_DEFAULT_TIMEOUT",
    "UV_HTTP_CONNECT_TIMEOUT", "UV_HTTP_RETRIES", "PIP_RETRIES",
    "UV_CONCURRENT_DOWNLOADS", "UV_CONCURRENT_INSTALLS", "UV_OFFLINE",
})

# Application variables are allowed, but startup, loader, transport and isolation
# controls remain host-owned on every backend. Compare case-insensitively so a
# portable profile cannot acquire different authority on Windows.
_RESERVED = frozenset("PATH HOME USER USERNAME USERPROFILE SHELL COMSPEC PATHEXT SYSTEMROOT WINDIR TEMP TMP TMPDIR LOCALAPPDATA APPDATA SANDBOX_RESOURCES ENV BASH_ENV BASHOPTS SHELLOPTS CDPATH IFS GCONV_PATH LOCPATH NLSPATH GETCONF_DIR HOSTALIASES RES_OPTIONS LOCALDOMAIN NODE_OPTIONS NODE_PATH RUBYOPT RUBYLIB PERL5OPT PERL5LIB PERLLIB JAVA_TOOL_OPTIONS JDK_JAVA_OPTIONS _JAVA_OPTIONS CLASSPATH R_ENVIRON R_PROFILE R_ENVIRON_USER R_PROFILE_USER ZDOTDIR FPATH FPATHEXT PROMPT_COMMAND WSLENV WSL_INTEROP WSL_DISTRO_NAME DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS XAUTHORITY".split())
_RESERVED_PREFIXES = ("LD_", "DYLD_", "_RLD", "LDR_", "PYTHON", "BASH_FUNC_", "XDG_", "OAW_", "SANDBOX_", "DOTNET_", "COMPLUS_", "COR_", "VIRTUAL_ENV", "CONDA_", "PIP_", "UV_")
# Explicit host integration settings, never passed into Sandbox processes.
HOST_ENVIRONMENT_NAMES = frozenset({"OAW_MINERU_TOKEN"})


@dataclass(frozen=True)
class _EnvironmentStep:
    value: str
    operation: str = "replace"
    separator: str | None = None


def environment_step(value, text=None):
    """Keep operations structured until the isolated runtime supplies its base."""
    if text is None:
        text = value if isinstance(value, str) else getattr(value, "value", "")
    return {"value": text,
            "operation": getattr(value, "operation", "replace"),
            "separator": getattr(value, "separator", None)}


def _steps(value):
    if isinstance(value, str):
        return [_EnvironmentStep(value)]
    raw = value if isinstance(value, list) else [value]
    if not raw:
        raise SandboxValidationError("Environment operations must not be empty")
    result = []
    for step in raw:
        if not isinstance(step, Mapping) or set(step) - {"value", "operation", "separator"} or not isinstance(step.get("value"), str):
            raise SandboxValidationError("Invalid environment operation")
        operation, separator = step.get("operation", "replace"), step.get("separator")
        if operation not in ("replace", "append", "prepend"):
            raise SandboxValidationError("Invalid environment operation")
        if separator is not None and (not isinstance(separator, str) or "\0" in separator or len(separator) > 100):
            raise SandboxValidationError("Environment separators must be NUL-free strings up to 100 characters")
        result.append(_EnvironmentStep(step["value"], operation, separator))
    return result


def _environment_size(values):
    if sum(len(key.encode('utf-8')) + len(value.encode('utf-8')) + 2 for key, value in values.items()) > 24000:
        raise SandboxValidationError("Command environment exceeds the portable 24 KB limit")


def validate_command_environment(values: Mapping, *, allow_target: bool = True,
                                 allow_host: bool = False) -> dict:
    if not isinstance(values, Mapping):
        raise SandboxValidationError("env must be an object")
    seen, texts = set(), {}
    for key, value in values.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SandboxValidationError("Environment names must be portable identifiers")
        normalized = key.upper()
        if normalized in seen:
            raise SandboxValidationError("Environment names must be unique ignoring case")
        seen.add(normalized)
        steps = _steps(value)
        path_extension = normalized == "PATH" and all(step.operation != "replace" for step in steps)
        allowed = (path_extension or normalized in _SAFE_OVERRIDE_NAMES or normalized in _INSTALL_OPTION_NAMES
                   or (allow_target and key == "OAW_TARGET_CONFIG_JSON")
                   or (allow_host and normalized in HOST_ENVIRONMENT_NAMES))
        if not allowed and (normalized in _RESERVED or normalized.startswith(_RESERVED_PREFIXES)):
            raise SandboxValidationError(f"Environment variable {key!r} is reserved by the sandbox")
        if any("\0" in step.value for step in steps):
            raise SandboxValidationError("Environment values must be NUL-free strings")
        if normalized == "PATH" and any(step.separator is not None or not step.value.strip() for step in steps):
            raise SandboxValidationError("PATH extensions require directories and the runtime's automatic separator")
        if normalized in HOST_ENVIRONMENT_NAMES and any(step.operation != "replace" for step in steps):
            raise SandboxValidationError("Host integration variables do not support environment operations")
        texts[key] = "".join(step.value + (step.separator or "") for step in steps)
    _environment_size(texts)
    return dict(values)


def minimal_windows_environment(
    workspace: Path,
    overrides: Mapping[str, str] | None = None,
    *,
    windows_directory: Path | None = None,
    storage_directory: Path | None = None,
    invocation_env: Mapping[str, object] | None = None,
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
        if normalized not in _SAFE_OVERRIDE_NAMES or not isinstance(value, str):
            raise SandboxValidationError(f"environment variable {key!r} is not in the sandbox allowlist")
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxValidationError("environment keys and values must be NUL-free")
        result[normalized if normalized in _SAFE_OVERRIDE_NAMES else key] = value
    apply_invocation_environment(result, invocation_env, overrides, windows=True)
    return result


def apply_invocation_environment(environment, invocation_env, overrides=None, *, windows=False):
    values = validate_command_environment({} if invocation_env is None else invocation_env)
    if {key.upper() for key in values} & {key.upper() for key in (overrides or {})}:
        raise SandboxValidationError("Invocation environment conflicts with backend overrides")
    resolved, replaced_keys = {}, []
    for key, value in values.items():
        previous_key = next((name for name in environment if name.upper() == key.upper()), None)
        current = environment.get(previous_key, "")
        for step in _steps(value):
            addition = step.value
            separator = step.separator or ""
            if key.upper() == "PATH":
                separator = ";" if windows else ":"
                entries = addition.split("\n") if "\n" in addition else addition.split(separator)
                entries = [entry.strip().rstrip("\r") for entry in entries]
                path_class = PureWindowsPath if windows else PurePosixPath
                if any(not entry or separator in entry or not path_class(entry).is_absolute() for entry in entries):
                    raise SandboxValidationError("PATH extensions must contain absolute directories, one per line or separated by the runtime's path separator")
                addition = separator.join(entries)
            if step.operation == "replace":
                current = addition
            elif not current:
                current = addition
            elif not addition:
                continue
            elif step.operation == "append":
                current += separator + addition
            else:
                current = addition + separator + current
        if previous_key is not None:
            replaced_keys.append(previous_key)
        # Structural runtime keys retain their canonical spelling.
        resolved["PATH" if key.upper() == "PATH" else key] = current
    _environment_size(resolved)
    for key in replaced_keys:
        del environment[key]
    environment.update(resolved)


def windows_environment_block(environment: Mapping[str, str]) -> str:
    """Encode a sorted, double-NUL-terminated CreateProcessW environment."""

    entries: list[str] = []
    for key, value in sorted(environment.items(), key=lambda item: item[0].upper()):
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxValidationError("invalid Windows environment entry")
        entries.append(f"{key}={value}")
    return "\x00".join(entries) + "\x00\x00"


def host_environment_path(raw: str, *, windows: bool | None = None) -> str:
    """Interpret source paths on the backend host, never guess a remote filesystem."""
    windows = os.name == "nt" if windows is None else windows
    if not isinstance(raw, str) or not raw or "\0" in raw:
        raise SandboxValidationError("Choose an absolute file or folder path on the backend host")
    if windows:
        # Accept the standard WSL spelling as an alias for a local Windows drive.
        match = re.fullmatch(r"/mnt/([A-Za-z])(?:/(.*))?", raw)
        if match:
            return str(PureWindowsPath(match[1].upper() + ":/" + (match[2] or "")))
        if raw.startswith("/") and not raw.startswith("//"):
            raise SandboxValidationError("This backend runs on Windows. Use a Windows path or /mnt/<drive>/...; a Linux /home/... path belongs to a specific Linux host or WSL distribution and cannot be inferred.")
    elif PureWindowsPath(raw).drive:
        raise SandboxValidationError("This backend runs on Linux. Select the file or folder at its mounted Linux path; a Windows drive is not automatically available on this host.")
    return raw


def folder_environment(environment, folders, workspace, workspace_access, *, windows=False):
    """Check overlapping grants and supply runtime paths without changing inputs."""
    result = dict(environment)
    seen = set()
    grants = [(Path(workspace), workspace_access)]
    for folder in folders:
        key = next((key for key in result if key.upper() == folder.name.upper()), folder.name)
        value = result.get(key, folder.source)
        validate_command_environment({key: value}, allow_target=False)
        identity = (folder.name.upper(), folder.environment_index)
        if identity in seen:
            raise SandboxValidationError("Folder variable names must be unique ignoring case")
        seen.add(identity)
        source = Path(folder.source)
        for path, access in grants:
            if (source.is_relative_to(path) or path.is_relative_to(source)) and folder.access != access:
                raise SandboxValidationError(f"Folder {folder.name} overlaps another folder or the working folder with different permissions")
        grants.append((source, folder.access))
        accessible = folder.source if windows else folder.linux_path
        if folder.name.upper() == "PATH" and folder.kind != "folder":
            raise SandboxValidationError("PATH extensions require a folder")
        if folder.environment_index is None:
            if key != folder.name:
                result.pop(key, None)
            result[folder.name] = accessible
        else:
            if not isinstance(value, list) or folder.environment_index >= len(value):
                raise SandboxValidationError("Folder operation does not match the command environment")
            value = [dict(step) for step in value]
            value[folder.environment_index]["value"] = accessible
            result[key] = value
    validate_command_environment(result)
    return result

