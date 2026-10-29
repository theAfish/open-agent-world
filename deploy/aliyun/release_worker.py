"""Release-time checks; migration runs inside each existing worker's systemd boundary."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import sys


def refresh_manifest(old, new):
    """Upgrade trusted Pack contracts only on already published card surfaces."""
    if old['layout'] != new['layout']:
        raise ValueError('Workspace layout changed; an explicit workspace migration is required')
    panels = lambda value: [{k: v for k, v in p.items() if k != 'sections'} for p in value['panels']]
    if panels(old) != panels(new) or set(old['permissions']) != set(new['permissions']):
        raise ValueError('Published card scope changed')
    previous, current = old.get('plugin_access', {}), new.get('plugin_access', {})
    if set(previous) != set(current):
        raise ValueError('Published Pack card scope changed')
    for node, grants in old['permissions'].items():
        if node not in previous and grants != new['permissions'][node]:
            raise ValueError('Core public permissions changed')
    result = {**old, **{k: new[k] for k in ('panels', 'permissions', 'plugin_access', 'plugin_versions', 'runtime_nodes') if k in new}}
    if 'workspaces' in old:
        current_workspaces = new.get('workspaces', [])
        if [w['legion_id'] for w in old['workspaces']] != [w['legion_id'] for w in current_workspaces]:
            raise ValueError('Published workspace scope or order changed')
        result['workspaces'] = [refresh_manifest(previous, current)[0]
                                for previous, current in zip(old['workspaces'], current_workspaces)]
    # Keep release identity, source lineage, runtime settings and saved layout.
    changes = [node for node in previous if previous[node] != current[node]
               or old['permissions'][node] != new['permissions'][node]]
    return result, changes


def prepare(source, previous, scratch):
    from backend.packs.adapters import (activate_source_adapter, prepare_source_adapter,
        _source_adapter_location, prepared_adapter_paths)
    from backend.packs.planning import environment_status, prepare_declared
    from backend.packs.spec import read_source_pack
    from backend.cloud.aliyun.app import approved_plugins
    bundle = Path(sys.prefix) / 'oaw-runtime-bundle'
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


async def migrate(config_path):
    from backend.config import Settings
    from backend.deploy import validate_manifest
    from backend.deployments import PublishRequest, release_spec, published_workspaces
    from backend.hosting.configuration import write_private_json
    from backend.hosting.worker import WorkerConfig, hosted_scope, create_worker_services
    value = json.loads(config_path.read_text())
    value['data_root'] = config_path.parent / value['data_root']
    config = WorkerConfig(**value)
    factory = create_worker_services
    if name := os.environ.get('OAW_HOSTED_SERVICES_FACTORY'):
        module, function = name.split(':', 1)
        factory = getattr(importlib.import_module(module), function)
    settings = replace(Settings.for_data_root(config.data_root), agent_runtime=config.agent_runtime,
        sandbox_runtime=None, inherit_runtime_environment=False,
        tenant_scope=hosted_scope(config.user_id, config.application_id, config.profile_id))
    services = factory(settings)
    try:
        before = {card.id: card.status for card in services.world.list_cards()}
        await services.startup()
        failures = [f for f in services.plugins.load_failures if f['phase'] != 'hosting_policy']
        if failures:
            raise ValueError(f'Pack startup failed: {failures}')
        newly_failed = [card.id for card in services.world.list_cards()
                        if card.status == 'error' and before.get(card.id) != 'error']
        if newly_failed:
            raise ValueError(f'Card startup/migration failed: {newly_failed}')
        changed = []
        if config.mode == 'runtime':
            target = config.data_root / 'deployment.json'
            old = json.loads(target.read_text())
            new = release_spec(services, PublishRequest(legion_ids=[w['legion_id'] for w in published_workspaces(old)], name=old['name'],
                allow_terminal=any('terminal' in p for p in old['permissions'].values())))
            result, changed = refresh_manifest(old, new)
            validate_manifest(result)
            write_private_json(target, result)
        return {'profile': config.profile_id, 'ready': True, 'changed_contracts': changed}
    finally:
        await services.shutdown()
        services.close()


def entry_profiles(profiles, choices):
    """The same entry selection as Gateway.profiles, without opening account stores."""
    entries = {}
    for profile in profiles:
        if profile.user_id not in choices:
            entries.setdefault(profile.user_id, profile)
        elif choices[profile.user_id] == profile.profile_id:
            entries[profile.user_id] = profile
    return tuple(entries.values())


def verify(hosting, *, entries_only=False):
    import httpx
    from backend.hosting.configuration import read_gateway_settings, unix_socket_path
    from backend.hosting.worker import mint_worker_token
    settings = read_gateway_settings(hosting / 'control/hosting.json')
    selected = settings.profiles
    if entries_only:
        import contextlib
        import sqlite3
        with contextlib.closing(sqlite3.connect(f'file:{hosting}/control/deployments.sqlite3?mode=ro', uri=True)) as db:
            choices = dict(db.execute('SELECT user_id,profile_id FROM hosting_entries'))
        selected = entry_profiles(selected, choices)
        if not selected:
            raise ValueError('No login entries available for release verification')
    results = []
    for profile in selected:
        socket = unix_socket_path(profile.upstream)
        if not socket:
            raise ValueError('Release verification requires the existing private Unix sockets')
        with httpx.Client(transport=httpx.HTTPTransport(uds=socket), base_url='http://localhost', timeout=120) as client:
            # The first unauthenticated call also exercises cold socket activation.
            if client.get('/api/deployment').status_code != 401:
                raise ValueError('Worker admitted a request without gateway identity')
            client.headers['Authorization'] = 'Bearer ' + mint_worker_token(profile.secret, profile.profile_id, profile.user_id)
            response = client.get('/api/deployment')
            if response.status_code != 200 or response.json()['mode'] != profile.mode:
                raise ValueError('Worker startup or deployment mode check failed')
            route = '/api/runtime-app' if profile.mode == 'runtime' else '/api/card-library'
            if client.get(route).status_code != 200:
                raise ValueError('Worker application bootstrap failed')
            if profile.mode == 'runtime' and client.get('/api/world').status_code != 404:
                raise ValueError('Runtime exposed its engineering canvas')
        results.append({'profile': profile.profile_id, 'mode': profile.mode, 'ready': True})
    return {'verified_profiles': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'migrate', 'verify'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--hosting', type=Path)
    parser.add_argument('--entries-only', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.source))
    if args.mode == 'prepare':
        result = prepare(args.source, args.previous, args.scratch)
    elif args.mode == 'migrate':
        result = asyncio.run(migrate(args.config))
    else:
        result = verify(args.hosting, entries_only=args.entries_only)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
