"""Verified portable executables and explicitly adopted external programs.

Managed artifacts are immutable application data. External discoveries retain
their original owner and path; observation never makes them removable Pack data.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
import zipfile

from .environments import _json_write, installation_lock, installation_is_active

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class _HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username is not None or parts.password is not None:
            raise ValueError('Executable downloads must remain on HTTPS without credentials')
        return super().redirect_request(request, response, code, message, headers, url)


def target_platform():
    architecture = platform.machine().lower()
    architecture = {"amd64": "x86_64", "arm64": "aarch64"}.get(architecture, architecture)
    return f"{sys.platform}-{architecture}"


@dataclass(frozen=True)
class ExecutableEnvironmentSpec:
    owner_id: str
    environment_id: str
    artifacts: tuple
    source_root: Path
    executable_names: tuple[str, ...] = ()
    verify_argv: tuple[str, ...] = ("--version",)
    verify_pattern: str | None = None
    accept_external: bool = False

    @classmethod
    def from_declaration(cls, owner, name, declaration, source_root):
        if declaration.target != "host-worker" or declaration.provider != "executable":
            raise ValueError("Executable provider requires a host-worker executable declaration")
        if declaration.management != 'managed' or declaration.lock or declaration.resources or declaration.prerequisites:
            raise ValueError('Executable provider requires managed portable artifacts; lockfiles, extra resources and environment prerequisites are unsupported')
        return cls(owner, name, tuple(declaration.artifacts), Path(source_root).resolve(),
                   tuple(declaration.executable_names), tuple(declaration.verify_argv),
                   declaration.verify_pattern, declaration.accept_external)

    @property
    def fingerprint(self):
        # Delivery locations change when a source Pack is archived. Content and
        # executable layout identify a runtime, not its owner or source path.
        artifacts = [{key: value for key, value in item.model_dump(mode="json").items()
                      if key not in {"path", "url"}} for item in self.artifacts]
        value = {"provider": "executable", "provider_version": 1, "platform": target_platform(),
                 "artifacts": artifacts, "executable_names": self.executable_names,
                 "accept_external": self.accept_external,
                 "verify_argv": self.verify_argv, "verify_pattern": self.verify_pattern}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def artifact(self):
        matches = [item for item in self.artifacts if item.platform == target_platform()]
        if len(matches) != 1:
            raise ValueError(f"No unique executable artifact for {target_platform()}")
        return matches[0]


@dataclass(frozen=True)
class ExecutableHandle:
    root: Path
    executable: str
    fingerprint: str
    operation_id: str
    management: str = "managed"


def _member(name: str, root: Path) -> Path:
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/"))
            or "\\" in name or ":" in name or any(ord(char) < 32 for char in name)):
        raise ValueError("Executable archive contains an unsafe path")
    for part in path.parts:
        if (part.endswith((".", " ")) or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                or any(char in part for char in '<>"|?*')):
            raise ValueError("Executable archive contains a non-portable path")
    if name.casefold() == "ready.json":
        raise ValueError("Executable archive uses a reserved receipt path")
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Executable archive path escapes its environment")
    return result


def _extract(archive: Path, destination: Path, kind: str, executable: str):
    if kind == "raw":
        target = _member(executable, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
        return
    total, names = 0, set()
    stream = zipfile.ZipFile(archive) if kind == "zip" else tarfile.open(archive, "r:*")
    with stream:
        members = stream.infolist() if kind == "zip" else stream.getmembers()
        if len(members) > 10000:
            raise ValueError("Executable archive contains too many entries")
        for member in members:
            name = member.filename if kind == "zip" else member.name
            if name in {".", "./"}:
                continue
            directory = member.is_dir() if kind == "zip" else member.isdir()
            name = name.rstrip("/") if directory else name
            path = _member(name, destination)
            key = name.casefold()
            if key in names:
                raise ValueError("Executable archive contains duplicate paths")
            names.add(key)
            mode = member.external_attr >> 16 if kind == "zip" else member.mode
            if kind == "zip" and stat.S_ISLNK(mode) or kind != "zip" and not (member.isfile() or directory):
                raise ValueError("Executable archive links and special files are unsupported")
            if directory:
                path.mkdir(parents=True, exist_ok=True)
                continue
            total += member.file_size if kind == "zip" else member.size
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("Executable archive expands beyond the supported limit")
            path.parent.mkdir(parents=True, exist_ok=True)
            source = stream.open(member) if kind == "zip" else stream.extractfile(member)
            with source, path.open("xb") as output:
                shutil.copyfileobj(source, output)
            if mode & 0o111:
                path.chmod(0o755)


class ExecutableEnvironmentManager:
    def __init__(self, data_root=None):
        from .environments import PackEnvironmentManager
        self.root = PackEnvironmentManager(data_root).root

    def _register(self, handle):
        from .lifecycle import ManagedResourceLedger
        if handle.management == 'managed':
            ManagedResourceLedger(self.root.parent).register(
                'environment:' + handle.fingerprint, handle.root, 'environment')
        return handle

    def lease(self, handle):
        from .environments import PackEnvironmentManager
        return PackEnvironmentManager(self.root.parent).lease(handle)

    def _operation(self, spec):
        return self.root / "operations" / (spec.fingerprint + ".json")

    def _probe(self, spec, executable):
        # Verification needs OS process support, not host API tokens or Python
        # activation. Keep probes deterministic and avoid credential exposure.
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LANG', 'LC_ALL'}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        completed = subprocess.run([str(executable), *spec.verify_argv], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30, env=environment, cwd=Path(executable).parent,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        output = completed.stdout[-65536:].decode("utf-8", errors="replace")
        if completed.returncode or spec.verify_pattern and not re.search(spec.verify_pattern, output):
            raise RuntimeError("Executable failed its declared readiness probe")

    def read(self, spec, *, root=None):
        root = Path(root) if root else self.root / "environments" / spec.fingerprint
        try:
            receipt = json.loads((root / "ready.json").read_text(encoding="utf-8"))
            if receipt["provider"] != "executable" or receipt["fingerprint"] != spec.fingerprint:
                raise ValueError("Executable receipt does not match declaration")
            executable = _member(receipt["executable"], root)
            if receipt["executable"] not in receipt["files"] or not receipt["files"]:
                raise ValueError("Executable is absent from the verified file list")
            for name, digest in receipt["files"].items():
                if _digest(_member(name, root)) != digest:
                    raise ValueError("Executable payload was modified")
            if not executable.is_file():
                raise ValueError("Executable is missing")
            return ExecutableHandle(root, str(executable), spec.fingerprint, receipt["operation_id"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Managed Pack executable is not ready") from exc

    def external(self, spec, *, path=None, verify=False):
        if not spec.accept_external:
            raise ValueError("This Pack does not accept an external executable")
        candidates = [str(path)] if path else [shutil.which(name) for name in spec.executable_names]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                executable = Path(candidate).resolve()
                if verify:
                    self._probe(spec, executable)
                    _json_write(self.root / "external" / (spec.fingerprint + ".json"), {
                        "provider": "executable", "management": "external", "executable": str(executable),
                        "sha256": _digest(executable), "fingerprint": spec.fingerprint})
                return ExecutableHandle(executable.parent, str(executable), spec.fingerprint, "", "external")
        raise RuntimeError("External Pack executable was not found")

    def read_external(self, spec):
        if not spec.accept_external:
            raise RuntimeError("This Pack does not accept an external executable")
        try:
            receipt = json.loads((self.root / "external" / (spec.fingerprint + ".json")).read_text(encoding="utf-8"))
            path = Path(receipt["executable"])
            if receipt["fingerprint"] != spec.fingerprint or receipt["sha256"] != _digest(path):
                raise ValueError("External executable changed after verification")
            return ExecutableHandle(path.parent, str(path), spec.fingerprint, "", "external")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("External executable has no valid readiness receipt") from exc

    def status(self, spec):
        try:
            handle = self.read(spec)
            return {"status": "ready", "management": "managed", "operation_id": handle.operation_id}
        except RuntimeError:
            pass
        if spec.accept_external:
            try:
                self.read_external(spec)
                return {"status": "ready", "management": "external", "operation_id": None}
            except RuntimeError:
                pass
            try:
                self.external(spec)
                return {"status": "missing", "management": "external", "operation_id": None,
                        "error": "External executable requires an explicit readiness check"}
            except RuntimeError:
                pass
        try:
            operation = json.loads(self._operation(spec).read_text(encoding="utf-8"))
            if operation["status"] == "preparing":
                try:
                    active = installation_is_active(self.root / "locks" / (spec.fingerprint + ".lock"))
                except OSError:
                    return {**operation, 'status': 'failed', 'error': 'Cannot verify executable preparation liveness'}
                if not active:
                    return {**operation, "status": "failed", "error": "Executable preparation was interrupted"}
            if operation['status'] not in {'ready', 'failed', 'preparing'}:
                return {**operation, 'status': 'failed', 'error': 'Invalid executable operation receipt'}
            return {**operation, "status": "failed"} if operation["status"] == "ready" else operation
        except (OSError, ValueError, KeyError):
            return {"status": "missing", "management": "managed", "operation_id": None}

    def prepare(self, spec):
        root = self.root / "environments" / spec.fingerprint
        with installation_lock(self.root / "locks" / (spec.fingerprint + ".lock")):
            try:
                handle = self.read(spec)
                self._probe(spec, handle.executable)
                return self._register(handle)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                pass
            if spec.accept_external:
                try:
                    return self.external(spec, verify=True)
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    pass
            artifact = spec.artifact()
            try:
                previous = json.loads(self._operation(spec).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
            operation_id = previous.get("operation_id") or uuid4().hex
            operation = {"status": "preparing", "provider": "executable", "management": "managed",
                         "operation_id": operation_id, "fingerprint": spec.fingerprint,
                         "owner_id": spec.owner_id, "environment_id": spec.environment_id}
            previous_stage = previous.get('stage', '')
            if re.fullmatch(r'[a-f0-9]{32}', previous_stage):
                stage = self.root / 'staging' / previous_stage
                payload = stage / 'payload'
                try:
                    staged = self.read(spec, root=payload)
                    self._probe(spec, staged.executable)
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    pass
                else:
                    root.parent.mkdir(parents=True, exist_ok=True)
                    if root.exists():
                        root.rename(stage / ('previous-invalid-' + uuid4().hex))
                    payload.rename(root)
                    _json_write(self._operation(spec), {**operation, 'stage': previous_stage, 'status': 'ready'})
                    return self._register(self.read(spec))
            stage = self.root / "staging" / uuid4().hex
            operation["stage"] = stage.name
            _json_write(self._operation(spec), operation)
            stage.mkdir(parents=True)
            try:
                archive = stage / "artifact.download"
                if artifact.path:
                    source = _member(artifact.path, spec.source_root)
                    if source.stat().st_size > MAX_ARCHIVE_BYTES:
                        raise ValueError("Executable artifact is too large")
                    shutil.copyfile(source, archive)
                else:
                    with build_opener(_HTTPSRedirects()).open(Request(artifact.url, headers={"User-Agent": "OpenAgentWorld-PackInstaller"}), timeout=30) as response, archive.open("xb") as output:
                        if urlsplit(response.url).scheme != "https":
                            raise ValueError("Executable download redirected outside HTTPS")
                        total = 0
                        while block := response.read(1024 * 1024):
                            total += len(block)
                            if total > min(MAX_ARCHIVE_BYTES, artifact.size_bytes or MAX_ARCHIVE_BYTES):
                                raise ValueError("Executable artifact exceeds its declared size")
                            output.write(block)
                if artifact.size_bytes and archive.stat().st_size != artifact.size_bytes or _digest(archive) != artifact.sha256:
                    raise ValueError("Executable artifact checksum or size does not match declaration")
                payload = stage / "payload"
                payload.mkdir()
                _extract(archive, payload, artifact.archive, artifact.executable)
                executable = _member(artifact.executable, payload)
                executable.chmod(executable.stat().st_mode | 0o111)
                self._probe(spec, executable)
                files = {item.relative_to(payload).as_posix(): _digest(item) for item in payload.rglob("*") if item.is_file()}
                _json_write(payload / "ready.json", {**operation, "status": "ready", "executable": artifact.executable, "files": files})
                root.parent.mkdir(parents=True, exist_ok=True)
                if root.exists():
                    root.rename(stage / "previous-invalid")
                payload.rename(root)
                _json_write(self._operation(spec), {**operation, "status": "ready"})
                return self._register(self.read(spec))
            except BaseException as exc:
                _json_write(self._operation(spec), {**operation, "status": "failed", "error": type(exc).__name__})
                raise
