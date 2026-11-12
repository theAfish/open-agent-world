"""Explicit, non-root two-VM acceptance. Requires separately approved resources.

OAW_ACCEPTANCE_SANDBOX_SETTINGS and the API key use runtime_acceptance's existing
private configuration. This program never installs clients, mounts storage,
changes IAM, enables production, calls models, or retries a failed operation.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.cloud.aliyun.pack_workers import AliyunPackWorkerExecutionProvider
from backend.errors import RuntimeUnavailableError
from backend.packs.workers import PackWorkerHost, PackWorkerSpec
from deploy.aliyun.runtime_acceptance import create_provider


PACK = 'oaw.worker-probe'
COMMAND = ['/usr/bin/python3', '-I', '-u', '-c',
    'import pathlib,sys; p=pathlib.Path("result.txt"); '
    'p.write_text(pathlib.Path("input.txt").read_text().upper()) if sys.argv[1]=="write" else None; '
    'assert p.read_text()=="DIFFRACTION"; '
    'print("ready",flush=True); assert sys.stdin.readline().strip()=="ping"; '
    'print("pong",flush=True); print("diagnostic",file=sys.stderr)']


def assignment(remote):
    return {'version': 1, 'profile_id': remote.profile_id, 'volume': remote.volume,
        'mount_path': '/mnt/oaw-workspaces/' + remote.profile_id, 'verified': True,
        'deployments': [{'pack_id': PACK, 'pack_version': '1.0.0', 'environment_id': 'stdlib',
            'template': remote.template, 'commands': {'probe': COMMAND}, 'environment': {}}]}


async def run_probe(provider):
    if not provider.configuration(PACK)['configured']:
        raise RuntimeUnavailableError('Install and authorize the dedicated mount before running acceptance')
    directory = provider.workspace_root / PACK / uuid4().hex
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'input.txt').write_text('diffraction')
    host = PackWorkerHost()
    host.configure(provider, inherit_environment=False)
    def forbidden(environment):
        raise AssertionError('Native preparation is forbidden')
    try:
        for phase in ('write', 'read'):
            spec = PackWorkerSpec(PACK, 'stdlib', 'probe', (phase,), forbidden, pack_version='1.0.0')
            worker = await host.start(spec, operation_id=directory.name + '-' + phase,
                                      workspace=directory, interactive=True)
            try:
                if (await worker.readline()).strip() != b'ready':
                    raise RuntimeError('Probe did not read the expected persistent file')
                await worker.write(b'ping\n')
                if (await worker.readline()).strip() != b'pong' or await worker.wait() != 0:
                    raise RuntimeError('Probe interaction or worker exit failed')
            finally:
                await worker.close()
            if (directory / 'result.txt').read_text() != 'DIFFRACTION':
                raise RuntimeError('Worker result did not reach ECS')
    finally:
        await host.shutdown()
    if (directory / 'console.log').read_text().count('diagnostic') != 2:
        raise RuntimeError('Worker diagnostics were not delivered')
    # Delete only known fixture files after both VM teardowns were confirmed.
    # On any failure leave files/receipts for inspection; never recursively remove.
    for name in ('input.txt', 'result.txt', 'console.log'):
        (directory / name).unlink()
    directory.rmdir()
    return {'sessions': 2, 'persistent_file': True, 'interactive_io': True, 'cleanup_confirmed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help='Isolated local diagnostic receipts directory')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() == 0:
        parser.error('Run as the dedicated non-root Linux diagnostic identity')
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    remote = create_provider(SimpleNamespace(data_root=args.state)).settings
    provider = AliyunPackWorkerExecutionProvider(args.state / 'bindings', remote, assignment(remote),
        lifetime_seconds=900, lease_seconds=300)
    print(json.dumps(asyncio.run(run_probe(provider))))


if __name__ == '__main__':
    main()
