import httpx
import pytest

from backend.security import model_discovery


def draft(**changes):
    return dict(id='connection', name='Test', adapter='openai', base_url='https://models.example/v1', api_key='private-key', **changes)


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(model_discovery.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_discovery_does_not_save_or_expose_credentials(client, monkeypatch):
    def handle(request):
        assert str(request.url) == 'https://models.example/v1/models'
        assert request.headers['authorization'] == 'Bearer private-key'
        return httpx.Response(200, json={'data': [{'id': 'chat-model'}, {'id': 'chat-model'}]})
    transport(monkeypatch, handle)
    response = client.post('/api/settings/models/discover', json=draft())
    assert response.status_code == 200, response.text
    assert response.json() == {'models': [{'id': 'chat-model', 'name': 'chat-model'}], 'truncated': False}
    assert 'private-key' not in response.text
    assert client.get('/api/settings/models').json()['connections'] == []


@pytest.mark.parametrize('status', [301, 401, 403, 404, 429, 500])
def test_errors_and_redirects_never_echo_provider_body(client, monkeypatch, status):
    transport(monkeypatch, lambda request: httpx.Response(status, json={'error': 'private-key'}, headers={'Location': 'https://elsewhere.example/models'}))
    response = client.post('/api/settings/models/discover', json=draft())
    assert response.status_code == 422
    assert 'private-key' not in response.text


def test_invalid_secret_input_is_redacted(client):
    response = client.post('/api/settings/models/discover', json={**draft(), 'adapter': 'private-key'})
    assert response.status_code == 422
    assert 'private-key' not in response.text


def test_saved_secret_is_reused_only_for_same_service(client, monkeypatch):
    saved = client.put('/api/settings/models', json={'revision': 0, 'connections': [draft()]}).json()['connections'][0]
    calls = []
    transport(monkeypatch, lambda request: (calls.append(request), httpx.Response(200, json={'data': []}))[1])
    assert client.post('/api/settings/models/discover', json=saved).status_code == 200
    assert calls[0].headers['authorization'] == 'Bearer private-key'
    assert client.post('/api/settings/models/discover', json={**saved, 'base_url': 'https://other.example/v1'}).status_code == 422
    assert len(calls) == 1


def test_gemini_paginates_and_filters_non_generation_models(client, monkeypatch):
    def handle(request):
        assert request.headers['x-goog-api-key'] == 'private-key'
        assert 'key' not in request.url.params
        if request.url.params.get('pageToken') == 'next':
            return httpx.Response(200, json={'models': [{'name': 'models/chat', 'supportedGenerationMethods': ['generateContent']}]})
        return httpx.Response(200, json={'models': [{'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']}], 'nextPageToken': 'next'})
    transport(monkeypatch, handle)
    response = client.post('/api/settings/models/discover', json={**draft(), 'adapter': 'gemini', 'base_url': ''})
    assert response.json()['models'] == [{'id': 'chat', 'name': 'chat'}]


def test_anthropic_headers_and_pagination(client, monkeypatch):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.headers['anthropic-version'] == '2023-06-01'
        assert request.headers['x-api-key'] == 'private-key'
        return httpx.Response(200, json={'data': [{'id': 'a', 'display_name': 'Model A'}], 'has_more': len(calls) == 1, 'last_id': 'a'})
    transport(monkeypatch, handle)
    response = client.post('/api/settings/models/discover', json={**draft(), 'adapter': 'anthropic', 'base_url': ''})
    assert response.status_code == 200
    assert calls[1].url.params['after_id'] == 'a'


def test_local_discovery_has_no_auth_header(client, monkeypatch):
    def handle(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'data': []})
    transport(monkeypatch, handle)
    response = client.post('/api/settings/models/discover', json={**draft(), 'api_key': None, 'auth_mode': 'none', 'base_url': 'http://localhost:11434/v1'})
    assert response.status_code == 200
