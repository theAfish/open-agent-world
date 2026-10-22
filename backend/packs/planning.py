"""Source-independent, read-only Pack plans and environment inventory."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from backend.packs.spec import BackendComponent, EnvironmentSpec, PackSpec, SpecDependency


def manifest_spec(manifest) -> PackSpec:
    environments = dict(manifest.environments)
    if manifest.runtime.sandbox.python and not any(item.management == 'legacy-shared' for item in environments.values()):
        name = 'legacy-sandbox'
        while name in environments:
            name += '-legacy'
        environments[name] = EnvironmentSpec(target='sandbox', management='legacy-shared',
            python=manifest.runtime.sandbox.python)
    return PackSpec(distribution_id=manifest.id, name=manifest.name, version=manifest.version,
        owner=manifest.owner_id, packs=manifest.provided_pack_ids, node_types=manifest.node_types,
        backend=BackendComponent(python=manifest.adapter.requirements),
        environments=environments, features=manifest.features, prerequisites=manifest.prerequisites,
        dependencies=tuple(SpecDependency(kind='pack', id=d.id, version=d.version) for d in manifest.dependencies.packs))


def environment_status(owner, name, declaration, source_root, data_root, *, legacy=None):
    result = {'id': name, 'target': declaration.target, 'provider': declaration.provider,
        'management': declaration.management, 'python': list(declaration.python), 'node': list(declaration.node),
        'state': 'missing', 'error': None, 'operation_id': None}
    if declaration.management == 'external' or declaration.provider == 'external':
        result['state'] = 'needs_configuration'
    elif declaration.management == 'legacy-shared':
        result['state'] = {'environment_ready': 'ready', 'environment_pending': 'preparing',
            'environment_failed': 'failed'}.get((legacy or {}).get('state'), 'on_use')
        result['error'] = (legacy or {}).get('error')
    elif declaration.lock:
        result.update(state='unsupported', error='This environment provider does not yet support lockfile installation')
    elif declaration.target == 'sandbox':
        # Readiness is target-specific. A local install is never remote evidence.
        from backend.sandbox.pack_profiles import unsupported_profile_reason
        reason = unsupported_profile_reason(declaration)
        result.update(state='unsupported' if reason else 'on_use', error=reason)
    elif declaration.provider not in {'python', 'executable'}:
        result.update(state='unsupported', error='This host environment provider is unavailable')
    elif declaration.platforms and sys.platform not in declaration.platforms:
        result.update(state='unsupported', error='Environment does not support this host platform')
    elif source_root is not None:
        try:
            manager, spec = environment_provider(owner, name, declaration, source_root, data_root)
            status = manager.status(spec)
            if status['status'] == 'missing':
                # Desktop setup prepares the same relocatable receipt in its
                # application bundle. Reading it must not create a user runtime.
                bundled_manager, _ = environment_provider(owner, name, declaration, source_root, Path(sys.prefix) / 'oaw-runtime-bundle')
                bundled = bundled_manager.status(spec)
                if bundled['status'] == 'ready':
                    status = bundled
            result['state'] = {'invalid': 'failed'}.get(status['status'], status['status'])
            result['operation_id'] = status.get('operation_id')
            result['error'] = status.get('error')
            result['management'] = status.get('management', result['management'])
        except (OSError, ValueError, RuntimeError) as exc:
            result.update(state='unsupported', error=str(exc))
    return result


def inventory(manager, legacy=None):
    from backend.packs.adapters import adapter_library_inventory, source_adapter_declaration
    registry = manager.registry
    if registry is None:
        return []
    legacy = legacy or {}
    result = []
    selected = manager.selected()
    for owner, spec in registry.pack_specs.items():
        loaded = registry.has_plugin(owner)
        root = registry.pack_roots.get(owner)
        frontend = []
        if root and spec.frontend and spec.frontend.project:
            try:
                frontend = sorted(json.loads((Path(root) / spec.frontend.project).read_text(encoding='utf-8')).get('dependencies', {}))
            except (OSError, ValueError, TypeError):
                pass
        source = registry.pack_sources.get(owner, 'bundled')
        try:
            adapter = (selected[spec.distribution_id].adapter if source == 'installed' and spec.distribution_id in selected
                       else source_adapter_declaration(spec, manager.root.parent))
        except (ValueError, OSError):
            # Broken optional receipts are reported by activation diagnostics.
            from backend.packs.manifest import AdapterDependencies
            adapter = AdapterDependencies(requirements=spec.backend.python)
        result.append({'id': spec.distribution_id, 'owner': owner, 'name': spec.name,
            'version': spec.version, 'source': source,
            'loaded': loaded, 'enabled': loaded and registry.is_enabled(owner),
            'enrolled': spec.distribution_id in selected if source == 'installed' else manager.source_enrolled(spec.distribution_id),
            'environments': [environment_status(owner, name, environment, root, manager.root.parent,
                legacy=legacy.get(owner)) for name, environment in spec.environments.items()],
            'backend_requirements': list(spec.backend.python), 'frontend_dependencies': frontend,
            'backend_libraries': adapter_library_inventory(adapter),
            'warnings': ['Legacy source declaration; add oaw.pack.toml for explicit dependency ownership'] if spec.legacy else []})
    return result


def plan(manager, manifest, *, source='archive', source_root=None):
    from backend.packs.adapters import adapter_library_inventory
    selected = manager.selected()
    provided = {pack: (m.version, m.owner_id, m.id) for m in selected.values() for pack in m.provided_pack_ids}
    if manager.registry:
        for pack in manager.registry.catalog(include_disabled=True).packs:
            if pack.id not in provided:
                owner = next(p for p in manager.registry.plugins() if p.id == pack.plugin_id)
                spec = manager.registry.pack_specs.get(owner.id)
                provided[pack.id] = (owner.version, owner.id, spec.distribution_id if spec else None)
    dependencies = []
    for dependency in manifest.dependencies.packs:
        candidate = provided.get(dependency.id)
        state = 'missing' if candidate is None else 'satisfied'
        if candidate and Version(candidate[0]) not in SpecifierSet(dependency.version):
            state = 'incompatible'
        elif candidate and getattr(dependency, 'distribution', None) and candidate[2] != dependency.distribution:
            state = 'incompatible'
        elif candidate and manager.registry and manager.registry.has_plugin(candidate[1]) and not manager.registry.is_enabled(candidate[1]):
            state = 'disabled'
        dependencies.append({'id': dependency.id, 'version': dependency.version, 'state': state})
    spec = manifest_spec(manifest)
    environments = [environment_status(spec.owner, name, environment, source_root, manager.root.parent)
                    for name, environment in spec.environments.items()]
    blockers = [f"Pack dependency {row['id']} is {row['state']}" for row in dependencies if row['state'] != 'satisfied']
    from backend.packs.manifest import Compatibility
    from backend.plugins.registry import _supports_plugin_api
    from backend import __version__
    try:
        compatibility = Compatibility.model_validate(manifest.compatibility.model_dump())
        if Version(__version__) not in SpecifierSet(compatibility.oaw):
            blockers.append(f'Pack requires OAW {compatibility.oaw}; host is {__version__}')
        if not _supports_plugin_api(compatibility.plugin_api):
            blockers.append(f'Unsupported Plugin API: {compatibility.plugin_api}')
    except ValueError:
        blockers.append('Unsupported compatibility declaration')
    for row in environments:
        declaration = spec.environments[row['id']]
        if declaration.prepare == 'install' and row['state'] == 'unsupported':
            blockers.append(f"{row['id']}: {row['error']}")
    return {'pack_id': manifest.id, 'name': manifest.name, 'version': manifest.version,
        'source': source, 'restart_required': True, 'dependencies': dependencies,
        'environments': environments, 'backend_libraries': adapter_library_inventory(manifest.adapter),
        'warnings': [f'{name}: {item.description or item.kind} requires configuration' for name, item in spec.prerequisites.items()],
        'blockers': blockers}


def environment_provider(owner, name, declaration, source_root, data_root):
    if declaration.provider == 'executable':
        from backend.packs.executables import ExecutableEnvironmentManager, ExecutableEnvironmentSpec
        return (ExecutableEnvironmentManager(data_root),
                ExecutableEnvironmentSpec.from_declaration(owner, name, declaration, source_root))
    from backend.packs.environments import PackEnvironmentManager, PythonEnvironmentSpec
    return (PackEnvironmentManager(data_root),
            PythonEnvironmentSpec.from_declaration(owner, name, declaration, source_root))


def prepare_declared(owner, name, declaration, source_root, data_root, *, prepare=True):
    manager, spec = environment_provider(owner, name, declaration, source_root, data_root)
    if declaration.provider == 'executable':
        if prepare:
            return manager.prepare(spec)
        if manager.status(spec)['management'] == 'external':
            return manager.read_external(spec)
        return manager.read(spec)
    return manager.prepare_python(spec) if prepare else manager.read_python(spec)


def prepare_environments(manifest, source_root, data_root, *, prepare=True):
    for name, declaration in manifest.environments.items():
        if declaration.target != 'host-worker' or declaration.prepare != 'install':
            continue
        if declaration.platforms and sys.platform not in declaration.platforms:
            raise ValueError(f"Environment {name} does not support {sys.platform}")
        prepare_declared(manifest.owner_id, name, declaration, source_root, data_root, prepare=prepare)
