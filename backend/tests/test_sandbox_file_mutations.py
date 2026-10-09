"""Real HTTP and filesystem operations; native Sandbox execution is mocked."""
import os
import base64

import pytest

from backend.sandbox.files import file_operation, operate
from backend.sandbox.models import ResourceAccess, SandboxSecurityError, SandboxValidationError
from backend.sandbox.mutations import mutate
from backend.tests.conftest import create_node
from backend.tests.test_skill_runtime import runtime_client


def test_regular_files_are_clickable_but_hardlinks_are_blocked(tmp_path):
    (tmp_path / 'notes.txt').write_text('preview me')
    (tmp_path / 'linked.txt').write_text('protected')
    os.link(tmp_path / 'linked.txt', tmp_path / 'alias.txt')
    entries = {entry['name']: entry for entry in operate(tmp_path, '', 'list')['entries']}
    assert entries['notes.txt']['blocked'] is False
    assert entries['linked.txt']['blocked'] is True
    assert entries['alias.txt']['blocked'] is True
    assert operate(tmp_path, 'notes.txt', 'preview')['text'] == 'preview me'


def test_http_move_preview_delete_file_and_nested_folder(runtime_client):
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    base = f"/api/sandboxes/{node['id']}/files"
    assert client.post(base, params={'path': 'source/笔记😀.txt'}, content=b'contents').status_code == 201
    assert client.post(base, params={'path': 'destination', 'directory': True}).status_code == 201
    listing = client.get(base, params={'operation': 'list', 'path': 'source'}).json()
    assert listing['entries'][0]['blocked'] is False
    moved = client.post(base + '/move', json={'path': 'source/笔记😀.txt', 'destination': 'destination/笔记😀.txt'})
    assert moved.status_code == 200, moved.text
    assert client.get(base, params={'operation': 'preview', 'path': 'destination/笔记😀.txt'}).json()['text'] == 'contents'
    assert client.get(base, params={'operation': 'list', 'path': 'source'}).json()['entries'] == []
    assert client.post(base + '/move', json={'path': 'destination', 'destination': 'source/destination'}).status_code == 200
    assert client.delete(base, params={'path': 'source/destination/笔记😀.txt'}).status_code == 200
    assert client.post(base, params={'path': 'source/another/deep.txt'}, content=b'deep').status_code == 201
    removed = client.delete(base, params={'path': 'source'})
    assert removed.status_code == 200, removed.text
    assert not {'source', 'destination'} & {entry['name'] for entry in client.get(base, params={'operation': 'list'}).json()['entries']}
    record = client.portal.call(backend._record, node['id'])
    assert record.workspace.is_dir()


def test_move_conflicts_never_replace_existing_files_or_folders(tmp_path):
    (tmp_path / 'a').write_text('a')
    (tmp_path / 'b').write_text('b')
    with pytest.raises(SandboxValidationError, match='already exists'):
        mutate(tmp_path, 'move', path='a', destination='b')
    assert (tmp_path / 'a').read_text() == 'a'
    assert (tmp_path / 'b').read_text() == 'b'
    (tmp_path / 'source').mkdir()
    (tmp_path / 'destination').mkdir()
    with pytest.raises(SandboxValidationError, match='already exists'):
        mutate(tmp_path, 'move', path='source', destination='destination')
    assert (tmp_path / 'source').is_dir() and (tmp_path / 'destination').is_dir()


def test_destination_created_during_move_is_preserved(tmp_path, monkeypatch):
    from backend.sandbox import mutations
    (tmp_path / 'source').write_text('source')
    original = mutations._rename
    def race(source, destination, *args):
        destination.write_text('new arrival')
        return original(source, destination, *args)
    monkeypatch.setattr(mutations, '_rename', race)
    with pytest.raises(SandboxValidationError, match='already exists'):
        mutate(tmp_path, 'move', path='source', destination='destination')
    assert (tmp_path / 'source').read_text() == 'source'
    assert (tmp_path / 'destination').read_text() == 'new arrival'


@pytest.mark.parametrize('path', ['', '../outside', '/absolute', 'C:/host', 'a\\b'])
@pytest.mark.parametrize('operation', ['delete', 'move'])
def test_mutations_reject_root_and_escaping_sources(tmp_path, path, operation):
    with pytest.raises(SandboxValidationError):
        mutate(tmp_path, operation, path=path, destination='destination')


@pytest.mark.parametrize('destination', ['', '../outside', '/absolute', 'C:/host', 'a\\b', 'source', 'source/child'])
def test_move_rejects_escaping_or_descendant_destinations(tmp_path, destination):
    (tmp_path / 'source').mkdir()
    with pytest.raises(SandboxValidationError):
        mutate(tmp_path, 'move', path='source', destination=destination)
    assert (tmp_path / 'source').is_dir()


@pytest.mark.parametrize('operation', ['delete', 'move'])
def test_mutations_reject_readonly_and_resource_roots(tmp_path, operation):
    (tmp_path / 'a').write_text('original')
    with pytest.raises(SandboxSecurityError, match='read-only'):
        file_operation(tmp_path, ResourceAccess.READ_ONLY, [], operation, path='a', destination='b')
    with pytest.raises(SandboxSecurityError, match='workspace'):
        file_operation(tmp_path, ResourceAccess.READ_WRITE, [], operation, root='resource:other', path='a', destination='b')
    assert (tmp_path / 'a').read_text() == 'original'


def test_delete_preflights_entire_folder_and_preserves_hardlinks(tmp_path):
    folder = tmp_path / 'folder'
    folder.mkdir()
    (folder / 'first').write_text('keep')
    (tmp_path / 'outside').write_text('protected')
    os.link(tmp_path / 'outside', folder / 'linked')
    with pytest.raises(SandboxSecurityError):
        mutate(tmp_path, 'delete', path='folder')
    assert (folder / 'first').read_text() == 'keep'
    assert (tmp_path / 'outside').read_text() == 'protected'


def test_replaced_source_is_not_deleted(tmp_path, monkeypatch):
    from backend.sandbox import mutations
    (tmp_path / 'source').write_text('old')
    original = mutations.transfer
    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / 'source').rename(tmp_path / 'old')
        (tmp_path / 'source').write_text('replacement')
        return result
    monkeypatch.setattr(mutations, 'transfer', replace)
    with pytest.raises(SandboxSecurityError, match='changed'):
        mutate(tmp_path, 'delete', path='source')
    assert (tmp_path / 'source').read_text() == 'replacement'


def test_http_mutations_respect_upload_and_publication_leases(runtime_client):
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    base = f"/api/sandboxes/{node['id']}/files"
    assert client.post(base, params={'path': 'original'}, content=b'keep').status_code == 201
    services = client.app.state.services
    services.resources.artifacts.source_leases[node['id']] = 1
    try:
        assert client.delete(base, params={'path': 'original'}).status_code == 409
        assert client.post(base + '/move', json={'path': 'original', 'destination': 'moved'}).status_code == 409
    finally:
        services.resources.artifacts.source_leases.pop(node['id'])
    record = client.portal.call(backend._record, node['id'])
    record.workspace_access = ResourceAccess.READ_ONLY
    for response in (client.delete(base, params={'path': 'original'}),
                     client.post(base + '/move', json={'path': 'original', 'destination': 'moved'})):
        assert response.status_code == 503 and 'read-only' in response.text
    assert (record.workspace / 'original').read_bytes() == b'keep'


def test_links_cannot_redirect_deletion_or_move(tmp_path):
    root, outside = tmp_path / 'workspace', tmp_path / 'outside'
    root.mkdir(); outside.mkdir()
    (outside / 'protected').write_text('keep')
    (root / 'source').write_text('source')
    try:
        (root / 'alias').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Creating symbolic links requires OS permission')
    with pytest.raises((SandboxSecurityError, SandboxValidationError)):
        mutate(root, 'delete', path='alias/protected')
    with pytest.raises((SandboxSecurityError, SandboxValidationError)):
        mutate(root, 'move', path='source', destination='alias/moved')
    assert (outside / 'protected').read_text() == 'keep'
    assert (root / 'source').read_text() == 'source'
    assert not (outside / 'moved').exists()


@pytest.mark.skipif(os.name != 'nt' or not os.environ.get('OAW_TEST_WSL_DISTRO'), reason='Select a WSL distro on Windows')
@pytest.mark.asyncio
async def test_real_wsl_managed_and_external_workspace_mutations(tmp_path):
    from backend.sandbox.wsl import WslSandboxBackend
    backend = WslSandboxBackend(tmp_path / 'managed', distribution=os.environ['OAW_TEST_WSL_DISTRO'])
    await backend.create('files-check')
    try:
        for external in (False, True):
            if external:
                workspace = tmp_path / 'external'
                workspace.mkdir()
                await backend.configure('files-check', workspace_path=str(workspace), workspace_access=ResourceAccess.READ_WRITE)
            await backend.file_operation('files-check', 'write', path='source/notes.txt', data=base64.b64encode(b'WSL contents').decode(), create_parents=True)
            await backend.file_operation('files-check', 'mkdir', path='destination')
            assert (await backend.file_operation('files-check', 'list', path='source'))['entries'][0]['blocked'] is False
            await backend.file_operation('files-check', 'move', path='source/notes.txt', destination='destination/notes.txt')
            assert (await backend.file_operation('files-check', 'preview', path='destination/notes.txt'))['text'] == 'WSL contents'
            await backend.file_operation('files-check', 'write', path='source/notes.txt', data=base64.b64encode(b'preserved').decode())
            with pytest.raises(SandboxValidationError, match='already exists'):
                await backend.file_operation('files-check', 'move', path='source/notes.txt', destination='destination/notes.txt')
            await backend.file_operation('files-check', 'move', path='destination', destination='source/destination')
            await backend.file_operation('files-check', 'delete', path='source')
            assert (await backend.file_operation('files-check', 'list'))['entries'] == []
        await backend.file_operation('files-check', 'write', path='keep', data=base64.b64encode(b'keep').decode())
        await backend.configure('files-check', workspace_path=str(workspace), workspace_access=ResourceAccess.READ_ONLY)
        with pytest.raises(SandboxSecurityError, match='read-only'):
            await backend.file_operation('files-check', 'delete', path='keep')
        assert (workspace / 'keep').read_bytes() == b'keep'
    finally:
        await backend.destroy('files-check')
