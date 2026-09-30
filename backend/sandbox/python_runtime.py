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
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

from .python_launchers import repair_python_launchers
from .models import SandboxBusyError, SandboxOperationError, SandboxPreparationError, SandboxValidationError


# Scientific wheels can exceed the old ten-minute wall-clock limit on a slow
# connection. uv still enforces its own connect/read timeouts for stalled I/O.
PACKAGE_INSTALL_TIMEOUT = 1800
RUNTIME_SETUP_TIMEOUT = 600
# A slow download may take time, but several minutes without any changed
# output/cache/staging file is a stuck bootstrap, not useful progress.
DARWIN_PYTHON_DOWNLOAD_IDLE_TIMEOUT = 600
DARWIN_PACKAGE_INSTALL_IDLE_TIMEOUT = 300
# Setup, uv bootstrap, dependency preflight, and dependency installation.
PREPARATION_TIMEOUT = 60 + RUNTIME_SETUP_TIMEOUT + 3 * PACKAGE_INSTALL_TIMEOUT + 120
LAUNCHER_VERSION = 1
DARWIN_MANAGED_PYTHON = "3.12"
DARWIN_RUNTIME_VERSION = 1


class _InstallationStalled(TimeoutError):
    """The managed interpreter download made no observable progress."""


def validate_requirements(requirements):
    # Index wheels only: never execute package build hooks in the backend.
    pattern = r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:==|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?"
    if not isinstance(requirements, (list, tuple)) or len(requirements) > 100:
        raise ValueError("requirements must contain at most 100 package specifiers")
    if any(not isinstance(r, str) or len(r) > 500 or not re.fullmatch(pattern, r) for r in requirements):
        raise ValueError("Use index package names with optional extras/version constraints; paths, URLs and installer options are unsupported")
    return list(requirements)


@contextmanager
def mutation_lock(root, *, wait_seconds=60):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "mutation.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + wait_seconds
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
                    raise SandboxBusyError(
                        "Shared Python is locked by another preparation. This command has not started; "
                        "check whether another OAW backend is preparing Python, then retry after it finishes."
                    ) from exc
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

    def __init__(self, data_root: Path, *, darwin_store: Path | None = None):
        self.root = Path(data_root).resolve() / "runtime" / "python"
        self.venv = self.root / "venv"
        self.base = self.root / "base"
        self.bin = self.venv / ("Scripts" if os.name == "nt" else "bin")
        self.python = self.bin / ("python.exe" if os.name == "nt" else "python")
        # The distribution is machine/user-level, but the venv remains scoped
        # to this OAW profile. A new checkout must not redownload CPython.
        self.darwin_store = (Path(darwin_store) if darwin_store is not None else
            Path.home() / "Library" / "Application Support" / "OpenAgentWorld" /
            "shared-python" / f"cpython-{DARWIN_MANAGED_PYTHON}-{platform.machine().lower()}-v{DARWIN_RUNTIME_VERSION}").resolve()

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

    def _uv_cache(self) -> Path:
        # CPython and wheel downloads survive a development-profile reset or
        # a new source checkout; uv itself manages concurrent cache access.
        return self.darwin_store / "cache" if sys.platform == "darwin" else self.root / "cache"

    def _darwin_base_python(self, root: Path | None = None) -> Path | None:
        """Locate CPython inside OAW's private uv installation directory."""
        root = self.base if root is None else root
        if not root.is_dir():
            return None
        names = (f"python{DARWIN_MANAGED_PYTHON}", "python3", "python")
        for name in names:
            candidates = sorted(root.glob(f"cpython-{DARWIN_MANAGED_PYTHON}-*/bin/{name}"))
            for candidate in candidates:
                stdlib = candidate.parent.parent / "lib" / f"python{DARWIN_MANAGED_PYTHON}" / "os.py"
                if candidate.is_file() and stdlib.is_file():
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

    def _darwin_swap_pending(self) -> bool:
        return any((self.root / name).exists() for name in (
            "base.previous", "venv.previous", "darwin-bootstrap.commit",
        ))

    def _recover_darwin_swap(self) -> None:
        previous_base = self.root / "base.previous"
        previous_venv = self.root / "venv.previous"
        commit_marker = self.root / "darwin-bootstrap.commit"
        if commit_marker.exists() and (
            self._darwin_base_python() is None or not self.python.is_file()
            or not (self.venv / "pyvenv.cfg").is_file()
        ):
            # A marker without a complete new pair cannot authorize removal
            # of the only recoverable interpreter and venv.
            commit_marker.unlink()
        if commit_marker.exists():
            # The new venv was created before an interrupted cleanup. Finish
            # committing it; rolling back just one old directory would mix
            # interpreters and virtual environments from different versions.
            if previous_base.exists():
                shutil.rmtree(previous_base)
            if previous_venv.exists():
                shutil.rmtree(previous_venv)
            commit_marker.unlink()
        else:
            # A terminated bootstrap may have left the old pair parked here.
            # Restore it before attempting another download.
            if previous_base.exists():
                if self.base.exists():
                    shutil.rmtree(self.base)
                previous_base.rename(self.base)
            if previous_venv.exists():
                if self.venv.exists():
                    shutil.rmtree(self.venv)
                previous_venv.rename(self.venv)

    def _cached_darwin_base(self, uv: str | None = None) -> Path:
        """Keep one verified CPython distribution across profiles/checkouts.

        The cache is never mounted into Seatbelt. Each profile copies it into
        its own read-only sandbox runtime path and creates its own venv there.
        """
        store = self.darwin_store
        base, staging, previous = (store / name for name in
            ("base", "base.partial", "base.previous"))
        # Another OAW checkout may be downloading into this store. The lock
        # must outlive that download, not expire at the normal 60-second limit.
        with mutation_lock(store, wait_seconds=PACKAGE_INSTALL_TIMEOUT + 60):
            if self._darwin_base_python(base) is not None:
                for leftover in (staging, previous):
                    if leftover.exists():
                        shutil.rmtree(leftover, ignore_errors=True)
                return base
            if self._darwin_base_python(previous) is not None:
                if base.exists():
                    shutil.rmtree(base)
                previous.rename(base)
                return base
            if previous.exists():
                shutil.rmtree(previous)
            if staging.exists():
                shutil.rmtree(staging)
            try:
                # Seed from an existing profile when upgrading an older OAW
                # checkout. No network is needed if it already has Python.
                if self._darwin_base_python() is not None:
                    # uv distributions can contain relative links which reach
                    # outside this tree. Preserve their contents, not link
                    # text that will break when copied to a new location.
                    shutil.copytree(self.base, staging, symlinks=False,
                                    ignore_dangling_symlinks=True)
                else:
                    if uv is None:
                        uv = self._ensure_uv()
                    staging.mkdir(parents=True)
                    self._run([uv, "--no-config", "--cache-dir", store / "cache",
                        "python", "install", "--install-dir", staging, "--no-bin",
                        DARWIN_MANAGED_PYTHON])
                if self._darwin_base_python(staging) is None:
                    raise SandboxPreparationError(
                        f"No usable CPython {DARWIN_MANAGED_PYTHON} was prepared below {staging}")
                moved_old = False
                try:
                    if base.exists():
                        base.rename(previous)
                        moved_old = True
                    staging.rename(base)
                except Exception:
                    if moved_old and not base.exists():
                        previous.rename(base)
                    raise
                if previous.exists():
                    shutil.rmtree(previous, ignore_errors=True)
                return base
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)

    def _ensure_darwin(self) -> None:
        """Provision self-contained CPython and a venv below OAW storage.

        A verified download is swapped in with rollback for the previous base
        and venv. The venv is built from the interpreter's final path: uv
        records that absolute path, so a staging path would break it.
        """
        self._recover_darwin_swap()
        previous_base = self.root / "base.previous"
        previous_venv = self.root / "venv.previous"
        commit_marker = self.root / "darwin-bootstrap.commit"
        uv = self._ensure_uv()
        cached_base = self._cached_darwin_base(uv)
        staging = self.root / "base.partial"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            shutil.copytree(cached_base, staging, symlinks=False,
                            ignore_dangling_symlinks=True)
            if self._darwin_base_python(staging) is None:
                raise SandboxPreparationError(
                    f"Cached CPython {DARWIN_MANAGED_PYTHON} is not usable below {staging}")
            moved_base = moved_venv = swapped_base = venv_started = False
            try:
                if self.base.exists():
                    self.base.rename(previous_base)
                    moved_base = True
                if self.venv.exists():
                    self.venv.rename(previous_venv)
                    moved_venv = True
                staging.rename(self.base)
                swapped_base = True
                base = self._darwin_base_python()
                if base is None:
                    raise SandboxPreparationError(
                        f"the swapped CPython {DARWIN_MANAGED_PYTHON} is not usable below {self.base}")
                # uv records the interpreter path in this venv. Build it at
                # its final location, but retain the old pair until it works.
                venv_started = True
                self._run([uv, "--no-config", "--cache-dir", self._uv_cache(),
                    "venv", "--python", base, "--no-python-downloads", "--clear", self.venv])
                if not self.python.is_file() or not (self.venv / "pyvenv.cfg").is_file():
                    raise SandboxPreparationError(
                        f"uv did not create a usable macOS Python venv below {self.venv}")
                commit_marker.write_text("ready", encoding="utf-8")
            except Exception:
                try:
                    if venv_started and self.venv.exists():
                        shutil.rmtree(self.venv)
                    if swapped_base and self.base.exists():
                        shutil.rmtree(self.base)
                    if moved_base:
                        previous_base.rename(self.base)
                    if moved_venv:
                        previous_venv.rename(self.venv)
                except OSError as rollback_error:
                    raise SandboxPreparationError(
                        f"Could not restore the previous macOS Python environment; "
                        f"inspect {previous_base} and {previous_venv}: {rollback_error}"
                    ) from rollback_error
                raise
            if previous_base.exists():
                shutil.rmtree(previous_base)
            if previous_venv.exists():
                shutil.rmtree(previous_venv)
            commit_marker.unlink()
        finally:
            shutil.rmtree(staging, ignore_errors=True)

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
        arguments = latest.get("argv") or ()
        if "python" in arguments and "install" in arguments:
            phase = "python_download"
        elif "venv" in arguments:
            phase = "venv_creation"
        elif "pip" in arguments and "install" in arguments:
            phase = "package_install"
        else:
            phase = "preparation"
        started_at = latest.get("time")
        progress_at = self._progress_mtime() if latest.get("state") == "running" else 0.0
        if not isinstance(started_at, (int, float)):
            started_at = None
        if started_at is not None:
            progress_at = max(progress_at, started_at)
        try:
            ready = json.loads((self.root / "ready.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ready = {}
        usable = (self.python.is_file() and (self.venv / "pyvenv.cfg").is_file()
                  and ready.get("launcher_version") == LAUNCHER_VERSION)
        if sys.platform == "darwin":
            usable = (usable and ready.get("darwin_runtime_version") == DARWIN_RUNTIME_VERSION
                      and self._darwin_base_python() is not None)
        return {"last_install_state": latest.get("state"),
            "ready": usable,
            "distribution_cached": (self._darwin_base_python(self.darwin_store / "base") is not None
                                    if sys.platform == "darwin" else None),
            "last_install_started_at": latest.get("time"),
            "last_install_elapsed_seconds": latest.get("elapsed_seconds"),
            "phase": phase if latest.get("state") == "running" else None,
            "seconds_without_progress": (max(0, round(time.time() - progress_at))
                                         if latest.get("state") == "running" else None),
            "idle_limit_seconds": ((DARWIN_PYTHON_DOWNLOAD_IDLE_TIMEOUT if phase == "python_download"
                                    else DARWIN_PACKAGE_INSTALL_IDLE_TIMEOUT)
                                   if latest.get("state") == "running" and "install" in arguments else None),
            "output_tail": tail("install-output.log", 8192),
            "note": "Progress observation only; use operation receipts for current execution status."}

    def _progress_mtime(self) -> float:
        """Observe files that uv can update even when its terminal is quiet."""
        latest = 0.0
        roots = [self.root / "install-output.log", self.root / "cache", self.root / "base.partial",
                 self.root / "tools", self.venv]
        if sys.platform == "darwin":
            roots.extend((self.darwin_store / "cache", self.darwin_store / "base.partial"))
        for root in roots:
            if root.is_file():
                try:
                    latest = max(latest, root.stat().st_mtime)
                except OSError:
                    pass
            elif root.is_dir():
                for directory, _, names in os.walk(root):
                    for name in names:
                        try:
                            latest = max(latest, (Path(directory) / name).stat().st_mtime)
                        except OSError:
                            pass
        return latest

    def _run_darwin_install(self, argv, output, environment, timeout):
        """Bound silent macOS downloads independently of the overall deadline."""
        idle_limit = (DARWIN_PYTHON_DOWNLOAD_IDLE_TIMEOUT if "python" in argv
                      else DARWIN_PACKAGE_INSTALL_IDLE_TIMEOUT)
        process = subprocess.Popen([str(a) for a in argv], stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, cwd=self.root, env=environment,
            start_new_session=True)
        started = last_progress = time.monotonic()
        marker = self._progress_mtime()
        try:
            while True:
                try:
                    code = process.wait(timeout=2)
                    return subprocess.CompletedProcess(argv, code)
                except subprocess.TimeoutExpired:
                    now = time.monotonic()
                    updated = self._progress_mtime()
                    if updated > marker:
                        marker, last_progress = updated, now
                    if now - last_progress >= idle_limit:
                        raise _InstallationStalled(
                            f"Managed macOS Python installation made no observable progress for "
                            f"{idle_limit}s; check the network or "
                            f"{self.root / 'install-output.log'} before retrying")
                    if now - started >= timeout:
                        raise subprocess.TimeoutExpired(argv, timeout)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()

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
                if sys.platform == "darwin" and "install" in argv:
                    result = self._run_darwin_install(argv, output, environment, timeout)
                else:
                    result = subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL,
                        stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
                        cwd=self.root, env=environment,
                        **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        except (OSError, subprocess.TimeoutExpired, _InstallationStalled) as exc:
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
        if sys.platform == "darwin" and self._darwin_swap_pending():
            self._recover_darwin_swap()
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
            self._run([uv, "--no-config", "--cache-dir", self._uv_cache(), "venv", "--python", base, "--no-python-downloads", self.venv])
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
            and (sys.platform != "darwin" or not self._darwin_swap_pending())
            and metadata.get("launcher_version") == LAUNCHER_VERSION):
            if sys.platform == "darwin" and self._darwin_base_python(self.darwin_store / "base") is None:
                # Existing profiles created before the shared store can seed
                # it without another network transfer.
                self._cached_darwin_base()
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
                    arguments = [uv, "--no-config", "--cache-dir", self._uv_cache(), "pip", "install",
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
