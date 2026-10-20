from __future__ import annotations

import sys
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


def load_plugin_registry(plugin_directory: Path | None = None, *, plugin_directories: tuple[Path, ...] = (), data_root: Path | None = None, load_installed: bool = True, load_unenrolled_sources: bool = False) -> PluginRegistry:
    """Load trusted packages in the project's plugins folder, then installed plugins.

    Each entry point exposes a zero-argument plugin factory. Loading is
    fail-closed: a broken, incompatible, or duplicate plugin prevents startup.
    """

    registry = create_builtin_registry()
    directory = (
        plugin_directory
        if plugin_directory is not None
        else Path(__file__).resolve().parents[2] / "plugins"
    )
    local: list[tuple[EntryPoint, Path]] = []
    from backend.packs.spec import read_source_pack
    source_specs = {}
    from backend.packs.installation import PackInstallationManager
    enrollment = PackInstallationManager(data_root) if data_root is not None else None
    bundled_directory = directory.resolve()
    for directory in dict.fromkeys((directory, *plugin_directories)):
        if not directory.is_dir():
            continue
        for package in sorted(directory.iterdir()):
            manifest = package / "pyproject.toml"
            if not package.is_dir() or not manifest.is_file():
                continue
            try:
                with manifest.open("rb") as stream:
                    project = tomllib.load(stream).get("project", {})
                declarations = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
                if not isinstance(declarations, dict):
                    raise TypeError(f"{ENTRY_POINT_GROUP} must be a table")
                if declarations:
                    source_specs[manifest] = read_source_pack(package)
                    spec = source_specs[manifest]
                    registry.pack_specs[spec.owner] = spec
                    registry.pack_roots[spec.owner] = package
                    registry.pack_sources[spec.owner] = "bundled" if package.parent.resolve() == bundled_directory else "development"
                for name, value in sorted(declarations.items()):
                    if not isinstance(value, str):
                        raise TypeError(f"entry point {name!r} must be a string")
                    local.append((EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP), manifest))
                if declarations:
                    source = str((package / "src" if (package / "src").is_dir() else package).resolve())
                    if source not in sys.path:
                        sys.path.insert(0, source)
            except Exception as exc:
                raise RuntimeError(f"Cannot discover plugin in {manifest}: {exc}") from exc
    discovered = entry_points()
    selected = (
        discovered.select(group=ENTRY_POINT_GROUP)
        if hasattr(discovered, "select")
        else discovered.get(ENTRY_POINT_GROUP, ())
    )
    local_keys = {(entry.name, entry.value) for entry, _ in local}
    candidates = local + [
        (entry, "installed distribution")
        for entry in sorted(selected, key=lambda item: item.name)
        if (entry.name, entry.value) not in local_keys
    ]
    pending = []
    for entry_point, origin in candidates:
        spec = source_specs.get(origin)
        if spec is not None and enrollment and not load_unenrolled_sources and not enrollment.source_enrolled(spec.distribution_id):
            continue
        if spec is not None and data_root is not None:
            from backend.packs.lifecycle import ManagedResourceLedger, environment_resources
            from backend.packs.adapters import activate_source_adapter, source_adapter_resource_ids
            ManagedResourceLedger(data_root).pin_process(
                environment_resources(spec, origin.parent) | source_adapter_resource_ids(spec, data_root), label=spec.owner)
            with startup_stage(f"check Pack installation ({spec.owner})"):
                activate_source_adapter(spec, data_root)
        try:
            factory = entry_point.load()
            if not callable(factory):
                raise TypeError("entry point must expose a plugin factory")
            plugin = create_plugin(factory, data_root=data_root,
                                   source_root=origin.parent if isinstance(origin, Path) else None)
            if not isinstance(getattr(plugin, "descriptor", None), PluginDescriptor):
                raise TypeError("plugin descriptor must be a PluginDescriptor")
            pending.append((plugin, entry_point, origin))
        except Exception as exc:
            raise RuntimeError(f"Cannot load plugin {entry_point.name!r} from {origin}: {exc}") from exc
    # Resolve declared dependencies before registration so cross-plugin presets
    # are validated atomically against their actual node/relationship owners.
    ordered = []
    available = {plugin.id for plugin in registry.plugins()}
    while pending:
        ready = [item for item in pending if set(item[0].descriptor.requires_plugins) <= available]
        if not ready:
            details = "; ".join(
                f"{plugin.descriptor.id} from {origin} requires {', '.join(sorted(set(plugin.descriptor.requires_plugins) - available))}"
                for plugin, _, origin in pending
            )
            raise RuntimeError(f"Missing or cyclic plugin dependencies: {details}")
        for item in ready:
            pending.remove(item)
            ordered.append(item)
            available.add(item[0].descriptor.id)
    for plugin, entry_point, origin in ordered:
        try:
            source_spec = source_specs.get(origin)
            if source_spec is not None and not source_spec.legacy:
                if (source_spec.owner != plugin.descriptor.id or source_spec.version != plugin.descriptor.version):
                    raise ValueError("Plugin descriptor disagrees with its static Pack identity/version")
            registry.install(plugin, source_spec=source_spec)
            if source_spec is not None:
                owner = plugin.descriptor.id
                actual_packs = tuple(p.id for p in registry.catalog(include_disabled=True).packs if p.plugin_id == owner)
                if source_spec.packs and set(actual_packs) != set(source_spec.packs):
                    raise ValueError("Plugin registration disagrees with its static provided Pack IDs")
                updates = {"owner": owner, "packs": actual_packs}
                if source_spec.legacy:
                    updates.update(distribution_id=owner, version=plugin.descriptor.version,
                        name=plugin.descriptor.name or owner)
                registry.pack_specs[owner] = source_spec.model_copy(update=updates)
                registry.pack_roots[owner] = origin.parent
                registry.pack_sources[owner] = "bundled" if origin.parent.parent.resolve() == bundled_directory else "development"
            if isinstance(origin, Path):
                with origin.open("rb") as stream:
                    manifest = tomllib.load(stream)
                requirements = manifest.get("tool", {}).get("open-agent-world", {}).get("runtime", {}).get("python")
                if requirements is not None:
                    from backend.sandbox.python_runtime import validate_requirements
                    registry.runtime_requirements[plugin.descriptor.id] = tuple(validate_requirements(requirements))
                if source_spec is not None and not source_spec.legacy:
                    requirements = source_spec.legacy_python_requirements
                    from backend.packs.spec import _requirements
                    previous = registry.runtime_requirements.get(plugin.descriptor.id, plugin.descriptor.python_requirements)
                    if previous and _requirements(previous) != _requirements(requirements):
                        raise ValueError("Legacy Sandbox requirements disagree with the static Pack declaration")
                    registry.runtime_requirements[plugin.descriptor.id] = tuple(requirements)
        except Exception as exc:
            raise RuntimeError(f"Cannot load plugin {entry_point.name!r} from {origin}: {exc}") from exc
    if data_root is not None:
        if load_installed:
            with startup_stage("load installed Packs"):
                load_installed_packs(registry, data_root)
        manager = PackInstallationManager(data_root, registry)
        manager.pin_loaded_resources()
        manager.reconcile_resources()
    return registry


def load_installed_packs(registry: PluginRegistry, data_root: Path) -> None:
    """Discover immutable wheels from user data; never install into host Python."""
    import importlib.util
    from urllib.parse import quote
    from backend.packs.installation import PackInstallationManager

    manager = PackInstallationManager(data_root, registry)
    pending = manager.selected()
    # Persisted enable/disable state is reconciled by CardLibraryStore next.
    # Environment bootstrap validates that enabled set, before any mutation.
    manager._check_selection(pending, check_requirements=False)
    # Resolve metadata first. No user code runs during inspection or validation.
    entries = {key: manager.startup_entrypoint(manifest) for key, manifest in pending.items()}
    providers = {pack: manifest.id for manifest in pending.values() for pack in manifest.provided_pack_ids}
    while pending:
        ready = [m for m in pending.values() if not any(providers.get(d.id) in pending for d in m.dependencies.packs)]
        if not ready:
            raise RuntimeError("Cyclic installed Pack dependencies")
        for manifest in ready:
            try:
                from backend.packs.lifecycle import ManagedResourceLedger
                ledger = ManagedResourceLedger(data_root)
                ledger.pin_process(manager.resource_ids(manifest), label=manifest.owner_id)
                if manifest.kind == "content":
                    from backend.packs.content import content_plugin
                    root = manager.version_path(manifest.id, manifest.version)
                    plugin = content_plugin(manifest, {name: (root / name).read_bytes() for name in manifest.content.paths}, registry)
                    registry.install(plugin, distribution=manifest)
                    registry.installed_packs[manifest.id] = manifest
                    registry.runtime_requirements[manifest.id] = ()
                    del pending[manifest.id]
                    continue
                origin = manager.version_path(manifest.id, manifest.version) / manifest.entrypoints.backend
                if manifest.schema_version == 3:
                    from backend.packs.adapters import activate_adapters
                    activate_adapters(manifest.adapter, data_root)
                    from backend.packs.modules import prepared_modules
                    origin = prepared_modules(manifest, manager.root / "cache" / "modules",
                        expected_digest=manager._checksums(manifest.id, manifest.version)[manifest.entrypoints.backend])
                    ledger.register('modules:' + origin.name, origin, 'modules')
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
                # Keep the installed backend tree free of host bytecode writes.
                previous_bytecode = sys.dont_write_bytecode
                try:
                    if manifest.schema_version == 3:
                        sys.dont_write_bytecode = True
                    plugin = create_plugin(EntryPoint(name=manifest.id, value=entries[manifest.id], group=ENTRY_POINT_GROUP).load(),
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
                registry.install(plugin, distribution=manifest)
                registry.installed_packs[manifest.id] = manifest
                registry.runtime_requirements[manifest.owner_id] = manifest.runtime.sandbox.python
                from backend.packs.planning import manifest_spec
                registry.pack_specs[manifest.owner_id] = manifest_spec(manifest)
                registry.pack_roots[manifest.owner_id] = manager.version_path(manifest.id, manifest.version)
                registry.pack_sources[manifest.owner_id] = "installed"
                registry.frontend_modules[manifest.owner_id] = {
                    "version": manifest.version, "api_version": manifest.compatibility.frontend_api,
                    "distribution_id": manifest.id,
                    "url": f"/api/packs/{manifest.id}/versions/{quote(manifest.version, safe='!')}/{quote(manifest.entrypoints.frontend)}",
                }
                del pending[manifest.id]
            except Exception as exc:
                raise RuntimeError(f"Cannot load installed Pack {manifest.id}@{manifest.version}: {exc}. Select a retained version with the Pack recovery CLI.") from exc
