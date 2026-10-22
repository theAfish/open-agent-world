"""Hash-locked shared adapter libraries, outside the application's site-packages.

These libraries deliberately share the host Python namespace. Compatibility is
checked before activation; dependency conflicts require another worker process,
not a per-Pack sys.path or an accidental replacement of an imported module.
"""
from __future__ import annotations

from email.parser import BytesParser
from functools import lru_cache
import hashlib
from importlib import metadata
from importlib.machinery import PathFinder, SourceFileLoader
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from uuid import uuid4
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import parse_tag, sys_tags
from packaging.utils import canonicalize_name
from packaging.version import Version

from backend.packs.manifest import AdapterDependencies, AdapterWheel, safe_path

_ACTIVATED_WHEELS: dict[Path, AdapterWheel] = {}


def _active(requirement: Requirement, extras=()) -> bool:
    return not requirement.marker or any(requirement.marker.evaluate({"extra": extra}) for extra in ("", *extras))


def host_distributions() -> dict[str, str]:
    """Mandatory host closure that every published Pack may depend on directly."""
    # Keep this public runtime contract aligned with backend/pyproject.toml.
    pending = [Requirement(value) for value in (
        "httpx>=0.28,<1", "cryptography>=45,<51", "fastapi>=0.116,<1",
        "pydantic>=2.11,<3", "packaging>=24,<27", "uvicorn[standard]>=0.35,<1")]
    result, visited = {}, set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in visited or not _active(requirement):
            continue
        visited.add(key)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        result[name] = distribution.version
        for text in distribution.requires or ():
            child = Requirement(text)
            if _active(child, requirement.extras):
                child.marker = None
                pending.append(child)
    return result


@lru_cache(maxsize=1)
def host_optional_distributions() -> dict[str, str]:
    """Installed OAW optional providers, usable only at target activation.

    These are never omitted by the author-side wheel resolver: a clean host may
    not have enabled them. OAW does not mutate host site-packages at runtime, so
    this process-lifetime inventory only changes after an application restart.
    """
    site_roots = {Path(sysconfig.get_path(kind)).resolve() for kind in ('purelib', 'platlib')}
    # Keep aligned with backend/pyproject.toml optional-dependencies. This is an
    # explicit host contract, not permission to reuse arbitrary site-packages.
    pending = [Requirement(value) for value in (
        'authlib>=1.7.2,<2', 'itsdangerous>=2.2,<3', 'google-adk>=2.8,<3', 'litellm>=1.74,<2')]
    result, seen = {}, set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if key in seen or not _active(requirement):
            continue
        seen.add(key)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        if (Path(distribution.locate_file('')).resolve() not in site_roots
                or Version(distribution.version) not in requirement.specifier):
            continue
        result[name] = distribution.version
        for value in distribution.requires or ():
            child = Requirement(value)
            if _active(child, requirement.extras):
                child.marker = None
                pending.append(child)
    return result


def _host_provides_wheel(wheel: AdapterWheel) -> bool:
    version = host_optional_distributions().get(wheel.name)
    return version is not None and Version(version) == Version(wheel.version)


def adapter_library_inventory(adapter: AdapterDependencies) -> list[dict]:
    """Describe the target's active ownership without preparing any libraries."""
    rows = {wheel.name: {'name': wheel.name, 'version': wheel.version,
                         'management': 'host-provided' if _host_provides_wheel(wheel) else 'managed'}
            for wheel in adapter.wheels}
    host = {**host_distributions(), **host_optional_distributions()}
    for text in adapter.requirements:
        requirement = Requirement(text)
        name = canonicalize_name(requirement.name)
        if _active(requirement) and name not in rows:
            version = host.get(name)
            provided = version is not None and Version(version) in requirement.specifier
            rows[name] = {'name': name, 'version': version,
                          'management': 'host-provided' if provided else 'managed'}
    return [rows[name] for name in sorted(rows)]


def wheel_contents(data: bytes) -> tuple[dict, dict[str, bytes]]:
    """Validate a library wheel without import, install hooks, or native loading."""
    from backend.packs.archive import zip_files
    with ZipFile(io.BytesIO(data)) as archive:
        raw = zip_files(archive)
    infos = {name.split('/')[0] for name in raw if name.split('/')[0].endswith('.dist-info')}
    if len(infos) != 1:
        raise ValueError("Dependency wheel requires one .dist-info directory")
    prefix = infos.pop()
    try:
        package = BytesParser().parsebytes(raw[prefix + '/METADATA'])
        wheel = BytesParser().parsebytes(raw[prefix + '/WHEEL'])
    except KeyError as exc:
        raise ValueError("Dependency wheel is missing standard metadata") from exc
    name = canonicalize_name(package.get('Name', ''))
    version = str(Version(package.get('Version', '')))
    tags = tuple(wheel.get_all('Tag', ()))
    if not tags:
        raise ValueError("Dependency wheel has no compatibility tags")
    for tag in tags:
        parse_tag(tag)
    files = {}
    for path, content in raw.items():
        pieces = path.split('/')
        if pieces[0].endswith('.data'):
            if len(pieces) < 3:
                raise ValueError("Malformed wheel data path")
            if pieces[1] in {'scripts', 'headers'}:
                # Runtime libraries need neither standalone CLI wrappers nor
                # C headers for compiling other packages on the end-user host.
                continue
            if pieces[1] not in {'purelib', 'platlib'}:
                raise ValueError("Dependency wheel uses an unsupported external data location")
            path = '/'.join(pieces[2:])
        if path.endswith(('.pth', '.pyc')) or '__pycache__' in path.split('/'):
            raise ValueError("Adapter dependency wheels cannot contain path hooks or bytecode")
        if path in files:
            raise ValueError("Dependency wheel library paths collide")
        files[safe_path(path)] = content
    roots = tuple(sorted({path.split('/')[0] for path in files if not path.startswith(prefix + '/')}))
    namespace_roots = tuple(root for root in roots if root not in files and root + '/__init__.py' not in files)
    for root in roots:
        module = root.split('.')[0]
        if module in sys.stdlib_module_names or module in {'backend', 'open_agent_world'} or module.startswith('oaw_'):
            raise ValueError(f"Dependency wheel shadows a protected module: {root}")
    info = dict(name=name, version=version, requires=tuple(package.get_all('Requires-Dist', ())),
                requires_python=package.get('Requires-Python', ''), tags=tags, roots=roots,
                namespace_roots=namespace_roots,
                file_hashes={name: hashlib.sha256(value).hexdigest() for name, value in files.items()
                             if name.split('/')[0] in roots})
    return info, files


def describe_wheel(data: bytes, path: str, *, source: str | None = None) -> AdapterWheel:
    info, _ = wheel_contents(data)
    return AdapterWheel(path=path, sha256=hashlib.sha256(data).hexdigest(), source=source, **info)


def verify_wheel(declaration: AdapterWheel, data: bytes, *, check_host: bool = True) -> dict[str, bytes]:
    if hashlib.sha256(data).hexdigest() != declaration.sha256:
        raise ValueError(f"Adapter dependency checksum mismatch: {declaration.name}")
    info, files = wheel_contents(data)
    if any(getattr(declaration, key) != value for key, value in info.items()):
        raise ValueError(f"Adapter dependency metadata mismatch: {declaration.name}")
    if not any(parse_tag(tag) & set(sys_tags()) for tag in declaration.tags):
        raise ValueError(f"Adapter wheel is incompatible with this Python/platform: {declaration.name}")
    if Version(platform.python_version()) not in SpecifierSet(declaration.requires_python):
        raise ValueError(f"Adapter dependency requires another Python: {declaration.name}")
    if not check_host:
        return files
    try:
        installed = metadata.distribution(declaration.name)
    except metadata.PackageNotFoundError:
        installed = None
    host_provided = installed is not None and _host_provides_wheel(declaration)
    if host_provided:
        recorded = {str(name).replace('\\', '/') for name in installed.files or ()}
        installed_root = Path(installed.locate_file('')).resolve()
        for name in files:
            if name.split('/')[0].endswith('.dist-info'):
                continue
            actual = Path(installed.locate_file(name))
            if name not in recorded or not actual.is_file() or not actual.resolve().is_relative_to(installed_root):
                raise ValueError(f'Host provider does not own the declared adapter file: {declaration.name}/{name}')
        if declaration.source and not _installed_source(declaration.name, declaration.source):
            raise ValueError(f'Host provider has unverified source: {declaration.name}')
    for root in declaration.roots:
        module_name = root[:-3] if root.endswith('.py') else root
        if not module_name.isidentifier():
            continue
        existing = PathFinder.find_spec(module_name)
        if existing is None or existing.origin in {'built-in', 'frozen'}:
            continue
        origin = Path(existing.origin) if existing.origin else None
        if host_provided and origin is not None:
            expected_origin = Path(installed.locate_file(root if root.endswith('.py') else root + '/__init__.py')).resolve()
            if root not in declaration.namespace_roots and origin.resolve() != expected_origin:
                raise ValueError(f'Host provider module has a different origin: {module_name}')
        if root not in declaration.namespace_roots and installed is None:
            raise ValueError(f"Adapter library root is already owned by another distribution: {root}")
        for name, value in files.items():
            if name.split('/')[0] != root:
                continue
            candidates = ([origin] if name == root and origin else
                          [Path(base) / name.split('/', 1)[1] for base in existing.submodule_search_locations or ()])
            for actual in candidates:
                if host_provided:
                    owned = Path(installed.locate_file(name))
                    # The same path cannot have two different resolved origins.
                    # Ownership/containment was checked above for every file;
                    # only aliases need another pair of filesystem resolutions.
                    if actual == owned or actual.resolve() == owned.resolve():
                        continue
                if actual.is_file() and hashlib.sha256(actual.read_bytes()).digest() != hashlib.sha256(value).digest():
                    raise ValueError(f"Adapter contribution conflicts with existing host file: {name}")
    if installed is not None:
        # Appending a cache path cannot replace an existing host package.
        # Explicit host providers retain host ownership; every other installed
        # library/resource must match the locked wheel bytes and module origin.
        for name, value in files.items():
            if name.split('/')[0].endswith('.dist-info'):
                continue
            actual = Path(installed.locate_file(name))
            if (not actual.is_file() or not host_provided and hashlib.sha256(actual.read_bytes()).digest()
                    != hashlib.sha256(value).digest()):
                raise ValueError(f"Existing host library differs from the locked adapter wheel: {declaration.name}/{name}")
            if name.endswith('.py'):
                module_name = name.removesuffix('.py').removesuffix('/__init__').replace('/', '.')
                loaded = sys.modules.get(module_name)
                origin = getattr(loaded, '__file__', None)
                if loaded is not None and (origin is None or Path(origin).resolve() != actual.resolve()):
                    raise ValueError(f"Loaded adapter module has a different origin: {module_name}; restart OAW")
    return files


def _installed_source(name: str, source: str) -> bool:
    try:
        distribution = metadata.distribution(name)
        active = _ACTIVATED_WHEELS.get(Path(distribution.locate_file('')).resolve())
        if active is not None and active.name == canonicalize_name(name) and active.source == source:
            # pip-built wheels do not contain installation direct_url.json.
            # Their pinned provenance was bound to the canonical wheel digest
            # and verified before this immutable path entered sys.path.
            return True
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    except (metadata.PackageNotFoundError, ValueError):
        return False
    if source.startswith('git+'):
        url, _, revision = source[4:].rpartition('@')
        return direct.get('url') == url and direct.get('vcs_info', {}).get('commit_id') == revision
    return direct.get('url') == source


def check_adapter_dependencies(adapters, *, allow_existing=False) -> None:
    """Validate the complete shared graph, including extras and loaded host state."""
    adapters = tuple(adapters)
    wheels = {}
    for adapter in adapters:
        for wheel in adapter.wheels:
            previous = wheels.get(wheel.name)
            if previous and (previous.version, previous.sha256) != (wheel.version, wheel.sha256):
                raise ValueError(f"Conflicting shared adapter dependency: {wheel.name}")
            wheels[wheel.name] = wheel
    roots, file_hashes = {}, {}
    for wheel in wheels.values():
        for root in wheel.roots:
            key = root.split('.')[0]
            previous = roots.get(key)
            if previous and previous.name != wheel.name and not (root in wheel.namespace_roots or root in previous.namespace_roots):
                raise ValueError(f"Conflicting shared adapter module ownership: {root}")
            if not previous or root not in wheel.namespace_roots:
                roots[key] = wheel
        for name, digest in wheel.file_hashes.items():
            if name in file_hashes and file_hashes[name] != digest:
                raise ValueError(f"Conflicting shared adapter file ownership: {name}")
            file_hashes[name] = digest
    host = host_distributions()
    pending = [Requirement(text) for adapter in adapters for text in adapter.requirements]
    seen = set()
    while pending:
        requirement = pending.pop()
        if not _active(requirement):
            continue
        name = canonicalize_name(requirement.name)
        key = (str(requirement), tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        wheel = wheels.get(name)
        if wheel:
            version = wheel.version
            if requirement.url and wheel.source != requirement.url:
                raise ValueError(f"Adapter direct reference lacks matching pinned provenance: {name}")
            if not any(parse_tag(tag) & set(sys_tags()) for tag in wheel.tags):
                raise ValueError(f"Adapter wheel is incompatible with this Python/platform: {name}")
            if Version(platform.python_version()) not in SpecifierSet(wheel.requires_python):
                raise ValueError(f"Adapter dependency requires another Python: {name}")
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                installed = None
            if installed and Version(installed) != Version(version):
                raise ValueError(f"Shared adapter dependency conflicts with host: {name} {version} versus {installed}; use compatible artifacts or a worker")
            if installed and wheel.source and not _installed_source(name, wheel.source):
                raise ValueError(f"Existing host adapter shadows pinned source with unverified provenance: {name}")
            if installed:
                origin = Path(metadata.distribution(name).locate_file('')).resolve()
                if origin.parent.name == 'adapter-wheels' and origin.name != wheel.sha256:
                    raise ValueError(f"Loaded shared adapter wheel has a different digest: {name}; restart OAW")
            dependencies = wheel.requires
        elif name in host or allow_existing:
            try:
                distribution = metadata.distribution(name)
            except metadata.PackageNotFoundError as exc:
                raise ValueError(f"Missing adapter dependency wheel: {name}") from exc
            version = distribution.version
            if requirement.url and not _installed_source(name, requirement.url):
                raise ValueError(f"Installed adapter dependency has unverified source: {name}")
            dependencies = distribution.requires or ()
        else:
            raise ValueError(f"Missing adapter dependency wheel: {name}; run resolve-adapter and pass --dependency-wheels")
        if Version(version) not in requirement.specifier:
            raise ValueError(f"Incompatible shared adapter dependency: {requirement} (available {version})")
        for value in dependencies:
            child = Requirement(value)
            if _active(child, requirement.extras):
                child.marker = None
                pending.append(child)


def collect_wheels(requirements, directory: Path | None) -> tuple[AdapterDependencies, dict[str, bytes]]:
    requirements = tuple(requirements)
    sources = {canonicalize_name(Requirement(text).name): Requirement(text).url for text in requirements if Requirement(text).url}
    wheels, files = [], {}
    host = host_distributions()
    if directory is not None:
        directory = Path(directory).resolve(strict=True)
        for path in sorted(directory.glob('*.whl')):
            if path.is_symlink() or not path.resolve().is_relative_to(directory):
                raise ValueError("Unsafe dependency wheel path")
            data = path.read_bytes()
            info, _ = wheel_contents(data)
            if info['name'] in host:
                if Version(info['version']) != Version(host[info['name']]):
                    raise ValueError(f"Resolved wheel changes the mandatory host contract: {info['name']}")
                continue
            target = 'backend/dependencies/' + safe_path(path.name)
            wheels.append(describe_wheel(data, target, source=sources.get(info['name'])))
            files[target] = data
    adapter = AdapterDependencies(requirements=requirements, wheels=tuple(wheels))
    check_adapter_dependencies([adapter])
    return adapter, files


def resolve_adapter(spec, directory: Path) -> Path:
    """Explicit author/install operation: resolve wheels in a private directory."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("Dependency wheel output directory must be empty")
    for text in spec.backend.python:
        requirement = Requirement(text)
        if requirement.url and not re.fullmatch(r'git\+https://[^\s@]+@(?:[a-f0-9]{40}|[a-f0-9]{64})', requirement.url):
            raise ValueError("Adapter source references must pin a full HTTPS Git commit")
    requirements = directory / 'requirements.txt'
    constraints = directory / 'host-constraints.txt'
    requirements.write_text('\n'.join(spec.backend.python) + '\n', encoding='utf-8')
    # This is a shared namespace: keep every currently installed version stable.
    # The resulting wheelhouse remains portable to another compatible host.
    installed = {canonicalize_name(dist.metadata['Name']): dist.version for dist in metadata.distributions()
                 if dist.metadata.get('Name') and not canonicalize_name(dist.metadata['Name']).startswith(('oaw-', 'open-agent-world'))}
    constraints.write_text('\n'.join(f'{name}=={version}' for name, version in sorted(installed.items())) + '\n', encoding='utf-8')
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError("Resolving adapter wheels requires uv")
    command = [uv, 'tool', 'run', '--from', 'pip', 'pip', '--python', sys.executable,
               'wheel', '--wheel-dir', str(directory), '--constraint', str(constraints), '-r', str(requirements)]
    verbose = os.environ.get('OPEN_AGENT_WORLD_SETUP_VERBOSE') == '1'
    if verbose:
        command.append('--verbose')
        print(f'Resolving adapter wheels: {", ".join(spec.backend.python)}', flush=True)
    result = subprocess.run(command, capture_output=not verbose, text=True, timeout=1800)
    if result.returncode:
        detail = ((result.stderr or result.stdout) or
                  f'exit code {result.returncode}; see dependency output above')
        raise RuntimeError('Adapter wheel resolution failed: ' + detail[-6000:])
    collect_wheels(spec.backend.python, directory)
    return directory


def prepare_adapters(adapter: AdapterDependencies, files: dict[str, bytes], data_root: Path, *, consumer: str) -> tuple[Path, ...]:
    from backend.packs.environments import installation_lock
    from backend.packs.lifecycle import ManagedResourceLedger
    from backend.packs.modules import _regular, _verify_tree
    check_adapter_dependencies([adapter])
    base = Path(data_root).resolve() / 'packs'
    cache = base / 'adapter-wheels'
    cache.mkdir(parents=True, exist_ok=True)
    ledger = ManagedResourceLedger(data_root)
    paths = []
    for wheel in adapter.wheels:
        contents = verify_wheel(wheel, files[wheel.path])
        # Preserve original bytes for explicit integrity checks and repair.
        contents['.oaw-wheel.whl'] = files[wheel.path]
        target = cache / wheel.sha256
        with installation_lock(base / 'adapter-locks' / (wheel.sha256 + '.lock')):
            if target.exists() or target.is_symlink():
                _verify_tree(target, contents)
            else:
                stage = cache / ('.stage-' + uuid4().hex)
                stage.mkdir()
                try:
                    for name, value in contents.items():
                        path = stage / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(value)
                    _verify_tree(stage, contents)
                    stage.replace(target)
                finally:
                    if stage.exists():
                        _regular(stage, cache, directory=True)
                        if stage.parent.resolve() != cache:
                            raise ValueError('Unsafe adapter staging cleanup')
                        shutil.rmtree(stage)
            resource = 'adapter:' + wheel.sha256
            ledger.register(resource, target, 'adapter-wheel')
            ledger.claim(consumer, resource)
        paths.append(target)
    return tuple(paths)


def prepared_adapter_paths(adapter: AdapterDependencies, data_root: Path, *, read_only: bool = False) -> tuple[Path, ...]:
    """Full integrity check for installation/diagnostics, never ordinary startup."""
    from backend.packs.modules import _verify_tree
    check_adapter_dependencies([adapter])
    paths = []
    for wheel in adapter.wheels:
        path = Path(data_root).resolve() / 'packs' / 'adapter-wheels' / wheel.sha256
        data = (path / '.oaw-wheel.whl').read_bytes()
        files = verify_wheel(wheel, data)
        files['.oaw-wheel.whl'] = data
        _verify_tree(path, files, discard_bytecode=not read_only)
        paths.append(path)
    return tuple(paths)


def check_prepared_adapter_paths(adapter: AdapterDependencies, data_root: Path) -> tuple[Path, ...]:
    """Check recorded installations without reading archives or walking files.

    Prepared code is trusted host-managed software, like site-packages. Runtime
    users must not have write access to it. Content verification belongs to the
    installer and the explicit integrity command, not every process startup.
    """
    from backend.packs.modules import check_module_roots, _regular
    active = tuple(wheel for path, wheel in _ACTIVATED_WHEELS.items() if str(path) in sys.path)
    check_adapter_dependencies([adapter, *(AdapterDependencies(wheels=(wheel,)) for wheel in active)])
    cache = Path(data_root).resolve() / 'packs' / 'adapter-wheels'
    paths = []
    for wheel in adapter.wheels:
        path = cache / wheel.sha256
        check_module_roots(path, wheel.roots, boundary=Path(data_root).resolve())
        _regular(path / '.oaw-wheel.whl', path, directory=False)
        _check_adapter_origins(wheel)
        paths.append(path)
    return tuple(paths)


def _check_adapter_origins(wheel: AdapterWheel) -> None:
    """Keep namespace ownership checks without rehashing installed host files."""
    try:
        installed = metadata.distribution(wheel.name)
    except metadata.PackageNotFoundError:
        installed = None
    for root in wheel.roots:
        module_name = root[:-3] if root.endswith('.py') else root
        if not module_name.isidentifier():
            continue
        existing = PathFinder.find_spec(module_name)
        if existing is None:
            continue
        if existing.origin in {'built-in', 'frozen'}:
            raise ValueError(f'Adapter library shadows a host module: {module_name}')
        if root in wheel.namespace_roots:
            continue  # Disjoint contributions are checked against recorded file ownership.
        if installed is None:
            raise ValueError(f'Adapter library root is already owned by another distribution: {root}')
        expected = Path(installed.locate_file(root if root.endswith('.py') else root + '/__init__.py')).resolve()
        if existing.origin is None or Path(existing.origin).resolve() != expected:
            raise ValueError(f'Host provider module has a different origin: {module_name}')
        loaded = sys.modules.get(module_name)
        if loaded is not None and (getattr(loaded, '__file__', None) is None
                                  or Path(loaded.__file__).resolve() != expected):
            raise ValueError(f'Loaded adapter module has a different origin: {module_name}; restart OAW')


class _ImmutableAdapterLoader(SourceFileLoader):
    """Never read or write bytecode in an immutable application bundle."""

    def get_code(self, fullname):
        return self.source_to_code(self.get_data(self.path), self.path)


class _SharedAdapterFinder:
    """Merge verified package contributions, like a shared site-packages tree.

    Wheels such as pymupdf_layout intentionally add files below another wheel's
    regular package. Package search locations must include both immutable trees;
    ordinary per-wheel sys.path entries alone cannot implement that layout.
    """
    roots: dict[str, list[Path]] = {}
    read_only_roots: set[Path] = set()

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        root = fullname.split('.')[0]
        if root not in cls.roots:
            return None
        parts = fullname.split('.')
        parents = list(sys.path if path is None else path)
        for base in cls.roots[root]:
            parent = str(base.joinpath(*parts[:-1]))
            if parent not in parents:
                parents.append(parent)
        spec = PathFinder.find_spec(fullname, parents, target)
        if spec is not None and spec.submodule_search_locations is not None:
            locations = list(spec.submodule_search_locations)
            for parent in parents:
                candidate = Path(parent) / parts[-1]
                if candidate.is_dir() and str(candidate) not in locations:
                    locations.append(str(candidate))
            spec.submodule_search_locations = locations
        if (spec is not None and isinstance(spec.loader, SourceFileLoader)
                and any(Path(spec.origin).is_relative_to(base) for base in cls.read_only_roots)):
            spec.loader = _ImmutableAdapterLoader(fullname, spec.origin)
        return spec


def activate_adapters(adapter: AdapterDependencies, data_root: Path, *, read_only: bool = False) -> tuple[Path, ...]:
    """Activate compatible installed libraries without repeating installation."""
    paths = check_prepared_adapter_paths(adapter, data_root)
    if read_only:
        _SharedAdapterFinder.read_only_roots.update(paths)
    for path in paths:
        if str(path) not in sys.path:
            sys.path.append(str(path))
    for wheel, path in zip(adapter.wheels, paths):
        _ACTIVATED_WHEELS[path] = wheel
        for root in wheel.roots:
            root = root[:-3] if root.endswith('.py') else root
            if not root.isidentifier():
                continue
            locations = _SharedAdapterFinder.roots.setdefault(root, [])
            if path not in locations:
                locations.append(path)
            for name, module in tuple(sys.modules.items()):
                if (name == root or name.startswith(root + '.')) and hasattr(module, '__path__'):
                    extension = path.joinpath(*name.split('.'))
                    if extension.is_dir() and str(extension) not in module.__path__:
                        module.__path__ = [*module.__path__, str(extension)]
    if adapter.wheels and _SharedAdapterFinder not in sys.meta_path:
        sys.meta_path.insert(sys.meta_path.index(PathFinder), _SharedAdapterFinder)
    return paths


def _source_receipt(spec, data_root):
    return Path(data_root).resolve() / 'packs' / 'adapter-sources' / (spec.spec_digest + '.json')


def source_adapter_resource_ids(spec, data_root) -> set[str]:
    location = _source_adapter_location(spec, data_root, include_bundle=False)
    if location is None:
        return set()
    adapter, _ = location
    return {'adapter:' + wheel.sha256 for wheel in adapter.wheels}


def _source_adapter_location(spec, data_root, *, include_bundle=True):
    """Resolve one receipt authority for planning and activation, without writes."""
    roots = [Path(data_root).resolve()]
    bundled = (Path(sys.prefix) / 'oaw-runtime-bundle').resolve()
    if include_bundle and bundled not in roots:
        roots.append(bundled)
    for root in roots:
        path = _source_receipt(spec, root)
        if path.is_file():
            adapter = AdapterDependencies.model_validate_json(path.read_bytes())
            if adapter.requirements != spec.backend.python:
                raise ValueError('Source adapter receipt differs from its declaration')
            return adapter, root
    return None


def source_adapter_declaration(spec, data_root) -> AdapterDependencies:
    location = _source_adapter_location(spec, data_root)
    return location[0] if location else AdapterDependencies(requirements=spec.backend.python)


def activate_source_adapter(spec, data_root):
    location = _source_adapter_location(spec, data_root)
    if location:
        adapter, root = location
        return activate_adapters(adapter, root, read_only=root == (Path(sys.prefix) / 'oaw-runtime-bundle').resolve())
    # A developer's existing environment remains supported, with explicit
    # installed-distribution and pinned-source provenance checks.
    check_adapter_dependencies([AdapterDependencies(requirements=spec.backend.python)], allow_existing=True)
    return ()


def _check_source_adapter_selection(adapter, data_root):
    from backend.packs.installation import PackInstallationManager
    selected = PackInstallationManager(Path(data_root)).selected()
    check_adapter_dependencies([adapter, *(manifest.adapter for manifest in selected.values())])


def prepare_source_adapter(spec, data_root, *, dependency_wheels: Path | None = None, consumer: str | None = None):
    from backend.packs.environments import installation_lock
    from backend.packs.lifecycle import ManagedResourceLedger
    consumer = consumer or 'source:' + spec.distribution_id
    root = Path(data_root).resolve() / 'packs'
    with installation_lock(root / 'adapter-source-locks' / (spec.spec_digest + '.lock')):
        receipt = _source_receipt(spec, data_root)
        if receipt.is_file():
            adapter = source_adapter_declaration(spec, data_root)
            _check_source_adapter_selection(adapter, data_root)
            try:
                ledger = ManagedResourceLedger(data_root)
                for wheel in adapter.wheels:
                    ledger.claim(consumer, 'adapter:' + wheel.sha256)
                return prepared_adapter_paths(adapter, data_root)
            except FileNotFoundError:
                # Source dependency artifacts may have been collected after
                # uninstall. Explicit restore resolves them again; tampering
                # raises ValueError and is never hidden by an automatic repair.
                pass
        private = None
        try:
            if dependency_wheels is None:
                bundled_root = (Path(sys.prefix) / 'oaw-runtime-bundle').resolve()
                bundled = (_source_adapter_location(spec, bundled_root, include_bundle=False)
                           if bundled_root != Path(data_root).resolve() else None)
                if bundled:
                    adapter, bundled_root = bundled
                    bundled_paths = prepared_adapter_paths(adapter, bundled_root, read_only=True)
                    files = {wheel.path: (path / '.oaw-wheel.whl').read_bytes()
                             for wheel, path in zip(adapter.wheels, bundled_paths)}
                else:
                    try:
                        adapter, files = collect_wheels(spec.backend.python, None)
                    except ValueError:
                        private = root / 'staging' / ('adapter-resolve-' + uuid4().hex)
                        dependency_wheels = resolve_adapter(spec, private)
            if dependency_wheels is not None:
                adapter, files = collect_wheels(spec.backend.python, dependency_wheels)
            _check_source_adapter_selection(adapter, data_root)
            paths = prepare_adapters(adapter, files, data_root, consumer=consumer)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            stage = receipt.with_suffix('.' + uuid4().hex + '.tmp')
            stage.write_text(adapter.model_dump_json(), encoding='utf-8')
            stage.replace(receipt)
            return paths
        finally:
            if private is not None and private.exists():
                if private.resolve().parent != (root / 'staging').resolve() or private.is_symlink():
                    raise ValueError('Unsafe adapter resolver cleanup')
                shutil.rmtree(private)
