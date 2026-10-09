import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents.tools import build_scoped_tool_callables
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.sandbox.models import (
    ResourceAccess, ResourceAttachment, SandboxInfo, SandboxState,
    SandboxStateError, SandboxSecurityError,
)
from backend.sandbox.wsl import WslSandboxBackend, _Active
from backend.tests.test_sandbox_manager import make_manager
from backend.tests.conftest import create_node
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def test_second_agent_start_does_not_remount_or_interrupt_active_command(runtime_client, monkeypatch):
    client, backend, native = runtime_client
    agent, sandbox, _, _, edges = setup_skill(client)
    assert client.patch(f"/api/edges/{edges[0]['id']}", json={'relationship': 'execute_manage'}).status_code == 200
    peer = create_node(client, 'agent')
    assert client.post('/api/edges', json={'source': peer['id'], 'target': sandbox['id'], 'relationship': 'execute'}).status_code == 201
    resource = create_node(client, 'text')
    assert client.post('/api/edges', json={'source': resource['id'], 'target': sandbox['id'], 'relationship': 'mount_read_only'}).status_code == 201
    services = client.app.state.services
    entered, release = threading.Event(), threading.Event()
    original = native.run_appcontainer
    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(native, 'run_appcontainer', blocking)
    async def scenario():
        first = asyncio.create_task(services.execute_sandbox(sandbox['id'], ['cmd.exe'], agent_id=peer['id']))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            mount = (await backend.get(sandbox['id'])).attachments[0]
            assert await backend.attach_resource(sandbox['id'], mount.resource_id, mount.source, mount.relative_path, mount.access) == mount
            async def unexpected(*args, **kwargs):
                raise AssertionError('Repeated Start must not perform lifecycle or mount changes')
            monkeypatch.setattr(backend, 'attach_resource', unexpected)
            monkeypatch.setattr(backend, 'start', unexpected)
            result = await WorldAgentCapabilityProvider(services).invoke_tool(agent['id'], f"sandbox.start:{sandbox['id']}", {})
            assert result['state'] == 'running' and not first.done()
            assert native.terminated_jobs == []
        finally:
            release.set()
            await first
    client.portal.call(scenario)


@pytest.mark.asyncio
async def test_manager_start_does_not_repeat_native_start_or_mounts(tmp_path, monkeypatch):
    manager, backend = make_manager(tmp_path)
    await manager.create('shared')
    await manager.start('shared')
    backend.records['shared'] = replace(backend.records['shared'], state=SandboxState.RUNNING)
    async def unexpected(*args, **kwargs):
        raise AssertionError('No resource or runtime mutation during an idempotent Start')
    monkeypatch.setattr(backend, 'start', unexpected)
    monkeypatch.setattr(backend, 'attach_resource', unexpected)
    assert (await manager.start('shared')).state == SandboxState.RUNNING


@pytest.mark.asyncio
async def test_wsl_running_start_and_unchanged_mount_are_noops(tmp_path, monkeypatch):
    backend = WslSandboxBackend(tmp_path, distribution='test')
    mount = ResourceAttachment('shared', 'notes', tmp_path / 'notes.txt', 'resources/notes.txt', ResourceAccess.READ_WRITE)
    backend._infos['shared'] = SandboxInfo('shared', SandboxState.RUNNING, Path('/workspace'), attachments=(mount,))
    backend._active['shared'] = {'running-command': _Active('unit')}
    async def unexpected(*args, **kwargs):
        raise AssertionError('No new worker may recover a live sibling or modify its mounts')
    monkeypatch.setattr(backend, '_request', unexpected)
    assert (await backend.start('shared')).state == SandboxState.RUNNING
    assert await backend.attach_resource('shared', 'notes', mount.source, mount.relative_path, mount.access) == mount
    assert (await backend.configure('shared', workspace_path=None, workspace_access=ResourceAccess.READ_WRITE)).state == SandboxState.RUNNING
    with pytest.raises(SandboxStateError):
        await backend.attach_resource('shared', 'notes', mount.source, mount.relative_path, ResourceAccess.READ_ONLY)


def test_agent_state_conflict_and_security_failure_are_tool_feedback(runtime_client, monkeypatch):
    client, _, native = runtime_client
    agent, sandbox, _, _, edges = setup_skill(client)
    assert client.patch(f"/api/edges/{edges[0]['id']}", json={'relationship': 'execute_manage'}).status_code == 200
    services = client.app.state.services
    async def scenario():
        provider = WorldAgentCapabilityProvider(services)
        definitions = await provider.list_tools(agent['id'])
        definition = next(d for d in definitions if d.name == 'start_sandbox')
        tool = build_scoped_tool_callables(provider, agent['id'], [definition])[0]
        async def conflicting(*args, **kwargs):
            raise SandboxStateError('stop the sandbox command before changing configuration or resources')
        with monkeypatch.context() as patch:
            patch.setattr(type(services), '_start_sandbox_locked', conflicting)
            result = await tool(sandbox=sandbox['id'])
            assert result['ok'] is False and result['error']['code'] == 'conflict'
            assert 'retry' in result['error']['message']
        assert (await tool(sandbox=sandbox['id']))['state'] == 'ready'
        async def unsafe(*args, **kwargs):
            raise SandboxSecurityError('native isolation failed')
        with monkeypatch.context() as patch:
            patch.setattr(type(services), '_start_sandbox_locked', unsafe)
            result = await tool(sandbox=sandbox['id'])
            assert result['ok'] is False
            assert result['error']['code'] == 'permission_denied'
            assert result['error']['message'] == 'native isolation failed'
            # The host API still rejects the operation; only model-facing calls
            # convert that rejection into feedback instead of killing the Run.
            with pytest.raises(PermissionDeniedError) as denied:
                await provider.invoke_tool(agent['id'], definition.capability_id, {'sandbox': sandbox['id']})
            assert isinstance(denied.value.__cause__, SandboxSecurityError)
            assert native.last_argv == ()
        assert (await tool(sandbox=sandbox['id']))['state'] == 'ready'
    client.portal.call(scenario)
