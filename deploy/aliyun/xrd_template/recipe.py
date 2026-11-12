"""Deploy a prebuilt, digest-pinned XRD image. Never build during VM startup."""
import re
import shlex


PREFIX = '/opt/oaw-xrd'
READY = PREFIX + '/ready.json'


def configure(body, image, revision):
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('XRD template source must be a full immutable Git commit')
    runtime = body['runtimeConfig']
    region = runtime['sandboxConfig']['image'].split('.')[1]
    if (not isinstance(image, str) or not re.fullmatch(
            r'[a-z0-9][a-z0-9-]*-registry-vpc\.' + re.escape(region)
            + r'\.cr\.aliyuncs\.com/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*@sha256:[0-9a-f]{64}', image)):
        raise ValueError('Use an ACR Enterprise VPC image digest in the template region')
    runtime.update(cpu=4, memorySize=8192)
    check = ('import json,pathlib; '
             f'r=json.loads(pathlib.Path({READY!r}).read_text()); '
             f'assert r["source_revision"] == {revision!r}; '
             'assert r["pack_id"] == "research.xrd" and r["qualx"] == "1.0.5-oaw"')
    runtime['sandboxConfig'].update(
        image=image, startCommand='sleep infinity',
        readyCommand=shlex.join(['python3', '-I', '-c', check]))
    return body


def deployment(pack_version, template):
    """Recipe only; callers must not mark a profile assignment verified from it."""
    launcher = PREFIX + '/pack/src/oaw_xrd/worker_launcher.py'
    return {'pack_id': 'research.xrd', 'pack_version': pack_version,
            'environment_id': 'scientific', 'template': template,
            'commands': {worker: [PREFIX + '/venv/bin/python', '-I', '-X', 'utf8', '-u',
                                  launcher, '-', worker]
                         for worker in ('worker.py', 'multiphase_worker.py')},
            'environment': {'OAW_XRD_QUALX_EXECUTABLE': PREFIX + '/qualx/bin/qualx',
                            'OAW_XRD_ENVIRONMENT': '-',
                            'QT_QPA_PLATFORM': 'offscreen', 'MPLBACKEND': 'Agg',
                            'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                            'TF_NUM_INTRAOP_THREADS': '1', 'TF_NUM_INTEROP_THREADS': '1'}}
