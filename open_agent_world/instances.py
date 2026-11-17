"""Discover local OAW launchers without starting a server or probing ports."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4


def instance_directory() -> Path:
    configured = os.environ.get("OPEN_AGENT_WORLD_INSTANCE_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "OpenAgentWorld" / "instances"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenAgentWorld" / "instances"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "open-agent-world" / "instances"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "open-agent-world" / "instances"


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied still denotes a live process.
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def list_instances() -> list[dict]:
    """Return well-formed, potentially live registrations; callers verify HTTP identity.

    PID reuse cannot prove an instance's identity. A client must compare the
    registration ID against GET /api/instance before selecting a discovered URL.
    """
    result = []
    for path in sorted(instance_directory().glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("id") != path.stem:
                continue
            pid = record.get("pid")
            if type(pid) is not int or pid < 1 or not _process_exists(pid):
                continue
            url = urlsplit(record["url"])
            if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
                    or url.username or url.password or url.path or url.query or url.fragment):
                continue
            if record.get("mode") not in {"production", "development", "preview"}:
                continue
            if not isinstance(record.get("version"), str) or not isinstance(record.get("data_root"), str):
                continue
            if record.get("profile") is not None and not isinstance(record["profile"], str):
                continue
            result.append(record)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


@contextmanager
def registered_instance(*, url: str, mode: str, profile: str | None, version: str, data_root: Path):
    """Keep one registration for precisely one running server lifetime."""
    directory = instance_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = uuid4().hex
    path = directory / f"{identity}.json"
    temporary = path.with_suffix(".tmp")
    record = {"id": identity, "pid": os.getpid(), "url": url, "mode": mode,
              "profile": profile, "version": version, "data_root": str(data_root)}
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        yield record
    finally:
        path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
