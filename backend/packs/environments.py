"""Host-owned private Python environments for trusted Pack workers.

Only explicit setup/installation calls prepare environments. Reading a receipt
never installs packages, imports Pack code, or touches the host's site-packages.
Worker dependencies use ``-I -S`` plus a private target directory; this is
dependency isolation, not an operating-system security sandbox.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
from typing import Callable
from uuid import uuid4


PROVIDER_VERSION = 2


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def clean_python_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith("PYTHON") and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX"}}


@contextmanager
def preparation_progress(label: str, *, interval: float = 15):
    """Keep setup visible even when the resolver or filesystem is silent."""
    started = time.monotonic()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(interval):
            print(f"  {label}: still working ({time.monotonic() - started:.0f}s)", flush=True)

    print(f"  {label}...", flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    except BaseException:
        print(f"  {label}: failed ({time.monotonic() - started:.1f}s)", flush=True)
        raise
    else:
        print(f"  {label}: done ({time.monotonic() - started:.1f}s)", flush=True)
    finally:
        stopped.set()
        thread.join()


def run_command(arguments, *, env):
    label = ("Resolving and installing Python packages" if list(arguments[1:3]) == ["pip", "install"]
             else "Preparing Python interpreter" if list(arguments[1:3]) == ["python", "install"]
             else "Verifying Python environment")
    with preparation_progress(label):
        subprocess.run([str(arg) for arg in arguments], check=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=1800,
                       **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))


@contextmanager
def installation_lock(path: Path, *, timeout: float = 1800):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
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
                    raise RuntimeError("Another Pack environment installation is still running") from exc
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def installation_is_active(path: Path) -> bool:
    """Observe an existing OS lock without creating or modifying its file.

    Receipt timestamps and recorded PIDs cannot establish liveness after a
    crash. The preparation lock is released by the OS when its owner exits.
    Other I/O errors propagate, so callers do not misreport unknown liveness.
    """
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return False
    with stream:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return True
            raise
        if os.name == "nt":
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
        return False


@dataclass(frozen=True)
class PythonEnvironmentSpec:
    owner_id: str
    environment_id: str
    requirements: tuple[str, ...] = ()
    requires_python: str = ">=3.11"
    verify_imports: tuple[str, ...] = ()
    verify_script: Path | None = None
    # File bytes contribute to identity, never absolute checkout/install paths.
    requirements_files: tuple[Path, ...] = ()
    resource_files: tuple[Path, ...] = ()

    @classmethod
    def from_declaration(cls, owner_id, environment_id, declaration, source_root):
        if declaration.target != "host-worker" or declaration.provider != "python":
            raise ValueError("Only host-worker Python environments use this provider")
        if declaration.management != "managed":
            raise ValueError("External environments are configured, not installed")
        if getattr(declaration, "lock", None):
            raise ValueError("This Python provider does not yet support lockfile installation; use explicit pinned requirements")
        root = Path(source_root).resolve()

        def resource(relative):
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Pack environment resource missing or outside Pack: {relative}")
            return path

        script = getattr(declaration, "verify_script", None)
        return cls(owner_id, environment_id, tuple(declaration.python),
                   declaration.requires_python or ">=3.11", tuple(declaration.verify_imports),
                   resource(script) if script else None,
                   resource_files=tuple(resource(item) for item in declaration.resources))

    def identity(self) -> dict:
        if not self.owner_id or not self.environment_id:
            raise ValueError("A Pack environment needs an owner and environment ID")
        for module in self.verify_imports:
            if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", module):
                raise ValueError(f"Invalid verification module: {module}")
        return {"provider": "python-target", "provider_version": PROVIDER_VERSION,
                "requirements": list(self.requirements), "requires_python": self.requires_python,
                "requirement_digests": [_digest(path) for path in self.requirements_files],
                "resource_digests": [_digest(path) for path in self.resource_files],
                "verify_imports": list(self.verify_imports),
                "verify_script": _digest(self.verify_script) if self.verify_script else None,
                "platform": sysconfig.get_platform(), "abi": sys.implementation.cache_tag}

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.identity(), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class EnvironmentHandle:
    root: Path
    python: str
    fingerprint: str
    operation_id: str


class PackEnvironmentManager:
    def __init__(self, data_root: Path | None = None, *, runner=run_command):
        if data_root is None:
            from backend.config import _default_data_root
            data_root = _default_data_root()
        self.root = Path(data_root).resolve() / "runtime"
        self.runner = runner

    def environment_root(self, spec: PythonEnvironmentSpec) -> Path:
        return self.root / "environments" / spec.fingerprint

    def _register(self, handle):
        from .lifecycle import ManagedResourceLedger
        ManagedResourceLedger(self.root.parent).register(
            'environment:' + handle.fingerprint, handle.root, 'environment')
        return handle

    @contextmanager
    def lease(self, handle):
        """Keep a managed worker alive through subprocess exit/cleanup.

        External and read-only application bundle environments are never owned
        by this profile. Their lifetime remains with their original installer.
        """
        expected = self.root / 'environments' / handle.fingerprint
        if getattr(handle, 'management', 'managed') == 'external' or handle.root != expected:
            yield handle
            return
        from .lifecycle import ManagedResourceLedger
        ledger = ManagedResourceLedger(self.root.parent)
        with installation_lock(self.root / 'locks' / (handle.fingerprint + '.lock')):
            if not (handle.root / 'ready.json').is_file():
                raise RuntimeError('Managed environment was removed; prepare it before launching')
            lease = ledger.lease('environment:' + handle.fingerprint)
            lease.__enter__()
        try:
            yield handle
        finally:
            lease.__exit__(None, None, None)

    def _operation_path(self, spec):
        return self.root / "operations" / (spec.fingerprint + ".json")

    def _seed_roots(self, spec):
        """Discover previous OAW installs only; never inherit host site-packages."""
        bundled = PackEnvironmentManager(Path(sys.prefix) / "oaw-runtime-bundle").environment_root(spec)
        roots = [bundled] if bundled != self.environment_root(spec) else []
        # Pre-unification workers lived under this interpreter-owned namespace.
        # Their unversioned v2 receipts need dependency and runtime verification
        # before they can seed a managed, profile-owned copy.
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", spec.owner_id) and spec.owner_id not in {".", ".."}:
            legacy = Path(sys.prefix) / "oaw-runtimes" / spec.owner_id
            if legacy.is_dir():
                roots.extend(sorted(path.parent for path in legacy.glob("*/ready.json")))
        return tuple(roots)

    @staticmethod
    def _verify_legacy_requirements(spec, seed, relative):
        """Reject stale versions or incomplete dependency closures before reuse.

        Legacy receipts lack a recipe fingerprint. Limit migration to the host
        interpreter and named requirements whose complete metadata is available
        inside the private packages directory. Other recipes reinstall normally.
        """
        from importlib import metadata
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
        if relative != "host" or spec.requirements_files:
            raise ValueError("Legacy recipe cannot be verified")
        version = ".".join(map(str, sys.version_info[:3]))
        if version not in SpecifierSet(spec.requires_python):
            raise ValueError("Legacy interpreter is incompatible")
        installed = {}
        for distribution in metadata.distributions(path=[str(seed / "packages")]):
            name = canonicalize_name(distribution.metadata["Name"])
            if name in installed:
                raise ValueError("Ambiguous legacy distribution")
            installed[name] = distribution
        pending, visited = [Requirement(value) for value in spec.requirements], set()
        while pending:
            requirement = pending.pop()
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            name = canonicalize_name(requirement.name)
            distribution = installed.get(name)
            # Direct URL provenance is absent in old environment receipts.
            if requirement.url or distribution is None or distribution.version not in requirement.specifier:
                raise ValueError("Legacy dependency does not match the declared recipe")
            requires_python = distribution.metadata.get("Requires-Python", "")
            if version not in SpecifierSet(requires_python):
                raise ValueError("Legacy dependency does not support this interpreter")
            key = (name, tuple(sorted(requirement.extras)))
            if key in visited:
                continue
            visited.add(key)
            for value in distribution.requires or ():
                child = Requirement(value)
                if not child.marker or any(child.marker.evaluate({"extra": extra}) for extra in ("", *requirement.extras)):
                    child.marker = None
                    pending.append(child)

    def _read(self, spec, root) -> EnvironmentHandle:
        try:
            receipt = json.loads((root / "ready.json").read_text(encoding="utf-8"))
            if receipt["format"] != 1 or receipt["fingerprint"] != spec.fingerprint:
                raise ValueError("Receipt does not match the declared environment")
            relative = receipt["python"]
            executable = Path(sys.executable) if relative == "host" else root / relative
            if relative != "host" and (Path(relative).is_absolute() or not executable.resolve().is_relative_to(root.resolve())):
                raise ValueError("Interpreter escapes environment")
            if not executable.is_file() or not (root / "packages").is_dir():
                raise ValueError("Environment interpreter or dependency directory is missing")
            for item in receipt["verified_files"]:
                path = (root / item["path"]).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file() or _digest(path) != item["sha256"]:
                    raise ValueError("Verified environment files were modified or removed")
            return EnvironmentHandle(root, str(executable), spec.fingerprint, receipt["operation_id"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Pack environment {spec.owner_id}/{spec.environment_id} is not ready: {root}. {exc}") from exc

    def read_python(self, spec: PythonEnvironmentSpec, *, root: Path | None = None) -> EnvironmentHandle:
        return self._read(spec, Path(root) if root is not None else self.environment_root(spec))

    def status(self, spec: PythonEnvironmentSpec) -> dict:
        try:
            handle = self.read_python(spec)
            return {"status": "ready", "fingerprint": handle.fingerprint, "operation_id": handle.operation_id}
        except RuntimeError:
            pass
        try:
            operation = json.loads(self._operation_path(spec).read_text(encoding="utf-8"))
            # A finished operation cannot make a broken/missing payload ready.
            if operation.get("status") == "ready":
                return {**operation, "status": "invalid", "error": "Verified environment payload is missing or changed"}
            if operation.get("status") == "preparing":
                try:
                    active = installation_is_active(self.root / "locks" / (spec.fingerprint + ".lock"))
                except OSError:
                    return {**operation, "status": "failed", "error_code": "status_unavailable",
                            "error": "Cannot inspect the environment preparation lock"}
                if not active:
                    # A read/doctor request must not discard a recoverable stage
                    # or rewrite the durable operation. Explicit retry reconciles
                    # it under the lock and retains the operation identity.
                    return {**operation, "status": "failed", "error_code": "interrupted",
                            "error": "Environment preparation was interrupted; retry resumes the recorded operation"}
            return operation
        except (OSError, ValueError):
            return {"status": "missing", "fingerprint": spec.fingerprint}

    def _verify(self, spec, handle, env, verifier):
        root = handle.root.resolve()
        # Deep imports and dependency cache writes can exceed MAX_PATH in
        # staging. Keep the same extended root when recording relative files.
        if os.name == "nt" and not str(root).startswith("\\\\?\\"):
            path = str(root)
            root = Path("\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path)
        probe = (
            "import importlib,json,pathlib,site,sys; "
            "site.addsitedir(str(pathlib.Path(sys.argv[1])/'packages')); "
            "modules=[importlib.import_module(name) for name in json.loads(sys.argv[2])]; "
            "paths=[str(pathlib.Path(m.__file__).resolve()) for m in modules if getattr(m,'__file__',None)]; "
            "pathlib.Path(sys.argv[3]).write_text(json.dumps(paths),encoding='utf-8')"
        )
        result = self.root / "operations" / (handle.fingerprint + ".verify-" + uuid4().hex + ".json")
        result.parent.mkdir(parents=True, exist_ok=True)
        self.runner([handle.python, "-I", "-S", "-B", "-c", probe, root,
                     json.dumps(spec.verify_imports), result], env=env)
        if spec.verify_script:
            self.runner([handle.python, "-I", "-S", "-B", spec.verify_script, "--check", root], env=env)
        if verifier:
            verifier(handle)
        verified = []
        for item in json.loads(result.read_text(encoding="utf-8")):
            path = Path(item).resolve()
            if path.is_relative_to(root):
                verified.append({"path": path.relative_to(root).as_posix(), "sha256": _digest(path)})
        result.unlink()
        return verified

    def prepare_python(self, spec: PythonEnvironmentSpec, *, verify: Callable | None = None,
                       seed_roots: tuple[Path, ...] = ()) -> EnvironmentHandle:
        fingerprint = spec.fingerprint
        root = self.environment_root(spec)
        operation_path = self._operation_path(spec)
        env = clean_python_environment()
        env.update(PYTHONUTF8="1", LITELLM_LOCAL_MODEL_COST_MAP="True")
        # Download caches are not installed dependencies. Reuse uv's configured
        # cache (also used by host sync) rather than downloading it per profile.
        seed_roots = tuple(dict.fromkeys((*seed_roots, *self._seed_roots(spec))))
        with installation_lock(self.root / "locks" / (fingerprint + ".lock")):
            try:
                handle = self.read_python(spec)
                # Explicit setup verifies cached environments, even offline.
                self._verify(spec, handle, env, verify)
                _json_write(operation_path, {"operation_id": handle.operation_id,
                    "fingerprint": fingerprint, "owner_id": spec.owner_id,
                    "environment_id": spec.environment_id, "status": "ready"})
                return self._register(handle)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                pass
            try:
                previous = json.loads(operation_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
            operation_id = previous.get("operation_id") or uuid4().hex
            operation = {"operation_id": operation_id, "fingerprint": fingerprint,
                         "owner_id": spec.owner_id, "environment_id": spec.environment_id,
                         "attempt": int(previous.get("attempt", 0)) + 1, "status": "preparing",
                         "started_at": datetime.now(timezone.utc).isoformat()}
            stage = self.root / "staging" / (fingerprint + "-" + uuid4().hex)
            operation["stage"] = stage.name
            # Persist identity before subprocesses/download side effects.
            _json_write(operation_path, operation)
            stage.mkdir(parents=True)
            try:
                previous_stage = previous.get("stage", "")
                recovery = self.root / "staging" / previous_stage
                if previous_stage and recovery.parent.resolve() == (self.root / "staging").resolve():
                    seed_roots = (recovery, *seed_roots)
                relative = self._populate(spec, stage, env, seed_roots, verify)
                executable = str(Path(sys.executable) if relative == "host" else stage / relative)
                handle = EnvironmentHandle(stage, executable, fingerprint, operation_id)
                verified = self._verify(spec, handle, env, verify)
                _json_write(stage / "ready.json", {"format": 1, "fingerprint": fingerprint,
                            "python": relative, "operation_id": operation_id, "verified_files": verified})
                root.parent.mkdir(parents=True, exist_ok=True)
                if root.exists():
                    # Keep damaged versions for diagnosis; never edit a published
                    # version in place. Different fingerprints remain untouched.
                    root.rename(self.root / "staging" / (fingerprint + "-invalid-" + uuid4().hex))
                stage.rename(root)
                handle = self.read_python(spec)
                _json_write(operation_path, {**operation, "status": "ready", "completed_at": datetime.now(timezone.utc).isoformat()})
                return self._register(handle)
            except BaseException as exc:
                # Do not record subprocess arguments: package URLs may contain credentials.
                _json_write(operation_path, {**operation, "status": "failed", "error": type(exc).__name__,
                                            "completed_at": datetime.now(timezone.utc).isoformat()})
                raise

    def _populate(self, spec, stage, env, seed_roots, verify):
        for seed in seed_roots:
            try:
                receipt = json.loads((seed / "ready.json").read_text(encoding="utf-8"))
                relative = receipt["python"]
                if receipt.get("format") == 1:
                    self._read(spec, seed)
                elif receipt.get("format") == 2:
                    self._verify_legacy_requirements(spec, seed, relative)
                else:
                    continue
                candidate = (seed / relative).resolve()
                if relative != "host" and (Path(relative).is_absolute() or not candidate.is_relative_to(seed.resolve()) or not candidate.is_file()):
                    continue
                if not (seed / "packages").is_dir():
                    continue
                executable = str(Path(sys.executable) if relative == "host" else candidate)
                print(f"  Checking existing environment for {spec.owner_id}/{spec.environment_id}: {seed}", flush=True)
                self._verify(spec, EnvironmentHandle(seed, executable, spec.fingerprint, "seed"), env, verify)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                continue
            else:
                with preparation_progress(f"Copying verified environment for {spec.owner_id}/{spec.environment_id}"):
                    shutil.copytree(seed / "packages", stage / "packages", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                    if relative != "host":
                        shutil.copytree(seed / "python", stage / "python", symlinks=False)
                return relative
        from packaging.specifiers import SpecifierSet
        constraint = SpecifierSet(spec.requires_python)
        relative = "host"
        executable = Path(sys.executable)
        uv = shutil.which("uv")
        if ".".join(map(str, sys.version_info[:3])) not in constraint:
            if not uv:
                raise RuntimeError("Preparing a Pack Python interpreter requires uv on PATH")
            env["UV_PYTHON_INSTALL_DIR"] = str(stage / "python")
            # Do not register downloaded interpreters globally or create user
            # PATH launchers: this interpreter belongs only to the environment.
            self.runner([uv, "python", "install", "--no-bin",
                         *(["--no-registry"] if os.name == "nt" else []), spec.requires_python], env=env)
            executable = Path(subprocess.check_output([uv, "python", "find", "--managed-python", spec.requires_python],
                env=env, text=True, timeout=60).strip()).resolve()
            relative = executable.relative_to(stage.resolve()).as_posix()
        (stage / "packages").mkdir(parents=True)
        if spec.requirements or spec.requirements_files:
            if not uv:
                raise RuntimeError("Preparing Pack dependencies requires uv on PATH")
            args = [uv, "pip", "install", "--python", executable, "--target", stage / "packages"]
            for path in spec.requirements_files:
                args.extend(["-r", path])
            self.runner([*args, *spec.requirements], env=env)
        return relative
