"""Prepare per-profile AgenticFS resources; never changes a running OAW installation.

Use a refreshed Alibaba CLI OAuth/STS profile and the operator SDK environment.
Unknown mutation outcomes stop at a durable reservation instead of being replayed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4


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
        profiles = json.loads(Path(config['credentials_file']).expanduser().read_text())['profiles']
        credential = next(p for p in profiles if p['name'] == config['cli_profile'])
        expiration = credential.get('sts_expiration')
        if expiration:
            expires = (datetime.fromtimestamp(expiration / 1000 if expiration > 10**11 else expiration, timezone.utc)
                       if isinstance(expiration, (int, float)) else datetime.fromisoformat(expiration.replace('Z', '+00:00')))
            if expires <= datetime.now(timezone.utc):
                raise ValueError('Refresh the CLI OAuth profile with sts GetCallerIdentity before provisioning')
        self.config, self.models = config, models
        self.runtime = util.RuntimeOptions(read_timeout=30000, connect_timeout=10000, autoretry=False)
        self.clients = {}
        for service in ('nas', 'ram', 'sandbox'):
            endpoint = 'ram.aliyuncs.com' if service == 'ram' else f'{service}.{config["region"]}.aliyuncs.com'
            self.clients[service] = Client(models.Config(access_key_id=credential['access_key_id'],
                access_key_secret=credential['access_key_secret'], security_token=credential.get('sts_token'),
                endpoint=endpoint, region_id=config['region']))

    def call(self, service, action, data):
        m = self.models
        if service == 'sandbox':
            params = m.Params(action=action, version='2026-08-20', protocol='HTTPS',
                pathname='/pop/2026-08-20/volumes', method='POST', auth_type='AK', style='ROA',
                req_body_type='json', body_type='json')
            request = m.OpenApiRequest(body=data)
        else:
            params = m.Params(action=action, version={'nas': '2017-06-26', 'ram': '2015-05-01'}[service],
                protocol='HTTPS', pathname='/', method='POST', auth_type='AK', style='RPC',
                req_body_type='formData', body_type='json')
            request = m.OpenApiRequest(query={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in data.items()})
        response = self.clients[service].call_api(params, request, self.runtime)['body']
        if response.get('code') not in (None, '200', 200):
            raise RuntimeError('Cloud operation failed: ' + str(response.get('code')))
        return response


def mount_policy(access_point_arn):
    return {'Version': '1', 'Statement': [{'Effect': 'Allow', 'Action': ['nas:ClientMount', 'nas:ClientWrite'],
        'Resource': '*', 'Condition': {'StringEquals': {'nas:AccessPointArn': access_point_arn}}}]}


def provision(config, profile_id, cloud, state_directory):
    if str(UUID(profile_id)) != profile_id:
        raise ValueError('Expected a canonical profile UUID')
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    path = Path(state_directory) / (profile_id + '.json')
    state = json.loads(path.read_text()) if path.exists() else {
        'profile_id': profile_id, 'configuration': fingerprint, 'steps': {}, 'verified': False}
    if state['profile_id'] != profile_id or state['configuration'] != fingerprint:
        raise ValueError('Existing provisioning receipt belongs to a different configuration')

    def step(name, service, action, data):
        previous = state['steps'].get(name)
        if previous:
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
            raise
        entry.update(phase='complete', response=response)
        write(path, state)
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
    observed = cloud.call('nas', 'DescribeAccessPoint', {'FileSystemId': config['filesystem'], 'AccessPointId': point['AccessPoint']['AccessPointId']})['AccessPoint']
    state['access_point'] = observed
    write(path, state)
    if observed['Status'].lower() != 'active':
        return {'profile_id': profile_id, 'phase': 'waiting_for_access_point'}
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
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    cloud = Cloud(config)
    for item in json.loads(args.profiles.read_text(encoding='utf-8')):
        print(json.dumps(provision(config, item['profile_id'], cloud, args.state_directory)), flush=True)


if __name__ == '__main__':
    main()
