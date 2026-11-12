"""Opt-in Pack workers in prebuilt VMs with a genuinely shared profile Volume.

No source copying, dependency installation, native preparation or ECS fallback.
The operator installs the mount and the reviewed, version-pinned launch recipes.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath
import re
import secrets
import tempfile

from backend.agents.execution import RuntimeExecutionProvider, command_arguments
from backend.errors import RuntimeUnavailableError
from backend.packs.workers import PackWorkerExecutionRequest
from .execution import AliyunRuntimeExecutionProvider
from .workspace import read_operator_json


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', value)


class AliyunPackWorkerExecutionProvider(RuntimeExecutionProvider):
    def __init__(self, root, settings, assignment, *, sdk=None, **runtime_options):
        expected = {'version', 'profile_id', 'volume', 'mount_path', 'deployments', 'verified'}
        if (not isinstance(assignment, dict) or set(assignment) != expected
                or type(assignment['version']) is not int or assignment['version'] != 1
                or assignment['profile_id'] != settings.profile_id
                or assignment['volume'] != settings.volume
                or assignment['volume'] != 'oaw-' + settings.profile_id
                or assignment['verified'] is not True or settings.workspace_store != 'volume'
                or settings.unavailable_reason):
            raise ValueError('Pack workers require a verified assignment to this profile Volume')
        mount = assignment['mount_path']
        if (not isinstance(mount, str) or mount != '/mnt/oaw-workspaces/' + settings.profile_id
                or not _identifier(settings.profile_id)):
            raise ValueError('Pack workers require the dedicated canonical profile mount path')
        self.mount = Path(mount)
        self.workspace_root = self.mount / 'packs'
        self.root, self.sdk = Path(root), sdk
        self.settings = replace(settings, volume_mount_path=mount)
        self.runtime_options = runtime_options
        self.deployments = {}
        if not isinstance(assignment['deployments'], list) or not assignment['deployments']:
            raise ValueError('Pack worker deployments must be a nonempty list')
        for item in assignment['deployments']:
            if (not isinstance(item, dict) or set(item) != {'pack_id', 'pack_version', 'environment_id',
                    'template', 'commands', 'environment'}
                    or any(not _identifier(item[key]) for key in
                           ('pack_id', 'pack_version', 'environment_id', 'template'))
                    or not isinstance(item['commands'], dict) or not item['commands']
                    or not isinstance(item['environment'], dict)):
                raise ValueError('Invalid pinned Pack worker deployment')
            for name, command in item['commands'].items():
                if not _identifier(name) or not isinstance(command, list) or not command:
                    raise ValueError('Invalid Pack worker command')
                command_arguments(command, item['environment'])
                executable = PurePosixPath(command[0])
                if not executable.is_absolute() or '..' in executable.parts:
                    raise ValueError('Pack worker executable must be an absolute template path')
            key = (item['pack_id'], item['pack_version'], item['environment_id'])
            if key in self.deployments:
                raise ValueError('Duplicate Pack worker deployment')
            self.deployments[key] = item

    def _mounted(self):
        # Missing mounts must not redirect persistent writes onto the ECS disk.
        return not self.mount.is_symlink() and self.mount.is_mount() and self.mount.resolve() == self.mount

    def configuration(self, runtime_id):
        return {'mode': 'remote', 'provider': 'aliyun.agent-sandbox',
                'configured': self._mounted() and any(key[0] == runtime_id for key in self.deployments),
                'pack_workers': 'shared-filesystem'}

    async def open(self, request):
        if not isinstance(request, PackWorkerExecutionRequest):
            raise RuntimeUnavailableError('A bound Pack worker request is required')
        spec = request.worker
        item = self.deployments.get((spec.pack_id, spec.pack_version, spec.environment_id))
        if (request.runtime_id != spec.pack_id or item is None or spec.worker_id not in item['commands']):
            raise RuntimeUnavailableError('No reviewed template matches this Pack version, environment and worker')
        if not self._mounted():
            raise RuntimeUnavailableError('The dedicated Pack worker Volume is not mounted on the host')
        workspace = request.native_workspace.resolve(strict=True)
        pack_root = self.workspace_root / spec.pack_id
        if not workspace.is_relative_to(pack_root) or not workspace.is_dir():
            raise RuntimeUnavailableError('Pack worker workspace is outside its assigned shared directory')
        command = tuple(item['commands'][spec.worker_id]) + spec.arguments
        command_arguments(command, item['environment'])
        settings = replace(self.settings, template=item['template'])
        provider = _DeploymentExecution(self.root, settings, sdk=self.sdk,
            launch=(command, item['environment']), **self.runtime_options)
        return await provider.open(request)


class _DeploymentExecution(AliyunRuntimeExecutionProvider):
    def __init__(self, *args, launch, **kwargs):
        super().__init__(*args, **kwargs)
        self.command, self.environment = launch

    async def _initialize_execution(self, execution, request, key):
        workspace = request.native_workspace.resolve(strict=True)
        home = '/tmp/oaw-pack-home/' + key
        # Shared service variables are still validated by the ordinary relay.
        # The reviewed recipe wins over user settings for its fixed variables.
        fixed = {name.upper() for name in self.environment}
        variables = {name: value for name, value in request.variables.items() if name.upper() not in fixed}
        await execution.initialize(str(workspace), home, environment={
            'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': home, 'USER': 'user', 'LANG': 'C.UTF-8',
            'TMPDIR': '/tmp', **self.environment}, variables=variables)
        # Verify both directions before the scientific process can have effects.
        # A mount flag or a successful SDK create alone does not prove sharing.
        challenge, response = secrets.token_bytes(32), secrets.token_bytes(32)
        with tempfile.NamedTemporaryFile(prefix='.oaw-worker-', dir=workspace, delete=False) as file:
            probe = Path(file.name)
            file.write(challenge)
        try:
            if await execution.read_file(probe.name, max_bytes=32) != challenge:
                raise RuntimeUnavailableError('Pack worker mount does not share host input files')
            await execution.write_file(probe.name, response)
            from backend.sandbox.files import pinned
            import os
            with pinned(probe, write=False) as descriptor:
                if os.read(descriptor, 33) != response:
                    raise RuntimeUnavailableError('Pack worker output is not visible on the host mount')
        finally:
            probe.unlink(missing_ok=True)
        execution.command = self.command


def load_worker_provider(data_root, remote, **runtime_options):
    assignment = read_operator_json(Path(data_root).parent / 'aliyun-pack-workers.json')
    if assignment is None:
        return None
    return AliyunPackWorkerExecutionProvider(Path(data_root) / 'pack-worker-executions',
                                            remote, assignment, **runtime_options)
