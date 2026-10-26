"""Verify a dedicated test Volume through OAW without changing hosted profiles.

Requires the OAW backend and e2b dependencies. Creates billable temporary VMs,
records their binding, and deletes only its own uniquely named marker file.
This single-volume probe does not certify isolation between different volumes.
"""
import argparse
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.cloud.aliyun.sandbox import AliyunSandboxBackend, AliyunSandboxSettings
from backend.sandbox.models import SandboxSecurityError, SandboxValidationError


async def verify(settings, state_directory):
    token = uuid4().hex
    root = Path(state_directory) / token
    root.mkdir(parents=True, mode=0o700)
    settings = replace(settings, profile_id='oaw-storage-probe-' + token)
    backend = AliyunSandboxBackend(root, settings)
    marker = 'oaw-storage-probe-' + token + '.txt'
    content = ('persistent-file-' + token).encode()
    receipt = {'volume': settings.volume, 'template': settings.template, 'marker': marker,
               'phase': 'reserved', 'cross_volume_isolation_verified': False}
    def save():
        path = root / 'result.json'
        path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        path.chmod(0o600)
    save()
    await backend.create(token)
    try:
        # A stopped logical sandbox must support the existing file browser API.
        await backend.file_operation(token, 'write', path=marker, data=base64.b64encode(content).decode())
        receipt['marker_written'] = True
        first = backend._read(token)['remote_id']
        receipt['first_remote_id'] = first
        save()
        await backend.terminate(token)
        # Reconstruct the adapter too, modelling a worker process restart.
        backend = AliyunSandboxBackend(root, settings)
        result = await backend.file_operation(token, 'preview', path=marker)
        second = backend._read(token)['remote_id']
        assert result['text'].encode() == content and second != first
        receipt.update(second_remote_id=second, sha256=hashlib.sha256(content).hexdigest())
        try:
            await backend.file_operation(token, 'preview', path='../etc/passwd')
        except (SandboxSecurityError, SandboxValidationError):
            receipt['path_escape_rejected'] = True
        else:
            raise AssertionError('Workspace path escape was accepted')
        await backend.file_operation(token, 'delete', path=marker)
        receipt.update(phase='passed', marker_removed=True)
    except Exception as error:
        receipt.update(phase='failed', error_type=type(error).__name__)
        raise
    finally:
        try:
            if receipt.get('marker_written') and not receipt.get('marker_removed'):
                try:
                    await backend.file_operation(token, 'delete', path=marker)
                    receipt['marker_removed'] = True
                except Exception as error:
                    receipt['marker_cleanup_error_type'] = type(error).__name__
            await backend.terminate(token)
            receipt['cleanup_confirmed'] = True
        finally:
            save()
    return {'receipt': str(root / 'result.json'), **receipt}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--region', required=True)
    parser.add_argument('--template', required=True)
    parser.add_argument('--volume', required=True, help='Dedicated test volume; never a live user volume')
    parser.add_argument('--storage-address', action='append', required=True)
    parser.add_argument('--state-directory', type=Path, required=True)
    args = parser.parse_args()
    settings = AliyunSandboxSettings(args.region, os.environ['OAW_ALIYUN_SANDBOX_API_KEY'],
        args.template, args.volume, 'probe', storage_addresses=tuple(args.storage_address))
    print(json.dumps(asyncio.run(verify(settings, args.state_directory))))


if __name__ == '__main__':
    main()
