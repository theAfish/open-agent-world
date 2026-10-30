"""Prepare per-profile AgenticFS resources; never changes a running OAW installation.

Use a refreshed Alibaba CLI OAuth/STS profile and the operator SDK environment.
Unknown mutation outcomes stop at a durable reservation instead of being replayed.
The hosted hook opts into exact, read-only reconciliation of these reservations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote, unquote
from uuid import UUID, uuid4


CREDENTIAL_FIELDS = {'credentials_file', 'cli_profile', 'credential_type', 'role_name'}


class ProvisioningPending(ValueError):
    """A read-only observation is needed; never repeat the reserved mutation."""


class CloudOperationError(RuntimeError):
    def __init__(self, action, code):
        self.code = code
        super().__init__(f'Cloud operation {action} failed ({code or "unavailable"})')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    if os.name != 'nt':
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Cloud:
    def __init__(self, config):
        from alibabacloud_tea_openapi.client import Client
        from alibabacloud_tea_openapi import utils_models as models
        from alibabacloud_tea_util import models as util
        source = config.get('credential_type', 'cli')
        if source == 'ecs_ram_role':
            from alibabacloud_credentials.client import Client as Credentials
            from alibabacloud_credentials.models import Config as CredentialsConfig
            # Pass the provider itself, not one STS snapshot, to preserve refresh.
            credential = {'credential': Credentials(CredentialsConfig(type='ecs_ram_role',
                role_name=config.get('role_name'), disable_imds_v1=True))}
        elif source == 'cli':
            profiles = json.loads(Path(config['credentials_file']).expanduser().read_text())['profiles']
            selected = next(p for p in profiles if p['name'] == config['cli_profile'])
            expiration = selected.get('sts_expiration')
            if expiration:
                expires = (datetime.fromtimestamp(expiration / 1000 if expiration > 10**11 else expiration, timezone.utc)
                           if isinstance(expiration, (int, float)) else datetime.fromisoformat(expiration.replace('Z', '+00:00')))
                if expires <= datetime.now(timezone.utc):
                    raise ValueError('Refresh the CLI OAuth profile with sts GetCallerIdentity before provisioning')
            credential = {'access_key_id': selected['access_key_id'], 'access_key_secret': selected['access_key_secret'],
                          'security_token': selected.get('sts_token')}
        else:
            raise ValueError('Choose CLI or ECS RAM role credentials explicitly')
        self.config, self.models = config, models
        self.runtime = util.RuntimeOptions(read_timeout=30000, connect_timeout=10000, autoretry=False)
        self.clients = {}
        for service in ('nas', 'ram', 'sandbox'):
            endpoint = 'ram.aliyuncs.com' if service == 'ram' else f'{service}.{config["region"]}.aliyuncs.com'
            self.clients[service] = Client(models.Config(**credential, endpoint=endpoint, region_id=config['region']))

    def call(self, service, action, data):
        m = self.models
        if service == 'sandbox':
            if action not in {'CreateVolume', 'ListVolumes', 'GetVolume'}:
                raise ValueError('Unsupported storage control operation')
            values = dict(data)
            pathname = '/pop/2026-08-20/volumes'
            if action == 'GetVolume':
                pathname += '/' + quote(values.pop('volumeID'), safe='')
            method = 'POST' if action == 'CreateVolume' else 'GET'
            params = m.Params(action=action, version='2026-08-20', protocol='HTTPS',
                pathname=pathname, method=method, auth_type='AK', style='ROA',
                req_body_type='json', body_type='json')
            request = m.OpenApiRequest(body=values) if method == 'POST' else m.OpenApiRequest(query=values)
        else:
            params = m.Params(action=action, version={'nas': '2017-06-26', 'ram': '2015-05-01'}[service],
                protocol='HTTPS', pathname='/', method='POST', auth_type='AK', style='RPC',
                req_body_type='formData', body_type='json')
            request = m.OpenApiRequest(query={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()})
        try:
            response = self.clients[service].call_api(params, request, self.runtime)['body']
        except Exception as error:
            # SDK errors must not put credential-bearing requests in journal logs.
            raise CloudOperationError(action, getattr(error, 'code', None)) from None
        if response.get('code') not in (None, '200', 200):
            raise CloudOperationError(action, response.get('code'))
        return response


def mount_policy(access_point_arn):
    return {'Version': '1', 'Statement': [{'Effect': 'Allow', 'Action': ['nas:ClientMount', 'nas:ClientWrite'],
        'Resource': '*', 'Condition': {'StringEquals': {'nas:AccessPointArn': access_point_arn}}}]}


def _document(value):
    return json.loads(unquote(value)) if isinstance(value, str) else value


def _require(condition, resource):
    if not condition:
        raise ValueError(f'Observed {resource} does not match the reserved profile resource')


def _pages(cloud, service, action, request, container, token='NextToken'):
    request = dict(request)
    result, seen = [], set()
    for _ in range(100):
        response = cloud.call(service, action, request)
        items = response[container]
        if isinstance(items, dict):
            items = items['AgenticSpace']
        _require(isinstance(items, list), action)
        result.extend(items)
        following = response.get(token)
        if not following:
            return result
        _require(isinstance(following, str) and following not in seen, action)
        seen.add(following)
        request[token] = following
    raise ValueError('Cloud resource listing exceeded the bounded reconciliation limit')


def _only(items, key, value, resource):
    matches = [item for item in items if item.get(key) == value]
    _require(len(matches) <= 1, resource)
    return matches[0] if matches else None


def _get_or_absent(cloud, action, request):
    try:
        return cloud.call('ram', action, request)
    except Exception as error:
        if getattr(error, 'code', None) in {'EntityNotExist.Role', 'EntityNotExist.Policy'}:
            return None
        raise


def reconcile_step(config, cloud, name, request, steps):
    """Return an exact observed result, or None. This function never mutates cloud resources."""
    if name == 'space':
        items = _pages(cloud, 'nas', 'DescribeAgenticSpaces',
                       {'FileSystemId': config['filesystem'], 'MaxResults': 100}, 'AgenticSpaces')
        item = _only(items, 'FileSystemPath', request['FileSystemPath'], 'AgenticSpace')
        if item is None:
            return None
        _require(all(item.get(key) == request[key] for key in ('FileSystemId', 'Azone', 'Description'))
                 and item.get('Quota') == {'SizeLimit': request['Quota.SizeLimit'],
                                          'FileCountLimit': request['Quota.FileCountLimit']}, 'AgenticSpace')
        if item.get('Status') != 'Running':
            return None
        return {'AgenticSpaceId': item['AgenticSpaceId']}
    if name == 'access_point':
        items = _pages(cloud, 'nas', 'ListAccessPoints', {'FileSystemId': config['filesystem'],
            'Filters.1.Name': 'AgenticSpaceId', 'Filters.1.Value': request['AgenticSpaceId'], 'MaxResults': 100}, 'AccessPoints')
        item = _only(items, 'AccessPointName', request['AccessPointName'], 'Access Point')
        if item is None:
            return None
        _require(all(item.get(key) == request[key] for key in ('FileSystemId', 'AgenticSpaceId', 'VpcId'))
                 and item.get('VSwitchId') == request['VswId'] and item.get('EnabledRam') is True, 'Access Point')
        if config.get('account_id'):
            _require(item.get('ARN') == f'acs:nas:{config["region"]}:{config["account_id"]}:accesspoint/{item["AccessPointId"]}', 'Access Point ARN')
        if str(item.get('Status', '')).lower() != 'active':
            return None
        return {'AccessPoint': item}
    if name in {'role', 'disable_console'}:
        response = _get_or_absent(cloud, 'GetRole', {'RoleName': request['RoleName']})
        if response is None:
            return None
        item, expected = response['Role'], steps['role']['request']
        _require(item.get('RoleName') == request['RoleName'] and item.get('Description') == expected['Description']
                 and _document(item.get('AssumeRolePolicyDocument')) == _document(expected['AssumeRolePolicyDocument']), 'RAM role')
        if config.get('account_id'):
            _require(item.get('Arn', '').lower() == f'acs:ram::{config["account_id"]}:role/{request["RoleName"]}'.lower(), 'RAM role ARN')
        if name == 'disable_console' and item.get('AllowConsoleLogin') is not False:
            return None
        return response
    if name == 'policy':
        response = _get_or_absent(cloud, 'GetPolicy', {'PolicyName': request['PolicyName'], 'PolicyType': 'Custom'})
        if response is None:
            return None
        item = response['Policy']
        _require(item.get('PolicyName') == request['PolicyName'] and item.get('PolicyType') == 'Custom'
                 and item.get('Description') == request['Description'], 'RAM policy')
        version = response.get('DefaultPolicyVersion')
        if version is None:
            version = cloud.call('ram', 'GetPolicyVersion', {'PolicyName': request['PolicyName'],
                'PolicyType': 'Custom', 'VersionId': item['DefaultVersion']})['PolicyVersion']
        _require(_document(version.get('PolicyDocument')) == _document(request['PolicyDocument']), 'RAM policy document')
        return response
    if name == 'attachment':
        policies = cloud.call('ram', 'ListPoliciesForRole', {'RoleName': request['RoleName']})['Policies']['Policy']
        _require(all(p.get('PolicyName') == request['PolicyName'] and p.get('PolicyType') == 'Custom' for p in policies)
                 and len(policies) <= 1, 'RAM role attachments')
        return {} if policies else None
    if name == 'volume':
        items = _pages(cloud, 'sandbox', 'ListVolumes', {'teamID': request['teamID'],
            'volumeName': request['volumeName'], 'maxResults': 100}, 'volumes', token='nextToken')
        item = _only(items, 'volumeName', request['volumeName'], 'Volume')
        if item is None:
            return None
        volume_id = item['volumeID']
        item = cloud.call('sandbox', 'GetVolume', {'teamID': request['teamID'], 'volumeID': volume_id})['volume']
        expected, actual = request['agenticFSVolumeConfig'], item.get('agenticFSVolumeConfig', {})
        _require(item.get('volumeID') == volume_id and item.get('volumeName') == request['volumeName'] and item.get('teamID') == request['teamID']
                 and actual.get('serverAddr', '').removesuffix(':/') == expected['serverAddr'].removesuffix(':/')
                 and actual.get('userID') == expected['userID'] and actual.get('groupID') == expected['groupID']
                 and item.get('mountConfig') == request['mountConfig'], 'Volume')
        return {'volume': item} if item.get('status') == 'AVAILABLE' else None
    raise ValueError('Unsupported reserved provisioning step')


def provision(config, profile_id, cloud, state_directory, *, reconcile=False):
    if str(UUID(profile_id)) != profile_id:
        raise ValueError('Expected a canonical profile UUID')
    fingerprint = hashlib.sha256(json.dumps({k: v for k, v in config.items() if k not in CREDENTIAL_FIELDS}, sort_keys=True).encode()).hexdigest()
    path = Path(state_directory) / (profile_id + '.json')
    state = json.loads(path.read_text()) if path.exists() else {
        'profile_id': profile_id, 'configuration': fingerprint, 'configuration_version': 2, 'steps': {}, 'verified': False}
    if state.get('profile_id') != profile_id:
        raise ValueError('Existing provisioning receipt belongs to a different profile')
    if state.get('configuration_version') is None:
        # A legacy receipt contains only the full hash, not the old config. Only
        # the original config can prove the one-time migration is legitimate.
        legacy = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        if state.get('configuration') != legacy:
            raise ValueError('Legacy provisioning receipt requires its original configuration before credential-source migration')
        state.update(configuration=fingerprint, configuration_version=2)
        write(path, state)
    if state.get('configuration_version') != 2 or state['configuration'] != fingerprint:
        raise ValueError('Existing provisioning receipt belongs to a different configuration')

    def step(name, service, action, data):
        previous = state['steps'].get(name)
        if previous:
            _require(previous.get('phase') in {'reserved', 'complete'}, 'provisioning receipt phase')
            _require({k: v for k, v in previous['request'].items() if k != 'ClientToken'} ==
                     {k: v for k, v in data.items() if k != 'ClientToken'}, 'provisioning receipt request')
            if reconcile:
                response = reconcile_step(config, cloud, name, previous['request'], state['steps'])
                if response is None:
                    raise ProvisioningPending(f'{name} has an uncertain outcome; waiting for exact resource reconciliation')
                previous.update(phase='complete', response=response)
                write(path, state)
                return response
            if previous['phase'] != 'complete':
                raise ValueError(f'{name} has an uncertain outcome; reconcile its resource before continuing')
            return previous['response']
        entry = {'phase': 'reserved', 'service': service, 'action': action, 'request': data,
                 'created_at': datetime.now(timezone.utc).isoformat()}
        state['steps'][name] = entry
        write(path, state)
        try:
            response = cloud.call(service, action, data)
        except Exception as error:
            entry['error_type'] = type(error).__name__
            entry['error_code'] = getattr(error, 'code', None)
            write(path, state)
            if reconcile:
                raise ProvisioningPending(f'{name} outcome is reserved for reconciliation') from None
            raise
        entry.update(phase='complete', response=response)
        write(path, state)
        if reconcile:
            observed = reconcile_step(config, cloud, name, data, state['steps'])
            if observed is None:
                raise ProvisioningPending(f'{name} is not ready for use')
            entry['response'] = observed
            write(path, state)
            return observed
        return response

    space = step('space', 'nas', 'CreateAgenticSpace', {'RegionId': config['region'],
        'FileSystemId': config['filesystem'], 'Azone': config['zone'],
        'FileSystemPath': '/oaw' + UUID(profile_id).hex + '/', 'Description': 'oaw-' + profile_id,
        'Quota.SizeLimit': config['quota_bytes'], 'Quota.FileCountLimit': config['quota_files'],
        'ClientToken': uuid4().hex})
    space_id = space['AgenticSpaceId']
    point = step('access_point', 'nas', 'CreateAccessPoint', {'RegionId': config['region'],
        'FileSystemId': config['filesystem'], 'AgenticSpaceId': space_id,
        'AccessPointName': 'oaw-' + profile_id, 'VpcId': config['vpc'], 'VswId': config['vswitch'], 'EnabledRam': True})
    observed = (point['AccessPoint'] if reconcile else cloud.call('nas', 'DescribeAccessPoint',
        {'FileSystemId': config['filesystem'], 'AccessPointId': point['AccessPoint']['AccessPointId']})['AccessPoint'])
    state['access_point'] = observed
    write(path, state)
    if observed['Status'].lower() != 'active':
        return {'profile_id': profile_id, 'phase': 'waiting_for_access_point'}
    if config.get('storage_role'):
        # The unattended ECS identity never receives RAM administration rights.
        # Agent Sandbox downscopes this operator-approved role for each mount.
        role = {'Arn': config['storage_role']}
    else:
        role_name = 'OawWorkspace-' + UUID(profile_id).hex
        trust = {'Version': '1', 'Statement': [{'Effect': 'Allow', 'Action': 'sts:AssumeRole',
                                               'Principal': {'Service': ['fc.aliyuncs.com']}}]}
        role = step('role', 'ram', 'CreateRole', {'RoleName': role_name, 'AssumeRolePolicyDocument': json.dumps(trust),
            'Description': 'OAW dedicated profile storage ' + profile_id, 'AllowConsoleLogin': False})['Role']
        step('disable_console', 'ram', 'UpdateRole', {'RoleName': role_name, 'NewAllowConsoleLogin': False})
        step('policy', 'ram', 'CreatePolicy', {'PolicyName': role_name, 'PolicyDocument': json.dumps(mount_policy(observed['ARN'])),
            'Description': 'Mount and write only this OAW profile access point'})
        step('attachment', 'ram', 'AttachPolicyToRole', {'RoleName': role_name, 'PolicyName': role_name, 'PolicyType': 'Custom'})
    volume = step('volume', 'sandbox', 'CreateVolume', {'teamID': config['team'], 'volumeName': 'oaw-' + profile_id,
        'agenticFSVolumeConfig': {'serverAddr': observed['DomainName'] + ':/', 'userID': 1000, 'groupID': 1000},
        'mountConfig': {'role': role['Arn'], 'vpcConfig': {'vpcId': config['vpc'], 'vSwitchIds': [config['vswitch']],
                                                       'securityGroupId': config['security_group']}}})['volume']
    state['volume'] = volume
    state['phase'] = 'provisioned_not_verified'
    write(path, state)
    return {'profile_id': profile_id, 'phase': state['phase'], 'volume': volume['volumeName'], 'access_point': observed['AccessPointId']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, required=True, help='JSON array of objects with profile_id; no passwords')
    parser.add_argument('--state-directory', type=Path, required=True)
    parser.add_argument('--reconcile', action='store_true', help='Adopt exact observed resources; never replay an uncertain mutation')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    cloud = Cloud(config)
    for item in json.loads(args.profiles.read_text(encoding='utf-8')):
        try:
            result = provision(config, item['profile_id'], cloud, args.state_directory, reconcile=args.reconcile)
        except ProvisioningPending:
            result = {'profile_id': item['profile_id'], 'phase': 'waiting_for_reconciliation'}
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
