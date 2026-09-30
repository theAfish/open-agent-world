"""Desktop folder selection without blocking the server's event loop."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import suppress
from pathlib import Path

from backend.errors import ConflictError, RuntimeUnavailableError

_dialog_lock = threading.Lock()


async def pick_folder(initial_path: str | None) -> str | None:
    return await _pick_path(initial_path, kind="folder")


async def pick_file(initial_path: str | None) -> str | None:
    return await _pick_path(initial_path, kind="file")


async def _pick_path(initial_path: str | None, *, kind: str) -> str | None:
    if not _dialog_lock.acquire(blocking=False):
        raise ConflictError("A path selection window is already open. Finish or cancel it first.")
    process = None
    try:
        initial = Path(initial_path) if initial_path else Path.home()
        if kind == "file" and initial.is_file():
            initial = initial.parent
        if not initial.is_absolute() or not initial.is_dir():
            initial = Path.home()
        if os.name == "nt":
            command = [
                str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
                "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                str(Path(__file__).with_name("folder_picker.ps1")),
            ]
        else:
            command = [sys.executable, str(Path(__file__).with_name("folder_picker_desktop.py"))]
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        # Paths are data, never interpolated into shell commands.
        output, _ = await asyncio.wait_for(process.communicate(
            json.dumps({"initial_path": str(initial), **({"kind": "file"} if kind == "file" else {})}).encode("utf-8")
        ), timeout=300)
        if process.returncode:
            raise RuntimeUnavailableError(f"Could not open the {kind} picker on the backend desktop. Enter the path manually.")
        selected = json.loads(output.decode("utf-8-sig"))
        if selected is None:
            return None
        if not isinstance(selected, str) or not Path(selected).is_absolute() or not (Path(selected).is_file() if kind == "file" else Path(selected).is_dir()):
            raise RuntimeUnavailableError(f"The picker did not return an existing absolute {kind}.")
        return str(Path(selected))
    except TimeoutError as exc:
        raise RuntimeUnavailableError("Path selection timed out. Click Browse to try again.") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeUnavailableError("Path selection is unavailable on this desktop. Enter the path manually.") from exc
    finally:
        try:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        finally:
            _dialog_lock.release()
