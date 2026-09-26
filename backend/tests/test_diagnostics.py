from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.tests.conftest import create_node


def report(client):
    response = client.get('/api/diagnostics')
    assert response.status_code == 200, response.text
    return response.json(), {row['id']: row for row in response.json()['checks']}


def save_model(client, **changes):
    connection = dict(id='test', name='Test', adapter='openai', base_url='https://model.example/v1',
                      api_key='private-key-do-not-return', models=[dict(id='test', name='Test', model_id='model')])
    connection.update(changes)
    response = client.put('/api/settings/models', json=dict(revision=0, default_model='oaw:model:test', connections=[connection]))
    assert response.status_code == 200, response.text


def test_full_canvas_check_is_read_only_and_does_not_construct_runtime(client, monkeypatch):
    services = client.app.state.services
    services.run_manager.default_runtime_provider_id = 'google.adk'
    agent = create_node(client, 'agent')
    distant = create_node(client, 'text', name='Far away', position={'x': 200000, 'y': -200000})
    save_model(client)
    monkeypatch.setattr(type(services.run_manager), '_provider', lambda *_: (_ for _ in ()).throw(AssertionError('must not initialize runtime')))
    before = client.get('/api/world').json()
    data, rows = report(client)
    assert data['card_count'] == 2
    assert rows[agent['id']]['code'] == 'model_configured'
    assert rows[distant['id']]['x'] == 200000
    assert client.get('/api/world').json() == before
    assert not services.run_manager.list_runs()
    assert 'private-key' not in str(data)
    assert 'model.example' not in str(data)


def test_missing_environment_credentials_are_actionable_without_leaking_values(client, monkeypatch):
    services = client.app.state.services
    services.run_manager.default_runtime_provider_id = 'google.adk'
    agent = create_node(client, 'agent')
    save_model(client, api_key=None, auth_mode='environment', environment_variable='OAW_HELP_TEST_KEY')
    monkeypatch.delenv('OAW_HELP_TEST_KEY', raising=False)
    assert report(client)[1][agent['id']]['code'] == 'model_configuration'
    monkeypatch.setenv('OAW_HELP_TEST_KEY', 'private-environment-value')
    data, rows = report(client)
    assert rows[agent['id']]['code'] == 'model_configured'
    assert 'private-environment-value' not in str(data)


def test_external_agent_does_not_require_oaw_model(client):
    services = client.app.state.services
    services.run_manager.default_runtime_provider_id = 'core.mock'
    agent = create_node(client, 'agent')
    assert report(client)[1][agent['id']]['code'] == 'external_agent'


def test_agent_model_check_follows_legion_override_and_opt_out(client):
    services = client.app.state.services
    services.run_manager.default_runtime_provider_id = 'google.adk'
    save_model(client)
    agent = create_node(client, 'agent', config={'model': 'oaw:model:missing'})
    group = client.post('/api/legion-groups', json={'name': 'Team', 'node_ids': [agent['id']]}).json()[0]
    response = client.patch(f"/api/nodes/{group['id']}", json={'config': {'mode': 'team', 'model_override': 'oaw:model:test'}})
    assert response.status_code == 200, response.text
    _, rows = report(client)
    assert rows[agent['id']]['code'] == 'model_configured'
    assert rows[agent['id']]['focus_id'] == group['id']
    response = client.patch(f"/api/nodes/{agent['id']}", json={'config': {'inherit_legion_model': False}})
    assert response.status_code == 200, response.text
    assert report(client)[1][agent['id']]['code'] == 'model_configuration'


def test_missing_runtime_and_unknown_model_are_not_reported_healthy(client):
    services = client.app.state.services
    agent = create_node(client, 'agent')
    services.run_manager.default_runtime_provider_id = None
    assert report(client)[1][agent['id']]['code'] == 'agent_runtime'
    services.run_manager.default_runtime_provider_id = 'google.adk'
    assert report(client)[1][agent['id']]['code'] == 'model_configuration'


def test_sandbox_stopped_unavailable_and_failed_checks_are_distinct(client, monkeypatch):
    cards = [create_node(client, 'sandbox') for _ in range(3)]

    async def inspect(self, node_id):
        if node_id == cards[2]['id']:
            raise RuntimeError('private-runtime-detail')
        return SimpleNamespace(state='stopped', available=node_id == cards[0]['id'], network_enabled=False)

    services = client.app.state.services
    monkeypatch.setattr(type(services), 'get_sandbox', inspect)
    start = AsyncMock(side_effect=AssertionError('must not start sandbox'))
    monkeypatch.setattr(type(services), 'start_sandbox', start)
    data, rows = report(client)
    assert rows[cards[0]['id']]['code'] == 'sandbox_stopped'
    assert rows[cards[0]['id']]['status'] == 'info'
    assert rows[cards[1]['id']]['code'] == 'sandbox_unavailable'
    assert rows[cards[1]['id']]['status'] == 'warning'
    assert rows[cards[2]['id']]['code'] == 'check_unavailable'
    assert 'private-runtime-detail' not in str(data)
    start.assert_not_called()


def test_equipment_conversation_is_connected_and_locates_owner(client):
    agent = create_node(client, 'agent', position={'x': 60000, 'y': 100})
    chat = create_node(client, 'conversation', equipment={'owner_id': agent['id'], 'relationship': 'participate'})
    empty = create_node(client, 'conversation')
    _, rows = report(client)
    assert rows[chat['id']]['code'] == 'card_configured'
    assert rows[chat['id']]['focus_id'] == agent['id']
    assert rows[chat['id']]['x'] == 60000
    assert rows[empty['id']]['code'] == 'conversation_unconnected'


def test_disabled_plugin_and_failed_environment_are_reported(client, monkeypatch):
    services = client.app.state.services
    card = create_node(client, 'text')
    owner = services.plugins.node_type_owner_id('text')
    monkeypatch.setattr(services.plugin_bootstrap, 'records', lambda: [{'id': owner, 'state': 'environment_failed'}])
    assert report(client)[1][card['id']]['code'] == 'plugin_environment'
    catalog = services.plugins.catalog()
    monkeypatch.setattr(services.plugins, 'catalog', lambda: catalog.model_copy(update={'node_types': []}))
    assert report(client)[1][card['id']]['code'] == 'plugin_unavailable'
