"""Persistent Python environment; only this host-owned manager mutates it.

No backend imports: this module also runs in the trusted WSL worker.
"""
from __future__ import annotations

import asyncio
import errno
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from .python_launchers import repair_python_launchers
from .models import SandboxBusyError, SandboxOperationError, SandboxPreparationError, SandboxValidationError


# Scientific wheels can exceed the old ten-minute wall-clock limit on a slow
# connection. uv still enforces its own connect/read timeouts for stalled I/O.
PACKAGE_INSTALL_TIMEOUT = 1800
RUNTIME_SETUP_TIMEOUT = 600
# Setup, uv bootstrap, dependency preflight, and dependency installation.
PREPARATION_TIMEOUT = 60 + RUNTIME_SETUP_TIMEOUT + 3 * PACKAGE_INSTALL_TIMEOUT + 120
LAUNCHER_VERSION = 1
DARWIN_MANAGED_PYTHON = "3.12"
DARWIN_RUNTIME_VERSION = 1


def validate_requirements(requirements):
    # Index wheels only: never execute package build hooks in the backend.
    pattern = r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:==|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?"
    if not isinstance(requirements, (list, tuple)) or len(requirements) > 100:
        raise ValueError("requirements must contain at most 100 package specifiers")
    if any(not isinstance(r, str) or len(r) > 500 or not re.fullmatch(pattern, r) for r in requirements):
        raise ValueError("Use index package names with optional extras/version constraints; paths, URLs and installer options are unsupported")
    return list(requirements)


@contextmanager
def mutation_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "mutation.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise SandboxBusyError("Shared Python is busy installing packages. Inspect Sandbox operations and wait for the installation before retrying; this command has not started.") from exc
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


async def finish_thread(function, *args):
    # Cancellation must not release ownership while an installer still runs.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class SharedPythonRuntime:
    kind = "python"

    def __init__(self, data_root: Path):
        self.root = Path(data_root).resolve() / "runtime" / "python"
        self.venv = self.root / "venv"
        self.base = self.root / "base"
        self.bin = self.venv / ("Scripts" if os.name == "nt" else "bin")
        self.python = self.bin / ("python.exe" if os.name == "nt" else "python")

    @property
    def installer_python(self) -> Path:
        if os.name == "nt":
            return self.base / "python.exe"
        if sys.platform == "darwin":
            managed = self._darwin_base_python()
            if managed is None:
                raise SandboxPreparationError("The managed macOS Python runtime has not been prepared")
            return managed
        return Path("/usr/bin/python3")

    def _uv(self):
        managed = self.root / "tools" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        return str(managed) if managed.is_file() else shutil.which("uv")

    def _darwin_base_python(self) -> Path | None:
        """Locate CPython inside OAW's private uv installation directory."""
        if not self.base.is_dir():
            return None
        names = (f"python{DARWIN_MANAGED_PYTHON}", "python3", "python")
        for name in names:
            candidates = sorted(self.base.glob(f"cpython-{DARWIN_MANAGED_PYTHON}-*/bin/{name}"))
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
        return None

    def _ensure_uv(self) -> str:
        """Return uv, bootstrapping a private copy with the backend Python."""
        uv = self._uv()
        if uv:
            return uv
        bootstrap_python = Path(sys.executable)
        if not bootstrap_python.is_file():
            raise SandboxPreparationError(
                "Cannot bootstrap uv: the Python interpreter running the OAW backend is unavailable")
        tools = self.root / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        self._run([bootstrap_python, "-I", "-m", "pip", "--isolated", "install",
            "--only-binary", ":all:", "--no-deps", "--target", tools, "uv"])
        uv = self._uv()
        if not uv:
            raise SandboxPreparationError(
                f"uv was installed but its executable was not found below {tools}")
        return uv

    def _ensure_darwin(self) -> None:
        """Provision self-contained CPython and a venv below OAW storage."""
        uv = self._ensure_uv()
        self.base.mkdir(parents=True, exist_ok=True)
        self._run([uv, "--no-config", "--cache-dir", self.root / "cache",
            "python", "install", "--install-dir", self.base, "--no-bin",
            DARWIN_MANAGED_PYTHON])
        base = self._darwin_base_python()
        if base is None:
            raise SandboxPreparationError(
                f"uv did not create a usable CPython {DARWIN_MANAGED_PYTHON} below {self.base}")
        self._run([uv, "--no-config", "--cache-dir", self.root / "cache",
            "venv", "--python", base, "--no-python-downloads", "--clear", self.venv])

    def snapshot(self):
        """Read bounded progress without acquiring a mutation lock or running code.

        Logs are observations, never evidence that an old worker is still alive.
        Operation receipts own liveness and restart recovery.
        """
        def tail(name, limit):
            try:
                with (self.root / name).open("rb") as stream:
                    stream.seek(max(0, stream.seek(0, 2) - limit))
                    return stream.read().decode("utf-8", "replace")
            except OSError:
                return ""
        latest = {}
        for line in reversed(tail("install.log", 65536).splitlines()):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    latest = value
                    break
            except ValueError:
                continue
        return {"last_install_state": latest.get("state"),
            "last_install_started_at": latest.get("time"),
            "last_install_elapsed_seconds": latest.get("elapsed_seconds"),
            "output_tail": tail("install-output.log", 8192),
            "note": "Progress observation only; use operation receipts for current execution status."}

    def _run(self, argv):
        environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "PIP_", "UV_")) and k.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX"}}
        output_path = self.root / "install-output.log"
        started = time.monotonic()
        record = {"time": time.time(), "argv": list(map(str, argv))}
        timeout = PACKAGE_INSTALL_TIMEOUT if "install" in argv else RUNTIME_SETUP_TIMEOUT
        with (self.root / "install.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps({**record, "state": "running", "output": str(output_path)}) + "\n")

        def output_tail():
            with output_path.open("rb") as output:
                output.seek(max(0, output.seek(0, 2) - 16000))
                return output.read().decode("utf-8", "replace")

        try:
            # Write progress as it arrives, including when the installer times out.
            # Pipes discarded TimeoutExpired's captured output and hid the phase
            # that stalled (resolution, download, unpacking, or installation).
            with output_path.open("wb") as output:
                result = subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
                    cwd=self.root, env=environment,
                    **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        except (OSError, subprocess.TimeoutExpired) as exc:
            tail = output_tail()
            with (self.root / "install.log").open("a", encoding="utf-8") as log:
                log.write(json.dumps({**record, "state": "failed", "elapsed_seconds": time.monotonic() - started,
                    "error": str(exc), "output": tail}) + "\n")
            raise SandboxPreparationError(f"Shared Python preparation failed: {exc}. {tail[-2000:]} See {self.root / 'install.log'}") from exc
        tail = output_tail()
        with (self.root / "install.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps({**record, "elapsed_seconds": time.monotonic() - started,
                "state": "ready" if result.returncode == 0 else "failed",
                "exit_code": result.returncode, "output": tail}) + "\n")
        if result.returncode:
            raise SandboxPreparationError(f"Shared Python preparation failed: {tail[-2000:]} (see {self.root / 'install.log'})")

    def _ensure(self):
        ready = self.root / "ready.json"
        if ready.is_file() and self.python.is_file() and (self.venv / "pyvenv.cfg").is_file():
            try:
                metadata = json.loads(ready.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metadata = {}
            if sys.platform != "darwin" or (
                metadata.get("darwin_runtime_version") == DARWIN_RUNTIME_VERSION
                and self._darwin_base_python() is not None
            ):
                return
        if sys.platform == "darwin":
            self._ensure_darwin()
            self._write_ready(launchers_ready=False)
            return
        base = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else "/usr/bin/python3"
        if os.name == "nt":
            # Copy only the interpreter and standard library, never site-packages
            # or Scripts. AppContainers receive no ACL on the host installation.
            source = Path(sys.base_prefix)
            self.base.mkdir(exist_ok=True)
            for pattern in ("python*.exe", "python*.dll", "python*.zip", "vcruntime*.dll"):
                for item in source.glob(pattern):
                    shutil.copy2(item, self.base / item.name)
            for name in ("Lib", "DLLs"):
                if (source / name).is_dir():
                    shutil.copytree(source / name, self.base / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "*.pth", "sitecustomize.py", "usercustomize.py"))
            base = self.base / "python.exe"
        uv = self._uv()
        if uv:
            self._run([uv, "--no-config", "--cache-dir", self.root / "cache", "venv", "--python", base, "--no-python-downloads", self.venv])
        else:
            self._run([base, "-I", "-m", "venv", self.venv])
            # This is still a pristine venv, before any user packages. Bootstrap
            # a standalone installer outside the execution environment so future
            # installations never run Python code or .pth files from that venv.
            self._run([self.python, "-I", "-m", "pip", "--isolated", "install",
                "--only-binary", ":all:", "--no-deps", "--target", self.root / "tools", "uv"])
        ready.write_text(json.dumps({"python": str(self.python)}), encoding="utf-8")

    def prepare_sync(self, requirements=(), bootstrap_key=None):
        requirements = validate_requirements(requirements)
        ready = self.root / "ready.json"
        try:
            metadata = json.loads(ready.read_text(encoding="utf-8")) if ready.is_file() else {}
        except (OSError, ValueError):
            metadata = {}
        runtime_current = (sys.platform != "darwin" or (
            metadata.get("darwin_runtime_version") == DARWIN_RUNTIME_VERSION
            and self._darwin_base_python() is not None
        ))
        if (not requirements and self.python.is_file() and runtime_current
            and metadata.get("launcher_version") == LAUNCHER_VERSION):
            return {"kind": self.kind, "python": str(self.python), "requirements": []}
        with mutation_lock(self.root):
            existed = self.python.is_file() and runtime_current
            self._ensure()
            self._repair_launchers()
            receipts_path = self.root / "bootstrap.json"
            receipts = json.loads(receipts_path.read_text()) if existed and receipts_path.exists() else {}
            if requirements and (bootstrap_key is None or receipts.get(bootstrap_key) != requirements):
                uv = self._uv()
                if uv:
                    arguments = [uv, "--no-config", "--cache-dir", self.root / "cache", "pip", "install",
                        "--python", self.installer_python, "--prefix", self.venv,
                        "--only-binary", ":all:", *requirements]
                    # Resolve the complete request before modifying working
                    # packages. Use the same mutation lock across both phases.
                    self._run([*arguments, "--dry-run"])
                    # Probe only the clean base interpreter. Querying the shared
                    # venv could execute a package's .pth/sitecustomize on the host.
                    # A crash mid-install must trigger repair on the next call.
                    self._write_ready(launchers_ready=False)
                    try:
                        self._run(arguments)
                    finally:
                        # A failed install may still have written some launchers.
                        self._repair_launchers()
                else:
                    raise SandboxPreparationError("Install uv on the execution platform to manage shared Python packages safely")
                # Any mutation invalidates older aggregate receipts, including
                # interactive package installs between two bootstrap passes.
                receipts = {}
                if bootstrap_key is not None:
                    receipts[bootstrap_key] = requirements
                temporary = receipts_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(receipts), encoding="utf-8")
                temporary.replace(receipts_path)
        return {"kind": self.kind, "python": str(self.python), "requirements": requirements}

    def _repair_launchers(self):
        repair_python_launchers(self.bin, self.installer_python, self.python)
        self._write_ready(launchers_ready=True)

    def _write_ready(self, *, launchers_ready):
        ready = self.root / "ready.json"
        temporary = ready.with_suffix(".tmp")
        payload = {"python": str(self.python),
            "launcher_version": LAUNCHER_VERSION if launchers_ready else 0}
        if sys.platform == "darwin":
            payload["darwin_runtime_version"] = DARWIN_RUNTIME_VERSION
            payload["base_python"] = str(self.installer_python)
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(ready)

    async def prepare(self, requirements=(), bootstrap_key=None):
        try:
            return await finish_thread(self.prepare_sync, requirements, bootstrap_key)
        except SandboxOperationError:
            raise
        except ValueError as exc:
            raise SandboxValidationError(str(exc)) from exc
        except OSError as exc:
            raise SandboxPreparationError(str(exc)) from exc

    def environment(self, environment):
        environment.update(PATH=str(self.bin) + os.pathsep + environment["PATH"],
            VIRTUAL_ENV=str(self.venv), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")

    def command(self, command):
        if command[0].lower() in {"python", "python3", "python.exe", "python3.exe", "/usr/bin/python3"}:
            return (str(self.python), *command[1:])
        return command
