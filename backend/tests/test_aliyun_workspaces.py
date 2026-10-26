from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.cloud.aliyun import hosted, workspace
from backend.cloud.aliyun.sandbox import AliyunSandboxSettings
from backend.config import Settings
from backend.hosting.worker import hosted_scope

spec = importlib.util.spec_from_file_location('aliyun_workspaces', Path(__file__).parents[2] / 'scripts/aliyun_workspaces.py')
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


def assignment(profile):
    return {'version': 1, 'profile_id': profile, 'region': 'cn-hangzhou', 'template': 'storage-template',
            'volume': 'oaw-' + profile, 'storage_addresses': ['172.28.1.39'], 'verified': True}


@pytest.mark.parametrize('address', ['0.0.0.0/0', '172.28.0.0/16', '127.0.0.1', '169.254.169.254',
                                     '100.100.100.200', '8.8.8.8', 'example.com', '::1'])
def test_storage_exception_cannot_open_metadata_public_or_whole_network(address):
    with pytest.raises(ValueError, match='exact private IPv4'):
        AliyunSandboxSettings('cn-hangzhou', 'key', 'base', 'private-volume', 'profile', storage_addresses=(address,))


def test_storage_exception_is_bound_to_volume_and_durable_identity():
    value = AliyunSandboxSettings('cn-hangzhou', 'key', 'base', 'private-volume', 'profile', storage_addresses=('172.28.1.39',))
    with pytest.raises(ValueError, match='dedicated Volume'):
        replace(value, workspace_store='ecs')


@pytest.fixture
def operator_file_metadata(monkeypatch):
    # Exercise content checks on Linux CI without requiring the runner to be root.
    original = os.fstat
    def owned(fd):
        result = list(original(fd))
        result[4] = 0
        return os.stat_result(result)
    monkeypatch.setattr(workspace.os, 'fstat', owned)


@pytest.mark.parametrize('change', [{'profile_id': str(uuid4())}, {'volume': 'another-users-volume'}, {'verified': False}, {'storage_addresses': []}, {'version': True}])
def test_assignment_rejects_unverified_or_foreign_volume(tmp_path, change, operator_file_metadata):
    profile = str(uuid4())
    # Content validation is exercised with the same reader as hosted startup.
    path = tmp_path / 'aliyun-workspace.json'
    path.write_text(json.dumps({**assignment(profile), **change}))
    path.chmod(0o600)
    with pytest.raises(ValueError, match='must be verified and belong'):
        workspace.load_assignment(tmp_path / 'data', profile)


def test_assignment_reads_operator_file_and_rejects_aliases(tmp_path, operator_file_metadata):
    profile = str(uuid4())
    assert workspace.load_assignment(tmp_path / 'data', profile) is None
    path = tmp_path / 'aliyun-workspace.json'
    path.write_text(json.dumps(assignment(profile)))
    path.chmod(0o600)
    assert workspace.load_assignment(tmp_path / 'data', profile) == assignment(profile)
    # A hard link into user-writable storage must never authorize a mount.
    (tmp_path / 'user-copy.json').hardlink_to(path)
    with pytest.raises(ValueError, match='operator-owned regular file'):
        workspace.load_assignment(tmp_path / 'data', profile)


@pytest.mark.parametrize('uid,mode', [(1000, 0o100600), (0, 0o100666)])
def test_posix_assignment_rejects_user_owned_or_writable_file(tmp_path, monkeypatch, uid, mode):
    path = tmp_path / 'aliyun-workspace.json'
    profile = str(uuid4())
    path.write_text(json.dumps(assignment(profile)))
    original = os.fstat
    def unsafe(fd):
        result = list(original(fd))
        result[0], result[4] = mode, uid
        return os.stat_result(result)
    # Only this module's platform decision changes, not pathlib's platform.
    monkeypatch.setattr(workspace, 'os', SimpleNamespace(name='posix', open=os.open, O_RDONLY=os.O_RDONLY,
        O_NOFOLLOW=getattr(os, 'O_NOFOLLOW', 0), fdopen=os.fdopen, fstat=unsafe))
    with pytest.raises(ValueError, match='operator-owned regular file'):
        workspace.load_assignment(tmp_path / 'data', profile)


def test_profile_assignment_overrides_ecs_without_affecting_other_profiles(tmp_path, monkeypatch):
    profile, other = str(uuid4()), str(uuid4())
    expected = assignment(profile)
    monkeypatch.setattr(hosted, 'load_assignment', lambda root, identifier: expected if identifier == profile else None)
    for key, value in {'OAW_ALIYUN_REGION': 'cn-hangzhou', 'OAW_ALIYUN_SANDBOX_API_KEY': 'key',
        'OAW_ALIYUN_WORKSPACE_STORE': 'ecs', 'OAW_ALIYUN_SANDBOX_TEMPLATE': 'old', 'OAW_ALIYUN_SANDBOX_READY': '1'}.items():
        monkeypatch.setenv(key, value)
    for key in ('OAW_ALIYUN_PROFILE_ID', 'OAW_ALIYUN_WORKSPACE_VOLUME'):
        monkeypatch.delenv(key, raising=False)
    captured = []
    monkeypatch.setattr(hosted, 'registry_factory', lambda remote: captured.append(remote))
    monkeypatch.setattr(hosted, 'approved_plugins', lambda root: object())
    monkeypatch.setattr(hosted, 'create_services', lambda *a, **k: SimpleNamespace(world=SimpleNamespace(list_cards=lambda: [])))
    for identifier in (profile, other):
        settings = replace(Settings.for_data_root(tmp_path / identifier / 'data'), tenant_scope=hosted_scope(str(uuid4()), 'canvas', identifier))
        hosted.create_hosted_services(settings)
    assert captured[0].volume == 'oaw-' + profile and captured[0].storage_addresses == ('172.28.1.39',)
    assert captured[0].template == 'storage-template' and captured[0].workspace_store == 'volume'
    assert captured[1].workspace_store == 'ecs' and not captured[1].storage_addresses


def test_provisioning_never_replays_an_unknown_cloud_mutation(tmp_path):
    class Cloud:
        calls = 0
        def call(self, *args):
            self.calls += 1
            raise TimeoutError('Response lost after provider accepted request')
    cloud = Cloud()
    config = {'region': 'cn-hangzhou', 'filesystem': 'fs', 'zone': 'cn-hangzhou-k', 'quota_bytes': 1024, 'quota_files': 10}
    profile = str(uuid4())
    with pytest.raises(TimeoutError):
        operator.provision(config, profile, cloud, tmp_path)
    with pytest.raises(ValueError, match='uncertain outcome'):
        operator.provision(config, profile, cloud, tmp_path)
    assert cloud.calls == 1
    receipt = json.loads((tmp_path / (profile + '.json')).read_text())
    assert receipt['steps']['space']['phase'] == 'reserved'
    assert receipt['steps']['space']['request']['ClientToken']


def test_mount_policy_only_authorizes_one_access_point():
    point = 'acs:nas:cn-hangzhou:123:accesspoint/ap-own'
    policy = operator.mount_policy(point)
    statement, = policy['Statement']
    assert statement['Condition'] == {'StringEquals': {'nas:AccessPointArn': point}}
    assert statement['Action'] == ['nas:ClientMount', 'nas:ClientWrite']


def test_provisioned_resources_resume_without_recreation_or_automatic_activation(tmp_path):
    profile = str(uuid4())
    class Cloud:
        calls = []
        def call(self, service, action, data):
            self.calls.append((service, action, data))
            return {
                'CreateAgenticSpace': {'AgenticSpaceId': 'space'},
                'CreateAccessPoint': {'AccessPoint': {'AccessPointId': 'ap-own'}},
                'DescribeAccessPoint': {'AccessPoint': {'AccessPointId': 'ap-own', 'Status': 'active',
                    'ARN': 'acs:nas:cn-hangzhou:123:accesspoint/ap-own', 'DomainName': 'own.nas.aliyuncs.com'}},
                'CreateRole': {'Role': {'Arn': 'acs:ram::123:role/own'}},
                'CreateVolume': {'volume': {'volumeName': 'oaw-' + profile}},
            }.get(action, {})
    cloud = Cloud()
    config = {'region': 'cn-hangzhou', 'filesystem': 'fs', 'zone': 'cn-hangzhou-k',
        'quota_bytes': 10 * 1024**3, 'quota_files': 10000, 'team': 'team', 'vpc': 'vpc',
        'vswitch': 'switch', 'security_group': 'group'}
    result = operator.provision(config, profile, cloud, tmp_path)
    assert result['phase'] == 'provisioned_not_verified'
    mutations = [call for call in cloud.calls if call[1] != 'DescribeAccessPoint']
    space, volume = mutations[0][2], mutations[-1][2]
    assert space['FileSystemPath'] == '/oaw' + profile.replace('-', '') + '/'
    assert volume['volumeName'] == 'oaw-' + profile
    assert volume['agenticFSVolumeConfig'] == {'serverAddr': 'own.nas.aliyuncs.com:/', 'userID': 1000, 'groupID': 1000}
    assert volume['mountConfig']['role'] == 'acs:ram::123:role/own'
    assert ('ram', 'UpdateRole', {'RoleName': 'OawWorkspace-' + profile.replace('-', ''), 'NewAllowConsoleLogin': False}) in mutations
    assert operator.provision(config, profile, cloud, tmp_path) == result
    assert [call for call in cloud.calls if call[1] != 'DescribeAccessPoint'] == mutations
    assert json.loads((tmp_path / (profile + '.json')).read_text())['verified'] is False
