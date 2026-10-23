import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from backend.hosting.policy import HostedPolicyMiddleware


@pytest.mark.parametrize('prefix', ['/api', '/api/runtime-app/workspace'])
@pytest.mark.parametrize('route', ['actions', 'resource'])
def test_upload_actions_allow_base64_files_but_still_reject_host_paths(prefix, route):
    app = FastAPI()
    app.add_middleware(HostedPolicyMiddleware)

    @app.post(prefix + '/nodes/{node_id}/' + route + '/{action}')
    async def action(request: Request, node_id: str, action: str):
        payload = await request.json()
        return {'bytes': len(payload['arguments']['content_base64']), 'action': action}

    encoded = 'A' * (5 * 1024 * 1024)  # A valid 3.75 MiB upload exceeds the old JSON limit.
    with TestClient(app) as client:
        path = prefix + '/nodes/knowledge/' + route + '/ingest'
        response = client.post(path, json={'arguments': {'filename': 'paper.pdf', 'content_base64': encoded}})
        assert response.status_code == 200 and response.json()['bytes'] == len(encoded)
        for name in ('/etc/passwd', '../../control/accounts.sqlite3', 'C:\\private'):
            response = client.post(path, json={'arguments': {'content_base64': encoded, 'options': {'path': name}}})
            assert response.status_code == 403


def test_upload_limit_has_a_distinct_error_and_configuration_keeps_its_smaller_limit(monkeypatch):
    from backend.hosting import policy
    app = FastAPI()
    app.add_middleware(HostedPolicyMiddleware)
    monkeypatch.setattr(policy, '_ACTION_BODY_LIMIT', 6 * 1024 * 1024)
    content = json.dumps({'arguments': {'content_base64': 'A' * (6 * 1024 * 1024)}})
    with TestClient(app) as client:
        for path, limit in (('/api/nodes/knowledge/actions/ingest', 6), ('/api/nodes', 4)):
            response = client.post(path, content=content, headers={'Content-Type': 'application/json'})
            assert response.status_code == 413
            assert response.json()['error'] == {'code': 'request_too_large', 'message': f'Request exceeds the {limit} MiB limit.'}
