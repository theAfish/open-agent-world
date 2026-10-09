"""Real HTTP and pinned filesystem coverage; native command execution is mocked."""
import os
import subprocess
import asyncio
import base64

import pytest

from backend.tests.test_skill_runtime import runtime_client
from backend.tests.conftest import create_node
from backend.sandbox.files import operate
from backend.sandbox.models import SandboxSecurityError, SandboxValidationError, ResourceAccess


def test_large_upload_and_download_use_bounded_chunks(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    base = f"/api/sandboxes/{node['id']}/files"
    original = backend.file_operation
    chunks = []
    async def track(sandbox_id, operation, **options):
        if operation == 'upload_chunk':
            chunks.append(len(base64.b64decode(options['data'])))
        return await original(sandbox_id, operation, **options)
    monkeypatch.setattr(backend, 'file_operation', track)
    content = b'x' * (17 * 1024 * 1024) + b'final'
    response = client.post(base, params={'path': 'large.bin'}, content=content)
    assert response.status_code == 201, response.text
    assert max(chunks) <= 256 * 1024 and sum(chunks) == len(content)
    response = client.get(base, params={'operation': 'download', 'path': 'large.bin'})
    assert response.content == content
    assert int(response.headers['content-length']) == len(content)
    record = client.portal.call(backend._record, node['id'])
    assert not list(record.workspace.glob('.oaw-upload-*'))


def test_disconnect_cleans_staging_and_releases_binding_lease(runtime_client):
    from backend.api.runtime import upload_sandbox_file
    from starlette.requests import ClientDisconnect
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    class DisconnectedRequest:
        async def stream(self):
            yield b'partial bytes'
            raise ClientDisconnect()
    async def run():
        services = client.app.state.services
        with pytest.raises(ClientDisconnect):
            await upload_sandbox_file(node['id'], 'nested/cancelled.bin', DisconnectedRequest(), services=services)
        record = await backend._record(node['id'])
        assert not (record.workspace / 'nested/cancelled.bin').exists()
        assert not list(record.workspace.rglob('.oaw-upload-*'))
        assert not services.resources.artifacts.source_leases
    client.portal.call(run)
    base = f"/api/sandboxes/{node['id']}/files"
    assert client.post(base, params={'path': 'nested/cancelled.bin'}, content=b'retry').status_code == 201


def test_cancellation_during_begin_still_cleans_the_created_file(runtime_client, monkeypatch):
    from backend.api.runtime import upload_sandbox_file
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    original = backend.file_operation
    async def run():
        created, release = asyncio.Event(), asyncio.Event()
        async def delayed(sandbox_id, operation, **options):
            result = await original(sandbox_id, operation, **options)
            if operation == 'upload_begin':
                created.set()
                await release.wait()
            return result
        monkeypatch.setattr(backend, 'file_operation', delayed)
        services = client.app.state.services
        task = asyncio.create_task(upload_sandbox_file(node['id'], 'cancelled', None, services=services))
        await created.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert services.resources.artifacts.source_leases.get(node['id'])
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = await backend._record(node['id'])
        assert not list(record.workspace.glob('.oaw-upload-*'))
        assert not services.resources.artifacts.source_leases
    client.portal.call(run)


def test_directory_pages_are_sorted_complete_and_search_nested_files(tmp_path):
    for index in reversed(range(625)):
        (tmp_path / f'file-{index:04}.txt').write_text(str(index))
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'needle.csv').write_text('a,b')
    entries, cursor = [], ''
    while True:
        page = operate(tmp_path, '', 'list', cursor=cursor)
        entries.extend(page['entries'])
        cursor = page['next_cursor']
        if not cursor:
            break
    assert len(entries) == 626
    assert entries[0]['name'] == 'nested'
    assert [entry['name'] for entry in entries[1:]] == [f'file-{index:04}.txt' for index in range(625)]
    assert operate(tmp_path, '', 'list', query='needle')['entries'][0]['path'] == 'nested/needle.csv'
    with pytest.raises(SandboxValidationError):
        operate(tmp_path, '', 'list', cursor='invalid')
    cursor = operate(tmp_path, '', 'list')['next_cursor']
    with pytest.raises(SandboxValidationError):
        operate(tmp_path, '', 'list', cursor=cursor, query='different search')


def test_staged_commit_preserves_a_destination_created_mid_upload(tmp_path):
    from backend.sandbox.streaming import upload_operation
    staged = upload_operation(tmp_path, 'upload_begin', path='result.txt')
    chunk = upload_operation(tmp_path, 'upload_chunk', path='result.txt', staging=staged['staging'],
        expected=staged['signature'], data=base64.b64encode(b'new').decode())
    (tmp_path / 'result.txt').write_text('existing')
    with pytest.raises(SandboxValidationError, match='already exists'):
        upload_operation(tmp_path, 'upload_commit', path='result.txt', staging=staged['staging'], expected=chunk['signature'], offset=3)
    upload_operation(tmp_path, 'upload_abort', path='result.txt', staging=staged['staging'], expected=chunk['signature'])
    assert (tmp_path / 'result.txt').read_text() == 'existing'
    assert not list(tmp_path.glob('.oaw-upload-*'))


def test_changed_staging_identity_cannot_be_written_or_removed(tmp_path):
    from backend.sandbox.streaming import upload_operation
    staged = upload_operation(tmp_path, 'upload_begin', path='result.txt')
    staging_path = tmp_path / staged['staging']
    staging_path.rename(tmp_path / 'original-staging')
    staging_path.write_text('replacement must survive')
    with pytest.raises(SandboxValidationError, match='changed'):
        upload_operation(tmp_path, 'upload_chunk', path='result.txt', staging=staged['staging'], expected=staged['signature'], data='')
    with pytest.raises(SandboxSecurityError, match='replaced'):
        upload_operation(tmp_path, 'upload_abort', path='result.txt', staging=staged['staging'], expected=staged['signature'])
    assert staging_path.read_text() == 'replacement must survive'


def test_upload_nested_binary_empty_folders_and_download_without_overwrite(runtime_client):
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    base = f"/api/sandboxes/{node['id']}/files"
    payload = b'\0\xffbinary\r\n'
    assert client.post(base, params={'path': 'project/nested/data.bin'}, content=payload).status_code == 201
    assert client.post(base, params={'path': 'project/empty', 'directory': True}).status_code == 201
    assert client.post(base, params={'path': 'project/empty', 'directory': True}).status_code == 201
    preview = client.get(base, params={'operation': 'list', 'path': 'project'}).json()
    assert {e['name'] for e in preview['entries']} == {'nested', 'empty'}
    assert client.get(base, params={'operation': 'download', 'path': 'project/nested/data.bin'}).content == payload
    duplicate = client.post(base, params={'path': 'project/nested/data.bin'}, content=b'replaced')
    assert duplicate.status_code == 422 and 'already exists' in duplicate.text
    assert client.get(base, params={'operation': 'download', 'path': 'project/nested/data.bin'}).content == payload
    assert client.post(base, params={'path': 'project/nested/data.bin', 'directory': True}).status_code == 422


@pytest.mark.parametrize('path', ['', '../escape', '/absolute', 'C:/host', 'a\\b', 'a//b'])
def test_upload_rejects_invalid_paths(runtime_client, path):
    client, _, _ = runtime_client
    node = create_node(client, 'sandbox')
    assert client.post(f"/api/sandboxes/{node['id']}/files", params={'path': path}, content=b'data').status_code == 422


def test_upload_limits_and_readonly_are_enforced_server_side(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    node = create_node(client, 'sandbox')
    base = f"/api/sandboxes/{node['id']}/files"
    monkeypatch.setattr('backend.sandbox.streaming.UPLOAD_LIMIT', 3)
    assert client.post(base, params={'path': 'large'}, content=b'four').status_code == 413
    assert client.post(base, params={'path': 'folder', 'directory': True}, content=b'x').status_code == 413
    record = client.portal.call(backend._record, node['id'])
    record.workspace_access = ResourceAccess.READ_ONLY
    for directory in (True, False):
        response = client.post(base, params={'path': 'blocked', 'directory': directory}, content=b'' if directory else b'ok')
        assert response.status_code == 503 and 'read-only' in response.text
    assert not (record.workspace / 'blocked').exists()
    assert not (record.workspace / 'large').exists()


def test_folder_creation_rejects_missing_root_readonly_and_escape(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    for relative, readonly in [('nested/empty', True), ('../outside', False)]:
        with pytest.raises((SandboxSecurityError, SandboxValidationError)):
            operate(root, relative, 'mkdir', read_only=readonly)
    with pytest.raises(SandboxValidationError):
        operate(root / 'missing', 'nested/empty', 'mkdir')
    assert not (root / 'missing').exists()
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = root / 'escape'
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], check=True, capture_output=True)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises((SandboxSecurityError, SandboxValidationError, OSError)):
            operate(root, 'escape/created', 'mkdir')
        assert not (outside / 'created').exists()
    finally:
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()
