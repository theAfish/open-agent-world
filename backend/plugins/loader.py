from __future__ import annotations

import sys
import logging
import os
import tomllib
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from backend.plugins.builtin import create_builtin_registry
from backend.plugins.registry import PluginDescriptor, PluginRegistry
from backend.startup_progress import startup_stage


ENTRY_POINT_GROUP = "open_agent_world.plugins"


def create_plugin(factory, *, data_root=None, source_root=None):
    """Existing zero-argument factories remain valid; context is opt-in."""
    import inspect
    from backend.config import _default_data_root
    from open_agent_world.plugin_api.context import PluginContext
    parameter = inspect.signature(factory).parameters.get('context')
    if parameter and parameter.kind in {parameter.KEYWORD_ONLY, parameter.POSITIONAL_OR_KEYWORD}:
        return factory(context=PluginContext(Path(data_root or _default_data_root()).resolve(), source_root))
    return factory()


def _failure(registry, identifier, origin, phase, exc, *, owner=None):
    # Storage corruption and resource exhaustion are host failures, including
    # when surfaced through plugin code. Never turn them into optional warnings.
    from backend.plugins.failures import raise_if_unsafe
    raise_if_unsafe(exc)
    if phase == 'accounting':
        raise exc
    logging.getLogger(__name__).error("Plugin %s unavailable during %s (%s): %s", identifier, phase, origin, exc)
    registry.load_failures.append(dict(id=identifier, owner=owner, source=str(origin),
        state="unavailable", phase=phase, error=str(exc), retry="repair_and_restart"))


def _check_strict(registry, strict):
    if strict and registry.load_failures:
        raise RuntimeError("Optional plugin checks failed: " + "; ".join(
            f"{row['id']} from {row['source']} ({row['phase']}): {row['error']}" for row in registry.load_failures))


def load_plugin_registry(plugin_directory: Path | None = None, *, plugin_directories: tuple[Path, ...] = (),
                         data_root: Path | None = None, load_installed: bool = True,
                         load_unenrolled_sources: bool = False, strict: bool = False,
                         core_only: bool = False, load_entry_points: bool = True) -> PluginRegistry:
    """Discover metadata first; isolate optional activation failures per owner.

    Core registration and host persistence errors remain fatal. Strict mode is
    for CI/release validation, and reports every failed optional contribution.
    Nothing here installs dependencies or repairs prepared artifacts.
    """
    registry = create_builtin_registry()
    if core_only or os.environ.get("OPEN_AGENT_WORLD_CORE_ONLY") == "1":
        registry.source_discovery_complete = False
        return registry
    from backend.packs.spec import read_source_pack, _requirements
    from backend.packs.installation import PackInstallationManager
    directory = plugin_directory if plugin_directory is not None else Path(__file__).resolve().parents[2] / "plugins"
    enrollment = PackInstallationManager(data_root) if data_root is not None else None
    bundled_directory = directory.resolve()
    local, source_specs, source_identities = [], {}, {}
    seen_roots = set()
    for directory in dict.fromkeys((directory, *plugin_directories)):
        if not directory.is_dir():
            continue
        for package in ([directory] if (directory / 'pyproject.toml').is_file() else sorted(directory.iterdir())):
            manifest = package / "pyproject.toml"
            if not package.is_dir() or not manifest.is_file() or package.resolve() in seen_roots:
                continue
            seen_roots.add(package.resolve())
            try:
                with manifest.open("rb") as stream:
                    project = tomllib.load(stream).get("project", {})
                declarations = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
                if not isinstance(declarations, dict):
                    raise TypeError(f"{ENTRY_POINT_GROUP} must be a table")
                if not declarations and not (package / 'oaw.pack.toml').is_file():
                    continue
                # Reserve discovered entrypoints even when static validation fails:
                # an editable distribution must not bypass a broken declaration.
                entries = [EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)
                           for name, value in sorted(declarations.items()) if isinstance(value, str)]
                local.extend((entry, manifest) for entry in entries)
                if not entries:
                    local.append((None, manifest))
                spec = read_source_pack(package)
                if spec.compatibility:
                    from backend import __version__
                    from backend.plugins.registry import _supports_plugin_api
                    from packaging.specifiers import SpecifierSet
                    from packaging.version import Version
                    if not _supports_plugin_api(spec.compatibility.plugin_api) or Version(__version__) not in SpecifierSet(spec.compatibility.oaw):
                        raise ValueError('Source Pack requires an incompatible OAW or Plugin API version')
                identities = {value for value in (spec.owner, spec.distribution_id, *spec.packs) if value}
                conflicts = [path for path, previous in source_identities.items() if identities & previous]
                # Keep conflicting identities reserved so a third declaration
                # cannot become authoritative just because the first two failed.
                source_identities[manifest] = identities
                if conflicts:
                    for path in conflicts:
                        previous = source_specs[path]
                        if previous is None:
                            continue
                        source_specs[path] = None
                        _failure(registry, previous.distribution_id, path, "discovery",
                                 ValueError("Ambiguous source Pack identity"), owner=previous.owner)
                    raise ValueError("Ambiguous source Pack identity")
                source_specs[manifest] = spec
                if spec.owner:
                    registry.pack_specs[spec.owner] = spec
                    registry.pack_roots[spec.owner] = package
                    registry.pack_sources[spec.owner] = "bundled" if package.parent.resolve() == bundled_directory else "development"
            except (Exception, SystemExit) as exc:
                source_specs[manifest] = None
                _failure(registry, package.name, manifest, "discovery", exc)
    discovered = entry_points() if load_entry_points else ()
    selected = discovered.select(group=ENTRY_POINT_GROUP) if hasattr(discovered, "select") else discovered.get(ENTRY_POINT_GROUP, ()) if isinstance(discovered, dict) else ()
    local_keys = {(entry.name, entry.value) for entry, _ in local if entry is not None}
    candidates = list(local)
    for entry in sorted(selected, key=lambda item: item.name):
        if (entry.name, entry.value) in local_keys:
            continue
        try:
            # Editable distribution metadata can outlive a broken/changed source
            # manifest. Its recorded source is authoritative; never activate its
            # stale entrypoint as an independent installed distribution.
            import json
            from urllib.parse import urlsplit
            from urllib.request import url2pathname
            direct = entry.dist.read_text('direct_url.json') if entry.dist else None
            if direct:
                location = urlsplit(json.loads(direct)['url'])
                if location.scheme == 'file' and Path(url2pathname(location.path)).resolve() in seen_roots:
                    continue
            candidates.append((entry, "installed distribution"))
        except (Exception, SystemExit) as exc:
            _failure(registry, entry.name, 'installed distribution metadata', 'discovery', exc)
    pending = []
    for entry, origin in candidates:
        spec = source_specs.get(origin)
        if isinstance(origin, Path) and spec is None:
            continue
        if spec is not None and enrollment and not load_unenrolled_sources and not enrollment.source_enrolled(spec.distribution_id):
            continue
        phase, previous_path = "environment", list(sys.path)
        try:
            if spec is not None and data_root is not None:
                from backend.packs.lifecycle import ManagedResourceLedger, environment_resources
                from backend.packs.adapters import activate_source_adapter, source_adapter_resource_ids
                resources = environment_resources(spec, origin.parent) | source_adapter_resource_ids(spec, data_root)
                phase = 'accounting'
                ManagedResourceLedger(data_root).pin_process(resources, label=spec.owner)
                phase = 'environment'
                with startup_stage(f"check Pack installation ({spec.owner})"):
                    activate_source_adapter(spec, data_root)
            if isinstance(origin, Path):
                package = origin.parent
                source = str((package / "src" if (package / "src").is_dir() else package).resolve())
                if source not in sys.path:
                    sys.path.insert(0, source)
            phase = "import"
            if entry is None:
                from backend.plugins.declarative import declarative_plugin
                plugin = declarative_plugin(spec)
            else:
                factory = entry.load()
                if not callable(factory):
                    raise TypeError("entry point must expose a plugin factory")
                plugin = create_plugin(factory, data_root=data_root, source_root=origin.parent if isinstance(origin, Path) else None)
            if not isinstance(getattr(plugin, "descriptor", None), PluginDescriptor):
                raise TypeError("plugin descriptor must be a PluginDescriptor")
            descriptor = plugin.descriptor
            if spec is not None and not spec.legacy and (spec.owner != descriptor.id or spec.version != descriptor.version):
                raise ValueError("Plugin descriptor disagrees with its static Pack identity/version")
            requirements = _requirements(descriptor.python_requirements)
            if spec is not None:
                declared = spec.legacy_python_requirements
                if requirements and requirements != _requirements(declared) and not spec.legacy:
                    raise ValueError("Legacy Sandbox requirements disagree with the static Pack declaration")
                requirements = (declared or requirements) if spec.legacy else declared
            pending.append((plugin, entry, origin, requirements))
        except (Exception, SystemExit) as exc:
            sys.path[:] = previous_path
            _failure(registry, spec.distribution_id if spec else entry.name, origin, phase, exc, owner=spec.owner if spec else None)
    # Only successfully committed registrations satisfy downstream dependencies.
    while pending:
        available = {p.id for p in registry.plugins() if registry.is_enabled(p.id)}
        ready = [item for item in pending if set(item[0].descriptor.requires_plugins) <= available]
        if not ready:
            for plugin, entry, origin, _ in pending:
                missing = sorted(set(plugin.descriptor.requires_plugins) - available)
                _failure(registry, plugin.descriptor.id, origin, "dependency",
                         ValueError("Missing or cyclic plugin dependencies: " + ", ".join(missing)), owner=plugin.descriptor.id)
            break
        for plugin, entry, origin, requirements in ready:
            pending.remove((plugin, entry, origin, requirements))
            spec = source_specs.get(origin)
            try:
                registry.install(plugin, source_spec=spec)
            except (Exception, SystemExit) as exc:
                _failure(registry, spec.distribution_id if spec else entry.name, origin, "registration", exc, owner=plugin.descriptor.id)
                continue
            owner = plugin.descriptor.id
            registry.runtime_requirements[owner] = tuple(requirements)
            if spec is not None:
                if spec.legacy:
                    spec = spec.model_copy(update=dict(owner=owner, distribution_id=owner, version=plugin.descriptor.version,
                        name=plugin.descriptor.name or owner,
                        packs=tuple(p.id for p in registry.catalog(include_disabled=True).packs if p.plugin_id == owner)))
                registry.pack_specs[owner] = spec
                registry.pack_roots[owner] = origin.parent
                registry.pack_sources[owner] = "bundled" if origin.parent.parent.resolve() == bundled_directory else "development"
    if data_root is not None:
        if load_installed:
            with startup_stage("load installed Packs"):
                load_installed_packs(registry, data_root)
        manager = PackInstallationManager(data_root, registry)
        manager.pin_loaded_resources()
        manager.reconcile_resources()
    _check_strict(registry, strict)
    return registry


def _record_installed(registry, manager, manifest):
    from urllib.parse import quote
    from backend.packs.planning import manifest_spec
    registry.installed_packs[manifest.id] = manifest
    registry.runtime_requirements[manifest.owner_id] = manifest.runtime.sandbox.python
    registry.pack_specs[manifest.owner_id] = manifest_spec(manifest)
    registry.pack_roots[manifest.owner_id] = manager.version_path(manifest.id, manifest.version)
    registry.pack_sources[manifest.owner_id] = 'installed'
    if manifest.entrypoints:
        registry.frontend_modules[manifest.owner_id] = {
            'version': manifest.version, 'api_version': manifest.compatibility.frontend_api,
            'distribution_id': manifest.id,
            'url': f"/api/packs/{manifest.id}/versions/{quote(manifest.version, safe='!')}/{quote(manifest.entrypoints.frontend)}",
        }


def load_installed_packs(registry: PluginRegistry, data_root: Path, *, strict: bool = False, safe_only: bool = False) -> None:
    """Discover immutable wheels from user data; never install into host Python."""
    import importlib.util
    from backend.packs.installation import PackInstallationManager

    manager = PackInstallationManager(data_root, registry)
    pending = manager.selected()
    if safe_only:
        # Never replace a loaded owner, run author Python, or touch live work.
        pending = {key: manifest for key, manifest in pending.items() if not registry.has_plugin(manifest.owner_id)
            and (manifest.kind == 'content' or (manager._startup_receipt(manifest) or {}).get('passive_spec'))}
    # Persisted enable/disable state is reconciled by CardLibraryStore next.
    # Environment bootstrap validates that enabled set, before any mutation.
    from backend.packs.planning import manifest_spec
    for manifest in pending.values():
        # Preserve static diagnostics even for an unimportable selected release.
        if manifest.owner_id not in registry.pack_specs:
            registry.pack_specs[manifest.owner_id] = manifest_spec(manifest)
            registry.pack_roots[manifest.owner_id] = manager.version_path(manifest.id, manifest.version)
            registry.pack_sources[manifest.owner_id] = "installed"
    providers = {pack: manifest.id for manifest in pending.values() for pack in manifest.provided_pack_ids}
    while pending:
        ready = [m for m in pending.values() if not any(providers.get(d.id) in pending for d in m.dependencies.packs)]
        if not ready:
            for manifest in pending.values():
                _failure(registry, manifest.id, manager.version_path(manifest.id, manifest.version), "dependency",
                         ValueError("Cyclic installed Pack dependencies"), owner=manifest.owner_id)
            break
        for manifest in ready:
            del pending[manifest.id]
            from backend.packs.lifecycle import ManagedResourceLedger
            ledger = ManagedResourceLedger(data_root)
            ledger.pin_process(manager.resource_ids(manifest), label=manifest.owner_id)
            phase, previous_path = "dependency", list(sys.path)
            try:
                manager._check_selection({**registry.installed_packs, manifest.id: manifest}, check_requirements=False)
                phase = "environment"
                entrypoint = manager.startup_entrypoint(manifest)
                passive_spec = (manager._startup_receipt(manifest) or {}).get('passive_spec')
                if manifest.kind == "content" or passive_spec:
                    if passive_spec:
                        from backend.plugins.declarative import declarative_plugin
                        plugin = declarative_plugin(passive_spec)
                        if (plugin.descriptor.id, plugin.descriptor.version, plugin.descriptor.plugin_api_version) != (
                                manifest.owner_id, manifest.version, manifest.compatibility.plugin_api):
                            raise ValueError('Passive declaration differs from installed ownership or compatibility')
                    else:
                        from backend.packs.content import content_plugin
                        root = manager.version_path(manifest.id, manifest.version)
                        plugin = content_plugin(manifest, {name: (root / name).read_bytes() for name in manifest.content.paths}, registry)
                    phase = "registration"
                    registry.install(plugin, distribution=manifest)
                    _record_installed(registry, manager, manifest)
                    continue
                origin = manager.version_path(manifest.id, manifest.version) / manifest.entrypoints.backend
                if manifest.schema_version == 3:
                    from backend.packs.adapters import activate_adapters
                    activate_adapters(manifest.adapter, data_root)
                    from backend.packs.modules import prepared_modules
                    origin = prepared_modules(manifest, manager.root / "cache" / "modules",
                        expected_digest=manager._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend])
                    phase = 'accounting'
                    ledger.register('modules:' + origin.name, origin, 'modules')
                    phase = 'environment'
                for module_name in manifest.module_names:
                    existing = importlib.util.find_spec(module_name)
                    if existing is None:
                        continue
                    # Same-version app instances in tests can share immutable code;
                    # changing a module version always requires a process restart.
                    if existing.origin is None or not existing.origin.replace("\\", "/").startswith(str(origin).replace("\\", "/") + "/"):
                        raise ValueError("Backend module ownership conflict; restart OAW to change versions")
                if str(origin) not in sys.path:
                    sys.path.append(str(origin))
                phase = "import"
                # Keep the installed backend tree free of host bytecode writes.
                previous_bytecode = sys.dont_write_bytecode
                try:
                    if manifest.schema_version == 3:
                        sys.dont_write_bytecode = True
                    plugin = create_plugin(EntryPoint(name=manifest.id, value=entrypoint, group=ENTRY_POINT_GROUP).load(),
                        data_root=data_root, source_root=manager.version_path(manifest.id, manifest.version))
                finally:
                    sys.dont_write_bytecode = previous_bytecode
                descriptor = plugin.descriptor
                dependencies = tuple(registry.owner_id("pack", d.id) for d in manifest.dependencies.packs)
                if (descriptor.id != manifest.owner_id or descriptor.version != manifest.version
                        or descriptor.plugin_api_version != manifest.compatibility.plugin_api
                        or set(descriptor.requires_plugins) != set(dependencies)
                        or (descriptor.python_requirements and descriptor.python_requirements != manifest.runtime.sandbox.python)):
                    raise ValueError("Backend descriptor disagrees with distribution identity, compatibility or dependencies")
                phase = "registration"
                contribution = (manager._startup_receipt(manifest) or {}).get('contribution_spec')
                if contribution:
                    from backend.packs.spec import PackSpec
                    contribution = PackSpec.model_validate(contribution)
                registry.install(plugin, distribution=manifest, source_spec=contribution)
                _record_installed(registry, manager, manifest)
            except (Exception, SystemExit) as exc:
                sys.path[:] = previous_path
                _failure(registry, manifest.id, manager.version_path(manifest.id, manifest.version), phase, exc, owner=manifest.owner_id)
    _check_strict(registry, strict)
