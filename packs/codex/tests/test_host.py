"""Exercise real OAW lifecycle and broker; live model test is explicit opt-in."""
import os
import json
import getpass
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.plugins.loader import load_plugin_registry
from backend.services import create_services
from oaw_codex.runtime import CodexRuntime
from oaw_codex import __main__ as launcher


def make_client(tmp_path, live=False):
    registry = load_plugin_registry(plugin_directories=(Path(__file__).resolve().parents[1],))
    settings = Settings.for_data_root(tmp_path / 'world')
    services = create_services(settings, plugins=registry)
    command = None if live else [sys.executable, str(Path(__file__).with_name('fake_server.py')), str(tmp_path / 'protocol.jsonl')]
    services.run_manager.provider_options['openai.codex'].update({'state_directory': tmp_path / 'state', 'server_command': command})
    return TestClient(create_app(settings, services=services), client=('127.0.0.1', 50000)), services


def create_agent(client, tmp_path):
    response = client.post('/api/nodes', json={'type': 'openai.codex.agent', 'name': 'Codex', 'config': {
        'runtime_provider_id': 'openai.codex', 'model': 'default',
        'workspace_path': str(tmp_path), 'codex_sandbox': 'workspace-write',
        'system_instruction': 'Follow the user request. Use only the provided workspace and connected tools.',
    }})
    assert response.status_code == 201, response.text
    return response.json()['id']


def wait_run(client, run_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = client.get(f'/api/runs/{run_id}').json()
        if record['status'] in {'succeeded', 'failed', 'cancelled', 'interrupted'}:
            return record
        time.sleep(.1)
    client.post(f'/api/runs/{run_id}/cancel')
    pytest.fail(f'Run did not finish within {timeout}s')


def test_host_records_success_failure_and_cancellation(tmp_path):
    client, services = make_client(tmp_path)
    try:
        with client:
            agent = create_agent(client, tmp_path)
            for prompt, expected in [('hello', 'succeeded'), ('fail', 'failed')]:
                response = client.post(f'/api/agents/{agent}/run', json={'prompt': prompt})
                assert response.status_code == 202, response.text
                record = wait_run(client, response.json()['run_id'])
                assert record['status'] == expected, record
                # Join cleanup before the next invocation uses the same Agent.
                client.portal.call(services.run_manager.wait_execution, record['run_id'])
            response = client.post(f'/api/agents/{agent}/run', json={'prompt': 'hang'})
            run_id = response.json()['run_id']
            time.sleep(.4)
            assert client.post(f'/api/runs/{run_id}/cancel').status_code == 200
            assert wait_run(client, run_id)['status'] == 'cancelled'
            assert client.delete(f'/api/nodes/{agent}').status_code == 200
    finally:
        services.close()


def test_trial_setup_and_conversation_reply(tmp_path, monkeypatch):
    client, services = make_client(tmp_path)
    def api(base, path, payload=None):
        response = client.get('/api' + path) if payload is None else client.post('/api' + path, json=payload)
        response.raise_for_status()
        return response.json()
    monkeypatch.setattr(launcher, 'api', api)
    try:
        with client:
            launcher.setup('unused', tmp_path, 'default')
            launcher.setup('unused', tmp_path, 'default')
            world = client.get('/api/world').json()
            assert len(world['nodes']) == 3 and len(world['edges']) == 2
            codex = next(n for n in world['nodes'] if n['id'] == 'codex-demo-agent')
            assert codex['type'] == 'openai.codex.agent'
            assert services.plugins.node_type_owner_id(codex['type']) == 'openai.codex'
            summary = client.get('/api/conversations/codex-demo-conversation').json()
            session = summary['sessions'][0]['id']
            path = f'/api/conversations/codex-demo-conversation/sessions/{session}/messages'
            response = client.post(path, json={'content': 'hello', 'mention_agent_ids': ['codex-demo-agent']})
            assert response.status_code == 202, response.text
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                messages = client.get(path).json()
                if any(m['sender_kind'] == 'agent' for m in messages):
                    break
                time.sleep(.1)
            assert any(m['sender_kind'] == 'agent' and m['content'] == 'Hello OAW' for m in messages), messages
    finally:
        services.close()


def test_palette_card_can_be_created_before_workspace_is_configured(tmp_path):
    client, services = make_client(tmp_path)
    try:
        with client:
            catalog = client.get('/api/catalog').json()
            definition = next(n for n in catalog['node_types'] if n['id'] == 'openai.codex.agent')
            assert 'core.agent' in definition['traits']
            assert 'reasoning_effort' in definition['config_schema']['properties']
            response = client.post('/api/nodes', json={'type': 'openai.codex.agent'})
            assert response.status_code == 201, response.text
            agent = response.json()['id']
            info = client.get(f'/api/agents/{agent}')
            assert info.status_code == 200, info.text
            assert info.json()['details']['available'] is True
            invalid = client.patch(f'/api/nodes/{agent}', json={'config': {'runtime_provider_id': 'core.mock'}})
            assert invalid.status_code == 422
    finally:
        services.close()


@pytest.mark.parametrize('custom_default', [False, True])
def test_blank_project_runs_in_shared_default_and_follows_setting_changes(tmp_path, custom_default):
    client, services = make_client(tmp_path)
    root = tmp_path / 'workspaces' if custom_default else services.settings.data_root
    root.mkdir(parents=True, exist_ok=True)
    try:
        with client:
            if custom_default:
                assert client.put('/api/settings/sandbox', json={'workspace_root': str(root)}).status_code == 200
            agents = []
            for _ in range(2):
                response = client.post('/api/nodes', json={'type': 'openai.codex.agent'})
                assert response.status_code == 201, response.text
                agents.append(response.json()['id'])
            workspace = root / 'codex-workspace'
            assert not workspace.exists()  # Discovery and card creation do not write project files.

            def run(agent):
                response = client.post(f'/api/agents/{agent}/run', json={'prompt': 'hello'})
                assert response.status_code == 202, response.text
                record = wait_run(client, response.json()['run_id'])
                assert record['status'] == 'succeeded', record
                client.portal.call(services.run_manager.wait_execution, record['run_id'])

            for agent in agents:
                run(agent)
                assert client.get(f'/api/nodes/{agent}').json()['config']['workspace_path'] == ''
            assert workspace.is_dir()
            marker = workspace / 'keep.txt'
            marker.write_text('keep')
            other = tmp_path / 'new-default'
            other.mkdir()
            assert client.put('/api/settings/sandbox', json={'workspace_root': str(other)}).status_code == 200
            run(agents[0])
            assert (other / 'codex-workspace').is_dir()
            assert (other / 'codex-workspace' / 'keep.txt').read_text() == 'keep'
            assert marker.read_text() == 'keep'
            # Explicit overrides take precedence; clearing restores default behavior.
            assert client.patch(f'/api/nodes/{agents[0]}', json={'config': {'workspace_path': str(tmp_path)}}).status_code == 200
            run(agents[0])
            assert client.patch(f'/api/nodes/{agents[0]}', json={'config': {'workspace_path': ''}}).status_code == 200
            run(agents[0])
            protocol = [json.loads(line) for line in (tmp_path / 'protocol.jsonl').read_text().splitlines()]
            threads = [p for p in protocol if p.get('method') in {'thread/start', 'thread/resume'}]
            assert [p['params']['cwd'] for p in threads] == [
                str(workspace), str(workspace), str(other / 'codex-workspace'), str(tmp_path), str(other / 'codex-workspace'),
            ]
            assert threads[2]['method'] == 'thread/start'  # A changed directory gets its own conversation scope.
    finally:
        services.close()


def test_minister_role_uses_codex_runtime_and_keeps_native_configuration(tmp_path):
    import json
    client, services = make_client(tmp_path)
    try:
        with client:
            agent = create_agent(client, tmp_path)
            before = client.get(f'/api/nodes/{agent}').json()
            response = client.post(f'/api/agents/{agent}/run', json={'prompt': 'hello'})
            normal = wait_run(client, response.json()['run_id'])
            assert normal['status'] == 'succeeded'
            client.portal.call(services.run_manager.wait_execution, normal['run_id'])
            assert client.patch(f'/api/nodes/{agent}', json={'minister': {}}).status_code == 200
            after = client.get(f'/api/nodes/{agent}').json()
            assert after['type'] == 'openai.codex.agent'
            for key, value in before['config'].items():
                assert after['config'][key] == value
            response = client.post(f'/api/agents/{agent}/run', json={'prompt': 'minister'})
            promoted = wait_run(client, response.json()['run_id'])
            assert promoted['status'] == 'succeeded', promoted
            assert promoted['runtime_provider_id'] == normal['runtime_provider_id'] == 'openai.codex'
            client.portal.call(services.run_manager.wait_execution, promoted['run_id'])
            protocol = [json.loads(line) for line in (tmp_path / 'protocol.jsonl').read_text().splitlines()]
            sessions = [item['params'] for item in protocol if item.get('method') in {'thread/start', 'thread/resume'}]
            assert 'local canvas administrator' not in sessions[0]['developerInstructions']
            assert 'local canvas administrator' in sessions[-1]['developerInstructions']
            assert before['config']['system_instruction'] in sessions[-1]['developerInstructions']
            assert sessions[-1]['cwd'] == sessions[0]['cwd'] and sessions[-1]['sandbox'] == sessions[0]['sandbox']
            assert client.patch(f'/api/nodes/{agent}', json={'minister': None}).status_code == 200
            assert not any(cap.kind.startswith('minister.') for cap in services.capabilities.derive(agent).capabilities)
    finally:
        services.close()


@pytest.mark.skipif(os.environ.get('OAW_CODEX_LIVE') != '1', reason='Set OAW_CODEX_LIVE=1 to run a real authenticated Codex turn')
def test_live_file_edit_and_oaw_tool(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    if os.name == 'nt':
        # pytest's private temp ACL grants OWNER RIGHTS, not the host user.
        # Codex-created files have a different owner; give the host explicit
        # access on this disposable shared workspace, as on a normal checkout.
        subprocess.run(['icacls', str(workspace), '/grant', f'{getpass.getuser()}:(OI)(CI)M'],
                       check=True, capture_output=True)
    client, services = make_client(tmp_path, live=True)
    try:
        with client:
            agent = create_agent(client, workspace)
            note = client.post('/api/nodes', json={'type': 'text', 'name': 'Codex verification note', 'content': 'before'}).json()
            edge = client.post('/api/edges', json={'source': agent, 'target': note['id'], 'relationship': 'read_edit'})
            assert edge.status_code == 201, edge.text
            prompt = ('Verify this integration with two small actions. Create codex-smoke.txt in cwd containing exactly OAW_CODEX_OK. '
                      'Then call oaw_list_tools and use oaw_invoke_tool to replace the connected text note with exactly OAW_TOOL_OK. '
                      'Do not use shell or HTTP for the note: it exists only in OAW. Do not delegate. Finally reply CODEX_SMOKE_OK.')
            response = client.post(f'/api/agents/{agent}/run', json={'prompt': prompt})
            assert response.status_code == 202, response.text
            record = wait_run(client, response.json()['run_id'], timeout=240)
            assert record['status'] == 'succeeded', record.get('error')
            assert (workspace / 'codex-smoke.txt').read_text().strip() == 'OAW_CODEX_OK'
            assert services.resources.read_text(note['id']).content.strip() == 'OAW_TOOL_OK'
    finally:
        services.close()

