"""Discover native clients without reading tokens or desktop conversation data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from open_agent_world.plugin_api import AgentRuntimeError


def _run(argv: list[str], timeout: int = 8) -> str:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **options).stdout.strip()


def desktop_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.name == "nt":
        # Only select process metadata. Never read complete command lines, tokens,
        # or desktop IPC internals. Prefer the running desktop's native runtime.
        script = """$p=Get-CimInstance Win32_Process -Filter \"Name='codex.exe' OR Name='ChatGPT.exe'\";
$p | Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress"""
        try:
            rows = json.loads(_run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]))
            rows = rows if isinstance(rows, list) else [rows]
            desktop_ids = {row["ProcessId"] for row in rows if "openai.codex_" in (row.get("ExecutablePath") or "").lower()}
            candidates.extend(Path(row["ExecutablePath"]) for row in rows
                              if row.get("ParentProcessId") in desktop_ids and row.get("ExecutablePath")
                              and Path(row["ExecutablePath"]).name.lower() == "codex.exe")
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            # Process enumeration may be disallowed. Installed native runtimes
            # remain discoverable; this does not imply a live desktop connection.
            pass
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "OpenAI/Codex/bin"
        if root.is_dir():
            candidates.extend(sorted(root.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True))
    elif sys.platform == "darwin":
        candidates.extend([Path("/Applications/Codex.app/Contents/Resources/codex"),
                           Path.home() / "Applications/Codex.app/Contents/Resources/codex"])
    return list(dict.fromkeys(path.resolve() for path in candidates if path.is_file()))


def discover(source: str = "auto", command: str = "") -> dict[str, str]:
    if source not in {"auto", "desktop", "cli", "manual"}:
        raise AgentRuntimeError("Unknown Codex client source")
    executable: str | None = None
    actual_source = source
    if source in {"auto", "desktop"}:
        candidates = desktop_candidates()
        if candidates:
            executable, actual_source = str(candidates[0]), "desktop"
        elif source == "desktop":
            raise AgentRuntimeError("Desktop Codex runtime was not found. Open the desktop App or choose a native executable manually.")
    if source == "manual":
        executable = shutil.which(command) if command.strip() else None
    elif executable is None:
        executable = shutil.which(os.environ.get("OAW_CODEX_COMMAND", "codex"))
        actual_source = "cli"
    if not executable:
        raise AgentRuntimeError("No native Codex runtime found. Install/open the desktop App or configure codex.exe manually.")
    if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise AgentRuntimeError("Select native codex.exe, not a shell wrapper.")
    try:
        version = _run([executable, "--version"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise AgentRuntimeError("The selected Codex executable could not report its version") from exc
    if not version.startswith("codex-cli "):
        raise AgentRuntimeError("Selected executable is not a recognized Codex CLI")
    return {"source": actual_source, "executable": str(Path(executable).resolve()), "version": version,
            "connection": "New OAW session using this local runtime; desktop chat is not attached"}
