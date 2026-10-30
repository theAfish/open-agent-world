import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.cloud.aliyun import provisioning
from backend.sandbox.models import SandboxSecurityError, SandboxStateError
from scripts import aliyun_workspaces as operator


MUTATIONS = {'CreateAgenticSpace', 'CreateAccessPoint', 'CreateRole', 'UpdateRole',
             'CreatePolicy', 'AttachPolicyToRole', 'CreateVolume'}


def settings():
    return {'region': 'cn-hangzhou', 'filesystem': 'fs-owned', 'zone': 'cn-hangzhou-k',
            'vpc': 'vpc-owned', 'vswitch': 'vsw-owned', 'security_group': 'sg-owned', 'team': 'team-owned',
            'account_id': '1234567890123456', 'quota_bytes': 10 * 1024**3, 'quota_files': 10000,
            'template': 'storage-template', 'storage_cidr': '172.28.1.0/24', 'credential_type': 'ecs_ram_role',
            'storage_role': 'acs:ram::1234567890123456:role/oaw-storage'}


class ControlCloud:
    """Small resource-state fake; lost responses happen after the provider mutation."""
    def __init__(self, config, lose=None):
        self.config, self.lose, self.calls = config, lose, []
        self.spaces, self.points, self.roles, self.policies, self.attachments, self.volumes = [], [], {}, {}, [], []

    def call(self, service, action, data):
        self.calls.append((action, deepcopy(data)))
        response = {}
        if action == 'CreateAgenticSpace':
            item = {key: data[key] for key in ('FileSystemId', 'FileSystemPath', 'Azone', 'Description')}
            item.update(AgenticSpaceId='space-own', Status='Running',
                        Quota={'SizeLimit': data['Quota.SizeLimit'], 'FileCountLimit': data['Quota.FileCountLimit']})
            self.spaces.append(item)
            response = {'AgenticSpaceId': 'space-own'}
        elif action == 'DescribeAgenticSpaces':
            response = {'AgenticSpaces': {'AgenticSpace': self.spaces}}
        elif action == 'CreateAccessPoint':
            item = {key: data[key] for key in ('FileSystemId', 'AgenticSpaceId', 'AccessPointName', 'VpcId', 'EnabledRam')}
            item.update(AccessPointId='ap-own', VSwitchId=data['VswId'], Status='active',
                        ARN='acs:nas:cn-hangzhou:1234567890123456:accesspoint/ap-own',
                        DomainName='ap-own.fs-owned.cn-hangzhou.nas.aliyuncs.com')
            self.points.append(item)
            response = {'AccessPoint': {'AccessPointId': 'ap-own'}}
        elif action == 'ListAccessPoints':
            response = {'AccessPoints': self.points}
        elif action == 'DescribeAccessPoint':
            response = {'AccessPoint': self.points[0]}
        elif action == 'CreateRole':
            item = {key: data[key] for key in ('RoleName', 'AssumeRolePolicyDocument', 'Description', 'AllowConsoleLogin')}
            item['Arn'] = 'acs:ram::1234567890123456:role/' + data['RoleName'].lower()
            self.roles[data['RoleName']] = item
            response = {'Role': item}
        elif action == 'GetRole':
            if data['RoleName'] not in self.roles:
                raise operator.CloudOperationError(action, 'EntityNotExist.Role')
            response = {'Role': self.roles[data['RoleName']]}
        elif action == 'UpdateRole':
            self.roles[data['RoleName']]['AllowConsoleLogin'] = data['NewAllowConsoleLogin']
        elif action == 'CreatePolicy':
            self.policies[data['PolicyName']] = {'Policy': {'PolicyName': data['PolicyName'], 'PolicyType': 'Custom',
                'Description': data['Description'], 'DefaultVersion': 'v1'},
                'DefaultPolicyVersion': {'PolicyDocument': data['PolicyDocument'], 'IsDefaultVersion': True}}
        elif action == 'GetPolicy':
            if data['PolicyName'] not in self.policies:
                raise operator.CloudOperationError(action, 'EntityNotExist.Policy')
            response = self.policies[data['PolicyName']]
        elif action == 'AttachPolicyToRole':
            self.attachments.append({'PolicyName': data['PolicyName'], 'PolicyType': data['PolicyType']})
        elif action == 'ListPoliciesForRole':
            response = {'Policies': {'Policy': self.attachments}}
        elif action == 'CreateVolume':
            item = deepcopy(data)
            item.update(volumeID='volume-own', status='AVAILABLE')
            self.volumes.append(item)
            response = {'volume': item}
        elif action == 'ListVolumes':
            response = {'volumes': self.volumes}
        elif action == 'GetVolume':
            response = {'volume': self.volumes[0]}
        else:
            raise AssertionError('Unexpected cloud action ' + action)
        if action == self.lose:
            self.lose = None
            raise TimeoutError('Provider committed; response lost')
        return deepcopy(response)


@pytest.mark.parametrize('action', sorted(MUTATIONS))
def test_reserved_mutations_resume_only_through_exact_read_only_reconciliation(tmp_path, action):
    config = settings()
    config.pop('storage_role')
    cloud, profile = ControlCloud(config, lose=action), str(uuid4())
    with pytest.raises(operator.ProvisioningPending):
        operator.provision(config, profile, cloud, tmp_path, reconcile=True)
    assert operator.provision(config, profile, cloud, tmp_path, reconcile=True)['phase'] == 'provisioned_not_verified'
    assert [name for name, _ in cloud.calls].count(action) == 1
    receipt = json.loads((tmp_path / (profile + '.json')).read_text())
    assert all(step['phase'] == 'complete' for step in receipt['steps'].values())


def test_absent_unknown_create_remains_reserved_without_replay(tmp_path):
    cloud, profile = ControlCloud(settings(), lose='CreateAgenticSpace'), str(uuid4())
    with pytest.raises(operator.ProvisioningPending):
        operator.provision(cloud.config, profile, cloud, tmp_path, reconcile=True)
    cloud.spaces.clear()  # Listing does not establish whether the original request failed.
    with pytest.raises(operator.ProvisioningPending):
        operator.provision(cloud.config, profile, cloud, tmp_path, reconcile=True)
    assert [name for name, _ in cloud.calls].count('CreateAgenticSpace') == 1
    assert json.loads((tmp_path / (profile + '.json')).read_text())['steps']['space']['phase'] == 'reserved'


@pytest.mark.parametrize('action,change', [
    ('CreateAgenticSpace', lambda c: c.spaces[0].update(FileSystemId='foreign-filesystem')),
    ('CreateAccessPoint', lambda c: c.points[0].update(AgenticSpaceId='foreign-space')),
    ('CreateRole', lambda c: c.roles[next(iter(c.roles))].update(AssumeRolePolicyDocument='{"Version":"1","Statement":[]}')),
    ('CreatePolicy', lambda c: c.policies[next(iter(c.policies))]['DefaultPolicyVersion'].update(PolicyDocument='{"Version":"1","Statement":[]}')),
    ('AttachPolicyToRole', lambda c: c.attachments.append({'PolicyName': 'AdministratorAccess', 'PolicyType': 'System'})),
    ('CreateVolume', lambda c: c.volumes[0]['mountConfig'].update(role='acs:ram::1234567890123456:role/foreign')),
])
def test_reconciliation_rejects_foreign_or_broader_resources(tmp_path, action, change):
    config = settings()
    config.pop('storage_role')
    cloud, profile = ControlCloud(config, lose=action), str(uuid4())
    with pytest.raises(operator.ProvisioningPending):
        operator.provision(config, profile, cloud, tmp_path, reconcile=True)
    change(cloud)
    before = [call for call in cloud.calls if call[0] in MUTATIONS]
    with pytest.raises(ValueError, match='does not match'):
        operator.provision(config, profile, cloud, tmp_path, reconcile=True)
    assert [call for call in cloud.calls if call[0] in MUTATIONS] == before


def test_duplicate_resource_matches_are_not_adopted(tmp_path):
    cloud, profile = ControlCloud(settings(), lose='CreateAgenticSpace'), str(uuid4())
    with pytest.raises(operator.ProvisioningPending):
        operator.provision(cloud.config, profile, cloud, tmp_path, reconcile=True)
    cloud.spaces.append(deepcopy(cloud.spaces[0]))
    with pytest.raises(ValueError, match='does not match'):
        operator.provision(cloud.config, profile, cloud, tmp_path, reconcile=True)


def test_preapproved_storage_role_never_uses_ram_administration(tmp_path):
    cloud, profile = ControlCloud(settings()), str(uuid4())
    operator.provision(cloud.config, profile, cloud, tmp_path, reconcile=True)
    assert set(name for name, _ in cloud.calls) <= {'CreateAgenticSpace', 'DescribeAgenticSpaces', 'CreateAccessPoint',
        'ListAccessPoints', 'CreateVolume', 'ListVolumes', 'GetVolume'}
    assert cloud.volumes[0]['mountConfig']['role'] == cloud.config['storage_role']


def test_credentials_rotate_after_explicit_legacy_receipt_migration(tmp_path):
    config = {**settings(), 'credential_type': 'cli', 'credentials_file': '/operator/original.json', 'cli_profile': 'original'}
    profile, cloud = str(uuid4()), ControlCloud(config)
    operator.provision(config, profile, cloud, tmp_path)
    path = tmp_path / (profile + '.json')
    receipt = json.loads(path.read_text())
    receipt.pop('configuration_version')
    receipt['configuration'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    operator.write(path, receipt)
    rotated = {**config, 'credential_type': 'ecs_ram_role', 'credentials_file': '/different.json', 'cli_profile': 'different'}
    with pytest.raises(ValueError, match='original configuration'):
        operator.provision(rotated, profile, cloud, tmp_path)
    operator.provision(config, profile, cloud, tmp_path)
    mutations = [call for call in cloud.calls if call[0] in MUTATIONS]
    operator.provision(rotated, profile, cloud, tmp_path)
    assert [call for call in cloud.calls if call[0] in MUTATIONS] == mutations
    with pytest.raises(ValueError, match='different configuration'):
        operator.provision({**rotated, 'filesystem': 'different'}, profile, cloud, tmp_path)


def test_control_adapter_maps_volume_reads_to_get_query_parameters():
    calls = []
    def call(params, request, runtime):
        calls.append((params, request))
        return {'body': {'code': '200'}}
    cloud = operator.Cloud.__new__(operator.Cloud)
    cloud.models = SimpleNamespace(Params=SimpleNamespace, OpenApiRequest=SimpleNamespace)
    cloud.clients, cloud.runtime = {'sandbox': SimpleNamespace(call_api=call)}, object()
    cloud.call('sandbox', 'ListVolumes', {'teamID': 'own-team', 'volumeName': 'own-volume'})
    cloud.call('sandbox', 'GetVolume', {'teamID': 'own-team', 'volumeID': 'volume/id'})
    assert calls[0][0].method == 'GET' and calls[0][1].query == {'teamID': 'own-team', 'volumeName': 'own-volume'}
    assert calls[1][0].pathname.endswith('/volumes/volume%2Fid') and calls[1][1].query == {'teamID': 'own-team'}


def test_ecs_credentials_keep_refresh_provider_and_require_imdsv2(monkeypatch):
    providers, clients = [], []
    def credentials(config):
        providers.append(config)
        return object()
    def client(config):
        clients.append(config)
        return object()
    models = SimpleNamespace(Config=SimpleNamespace)
    for name, module in {
        'alibabacloud_tea_openapi': SimpleNamespace(utils_models=models),
        'alibabacloud_tea_openapi.client': SimpleNamespace(Client=client),
        'alibabacloud_tea_util': SimpleNamespace(models=SimpleNamespace(RuntimeOptions=SimpleNamespace)),
        'alibabacloud_credentials.client': SimpleNamespace(Client=credentials),
        'alibabacloud_credentials.models': SimpleNamespace(Config=SimpleNamespace),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    operator.Cloud(settings())
    assert providers[0].type == 'ecs_ram_role' and providers[0].disable_imds_v1 is True
    assert len(clients) == 3 and all(c.credential is clients[0].credential for c in clients)
    assert not any(hasattr(c, 'access_key_secret') for c in clients)


@pytest.mark.parametrize('change', [{'storage_role': 'acs:ram::9999999999999999:role/foreign'},
    {'credential_type': 'cli'}, {'storage_cidr': '0.0.0.0/0'}, {'quota_files': True}, {'arbitrary_shell': 'forbidden'}])
def test_unattended_settings_reject_unbounded_or_foreign_configuration(change):
    with pytest.raises(ValueError):
        provisioning.validate_settings({**settings(), **change})


@pytest.fixture
def portable_private_files(monkeypatch):
    # Exercise orchestration without assuming that the CI account is root.
    monkeypatch.setattr(provisioning, 'os', SimpleNamespace(name='nt', open=os.open, fdopen=os.fdopen,
        fstat=os.fstat, O_RDONLY=os.O_RDONLY, O_NOFOLLOW=getattr(os, 'O_NOFOLLOW', 0), O_NONBLOCK=getattr(os, 'O_NONBLOCK', 0)))


def test_hook_waits_before_verification_and_caches_only_verified_own_assignment(tmp_path, monkeypatch, portable_private_files):
    cloud, profile = ControlCloud(settings(), lose='CreateAccessPoint'), SimpleNamespace(profile_id=str(uuid4()))
    monkeypatch.setattr(provisioning, 'Cloud', lambda config: cloud)
    monkeypatch.setattr(provisioning, '_addresses', lambda hostname, config: ['172.28.1.39'])
    monkeypatch.setattr(provisioning, '_api_key', lambda directory: 'private-key')
    probes = []
    async def verify(remote, directory):
        probes.append(remote)
        return True
    monkeypatch.setattr(provisioning, '_verify_volume', verify)
    assert provisioning.prepare_profile(profile, settings(), tmp_path) is None
    assert not probes and not (tmp_path / 'instances').exists()
    cloud.points[0]['Status'] = 'pending'
    assert provisioning.prepare_profile(profile, settings(), tmp_path) is None
    cloud.points[0]['Status'] = 'active'
    result = provisioning.prepare_profile(profile, settings(), tmp_path)
    assert result['aliyun-workspace.json']['volume'] == 'oaw-' + profile.profile_id
    assert probes[0].profile_id == profile.profile_id and probes[0].storage_addresses == ('172.28.1.39',)
    calls = list(cloud.calls)
    assert provisioning.prepare_profile(profile, settings(), tmp_path) == result and cloud.calls == calls and len(probes) == 1
    ready = tmp_path / 'resource-provisioning/aliyun' / profile.profile_id / 'assignment.json'
    value = json.loads(ready.read_text())
    value['assignment']['profile_id'] = str(uuid4())
    operator.write(ready, value)
    with pytest.raises(ValueError, match='differs from this deployment'):
        provisioning.prepare_profile(profile, settings(), tmp_path)


def test_unavailable_volume_and_unknown_verification_never_activate_assignment(tmp_path, monkeypatch, portable_private_files):
    cloud, profile = ControlCloud(settings(), lose='CreateVolume'), SimpleNamespace(profile_id=str(uuid4()))
    monkeypatch.setattr(provisioning, 'Cloud', lambda config: cloud)
    monkeypatch.setattr(provisioning, '_addresses', lambda *args: ['172.28.1.39'])
    monkeypatch.setattr(provisioning, '_api_key', lambda directory: 'private-key')
    probes = []
    async def verify(remote, directory):
        probes.append(remote)
        raise SandboxStateError('Provider VM creation outcome unknown')
    monkeypatch.setattr(provisioning, '_verify_volume', verify)
    assert provisioning.prepare_profile(profile, settings(), tmp_path) is None
    cloud.volumes[0]['status'] = 'CREATING'
    assert provisioning.prepare_profile(profile, settings(), tmp_path) is None and not probes
    cloud.volumes[0]['status'] = 'AVAILABLE'
    assert provisioning.prepare_profile(profile, settings(), tmp_path) is None and len(probes) == 1
    assert not (tmp_path / 'resource-provisioning/aliyun' / profile.profile_id / 'assignment.json').exists()
    assert [action for action, _ in cloud.calls].count('CreateVolume') == 1


def test_endpoint_resolution_never_expands_beyond_operator_subnet(monkeypatch):
    monkeypatch.setattr(provisioning.socket, 'getaddrinfo', lambda *a, **k: [(None, None, None, None, ('172.28.2.39', 2049))])
    with pytest.raises(ValueError, match='approved private subnet'):
        provisioning._addresses('ap-own.fs.cn-hangzhou.nas.aliyuncs.com', settings())


def test_private_environment_reader_does_not_evaluate_shell(tmp_path, portable_private_files):
    path = tmp_path / 'worker-common.env'
    path.write_text('UNRELATED="do not parse this value"\nOAW_ALIYUN_SANDBOX_API_KEY="literal$(command)`text`"\n')
    assert provisioning._api_key(tmp_path) == 'literal$(command)`text`'
    alias = tmp_path / 'alias.env'
    alias.hardlink_to(path)
    with pytest.raises(ValueError, match='private root-owned'):
        provisioning._api_key(tmp_path)


@pytest.mark.parametrize('uid,mode', [(1000, 0o100600), (0, 0o100640)])
def test_private_environment_reader_rejects_nonprivate_metadata(tmp_path, monkeypatch, uid, mode):
    path = tmp_path / 'worker-common.env'
    path.write_text('OAW_ALIYUN_SANDBOX_API_KEY=private\n')
    original = os.fstat
    def foreign(fd):
        value = list(original(fd))
        value[4], value[0] = uid, mode
        return os.stat_result(value)
    monkeypatch.setattr(provisioning, 'os', SimpleNamespace(name='posix', open=os.open, fdopen=os.fdopen,
        fstat=foreign, O_RDONLY=os.O_RDONLY, O_NOFOLLOW=getattr(os, 'O_NOFOLLOW', 0), O_NONBLOCK=getattr(os, 'O_NONBLOCK', 0)))
    with pytest.raises(ValueError, match='private root-owned'):
        provisioning._api_key(tmp_path)


@pytest.mark.parametrize('failure', ['binding', 'write'])
def test_own_marker_probe_resumes_local_crash_or_lost_write_without_repeating_it(tmp_path, monkeypatch, portable_private_files, failure):
    files, writes, vms = {}, [], []
    lose_write, lose_binding = [failure == 'write'], [failure == 'binding']
    class Backend:
        def __init__(self, directory, remote):
            self.path, self.settings = directory / 'aliyun-sandbox.json', remote
        def _configuration(self):
            return {'profile': self.settings.profile_id, 'volume': self.settings.volume}
        def _read(self, sandbox):
            return json.loads(self.path.read_text())
        async def create(self, sandbox):
            operator.write(self.path, {'remote_id': None, 'state': 'stopped'})
            if lose_binding.pop() if lose_binding else False:
                raise RuntimeError('Process interrupted after local binding creation')
        async def terminate(self, sandbox):
            operator.write(self.path, {'remote_id': None, 'state': 'stopped'})
        async def file_operation(self, sandbox, operation, **options):
            if self._read(sandbox)['remote_id'] is None:
                vms.append('vm-' + str(len(vms)))
                operator.write(self.path, {'remote_id': vms[-1]})
            path = options['path']
            if path.startswith('../'):
                raise SandboxSecurityError('Path escape rejected')
            if operation == 'write':
                import base64
                files[path] = base64.b64decode(options['data']).decode()
                writes.append(path)
                if lose_write.pop() if lose_write else False:
                    raise SandboxStateError('Own marker write response lost')
            elif operation == 'preview':
                if path not in files:
                    raise FileNotFoundError(path)
                return {'text': files[path]}
            elif operation == 'delete':
                if path not in files:
                    raise FileNotFoundError(path)
                del files[path]
            return {}
    monkeypatch.setattr(provisioning, 'AliyunSandboxBackend', Backend)
    remote = provisioning.AliyunSandboxSettings('cn-hangzhou', 'private', 'template', 'oaw-volume', str(uuid4()))
    with pytest.raises(RuntimeError if failure == 'binding' else SandboxStateError):
        asyncio.run(provisioning._verify_volume(remote, tmp_path))
    assert asyncio.run(provisioning._verify_volume(remote, tmp_path)) is True
    result = json.loads((tmp_path / 'verification.json').read_text())
    assert len(writes) == 1 and not files and result['first_remote_id'] != result['second_remote_id']
    assert result['path_escape_rejected'] and result['cleanup_confirmed'] and result['marker_removed']
    count = len(vms)
    assert asyncio.run(provisioning._verify_volume(remote, tmp_path)) is True and len(vms) == count
