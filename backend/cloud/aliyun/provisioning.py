"""Root-only resource preparation for newly deployed hosted profiles.

The generic deployment reconciler owns hook loading and instance installation.
This hook never reads browser-selected cloud configuration or tenant data.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
from uuid import UUID, uuid4

from scripts.aliyun_workspaces import Cloud, CloudOperationError, CREDENTIAL_FIELDS, ProvisioningPending, provision, write

from backend.sandbox.models import SandboxSecurityError, SandboxStateError, SandboxValidationError
from .sandbox import AliyunSandboxBackend, AliyunSandboxSettings


REQUIRED = {'region', 'filesystem', 'zone', 'vpc', 'vswitch', 'security_group', 'team', 'account_id',
            'quota_bytes', 'quota_files', 'template', 'storage_cidr', 'credential_type', 'storage_role'}
CLOUD_FIELDS = REQUIRED - {'template', 'storage_cidr'}


def validate_settings(value):
    if not isinstance(value, dict) or not REQUIRED <= value.keys() or value.keys() - REQUIRED - {'role_name'}:
        raise ValueError('Invalid operator-owned Alibaba resource settings')
    if value['credential_type'] != 'ecs_ram_role':
        raise ValueError('Unattended resource preparation requires an ECS RAM role')
    for key in ('region', 'filesystem', 'zone', 'vpc', 'vswitch', 'security_group', 'team', 'template'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value[key]):
            raise ValueError('Invalid Alibaba resource identifier')
    if not re.fullmatch(r'[a-z][a-z0-9-]{2,48}', value['region']) or not value['zone'].startswith(value['region'] + '-'):
        raise ValueError('Resource region and zone must agree')
    if not isinstance(value['account_id'], str) or not re.fullmatch(r'[0-9]{8,20}', value['account_id']):
        raise ValueError('Expected the operator-approved Alibaba account ID')
    if not isinstance(value['storage_role'], str) or not re.fullmatch(
            r'acs:ram::' + value['account_id'] + r':role/[A-Za-z0-9.-]{1,64}', value['storage_role']):
        raise ValueError('Storage role must belong to the operator-approved account')
    if 'role_name' in value and (not isinstance(value['role_name'], str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,64}', value['role_name'])):
        raise ValueError('Invalid ECS provisioning role name')
    if (type(value['quota_bytes']) is not int or not 10 * 1024**3 <= value['quota_bytes'] <= 1024000 * 1024**3
            or value['quota_bytes'] % 1024**3 or type(value['quota_files']) is not int
            or not 10000 <= value['quota_files'] <= 100000000):
        raise ValueError('Invalid AgenticSpace quota')
    try:
        if not isinstance(value['storage_cidr'], str):
            raise ValueError('Expected a subnet string')
        network = ipaddress.IPv4Network(value['storage_cidr'], strict=True)
    except (ValueError, TypeError):
        raise ValueError('Expected one exact private storage subnet') from None
    if not any(network.subnet_of(ipaddress.IPv4Network(cidr)) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('Expected one exact private storage subnet')
    return dict(value)


def _private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode) or (os.name != 'nt' and (info.st_uid != 0 or info.st_mode & 0o077)):
        raise ValueError('Resource state must remain in a private root-owned directory')
    return path


def _read_private(path, limit=1024 * 1024):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                or (os.name != 'nt' and (info.st_uid != 0 or info.st_mode & 0o077))):
            raise ValueError('Expected a bounded private root-owned provisioning file')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Provisioning file exceeds its size limit')
        return data


def _api_key(directory):
    # Parse only the required EnvironmentFile assignment, with no shell or eval.
    matches = []
    for line in _read_private(directory / 'worker-common.env').decode('utf-8').splitlines():
        if line.strip().startswith('OAW_ALIYUN_SANDBOX_API_KEY='):
            parts = shlex.split(line.strip(), comments=False, posix=True)
            if len(parts) != 1:
                raise ValueError('Sandbox API key must use one EnvironmentFile assignment')
            matches.append(parts[0].split('=', 1)[1])
    if len(matches) != 1 or not matches[0]:
        raise ValueError('Configure one Sandbox API key in the private worker-common environment')
    return matches[0]


def _addresses(hostname, settings):
    if not re.fullmatch(r'[a-z0-9.-]+\.' + re.escape(settings['region']) + r'\.nas\.aliyuncs\.com', hostname):
        raise ValueError('Unexpected storage access-point domain')
    records = socket.getaddrinfo(hostname, 2049, family=socket.AF_INET, type=socket.SOCK_STREAM)
    addresses = sorted({record[4][0] for record in records})
    network = ipaddress.IPv4Network(settings['storage_cidr'])
    if not 1 <= len(addresses) <= 5 or any(ipaddress.IPv4Address(address) not in network for address in addresses):
        raise ValueError('Storage access-point addresses must belong to the approved private subnet')
    return addresses


async def _verify_volume(remote, directory):
    """Resume a fixed, own-marker probe without discarding unknown VM bindings."""
    path = directory / 'verification.json'
    backend = AliyunSandboxBackend(directory, remote)
    sandbox_id = 'oaw-storage-check'
    if path.exists():
        receipt = json.loads(_read_private(path))
        if receipt['configuration'] != backend._configuration():
            raise ValueError('Persistence verification belongs to a different cloud assignment')
        if not backend.path.exists():
            raise ValueError('Persistence verification lost its durable Sandbox binding')
    else:
        if backend.path.exists():
            raise ValueError('Unexpected pre-existing persistence verification binding')
        token = uuid4().hex
        receipt = {'configuration': backend._configuration(), 'marker': 'oaw-storage-check-' + token + '.txt',
                   'content': 'persistent-file-' + token, 'phase': 'writing'}
        # The local binding must exist before any cloud VM can be requested.
        await backend.create(sandbox_id)
        write(path, receipt)
    if receipt['phase'] == 'passed':
        return True
    try:
        if receipt['phase'] == 'writing':
            # A prior helper could have completed its write before losing its
            # response. Stop its VM, then inspect the unique marker first.
            await backend.terminate(sandbox_id)
            try:
                result = await backend.file_operation(sandbox_id, 'preview', path=receipt['marker'])
            except FileNotFoundError:
                await backend.file_operation(sandbox_id, 'write', path=receipt['marker'],
                    data=base64.b64encode(receipt['content'].encode()).decode())
                result = await backend.file_operation(sandbox_id, 'preview', path=receipt['marker'])
            if result.get('text') != receipt['content']:
                raise ValueError('New volume persistence marker has unexpected content')
            receipt.update(first_remote_id=backend._read(sandbox_id)['remote_id'], phase='recreating')
            write(path, receipt)
        if receipt['phase'] == 'recreating':
            await backend.terminate(sandbox_id)
            backend = AliyunSandboxBackend(directory, remote)
            result = await backend.file_operation(sandbox_id, 'preview', path=receipt['marker'])
            second = backend._read(sandbox_id)['remote_id']
            if result.get('text') != receipt['content'] or not second or second == receipt['first_remote_id']:
                raise ValueError('New volume failed persistence verification after VM replacement')
            receipt.update(second_remote_id=second, phase='cleanup', persistence_verified=True)
            write(path, receipt)
        if receipt['phase'] == 'cleanup':
            try:
                await backend.file_operation(sandbox_id, 'preview', path='../etc/passwd')
            except (SandboxSecurityError, SandboxValidationError):
                receipt['path_escape_rejected'] = True
            else:
                raise ValueError('Volume file protocol accepted a path escape')
            try:
                await backend.file_operation(sandbox_id, 'delete', path=receipt['marker'])
            except FileNotFoundError:
                pass  # A previous own-marker deletion already completed.
            receipt.update(marker_removed=True, phase='terminating')
            write(path, receipt)
        if receipt['phase'] != 'terminating':
            raise ValueError('Unknown persistence verification phase')
        await backend.terminate(sandbox_id)
        receipt.update(phase='passed', cleanup_confirmed=True)
        write(path, receipt)
        return True
    except BaseException as error:
        receipt['error_type'] = type(error).__name__
        try:
            await backend.terminate(sandbox_id)
        except Exception as cleanup_error:
            receipt['cleanup_error_type'] = type(cleanup_error).__name__
        write(path, receipt)
        raise


def prepare_profile(profile, settings, directory):
    """Trusted hosting hook: None while waiting, private assignment only when verified."""
    settings = validate_settings(settings)
    profile_id = profile.profile_id
    if not isinstance(profile_id, str) or str(UUID(profile_id)) != profile_id:
        raise ValueError('Expected a canonical deployment profile UUID')
    directory = Path(directory)
    root = _private_directory(directory / 'resource-provisioning')
    root = _private_directory(root / 'aliyun')
    private = _private_directory(root / profile_id)
    configuration = hashlib.sha256(json.dumps({k: v for k, v in settings.items() if k not in CREDENTIAL_FIELDS}, sort_keys=True).encode()).hexdigest()
    ready = private / 'assignment.json'
    if ready.exists():
        value = json.loads(_read_private(ready))
        assignment = value['assignment']
        if (value.get('configuration') != configuration or assignment.get('profile_id') != profile_id
                or assignment.get('volume') != 'oaw-' + profile_id or assignment.get('verified') is not True
                or assignment.get('region') != settings['region'] or assignment.get('template') != settings['template']):
            raise ValueError('Verified resource assignment differs from this deployment')
        return {'aliyun-workspace.json': assignment}
    receipt_path = private / (profile_id + '.json')
    if receipt_path.exists():
        _read_private(receipt_path)
    cloud = Cloud({k: v for k, v in settings.items() if k in CLOUD_FIELDS or k == 'role_name'})
    try:
        result = provision(cloud.config, profile_id, cloud, private, reconcile=True)
    except (ProvisioningPending, TimeoutError, ConnectionError):
        return None
    except CloudOperationError as error:
        if error.code is None or str(error.code).startswith(('Throttling', 'InternalError', 'ServiceUnavailable', 'RequestTimeout')):
            return None
        raise
    if result['phase'] != 'provisioned_not_verified':
        return None
    receipt = json.loads(_read_private(receipt_path))
    addresses = _addresses(receipt['access_point']['DomainName'], settings)
    remote = AliyunSandboxSettings(settings['region'], _api_key(directory), settings['template'],
                                   'oaw-' + profile_id, profile_id, storage_addresses=tuple(addresses))
    probe = _private_directory(private / 'probe')
    try:
        verified = asyncio.run(asyncio.wait_for(_verify_volume(remote, probe), timeout=150))
    except (SandboxStateError, TimeoutError, ConnectionError):
        return None
    if not verified:
        return None
    assignment = {'version': 1, 'profile_id': profile_id, 'region': settings['region'], 'template': settings['template'],
                  'volume': 'oaw-' + profile_id, 'storage_addresses': addresses, 'verified': True}
    write(ready, {'configuration': configuration, 'assignment': assignment})
    return {'aliyun-workspace.json': assignment}
