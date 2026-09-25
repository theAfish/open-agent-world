from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from backend.packs.archive import MAX_ARCHIVE_BYTES
from backend.packs.marketplace import MarketplaceClient, MarketplaceError, PACK_MIME, PackVersion
from backend.tests.test_pack_installation import MANIFEST, artifact


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), error=None, wait=False):
        self.chunks, self.error, self.wait = chunks, error, wait
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            yield chunk
        if self.wait:
            await asyncio.Event().wait()
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def version_metadata(data, manifest=None):
    manifest = manifest or MANIFEST
    return {"pack_id": manifest["id"], "version": manifest["version"],
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "manifest": manifest}


class FakeMarketplace:
    def __init__(self, manifest=None, data=None):
        self.manifest = deepcopy(manifest or MANIFEST)
        self.data = data if data is not None else artifact(self.manifest)
        self.calls = []
        self.status = 200

    @property
    def metadata(self):
        return version_metadata(self.data, self.manifest)

    @property
    def listing(self):
        return {"id": self.manifest["id"], "name": "Greeter", "summary": "External Greeter Pack",
                "description": "Greeting card", "latest_version": self.manifest["version"]}

    def __call__(self, request):
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "PRIVATE STORAGE SECRET"}})
        path = request.url.path
        if path.endswith('/download'):
            return httpx.Response(200, headers={"Content-Type": PACK_MIME, "Content-Length": str(len(self.data)),
                "X-OAW-Pack-SHA256": self.metadata["sha256"]}, stream=Stream([self.data[:20], self.data[20:]]))
        if path.endswith('/versions/' + self.manifest['version']):
            payload = self.metadata
        elif path.endswith('/versions'):
            payload = {"items": [self.metadata], "next_cursor": None}
        elif path.endswith('/' + self.manifest['id']):
            payload = {**self.listing, "versions": [self.manifest["version"]]}
        else:
            query = request.url.params.get('query', '')
            payload = {"items": [self.listing] if query.lower() in json.dumps(self.listing).lower() else [], "next_cursor": None}
        # Exercise true incremental reads, including metadata.
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=Stream([json.dumps(payload).encode()]))


@pytest.mark.asyncio
async def test_protocol_and_sanitized_dtos():
    fake = FakeMarketplace()
    client = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fake))
    assert (await client.list_packs(query='Greeter', cursor='a.pack', limit=5)).items[0].id == 'test.greeter'
    assert dict(fake.calls[-1].url.params) == {'query': 'Greeter', 'cursor': 'a.pack', 'limit': '5'}
    assert (await client.get_pack('test.greeter')).versions == ['0.1.0']
    assert len((await client.list_versions('test.greeter')).items) == 1
    metadata = await client.get_version('test.greeter', '0.1.0')
    assert await client.download(metadata) == fake.data
    assert 'entrypoints' not in metadata.model_dump()['manifest']
    assert all(r.headers['accept-encoding'] == 'identity' for r in fake.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [(404,404), (429,429), (500,503), (503,503), (504,504), (302,503), (401,503)])
async def test_http_failure_sanitized(status, expected):
    fake = FakeMarketplace(); fake.status = status
    client = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fake))
    with pytest.raises(MarketplaceError) as caught:
        await client.list_packs()
    assert caught.value.status_code == expected
    assert 'PRIVATE' not in str(caught.value)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('error,expected', [(httpx.ConnectError('secret'),503), (httpx.ConnectTimeout('secret'),504),
                                         (httpx.ReadTimeout('secret'),504), (httpx.RemoteProtocolError('secret'),503)])
async def test_transport_failure(error, expected):
    def fail(request):
        raise error
    client = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fail))
    with pytest.raises(MarketplaceError) as caught:
        await client.list_packs()
    assert caught.value.status_code == expected
    assert 'secret' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('url', [None, '', 'file:///tmp', 'https://secret@market.test', 'https://market.test?token=secret', 'http://market.test:bad'])
async def test_unconfigured_and_invalid_url_are_lazy(url):
    client = MarketplaceClient(url)
    with pytest.raises(MarketplaceError) as caught:
        await client.list_packs()
    assert caught.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['mime', 'length', 'missing_length', 'digest_header', 'digest_body', 'short', 'long', 'interrupted', 'encoding'])
async def test_download_integrity_and_cleanup(case):
    data = b'canonical-pack'
    metadata = PackVersion.model_validate(version_metadata(data))
    headers = {'Content-Type': PACK_MIME, 'Content-Length': str(len(data)), 'X-OAW-Pack-SHA256': metadata.sha256}
    body, error = data, None
    if case == 'mime': headers['Content-Type'] = 'text/html'
    if case == 'length': headers['Content-Length'] = str(len(data) + 1)
    if case == 'missing_length': del headers['Content-Length']
    if case == 'digest_header': headers['X-OAW-Pack-SHA256'] = '0' * 64
    if case == 'digest_body': body = b'x' * len(data)
    if case == 'short': body = data[:-1]
    if case == 'long': body = data + b'x'
    if case == 'encoding': headers['Content-Encoding'] = 'gzip'
    if case == 'interrupted': body, error = data[:3], httpx.RemoteProtocolError('PRIVATE')
    stream = Stream([body], error=error)
    client = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(lambda r:
        httpx.Response(200, headers=headers, stream=stream)))
    with pytest.raises(MarketplaceError) as caught:
        await client.download(metadata)
    assert stream.closed
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('download', [False, True])
async def test_total_deadline_and_cancellation_close_stream(cancel, download):
    stream = Stream(wait=True)
    metadata = PackVersion.model_validate(version_metadata(b'pack'))
    headers = {'Content-Type': PACK_MIME if download else 'application/json',
               'Content-Length': '4', 'X-OAW-Pack-SHA256': metadata.sha256}
    client = MarketplaceClient('https://marketplace.test', request_timeout=.02 if not cancel else 30,
        download_timeout=.02 if not cancel else 30,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, headers=headers, stream=stream)))
    task = asyncio.create_task(client.download(metadata) if download else client.list_packs())
    await stream.started.wait()
    if cancel: task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else MarketplaceError):
        await task
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'size_bytes': MAX_ARCHIVE_BYTES + 1}, {'size_bytes': 0}, {'sha256': 'bad'},
                                 {'pack_id': 'other.pack'}, {'version': '0.2.0'}, {'manifest': {}}])
async def test_invalid_authoritative_metadata(change):
    payload = {**version_metadata(b'pack'), **change}
    client = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(lambda r:
        httpx.Response(200, headers={'Content-Type': 'application/json'}, stream=Stream([json.dumps(payload).encode()]))))
    with pytest.raises(MarketplaceError):
        await client.get_version('test.greeter', '0.1.0')


@pytest.mark.asyncio
async def test_identity_cannot_construct_arbitrary_url():
    fake = FakeMarketplace()
    client = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fake))
    for pack_id, version in [('https://evil.test', '0.1'), ('../packs', '0.1'), ('test.greeter', '../../secret')]:
        with pytest.raises(ValueError):
            await client.get_version(pack_id, version)
    assert not fake.calls
