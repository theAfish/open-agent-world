"""Static source diagnostics and schema 3 packaging of prebuilt artifacts.

Build commands belong to the author. These helpers never import a Pack, run its
setup entry point, fetch dependencies, or install into the host interpreter.
"""
from __future__ import annotations

import ast
import base64
from configparser import ConfigParser
import csv
from email.parser import BytesParser
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import re
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from backend.packs.archive import inspect_archive, zip_files
from backend.packs.manifest import PackManifest, safe_path
from backend.packs.spec import PackSpec, discover_source_packs, read_source_pack


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return buffer.getvalue()


def _host_requirements(spec: PackSpec) -> list[dict]:
    from backend.packs.adapters import host_distributions, host_optional_distributions
    host = {**host_distributions(), **host_optional_distributions()}
    result = []
    for text in spec.backend.python:
        requirement = Requirement(text)
        row = {"requirement": text, "state": "available", "installed_version": None,
               "management": "host-provided" if canonicalize_name(requirement.name) in host else "managed"}
        if requirement.marker and not requirement.marker.evaluate():
            row["state"] = "not_applicable"
        else:
            try:
                row["installed_version"] = metadata.version(requirement.name)
                if requirement.url:
                    row["state"] = "unverified_source"
                elif Version(row["installed_version"]) not in requirement.specifier:
                    row["state"] = "incompatible"
            except metadata.PackageNotFoundError:
                row["state"] = "missing"
        result.append(row)
    return result


def doctor(source: Path, *, data_root: Path | None = None) -> dict:
    """Read declarations and receipts; do not load plugins or prepare runtimes."""
    source = Path(source).resolve()
    specs = (read_source_pack(source),) if (source / "pyproject.toml").is_file() else discover_source_packs([source])
    reports = []
    for spec in specs:
        environments = []
        for name, declaration in spec.environments.items():
            if data_root is not None:
                from backend.packs.planning import environment_status
                row = environment_status(spec.owner, name, declaration, spec.source_root, data_root)
            else:
                state = ("needs_configuration" if declaration.management == "external"
                         else "on_use" if declaration.target == "sandbox" else "unchecked")
                error = None
                if declaration.target == "host-worker" and declaration.management == "managed" and declaration.lock:
                    state, error = "unsupported", "This Python provider does not yet support lockfile installation"
                row = {"id": name, "state": state, "error": error, "target": declaration.target,
                       "provider": declaration.provider, "management": declaration.management}
            row["declaration"] = declaration.model_dump(mode="json")
            environments.append(row)
        warnings = []
        if spec.legacy:
            warnings.append("Legacy entry-point source: runtime identity is resolved only when loaded; add oaw.pack.toml")
        if spec.frontend and spec.frontend.mode == "host-build":
            warnings.append("Frontend still uses the host build; provide independently built artifacts before publishing")
        if any(item.target == "sandbox" and item.management == "managed" for item in spec.environments.values()):
            warnings.append("Sandbox readiness depends on the actual selected Sandbox and feature binding")
        reports.append({"spec": spec.model_dump(mode="json"), "spec_digest": spec.spec_digest,
                        "host_requirements": _host_requirements(spec), "environments": environments,
                        "warnings": warnings})
    return {"source": str(source), "packs": reports, "read_only": True}


def _rewrite_wheel(data: bytes, spec: PackSpec, source: Path) -> tuple[bytes, tuple[str, ...]]:
    with ZipFile(io.BytesIO(data)) as wheel:
        files = zip_files(wheel)
    roots = {name.split("/")[0] for name in files}
    infos = [name for name in roots if name.endswith(".dist-info")]
    if len(infos) != 1:
        raise ValueError("Adapter wheel must contain exactly one .dist-info directory")
    prefix = infos[0]
    modules = roots - {prefix}
    if any(not re.fullmatch(r"oaw_[a-z][a-z0-9_]*", name) for name in modules):
        raise ValueError("Adapter wheel roots must use private oaw_ package names")
    pyproject = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8-sig"))
    packages = pyproject.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("packages")
    if not isinstance(packages, list) or any(not isinstance(value, str) for value in packages):
        raise ValueError("Declare owned adapter package paths in tool.hatch.build.targets.wheel.packages before publishing")
    declared_modules = {Path(safe_path(value)).name for value in packages}
    if declared_modules != modules:
        raise ValueError("Adapter wheel package roots differ from the source declaration")
    declarations = ConfigParser(interpolation=None)
    declarations.optionxform = str
    declarations.read_string(files[f"{prefix}/entry_points.txt"].decode("utf-8"))
    extra = set(declarations.sections()) - {"open_agent_world.plugins", "open_agent_world.setup", "console_scripts", "gui_scripts"}
    if extra:
        raise ValueError("Adapter wheel declares unsupported entry-point groups: " + ", ".join(sorted(extra)))
    entries = dict(declarations["open_agent_world.plugins"])
    if entries != spec.entrypoints or len(entries) != 1:
        raise ValueError("Adapter wheel plugin entry point differs from source or is not singular")
    entry = next(iter(entries.values()))
    primary = entry.split(":", 1)[0].split(".", 1)[0]
    if primary not in modules:
        raise ValueError("Adapter entry point does not belong to its declared packages")
    package = BytesParser().parsebytes(files[f"{prefix}/METADATA"])
    if package.get("Version") != spec.version:
        raise ValueError("Adapter wheel version differs from source")
    declared = {str(Requirement(value)) for value in package.get_all("Requires-Dist", [])
                if not (Requirement(value).marker and "extra" in str(Requirement(value).marker))}
    if declared != set(spec.backend.python):
        raise ValueError("Adapter wheel runtime dependencies differ from source pyproject.toml")
    for name, content in files.items():
        if not name.endswith(".py") or name.split("/")[0] not in modules:
            continue
        tree = ast.parse(content, filename=name)
        for node in ast.walk(tree):
            imports = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            if any(module == "backend" or module.startswith("backend.") for module in imports):
                raise ValueError(f"{name} imports private host backend code; migrate it to the public Plugin API before publishing")
    files[f"{prefix}/entry_points.txt"] = f"[open_agent_world.plugins]\n{spec.owner} = {entry}\n".encode()
    if spec.contributions.cards or spec.designs:
        files[f'{prefix}/oaw.pack.json'] = spec.model_dump_json().encode()
    # RECORD must describe the rewritten wheel, never retain upstream signatures.
    for name in (f"{prefix}/RECORD.jws", f"{prefix}/RECORD.p7s"):
        files.pop(name, None)
    record_name = f"{prefix}/RECORD"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, content in sorted(files.items()):
        if name != record_name:
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
            writer.writerow((name, f"sha256={digest}", len(content)))
    writer.writerow((record_name, "", ""))
    files[record_name] = record.getvalue().encode()
    return _zip(files), (primary, *sorted(modules - {primary}))


def _dependencies(spec: PackSpec, source: Path) -> list[dict]:
    if not spec.dependencies:
        return []
    catalog = discover_source_packs([source.parent])
    result = []
    for dependency in spec.dependencies:
        if dependency.optional:
            raise ValueError(f"Optional Pack dependency {dependency.id} needs an explicit release selection before building")
        candidates = [item for item in catalog if
                      (dependency.kind == "owner" and item.owner == dependency.id)
                      or (dependency.kind == "distribution" and item.distribution_id == dependency.id)
                      or (dependency.kind == "pack" and dependency.id in item.packs)]
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous dependency identity: {dependency.id}")
        if not candidates:
            if dependency.kind == "pack":
                result.append({"id": dependency.id, "version": dependency.version})
                continue
            raise ValueError(f"Cannot resolve {dependency.kind} {dependency.id}; supply its source declaration alongside this Pack")
        target = candidates[0]
        if not target.packs:
            raise ValueError(f"Dependency {dependency.id} has no static provided Pack IDs")
        result.append({"id": dependency.id if dependency.kind == "pack" else target.packs[0],
                       "version": dependency.version, "distribution": target.distribution_id})
    return result


def _declarative_wheel(spec: PackSpec) -> tuple[bytes, tuple[str, ...]]:
    """A conventional adapter wheel; installation still uses the same ABI."""
    module = 'oaw_declarative_' + hashlib.sha256(spec.owner.encode()).hexdigest()[:20]
    prefix = f'{module}-{spec.version}.dist-info'
    source = '''import json
from pathlib import Path
from open_agent_world.plugin_api import declarative_plugin
def create_plugin():
    return declarative_plugin(json.loads(Path(__file__).with_name('contributions.json').read_text(encoding='utf-8')))
'''
    files = {
        module + '/__init__.py': source.encode(),
        module + '/contributions.json': spec.model_dump_json().encode(),
        prefix + '/METADATA': ('Metadata-Version: 2.1\nName: ' + module.replace('_', '-') + '\nVersion: ' + spec.version + '\n'
            + ('Requires-Python: ' + spec.backend.requires_python + '\n' if spec.backend.requires_python else '')
            + ''.join('Requires-Dist: ' + item + '\n' for item in spec.backend.python)).encode(),
        prefix + '/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        prefix + '/entry_points.txt': f'[open_agent_world.plugins]\n{spec.owner}={module}:create_plugin\n'.encode(),
    }
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, content in sorted(files.items()):
        writer.writerow((name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode(), len(content)))
    writer.writerow((prefix + '/RECORD', '', ''))
    files[prefix + '/RECORD'] = record.getvalue().encode()
    return _zip(files), (module,)


def passive_wheel_spec(data: bytes, manifest) -> PackSpec | None:
    """Recognize only our generated adapter, by all uncompressed file bytes.

    No author-provided Python is executed or treated as passive by declaration.
    This runs at the installation boundary, not on every application startup.
    """
    if manifest.schema_version != 3 or not manifest.module_name.startswith('oaw_declarative_'):
        return None
    with ZipFile(io.BytesIO(data)) as wheel:
        files = zip_files(wheel)
    raw = files.get(manifest.module_name + '/contributions.json')
    if raw is None:
        return None
    spec = PackSpec.model_validate_json(raw)
    if (spec.entrypoints or not spec.contributions.cards or spec.dependencies or spec.backend.python
            or spec.environments or spec.features or spec.prerequisites or spec.legacy_python_requirements):
        return None
    if (spec.owner, spec.distribution_id, spec.version, spec.packs, spec.node_types) != (
            manifest.owner_id, manifest.id, manifest.version, manifest.provided_pack_ids, manifest.node_types):
        return None
    if spec.compatibility is None or spec.compatibility.model_dump() != manifest.compatibility.model_dump():
        return None
    expected, modules = _declarative_wheel(spec)
    with ZipFile(io.BytesIO(expected)) as wheel:
        if files != zip_files(wheel) or modules != manifest.module_names:
            return None
    return spec


def build_project(source: Path, output: Path, *, backend_wheel: Path | None = None, frontend_dir: Path | None = None, dependency_wheels: Path | None = None,
                  plugin_api: str | None = None, oaw: str | None = None) -> Path:
    """Compile static metadata around an author's already-built wheel and UI."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new artifact path")
    spec = read_source_pack(source)
    if spec.legacy or not spec.owner or not spec.packs:
        raise ValueError("build-project requires oaw.pack.toml with explicit owner and provided Pack IDs")
    compatibility = spec.compatibility.model_dump() if spec.compatibility else {}
    if plugin_api is not None:
        compatibility["plugin_api"] = plugin_api
    if oaw is not None:
        compatibility["oaw"] = oaw
    if "plugin_api" not in compatibility:
        raise ValueError("Declare compatibility.plugin_api or pass --plugin-api matching the runtime descriptor")
    compatibility.setdefault("oaw", ">=0.1.0,<1")
    compatibility.setdefault("frontend_api", 1)
    if spec.contributions.cards and not spec.entrypoints:
        if backend_wheel is not None:
            raise ValueError('Declarative cards generate their adapter; omit --backend-wheel')
        # Validate the same registration before producing an artifact. This only
        # executes Core's compiler, never third-party Python or build hooks.
        from backend.plugins.declarative import declarative_plugin
        from backend.plugins.registry import PluginRegistry
        from backend.packs.spec import CompatibilitySpec
        if Version(compatibility['plugin_api']) < Version('1.37'):
            raise ValueError('Declarative cards require Plugin API 1.37 or newer')
        spec = spec.model_copy(update={'compatibility': CompatibilitySpec.model_validate(compatibility)})
        PluginRegistry().install(declarative_plugin(spec), source_spec=spec)
        wheel, modules = _declarative_wheel(spec)
    else:
        if backend_wheel is None:
            raise ValueError('Advanced Plugin API projects require --backend-wheel')
        wheel, modules = _rewrite_wheel(Path(backend_wheel).read_bytes(), spec, source)
    files = {"backend/adapter.whl": wheel}
    from backend.packs.adapters import collect_wheels
    adapter, dependency_files = collect_wheels(spec.backend.python, dependency_wheels)
    files.update(dependency_files)
    if frontend_dir is None:
        if spec.frontend is not None:
            raise ValueError("A declared frontend requires --frontend-dir with its prebuilt artifacts "
                             "(run the project's `npm run build` first, or remove [components.frontend] "
                             "from oaw.pack.toml for a configuration-only card)")
        files['frontend/index.js'] = b'export default {apiVersion:1,views:{}};\n'
    else:
        frontend_dir = Path(frontend_dir).resolve(strict=True)
        if not frontend_dir.is_dir():
            raise ValueError("Frontend input must be a directory containing index.js")
        for path in sorted(frontend_dir.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(frontend_dir):
                raise ValueError("Frontend input must not contain symlinks")
            if path.is_file():
                files[safe_path("frontend/" + path.relative_to(frontend_dir).as_posix())] = path.read_bytes()
    if "frontend/index.js" not in files:
        raise ValueError("Prebuilt frontend directory must contain index.js")

    def resource(relative: str | None) -> str | None:
        if relative is None:
            return None
        destination = safe_path("runtime/" + relative)
        path = source / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(source) or not path.is_file():
            raise ValueError(f"Unsafe or missing runtime resource: {relative}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != spec.source_files.get(relative):
            raise ValueError(f"Runtime resource changed since static discovery: {relative}")
        files[destination] = data
        return destination

    for path in spec.runtime_resources:
        resource(path)
    environments = {}
    for name, environment in spec.environments.items():
        item = environment.model_dump(mode="json")
        for key in ("verify_script", "entrypoint", "lock"):
            item[key] = resource(item[key])
        for key in ("resources", "requirement_files"):
            item[key] = [resource(value) for value in item[key]]
        item["artifact_hashes"] = {resource(path): digest for path, digest in item["artifact_hashes"].items()}
        for executable_artifact in item["artifacts"]:
            if executable_artifact["path"] is not None:
                executable_artifact["path"] = resource(executable_artifact["path"])
        environments[name] = item
    manifest = PackManifest.model_validate({
        "schema_version": 3, "id": spec.distribution_id, "name": spec.name, "version": spec.version,
        "owner": spec.owner, "packs": spec.packs, "node_types": spec.node_types, "backend_modules": modules,
        "compatibility": compatibility, "dependencies": {"packs": _dependencies(spec, source)},
        "runtime": {"sandbox": {"python": spec.legacy_python_requirements}},
        "entrypoints": {"backend": "backend/adapter.whl", "frontend": "frontend/index.js"},
        "environments": environments, "features": {name: value.model_dump() for name, value in spec.features.items()},
        "prerequisites": {name: value.model_dump() for name, value in spec.prerequisites.items()},
        "adapter": adapter.model_dump(),
    })
    files["manifest.json"] = manifest.model_dump_json(indent=2).encode()
    files["checksums.json"] = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                                         sort_keys=True, indent=2).encode()
    artifact = _zip(files)
    # Publication validates the delivered bytes. A developer's modified local
    # optional library does not change those bytes; end-user inspection still
    # checks actual host shadowing before any installation/preparation.
    inspect_archive(artifact, check_host_libraries=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(artifact)
    return output
