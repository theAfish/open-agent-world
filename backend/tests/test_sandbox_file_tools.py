"""Read-only agent tools reuse the UI's pinned file boundary and pagination."""
import os
import pytest
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.sandbox.files import DIRECTORY_LIMIT
from backend.sandbox.models import SandboxSecurityError


def test_workspace_tools_page_without_executing_or_writing_and_recheck_authority(runtime_client):
    client, backend, native = runtime_client
    agent, sandbox, _, _, edges = setup_skill(client)
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    text = 'line abcdefghijklmnop\n' * 500
    (root / 'existing.py').write_text(text)
    (root / 'nested').mkdir()
    (root / 'nested' / 'candidate.extxyz').write_text('0\nempty\n')
    (root / 'binary.bin').write_bytes(b'\0\xff')
    for n in range(DIRECTORY_LIMIT + 1):
        (root / f'item-{n:04}.txt').touch()
    before = {p.relative_to(root): p.stat().st_mtime_ns for p in root.rglob('*')}
    def invoke(name, **args):
        return client.portal.call(provider.invoke_tool, agent['id'], 'operation:' + name, {'sandbox': sandbox['id'], **args})
    tools = client.portal.call(provider.list_tools, agent['id'])
    assert {'list_sandbox_files', 'read_sandbox_file'} <= {t.name for t in tools}
    assert all(client.app.state.services.plugins.capability_definition(kind).read_only
               for kind in ('sandbox.list_files', 'sandbox.read_file'))
    first = invoke('list_sandbox_files')
    second = invoke('list_sandbox_files', cursor=first['next_cursor'])
    assert first['truncated'] and not second['truncated']
    assert len(first['entries']) + len(second['entries']) == len(list(root.iterdir()))
    empty = invoke('list_sandbox_files', query='missing.extxyz')
    assert empty['entries'] == [] and empty['next_cursor'] is None and empty['scope'] == 'recursive filename search'
    found = invoke('list_sandbox_files', query='candidate.extxyz')
    assert [e['path'] for e in found['entries']] == ['nested/candidate.extxyz']
    page = invoke('read_sandbox_file', path='existing.py', limit=8000)
    assert page['text'] == text[:8000] and page['next_offset'] == 8000
    tail = invoke('read_sandbox_file', path='existing.py', offset=page['next_offset'])
    assert page['text'] + tail['text'] == text and tail['next_offset'] is None
    eof = invoke('read_sandbox_file', path='existing.py', offset=100000)
    assert eof['text'] == '' and eof['offset'] == len(text)
    assert invoke('read_sandbox_file', path='binary.bin')['state'] == 'unsupported'
    for name, args in [('list_sandbox_files', {'path': '../'}), ('read_sandbox_file', {'path': '../outside'})]:
        with pytest.raises((SandboxSecurityError, ResourceValidationError)):
            invoke(name, **args)
    with pytest.raises(ResourceValidationError):
        invoke('read_sandbox_file', path='existing.py', limit=8001)
    if os.name != 'nt':
        nested_mtime = (root / 'nested').stat().st_mtime_ns
        (root / 'nested' / 'escape').symlink_to(root.parent)
        with pytest.raises((OSError, SandboxSecurityError)):
            invoke('read_sandbox_file', path='nested/escape/anything')
        (root / 'nested' / 'escape').unlink()
        os.utime(root / 'nested', ns=(nested_mtime, nested_mtime))
    assert {p.relative_to(root): p.stat().st_mtime_ns for p in root.rglob('*')} == before
    client.delete('/api/edges/' + edges[0]['id'])
    for name, args in [('list_sandbox_files', {}), ('read_sandbox_file', {'path': 'existing.py'})]:
        with pytest.raises(PermissionDeniedError):
            invoke(name, **args)
