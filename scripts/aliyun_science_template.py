"""Build an immutable, preinstalled scientific Sandbox template without ACR.

Only public dependencies enter the template. Credentials and user files are
supplied at runtime, never included in its build command or snapshot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex

from scripts.aliyun_workspaces import Cloud, write


REQUIREMENTS = [
    "numpy==2.5.4", "scipy==1.18.1", "ase==3.29.0", "pymatgen==2026.10.24",
    "matplotlib==3.11.2", "mp-api==0.45.13", "emmet-core==0.85.1",
]
READY = "/opt/oaw-python/.oaw-science-ready.json"
PROBE = '''
import importlib.metadata as metadata, io, json, pathlib, platform
import numpy as np
from scipy.linalg import solve
from ase import Atoms
from pymatgen.core import Lattice, Structure
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from mp_api.client import MPRester
import emmet.core
assert np.allclose(solve(np.eye(2), np.ones(2)), [1, 1])
assert Atoms('H2', positions=[[0,0,0], [0,0,0.74]]).get_distance(0,1) == 0.74
assert len(Structure(Lattice.cubic(3.5), ['Si'], [[0,0,0]])) == 1
image = io.BytesIO()
plt.plot([0,1], [0,1]); plt.savefig(image, format='png'); plt.close('all')
assert image.getvalue().startswith(bytes([137]) + b'PNG')
report = {'python_version': platform.python_version(), 'package_versions': {
    name: metadata.version(name) for name in ['numpy','scipy','ase','pymatgen','matplotlib','mp-api','emmet-core']}}
pathlib.Path('/opt/oaw-python/.oaw-science-ready.json').write_text(json.dumps(report))
print(json.dumps(report), flush=True)
'''


def template_body(config, name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,35}', name):
        raise ValueError('Expected a unique template name of at most 36 characters')
    region, team = config['region'], config['team']
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,48}', region) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', team):
        raise ValueError('Invalid region or team')
    python = '/opt/oaw-python/bin/python'
    steps = [
        shlex.join(['python3', '-I', '-m', 'venv', '/opt/oaw-python']),
        shlex.join([python, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
            'install', '--only-binary=:all:', '--no-cache-dir', '--index-url',
            'https://mirrors.aliyun.com/pypi/simple/', 'uv==0.12.7']),
        shlex.join(['/opt/oaw-python/bin/uv', 'pip', 'install', '--python', python,
            '--only-binary=:all:', '--no-cache', '--index-url',
            'https://mirrors.aliyun.com/pypi/simple/', *REQUIREMENTS]),
        shlex.join([python, '-I', '-m', 'pip', 'check']),
        shlex.join([python, '-I', '-c', PROBE]),
        'exec sleep infinity',
    ]
    command = shlex.join(['sudo', '-n', 'sh', '-c', ' && '.join(steps)])
    return {'name': name, 'teamID': team, 'buildConfig': {'installStorageClients': True},
            'runtimeConfig': {'cpu': 2, 'memorySize': 2048, 'diskSize': 15360, 'internetAccess': True,
                'sandboxConfig': {'generation': 2, 'osType': 'linux-amd64',
                    'image': f'fc-e2b-registry.{region}.cr.aliyuncs.com/runtime-v2/base:v0.0.53',
                    'startCommand': command, 'readyCommand': 'test -s ' + READY}}}


def call(cloud, action, path, *, body=None, query=None):
    models = cloud.models
    params = models.Params(action=action, version='2026-08-20', protocol='HTTPS',
        pathname=path, method='POST' if body is not None else 'GET', auth_type='AK',
        style='ROA', req_body_type='json', body_type='json')
    query = {key: str(value) for key, value in query.items()} if query is not None else None
    return cloud.clients['sandbox'].call_api(params, models.OpenApiRequest(body=body, query=query), cloud.runtime)['body']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'status'])
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    parser.add_argument('--name')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    cloud = Cloud(config)
    if args.action == 'build':
        if args.receipt.exists():
            raise ValueError('Template creation is reserved; inspect its receipt instead of repeating it')
        body = template_body(config, args.name or '')
        state = {'phase': 'reserved', 'name': body['name'], 'requirements': REQUIREMENTS}
        write(args.receipt, state)
        try:
            result = call(cloud, 'CreateTemplate', '/pop/2026-08-20/templates', body=body)
            state.update(phase='submitted', template_id=result['templateID'])
        except Exception as error:
            state.update(phase='needs_reconciliation', error_type=type(error).__name__, code=getattr(error, 'code', None))
        write(args.receipt, state)
    else:
        state = json.loads(args.receipt.read_text())
        result = call(cloud, 'GetTemplate', '/pop/2026-08-20/templates/' + state['template_id'], query={'teamID': config['team']})
        state['status'] = result.get('status')
        state['phase'] = result.get('status', {}).get('state', 'unknown')
        write(args.receipt, state)
    print(json.dumps(state))


if __name__ == '__main__':
    main()
