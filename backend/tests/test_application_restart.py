from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import Mock

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.tests.test_pack_installation import MANIFEST, artifact


HEADERS = {"X-OAW-Restart": "1"}


def test_restart_requires_a_launcher_and_blocks_writes_after_acceptance(tmp_path):
    settings = Settings.for_data_root(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert not client.get('/api/application').json()['restart_supported']
        assert client.post('/api/application/restart', headers=HEADERS).status_code == 409
    restart = Mock()
    with TestClient(create_app(settings, restart=restart)) as client:
        profile = client.get('/api/application').json()
        assert profile['restart_supported']
        assert client.post('/api/application/restart').status_code == 403
        restart.assert_not_called()
        response = client.post('/api/application/restart', headers=HEADERS)
        assert response.status_code == 202
        assert response.json() == {'instance_id': profile['instance_id']}
        restart.assert_called_once_with()
        assert client.post('/api/nodes', json={'type': 'text'}).status_code == 503
        assert client.get('/api/application').json()['profile_id'] == profile['profile_id']


@pytest.mark.parametrize('origin,peer', [('https://example.com', '127.0.0.1'),
                                       ('http://localhost:5173', '192.168.1.2')])
def test_restart_rejects_remote_origins_and_authenticated_remote_peers(tmp_path, origin, peer):
    restart = Mock()
    token = 'test-host-token-' + 'x' * 32
    settings = replace(Settings.for_data_root(tmp_path), control_plane_token=token)
    with TestClient(create_app(settings, restart=restart), client=(peer, 1234)) as client:
        response = client.post('/api/application/restart', headers={**HEADERS, 'Origin': origin,
                                                                   'Authorization': 'Bearer ' + token})
        assert response.status_code == 403
        restart.assert_not_called()


def test_launcher_restarts_installed_python_code_on_the_same_address_and_preserves_data(tmp_path):
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = {**os.environ, 'OPEN_AGENT_WORLD_DATA_ROOT': str(tmp_path / 'profile'),
           'OPEN_AGENT_WORLD_AGENT_RUNTIME': 'core.mock', 'OPEN_AGENT_WORLD_PLUGIN_DIRS': '',
           'OPEN_AGENT_WORLD_MARKETPLACE_URL': '', 'PYTHONIOENCODING': 'utf-8'}
    env.pop('OPEN_AGENT_WORLD_CORE_ONLY', None)
    with (tmp_path / 'launcher.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-B', '-m', 'backend.launcher', '--desktop',
                                    '--no-sandbox', '--port', str(port), '--strict-port'], cwd=root,
                                   env=env, stdin=subprocess.PIPE, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
                def ready(previous=None):
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline and process.poll() is None:
                        try:
                            response = client.get('/api/application')
                            if response.status_code == 200 and response.json()['instance_id'] != previous:
                                return response.json()
                        except httpx.HTTPError:
                            pass
                        time.sleep(.1)
                    pytest.fail('Launcher did not recover:\n' + (tmp_path / 'launcher.log').read_text(encoding='utf-8')[-5000:])

                profile = ready()
                card = client.post('/api/nodes', json={'type': 'text', 'content': 'Keep my work'}).json()
                response = client.patch('/api/application/preferences', json={
                    'profile_id': profile['profile_id'], 'generation': profile['generation'],
                    'changes': {'oaw-theme': 'dark'},
                })
                assert response.status_code == 200
                for version in ('0.1.0', '0.2.0'):
                    manifest = deepcopy(MANIFEST)
                    manifest['version'] = version
                    installed = client.post('/api/packs/install', content=artifact(manifest),
                        headers={'Content-Type': 'application/vnd.oaw.pack', 'X-OAW-Pack-Install': '1'})
                    assert installed.status_code == 201, installed.text
                    assert installed.json()['restart_required']
                    response = client.post('/api/application/restart', headers=HEADERS)
                    assert response.status_code == 202, response.text
                    current = ready(profile['instance_id'])
                    assert current['profile_id'] == profile['profile_id']
                    assert current['generation'] == profile['generation']
                    assert current['values']['oaw-theme'] == 'dark'
                    loaded = client.get('/api/packs').json()['versions']
                    assert [row['version'] for row in loaded if row['loaded']] == [version]
                    assert client.get(f"/api/resources/{card['id']}/content").text == 'Keep my work'
                    profile = current
        finally:
            process.stdin.write(b'shutdown\n')
            process.stdin.flush()
            process.stdin.close()
            process.wait(timeout=40)
        assert process.returncode == 0
