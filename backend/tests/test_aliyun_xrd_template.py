import ast
import json
from pathlib import Path
import shlex
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest

from deploy.aliyun.xrd_template.recipe import PREFIX, READY, configure, deployment
from scripts import aliyun_science_template as templates


ROOT = Path(__file__).resolve().parents[2]
REVISION = 'e16c64b35c7bf4a0ec016e2ed000b137fdb03e0d'
IMAGE = 'example-registry-vpc.cn-hangzhou.cr.aliyuncs.com/oaw/xrd@sha256:' + 'a'*64


def test_xrd_deployment_only_uses_a_fixed_prebuilt_image():
    body = configure(templates.template_body({'region': 'cn-hangzhou', 'team': 'test',
                     'api_key': 'private-token'}, 'oaw-xrd-test'), IMAGE, REVISION)
    runtime = body['runtimeConfig']
    sandbox = runtime['sandboxConfig']
    assert sandbox['image'] == IMAGE
    assert sandbox['startCommand'] == 'sleep infinity'
    ready = shlex.split(sandbox['readyCommand'])
    ast.parse(ready[-1])
    assert ready[:3] == ['python3', '-I', '-c']
    assert READY in ready[-1] and REVISION in ready[-1]
    assert all(word not in sandbox['startCommand'] for word in ('sudo', 'pip', 'git', 'apt', 'curl'))
    assert runtime['cpu'] == 4 and runtime['memorySize'] == 8192
    assert body['buildConfig']['installStorageClients'] is True
    assert 'private-token' not in json.dumps(body)


@pytest.mark.parametrize('revision', ['dev', 'e16c64b3', '../source', 'a'*40+';id', None])
def test_mutable_or_injectable_source_is_rejected(revision):
    with pytest.raises(ValueError, match='immutable Git commit'):
        configure({}, IMAGE, revision)


@pytest.mark.parametrize('image', [IMAGE.split('@')[0]+':latest', IMAGE.replace('cn-hangzhou', 'cn-beijing'),
                                  'https://'+IMAGE, IMAGE.replace('example-registry-vpc', 'evil.example'),
                                  IMAGE.replace('/oaw/', '/../../'), 'user:password@'+IMAGE])
def test_mutable_wrong_region_or_credential_bearing_images_are_rejected(image):
    with pytest.raises(ValueError, match='VPC image digest'):
        configure(templates.template_body({'region': 'cn-hangzhou', 'team': 'test'}, 'xrd'), image, REVISION)


def test_readiness_rejects_an_image_built_from_another_commit(tmp_path):
    body = configure(templates.template_body({'region': 'cn-hangzhou', 'team': 'test'}, 'xrd'), IMAGE, REVISION)
    check = shlex.split(body['runtimeConfig']['sandboxConfig']['readyCommand'])[-1]
    record = tmp_path/'ready.json'
    check = check.replace(repr(READY), repr(str(record)))
    record.write_text(json.dumps({'source_revision': '0'*40, 'pack_id': 'research.xrd', 'qualx': '1.0.5-oaw'}))
    with pytest.raises(AssertionError):
        exec(compile(check, '<readiness>', 'exec'), {})
    record.write_text(json.dumps({'source_revision': REVISION, 'pack_id': 'research.xrd', 'qualx': '1.0.5-oaw'}))
    exec(compile(check, '<readiness>', 'exec'), {})


def test_linux_lock_tracks_the_pack_scientific_dependencies():
    path = ROOT / 'plugins/xrd/src/oaw_xrd/OAW_XRDfit/requirements.txt'
    lock = ROOT / 'deploy/aliyun/xrd_template/requirements.lock'
    locked = [Requirement(line) for line in lock.read_text().splitlines() if line.strip() and not line.startswith('#')]
    versions = {canonicalize_name(r.name): str(r.specifier) for r in locked}
    assert len(versions) == len(locked)
    assert all(str(r.specifier).startswith('==') and not r.url and not r.marker for r in locked)
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            required = Requirement(line)
            assert versions[canonicalize_name(required.name)] == str(required.specifier)


def test_generated_recipe_uses_existing_workers_and_never_asserts_profile_acceptance():
    recipe = deployment('0.3.1', 'reviewed-template')
    assert set(recipe['commands']) == {'worker.py', 'multiphase_worker.py'}
    for worker, argv in recipe['commands'].items():
        assert argv == [PREFIX+'/venv/bin/python', '-I', '-X', 'utf8', '-u',
                        PREFIX+'/pack/src/oaw_xrd/worker_launcher.py', '-', worker]
    assert recipe['environment']['OAW_XRD_ENVIRONMENT'] == '-'
    assert 'verified' not in recipe and 'mount_path' not in recipe


def test_plan_is_offline_and_does_not_reserve_a_cloud_operation(tmp_path, monkeypatch, capsys):
    config = tmp_path/'config.json'
    config.write_text(json.dumps({'region': 'cn-hangzhou', 'team': 'test'}))
    monkeypatch.setattr(templates, 'Cloud', lambda *_: pytest.fail('plan must not connect to cloud'))
    monkeypatch.setattr(sys, 'argv', ['template', 'plan', '--config', str(config),
                        '--name', 'oaw-xrd-test', '--xrd-revision', REVISION, '--xrd-image', IMAGE])
    templates.main()
    assert json.loads(capsys.readouterr().out)['runtimeConfig']['cpu'] == 4
    assert list(tmp_path.iterdir()) == [config]


def test_uncertain_xrd_creation_preserves_revision_and_cannot_be_replayed(tmp_path, monkeypatch, capsys):
    config, receipt = tmp_path/'config.json', tmp_path/'receipt.json'
    config.write_text(json.dumps({'region': 'cn-hangzhou', 'team': 'test'}))
    monkeypatch.setattr(sys, 'argv', ['template', 'build', '--config', str(config), '--receipt', str(receipt),
                        '--name', 'oaw-xrd-test', '--xrd-revision', REVISION, '--xrd-image', IMAGE])
    monkeypatch.setattr(templates, 'Cloud', lambda *_: object())
    def lost_response(*args, **kwargs):
        assert json.loads(receipt.read_text())['phase'] == 'reserved'
        raise TimeoutError('response lost')
    monkeypatch.setattr(templates, 'call', lost_response)
    templates.main()
    result = json.loads(capsys.readouterr().out)
    assert result['phase'] == 'needs_reconciliation' and result['revision'] == REVISION
    assert result['recipe'] == 'research.xrd' and 'requirements' not in result
    monkeypatch.setattr(templates, 'Cloud', lambda *_: pytest.fail('reserved mutation must not connect'))
    with pytest.raises(ValueError, match='creation is reserved'):
        templates.main()
