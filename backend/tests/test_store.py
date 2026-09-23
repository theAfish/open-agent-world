from copy import deepcopy
from dataclasses import replace

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.packs.marketplace import MarketplaceClient
from backend.tests.test_marketplace import FakeMarketplace
from backend.tests.test_pack_installation import MANIFEST, artifact, isolate_modules

HEADERS = {'X-OAW-Pack-Install': '1'}
URL = '/api/store/packs/test.greeter/versions/0.1.0/install'


@pytest.fixture
def store(tmp_path):
    app = create_app(Settings.for_data_root(tmp_path))
    with TestClient(app) as client:
        fake = FakeMarketplace()
        app.state.services.marketplace = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fake))
        yield client, fake


def test_store_search_detail_get_restart_and_already_installed(store):
    client, fake = store
    assert client.get('/api/store/packs', params={'query': 'absent'}).json()['items'] == []
    item = client.get('/api/store/packs', params={'query': 'greeter', 'limit': 1, 'cursor': 'a.pack'}).json()['items'][0]
    assert item['can_install'] and not item['installed_version']
    assert client.get('/api/store/packs/test.greeter').json()['versions'] == ['0.1.0']
    metadata = client.get('/api/store/packs/test.greeter/versions/0.1.0').json()
    assert metadata['manifest']['runtime']['sandbox']['python'] == []
    assert client.get('/api/store/packs/test.greeter/versions').status_code == 200
    assert client.post(URL).status_code == 403
    result = client.post(URL, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json() == {'installed_version': '0.1.0', 'available_version': '0.1.0', 'loaded_version': None,
                            'update_available': False, 'restart_required': True, 'can_install': False}
    installed = client.get('/api/packs').json()
    assert installed['versions'][0]['digest'] == fake.metadata['sha256']
    assert installed['versions'][0]['selected'] and not installed['versions'][0]['loaded']
    assert 'test.greeter' not in client.get('/api/catalog').json()['frontend_modules']
    assert client.post(URL, headers=HEADERS).status_code == 200
    assert len([r for r in fake.calls if r.url.path.endswith('/download')]) == 1


def test_unavailable_isolated_from_local_world_and_local_file(store):
    client, fake = store
    fake.status = 503
    result = client.get('/api/store/packs')
    assert result.status_code == 503 and 'PRIVATE' not in result.text
    for path in ['/api/health', '/api/catalog', '/api/packs', '/api/card-library', '/api/nodes']:
        assert client.get(path).status_code == 200
    assert client.post('/api/packs/install', headers={**HEADERS, 'Content-Type': 'application/vnd.oaw.pack'}, content=fake.data).status_code == 201


@pytest.mark.parametrize('url', [None, 'invalid://marketplace'])
def test_no_marketplace_does_not_block_startup(tmp_path, url):
    app = create_app(replace(Settings.for_data_root(tmp_path), marketplace_url=url))
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/catalog').json()['packs']
        assert client.get('/api/store/packs').status_code == 503


def test_pep440_update_and_retained_version_selection(store):
    client, fake = store
    fake.manifest['version'] = '0.9.0'; fake.data = artifact(fake.manifest)
    assert client.post(URL.replace('0.1.0', '0.9.0'), headers=HEADERS).status_code == 200
    old = fake.data
    fake.manifest['version'] = '0.10.0'; fake.data = artifact(fake.manifest)
    item = client.get('/api/store/packs').json()['items'][0]
    assert item['update_available'] and item['installed_version'] == '0.9.0'
    assert client.post(URL.replace('0.1.0', '0.10.0'), headers=HEADERS).status_code == 200
    versions = client.get('/api/packs').json()['versions']
    assert len(versions) == 2
    assert next(r for r in versions if r['selected'])['version'] == '0.10.0'
    fake.manifest['version'] = '0.9.0'; fake.data = old
    older = client.get('/api/store/packs').json()['items'][0]
    assert not older['update_available'] and not older['can_install']
    assert client.post(URL.replace('0.1.0', '0.9.0'), headers=HEADERS).status_code == 200
    assert len([r for r in fake.calls if r.url.path.endswith('/download')]) == 2


@pytest.mark.parametrize('failure', ['archive', 'identity', 'version', 'dependency', 'compatibility', 'python'])
def test_final_validation_is_always_existing_installer(store, failure):
    client, fake = store
    manifest = deepcopy(MANIFEST)
    if failure == 'identity': manifest['id'] = 'other.pack'
    if failure == 'version': manifest['version'] = '0.2.0'
    if failure == 'dependency': manifest['dependencies']['packs'] = [{'id': 'missing.pack', 'version': '>=1'}]
    if failure == 'compatibility': manifest['compatibility']['oaw'] = '>=999'
    if failure == 'python': manifest['runtime']['sandbox']['python'] = ['numpy<1', 'numpy>=2']
    fake.data = b'not a zip' if failure == 'archive' else artifact(manifest)
    result = client.post(URL, headers=HEADERS)
    assert result.status_code == 422, result.text
    assert client.get('/api/packs').json()['versions'] == []
    assert not list(client.app.state.services.pack_installations.root.glob('staging/*'))


def test_restart_merges_loaded_state(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    fake = FakeMarketplace()
    with TestClient(create_app(settings)) as client:
        client.app.state.services.marketplace = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fake))
        assert client.post(URL, headers=HEADERS).status_code == 200
    with TestClient(create_app(settings)) as client:
        client.app.state.services.marketplace = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(fake))
        item = client.get('/api/store/packs').json()['items'][0]
        assert item['loaded_version'] == item['installed_version'] == '0.1.0'
        assert not item['restart_required'] and not item['can_install']


def test_immutable_remote_replacement_is_rejected(store):
    client, fake = store
    assert client.post(URL, headers=HEADERS).status_code == 200
    fake.data = artifact(source='# replacement')
    assert client.post(URL, headers=HEADERS).status_code == 409


@pytest.mark.asyncio
async def test_browser_disconnect_cancels_acquisition():
    from backend.api.store import while_connected
    from backend.packs.marketplace import PackVersion, PACK_MIME
    from backend.tests.test_marketplace import Stream, version_metadata
    from fastapi import HTTPException, Request
    stream = Stream(wait=True)
    metadata = PackVersion.model_validate(version_metadata(b'pack'))
    marketplace = MarketplaceClient('https://marketplace.test', transport=httpx.MockTransport(lambda r:
        httpx.Response(200, headers={'Content-Type': PACK_MIME, 'Content-Length': '4',
                                    'X-OAW-Pack-SHA256': metadata.sha256}, stream=stream)))
    async def receive():
        await stream.started.wait()
        return {'type': 'http.disconnect'}
    request = Request({'type': 'http'}, receive=receive)
    with pytest.raises(HTTPException) as caught:
        await while_connected(request, marketplace.download(metadata))
    assert caught.value.status_code == 499
    assert stream.closed
