import json
import sys

import pytest

from deploy.aliyun.xrd_template.recipe import READY
from scripts import aliyun_acs_science_trial as trial


REVISION = 'dc17e2cce5e83361cfd0eead5c156bfdca194e76'
IMAGE = 'ghcr.io/example/xrd@sha256:' + 'a' * 64


@pytest.mark.parametrize('repository', ['ghcr.io/example/xrd',
    'registry.cn-hangzhou.aliyuncs.com/example/xrd',
    'crpi-example.cn-hangzhou.personal.cr.aliyuncs.com/example/xrd'])
def test_trial_accepts_personal_and_third_party_digest_images(repository):
    image = repository + '@sha256:' + 'a' * 64
    manifest = trial.job_manifest(image, REVISION, 'oaw-science-trial', 'xrd-001')
    assert manifest['spec']['template']['spec']['containers'][0]['image'] == image


def test_trial_bounds_execution_and_excludes_host_and_user_storage():
    manifest = trial.job_manifest(IMAGE, REVISION, 'oaw-science-trial', 'xrd-001', 'registry-auth')
    job = manifest['spec']
    assert job['parallelism'] == job['completions'] == 1
    assert job['backoffLimit'] == 0 and job['activeDeadlineSeconds'] == 900
    pod = job['template']['spec']
    assert pod['restartPolicy'] == 'Never' and pod['automountServiceAccountToken'] is False
    assert pod['securityContext'] == {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000}
    assert pod['imagePullSecrets'] == [{'name': 'registry-auth'}]
    assert pod['volumes'] == [{'name': 'scratch', 'emptyDir': {'sizeLimit': '1Gi'}}]
    container = pod['containers'][0]
    assert container['securityContext'] == {'allowPrivilegeEscalation': False,
        'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    assert container['resources']['limits'] == {'cpu': '4', 'memory': '8Gi', 'ephemeral-storage': '2Gi'}


@pytest.mark.parametrize('image', [IMAGE.split('@')[0] + ':latest', 'https://' + IMAGE,
                                  'user:password@' + IMAGE, '../xrd@sha256:' + 'a' * 64])
def test_trial_rejects_mutable_or_credential_bearing_images(image):
    with pytest.raises(ValueError, match='image repository'):
        trial.job_manifest(image, REVISION, 'trial', 'xrd-001')


@pytest.mark.parametrize('revision,namespace,name,secret', [
    ('dev', 'trial', 'xrd', None), (REVISION, 'kube-system/../default', 'xrd', None),
    (REVISION, '', 'xrd', None), (REVISION, 'trial', 'xrd;id', None),
    (REVISION, 'trial', 'xrd', 'registry/auth')])
def test_trial_rejects_invalid_resource_identity(revision, namespace, name, secret):
    with pytest.raises(ValueError):
        trial.job_manifest(IMAGE, revision, namespace, name, secret)


def test_trial_rejects_wrong_image_source_before_scientific_execution(tmp_path):
    manifest = trial.job_manifest(IMAGE, REVISION, 'trial', 'xrd')
    program = manifest['spec']['template']['spec']['containers'][0]['command'][-1]
    record = tmp_path / 'ready.json'
    record.write_text(json.dumps({'source_revision': '0' * 40}))
    program = program.replace(repr(READY), repr(str(record)))
    with pytest.raises(RuntimeError, match='source revision'):
        exec(compile(program, '<acs-acceptance>', 'exec'), {'__name__': '__main__'})


def test_cli_renders_offline_without_cloud_configuration(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['trial', '--image', IMAGE, '--revision', REVISION,
                                    '--namespace', 'trial', '--name', 'xrd-001'])
    trial.main()
    manifest = json.loads(capsys.readouterr().out)
    assert manifest['kind'] == 'Job' and manifest['metadata']['namespace'] == 'trial'
