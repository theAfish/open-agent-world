"""Render an offline ACS Job for the existing XRD image acceptance command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from deploy.aliyun.xrd_template.recipe import PREFIX, READY


ACCEPTANCE = Path(__file__).resolve().parents[1] / 'deploy/aliyun/xrd_template/acceptance.py'


def job_manifest(image, revision, namespace, name, image_pull_secret=None):
    if not isinstance(image, str) or not re.fullmatch(
            r'[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/'
            r'(?:[a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*@sha256:[0-9a-f]{64}', image):
        raise ValueError('Use a fully qualified image repository pinned by SHA-256 digest')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Use the full source commit recorded by the reviewed image')
    for value in (namespace, name, image_pull_secret):
        if value is not None and (not isinstance(value, str) or len(value) > 63
                or not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?', value)):
            raise ValueError('Use a DNS label of at most 63 characters for resource names')
    if not namespace or not name:
        raise ValueError('Specify the isolated trial namespace and a unique Job name')

    program = f'''import json
from pathlib import Path
record = json.loads(Path({READY!r}).read_text())
if record.get('source_revision') != {revision!r}:
    raise RuntimeError('Image source revision differs from the reviewed trial')
if record.get('pack_id') != 'research.xrd' or record.get('qualx') != '1.0.5-oaw':
    raise RuntimeError('Image does not contain the reviewed XRD environment')
''' + ACCEPTANCE.read_text(encoding='utf-8')

    resources = {'cpu': '4', 'memory': '8Gi', 'ephemeral-storage': '2Gi'}
    pod = {
        'automountServiceAccountToken': False,
        'enableServiceLinks': False,
        'restartPolicy': 'Never',
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000},
        'containers': [{
            'name': 'scientific-acceptance', 'image': image, 'imagePullPolicy': 'Always',
            'command': [PREFIX + '/venv/bin/python', '-I', '-c', program],
            'args': ['--directory', '/tmp/oaw-xrd-acceptance'],
            'env': [{'name': 'HOME', 'value': '/tmp'}, {'name': 'XDG_CACHE_HOME', 'value': '/tmp/.cache'}],
            'resources': {'requests': dict(resources), 'limits': dict(resources)},
            'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL']}},
            'volumeMounts': [{'name': 'scratch', 'mountPath': '/tmp'}],
        }],
        'volumes': [{'name': 'scratch', 'emptyDir': {'sizeLimit': '1Gi'}}],
    }
    if image_pull_secret:
        pod['imagePullSecrets'] = [{'name': image_pull_secret}]
    return {
        'apiVersion': 'batch/v1', 'kind': 'Job',
        'metadata': {'name': name, 'namespace': namespace},
        'spec': {
            'completions': 1, 'parallelism': 1, 'backoffLimit': 0,
            'activeDeadlineSeconds': 900, 'ttlSecondsAfterFinished': 3600,
            'template': {
                'metadata': {'labels': {'app.kubernetes.io/name': 'oaw-science-trial',
                    'alibabacloud.com/compute-class': 'general-purpose',
                    'alibabacloud.com/compute-qos': 'default'}},
                'spec': pod,
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--image-pull-secret')
    args = parser.parse_args()
    try:
        manifest = job_manifest(args.image, args.revision, args.namespace, args.name, args.image_pull_secret)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
