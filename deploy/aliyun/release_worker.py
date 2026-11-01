"""Unprivileged release checks; migrations retain each hosted worker's boundary."""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys


def prepare(source, previous, scratch, bundle, *, preview=False):
    if preview:
        from backend.plugins.loader import load_plugin_registry
        registry = load_plugin_registry(data_root=scratch / 'probe', core_only=True,
                                        load_installed=False, load_entry_points=False)
        if registry.load_failures:
            raise ValueError(f'Preview Core preparation failed: {registry.load_failures}')
        return {'prepared_packs': [], 'composition': 'preview-core'}
    from backend.packs.adapters import (activate_source_adapter, prepare_source_adapter,
        _source_adapter_location, prepared_adapter_paths)
    from backend.packs.planning import environment_status, prepare_declared
    from backend.packs.spec import read_source_pack
    from backend.cloud.aliyun.app import approved_plugins
    report = []
    for declaration in sorted((source / 'plugins').glob('*/oaw.pack.toml')):
        spec = read_source_pack(declaration.parent)
        try:
            activate_source_adapter(spec, bundle)
        except (ValueError, RuntimeError, ImportError):
            wheelhouse = None
            old_path = previous / declaration.parent.relative_to(source)
            if (old_path / 'oaw.pack.toml').is_file():
                old = read_source_pack(old_path)
                location = _source_adapter_location(old, bundle)
                if location and old.backend.python == spec.backend.python:
                    adapter, root = location
                    wheelhouse = scratch / spec.owner
                    wheelhouse.mkdir(parents=True, exist_ok=True)
                    for wheel, path in zip(adapter.wheels, prepared_adapter_paths(adapter, root, read_only=True)):
                        (wheelhouse / Path(wheel.path).name).write_bytes((path / '.oaw-wheel.whl').read_bytes())
            prepare_source_adapter(spec, bundle, dependency_wheels=wheelhouse)
        for name, environment in spec.environments.items():
            if environment.target == 'host-worker' and environment.prepare == 'install':
                state = environment_status(spec.owner, name, environment, spec.source_root, bundle)
                if state['state'] != 'ready':
                    prepare_declared(spec.owner, name, environment, spec.source_root, bundle)
                    state = environment_status(spec.owner, name, environment, spec.source_root, bundle)
                if state['state'] != 'ready':
                    raise ValueError(f'{spec.owner}/{name}: runtime is not ready')
        report.append(spec.owner)
    # Prepared content is immutable to tenants, but must be readable by worker UIDs.
    for path in [bundle, *bundle.rglob('*')]:
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    registry = approved_plugins(scratch / 'probe')
    failures = [f for f in registry.load_failures if f['phase'] != 'hosting_policy']
    if failures:
        raise ValueError(f'Pack preparation failed: {failures}')
    return {'prepared_packs': report}


async def migrate(config_path, *, preview=False):
    from backend.hosting.upgrades import migrate_profile
    from backend.hosting.worker import WorkerConfig, create_worker_services
    value = json.loads(config_path.read_text())
    value['data_root'] = config_path.parent / value['data_root']
    config = WorkerConfig(**value)
    factory = create_worker_services
    if preview:
        from backend.hosting.preview import create_preview_services
        factory = create_preview_services
    elif name := os.environ.get('OAW_HOSTED_SERVICES_FACTORY'):
        module, function = name.split(':', 1)
        factory = getattr(importlib.import_module(module), function)
    return await migrate_profile(config, services_factory=factory)


def entry_profiles(profiles, choices):
    """The same entry selection as Gateway.profiles, without opening account stores."""
    entries = {}
    for profile in profiles:
        if profile.user_id not in choices:
            entries.setdefault(profile.user_id, profile)
        elif choices[profile.user_id] == profile.profile_id:
            entries[profile.user_id] = profile
    return tuple(entries.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'migrate'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--environment', choices=('production', 'preview'), default='production')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError('Application release checks cannot run as root')
    sys.path.insert(0, str(args.source))
    if args.mode == 'prepare':
        if any(value is None for value in (args.previous, args.scratch, args.bundle)):
            parser.error('--previous, --scratch and --bundle are required for preparation')
        result = prepare(args.source, args.previous, args.scratch, args.bundle, preview=args.environment == 'preview')
    elif args.mode == 'migrate':
        result = asyncio.run(migrate(args.config, preview=args.environment == 'preview'))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
