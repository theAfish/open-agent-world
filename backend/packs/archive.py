"""Inspect both ZIP layers without importing or running Pack code."""
from __future__ import annotations

from configparser import ConfigParser, Error as ConfigParserError
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
import io
import json
import stat
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from backend.packs.manifest import PackManifest, safe_path

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096


def zip_files(archive: ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
        raise ValueError("Pack archive exceeds extraction limits")
    seen: set[str] = set()
    files = {}
    for item in infos:
        if item.orig_filename != item.filename:
            raise ValueError("Unsafe archive path containing a NUL byte")
        name = safe_path(item.filename.rstrip("/") if item.is_dir() else item.filename)
        key = name.casefold()
        kind = stat.S_IFMT(item.external_attr >> 16)
        if key in seen or kind not in {0, stat.S_IFREG, stat.S_IFDIR} or item.flag_bits & 1:
            raise ValueError(f"Duplicate, encrypted or non-regular archive member: {name}")
        seen.add(key)
        if not item.is_dir():
            files[name] = archive.read(item)
    file_keys = {n.casefold() for n in files}
    for name in files:
        if any(parent.casefold() in file_keys for parent in _parents(name)):
            raise ValueError(f"Archive file/directory collision: {name}")
    return files


def _parents(path):
    parts = path.split("/")
    return ("/".join(parts[:i]) for i in range(1, len(parts)))


def json_object(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def inspect_wheel(data: bytes, manifest: PackManifest) -> str:
    with ZipFile(io.BytesIO(data)) as wheel:
        files = zip_files(wheel)
    roots = {name.split("/")[0] for name in files}
    info = [name for name in roots if name.endswith(".dist-info")]
    if len(info) != 1 or roots != {*manifest.module_names, info[0]}:
        raise ValueError(f"Backend wheel must contain only {manifest.module_name}/ and one .dist-info/")
    prefix = info[0]
    if any(f"{module}/__init__.py" not in files for module in manifest.module_names):
        raise ValueError("Backend wheel is missing its package __init__.py")
    for name in files:
        if name.endswith((".pth", ".pyd", ".so", ".dll", ".pyc")):
            raise ValueError("Backend wheel must be pure Python, without path hooks or native libraries")
    wheel_meta = BytesParser().parsebytes(files[f"{prefix}/WHEEL"])
    if wheel_meta.get("Root-Is-Purelib", "").lower() != "true" or wheel_meta.get_all("Tag") != ["py3-none-any"]:
        raise ValueError("Backend must be a pure Python py3-none-any wheel")
    package = BytesParser().parsebytes(files[f"{prefix}/METADATA"])
    if package.get("Version") != manifest.version:
        raise ValueError("Backend wheel version differs from manifest")
    requires_python = package.get("Requires-Python", '')
    requirements = tuple(package.get_all("Requires-Dist", []))
    from backend.packs.adapters import check_adapter_dependencies
    declared = {str(Requirement(value)) for value in requirements
                if not (Requirement(value).marker and 'extra' in str(Requirement(value).marker))}
    if declared != {str(Requirement(value)) for value in manifest.adapter.requirements}:
        raise ValueError("Adapter dependency declaration differs from wheel metadata")
    check_adapter_dependencies([manifest.adapter])
    check_backend_python(requires_python)
    declarations = ConfigParser(interpolation=None)
    declarations.optionxform = str
    try:
        declarations.read_string(files[f"{prefix}/entry_points.txt"].decode("utf-8"))
    except ConfigParserError as exc:
        raise ValueError("Invalid backend entry point metadata") from exc
    if declarations.sections() != ["open_agent_world.plugins"]:
        raise ValueError("Backend wheel must declare only one OAW factory")
    entries = list(declarations["open_agent_world.plugins"].items())
    if len(entries) != 1 or entries[0][0] != manifest.owner_id:
        raise ValueError("Backend entry point name must equal runtime owner")
    entry = entries[0][1]
    import re
    if not re.fullmatch(re.escape(manifest.module_name) + r"(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", entry):
        raise ValueError("Backend factory must belong to its Pack module")
    return entry


def check_backend_python(requires_python: str) -> None:
    """Check adapter interpreter compatibility without reading wheel bytes."""
    import platform
    if Version(platform.python_version()) not in SpecifierSet(requires_python):
        raise ValueError("Backend wheel requires a different host Python version")


@dataclass(frozen=True)
class InspectedPack:
    manifest: PackManifest
    files: dict[str, bytes]
    digest: str
    backend_entry: str | None


def inspect_archive(data: bytes, *, check_host_libraries: bool = True) -> InspectedPack:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Pack archive is too large")
    with ZipFile(io.BytesIO(data)) as archive:
        files = zip_files(archive)
    if "manifest.json" not in files or "checksums.json" not in files:
        raise ValueError("Pack must contain manifest.json and checksums.json")
    if len(files["manifest.json"]) > 2 * 1024 * 1024 or len(files["checksums.json"]) > 1024 * 1024:
        raise ValueError("Pack metadata is too large")
    manifest = PackManifest.model_validate(json_object(files["manifest.json"]))
    manifest.check_compatibility()
    for name in files:
        allowed = name in {"manifest.json", "checksums.json", "README.md"}
        if manifest.kind == "content":
            allowed = allowed or name in manifest.content.paths
        else:
            allowed = allowed or name.startswith(("backend/", "frontend/", "assets/", "runtime/"))
        if not allowed:
            raise ValueError(f"Unexpected Pack file: {name}")
    checksums = json_object(files["checksums.json"])
    if isinstance(checksums, dict):
        for name in checksums:
            safe_path(name)
    if not isinstance(checksums, dict) or set(checksums) != set(files) - {"checksums.json"}:
        raise ValueError("checksums.json must cover every file except itself")
    for name, digest in checksums.items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise ValueError(f"Checksum mismatch: {name}")
    if manifest.kind == "content":
        from backend.packs.content import read_presets
        from backend.packs.factory_cards import read_recipes
        read_presets(manifest, files)
        read_recipes(manifest, files)
        return InspectedPack(manifest, files, hashlib.sha256(data).hexdigest(), None)
    assert manifest.entrypoints is not None
    for entry in (manifest.entrypoints.backend, manifest.entrypoints.frontend):
        if entry not in files:
            raise ValueError(f"Missing Pack artifact: {entry}")
    for environment in manifest.environments.values():
        resources = (*environment.resources, *environment.requirement_files,
                     *(item for item in (environment.verify_script, environment.entrypoint, environment.lock) if item))
        for resource in resources:
            safe_path(resource)
            if not resource.startswith("runtime/") or resource not in files:
                raise ValueError(f"Missing or unsafe runtime resource: {resource}")
        # Every file digest above has already been checked against these bytes.
        # Compare declarations with that verified map instead of hashing again.
        if environment.lock_digest and (not environment.lock or
                checksums[environment.lock] != environment.lock_digest):
            raise ValueError("Environment lock digest does not match its artifact")
        for resource, digest in environment.artifact_hashes.items():
            if resource not in resources or checksums[resource] != digest:
                raise ValueError(f"Environment artifact digest does not match: {resource}")
        for artifact in environment.artifacts:
            if artifact.path is not None:
                safe_path(artifact.path)
                if not artifact.path.startswith('runtime/') or artifact.path not in files:
                    raise ValueError(f"Missing executable artifact: {artifact.path}")
                payload = files[artifact.path]
                if (checksums[artifact.path] != artifact.sha256
                        or artifact.size_bytes is not None and len(payload) != artifact.size_bytes):
                    raise ValueError(f"Executable artifact checksum/size mismatch: {artifact.path}")
    from backend.packs.adapters import verify_wheel
    for wheel in manifest.adapter.wheels:
        if wheel.path not in files:
            raise ValueError(f"Missing adapter dependency wheel: {wheel.path}")
        verify_wheel(wheel, files[wheel.path], check_host=check_host_libraries)
    backend_entry = inspect_wheel(files[manifest.entrypoints.backend], manifest)
    return InspectedPack(manifest, files, hashlib.sha256(data).hexdigest(), backend_entry)
