import hashlib
import json
from dataclasses import replace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.errors import ResourceValidationError
from backend.file_transfer import FILE_CHUNK_BYTES, MAX_FILE_BYTES, receive_file
from backend.hosting.policy import HostedPolicyMiddleware
from backend.plugins.resources import NodeResourceAction
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['oversize', 'checksum', 'cancelled'])
async def test_file_lease_publishes_only_verified_eof_and_closes_source(failure):
    import asyncio
    closed, entered = [], []

    async def chunks():
        try:
            yield b'data'
            if failure == 'cancelled':
                raise asyncio.CancelledError
        finally:
            closed.append(True)

    expected = asyncio.CancelledError if failure == 'cancelled' else ResourceValidationError
    with pytest.raises(expected):
        async with receive_file(chunks(), name='input.xlsx', max_bytes=3 if failure == 'oversize' else 20,
                                expected_sha256='wrong' if failure == 'checksum' else None):
            entered.append(True)
    assert closed and not entered


@pytest.mark.asyncio
async def test_seekable_file_lease_is_disk_backed_and_normalizes_chunks():
    async def chunks():
        yield b'a' * (FILE_CHUNK_BYTES + 3)

    async with receive_file(chunks(), name='sample.xlsx') as file:
        assert file.stream.fileno() >= 0
        assert file.size_bytes == FILE_CHUNK_BYTES + 3
        assert file.sha256 == hashlib.sha256(b'a' * file.size_bytes).hexdigest()
        assert [len(chunk) async for chunk in file.chunks()] == [FILE_CHUNK_BYTES, 3]
    assert file.stream.closed


def test_resource_files_stream_outside_action_json_and_close_after_action(client, monkeypatch):
    services = client.app.state.services
    received = []

    def action(context, arguments):
        file = context.files['input']
        digest, largest = hashlib.sha256(), 0
        while chunk := file.stream.read(FILE_CHUNK_BYTES):
            digest.update(chunk)
            largest = max(largest, len(chunk))
        assert not any(isinstance(value, bytes) for value in arguments.values())
        received.append(file.stream)
        return {'sha256': digest.hexdigest(), 'size': file.size_bytes, 'largest': largest}

    def configure(registration):
        registration.register_node_type(replace(services.plugins.node_type('core.artifact-collection'),
            id='test.file_transfer.resource', traits=frozenset(), templateable=False,
            resource_actions={'save': NodeResourceAction(action)}))

    install_test_plugin(services.plugins, 'test.file_transfer', configure)
    node = create_node(client, 'test.file_transfer.resource')
    # Deliberately cross the former 32 MiB threshold. httpx's test transport
    # materializes the request; production multipart parsing spools it to disk.
    data = b'x' * (33 * 1024 * 1024)
    response = client.post(f"/api/nodes/{node['id']}/resource/save/files",
        data={'request': json.dumps({'arguments': {'label': 'retained file'}})},
        files={'input': ('large.xlsx', data, 'application/octet-stream')})
    assert response.status_code == 200, response.text
    assert response.json() == {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'largest': FILE_CHUNK_BYTES}
    assert received[0].closed
    assert client.get('/api/files/policy').json()['max_file_bytes'] == MAX_FILE_BYTES


@pytest.mark.parametrize('failure', ['limit', 'host_path', 'duplicate', 'invalid_filename'])
def test_hosted_file_actions_keep_size_metadata_and_identity_guards(monkeypatch, failure):
    from backend import resource_files as module
    from backend.hosting.policy import unsafe_host_configuration
    app = FastAPI()
    app.add_middleware(HostedPolicyMiddleware)
    handles = []

    @app.post('/api/nodes/{node}/resource/{action}/files')
    async def upload(request: Request, node: str, action: str):
        from fastapi import HTTPException
        async with module.resource_files(request) as (metadata, files):
            handles.extend(file.stream for file in files.values())
            if unsafe_host_configuration(metadata.arguments):
                raise HTTPException(403)
            return {'size': files['input'].size_bytes}

    if failure == 'limit':
        monkeypatch.setattr(module, 'MAX_FILE_BYTES', 3)
    args = {'path': '../../control/accounts.sqlite3'} if failure == 'host_path' else {}
    files = [('input', ('../sample.xlsx' if failure == 'invalid_filename' else 'sample.xlsx', b'data'))]
    if failure == 'duplicate':
        files += files
    with TestClient(app) as client:
        response = client.post('/api/nodes/test/resource/save/files',
            data={'request': json.dumps({'arguments': args})}, files=files)
    assert response.status_code == (403 if failure == 'host_path' else 413 if failure == 'limit' else 422)
    assert all(handle.closed for handle in handles)


def test_malformed_multipart_is_a_client_error():
    from backend.resource_files import resource_files
    app = FastAPI()
    @app.post('/upload')
    async def upload(request: Request):
        async with resource_files(request):
            raise AssertionError('Malformed input cannot reach the consumer')
    with TestClient(app) as client:
        response = client.post('/upload', content=b'not-the-declared-boundary',
            headers={'Content-Type': 'multipart/form-data; boundary=expected'})
    assert response.status_code == 400
