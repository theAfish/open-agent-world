from fastapi.testclient import TestClient
import httpx

from backend import official_marketplace
from backend.config import Settings
from backend.main import create_app


def test_release_endpoint_and_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setattr(official_marketplace, 'OFFICIAL_MARKETPLACE_URL', 'https://store.example.test')
    monkeypatch.setenv('OPEN_AGENT_WORLD_DATA_ROOT', str(tmp_path))
    monkeypatch.delenv('OPEN_AGENT_WORLD_MARKETPLACE_URL', raising=False)
    assert Settings.from_environment().marketplace_url == 'https://store.example.test'
    assert Settings.for_data_root(tmp_path).marketplace_url == 'https://store.example.test'
    monkeypatch.setenv('OPEN_AGENT_WORLD_MARKETPLACE_URL', 'http://127.0.0.1:1234')
    assert Settings.from_environment().marketplace_url == 'http://127.0.0.1:1234'
    monkeypatch.setenv('OPEN_AGENT_WORLD_MARKETPLACE_URL', '')
    assert Settings.from_environment().marketplace_url is None


def test_official_endpoint_is_lazy_and_failure_is_local(monkeypatch, tmp_path):
    monkeypatch.setattr(official_marketplace, 'OFFICIAL_MARKETPLACE_URL', 'https://store.example.test')
    calls = []

    async def offline(self, request, **kwargs):
        calls.append(request.url)
        raise httpx.ConnectError('offline')

    monkeypatch.setattr(httpx.AsyncClient, 'send', offline)
    with TestClient(create_app(Settings.for_data_root(tmp_path))) as client:
        for route in ('/api/health', '/api/catalog', '/api/packs', '/api/card-library', '/api/nodes'):
            assert client.get(route).status_code == 200
        assert not calls
        assert client.get('/api/store/packs').status_code == 503
        assert calls and calls[0].host == 'store.example.test'
        assert client.get('/api/health').status_code == 200
