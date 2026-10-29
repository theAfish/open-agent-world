"""Role persistence and authority across normal, strict-plugin and legacy Agents."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.main import create_app
from backend.minister import INSTRUCTION, LEGACY_INSTRUCTION, control, migrate_legacy_ministers
from backend.tests.conftest import create_node
from backend.tests.test_minister import call, create_minister


@pytest.mark.parametrize('agent_type', ['agent', 'openai.codex.agent'])
def test_role_card_is_inert_and_is_consumed_when_applied(codex_client, agent_type):
    client = codex_client
    role = create_node(client, 'core.minister-role')
    agent = create_node(client, agent_type)
    catalog = client.get('/api/catalog').json()
    spec = next(item for item in catalog['node_types'] if item['id'] == role['type'])
    assert 'core.agent' not in spec['traits'] and role['minister'] is None
    assert any(role['type'] in pack['cards'] for pack in catalog['packs'])
    result = client.post(f"/api/ministers/{agent['id']}/appoint", json={
        'source_id': role['id'], 'source_revision': role['revision'], 'expected_revision': agent['revision']})
    assert result.status_code == 200, result.text
    promoted = result.json()
    assert (promoted['id'], promoted['type'], promoted['config']) == (agent['id'], agent['type'], agent['config'])
    assert promoted['minister'] == {'control_radius': 1200, 'allow_canvas_edits': True}
    assert client.get(f"/api/nodes/{role['id']}").status_code == 404


def test_failed_or_stale_role_card_drop_preserves_both_cards(client, monkeypatch):
    from backend.errors import ResourceValidationError
    role = create_node(client, 'core.minister-role')
    agent = create_node(client, 'agent')
    request = {'source_id': role['id'], 'source_revision': role['revision'], 'expected_revision': agent['revision']}
    url = f"/api/ministers/{agent['id']}/appoint"
    assert client.post(url, json={**request, 'expected_revision': agent['revision'] + 1}).status_code == 409
    def failed_delete(*args, **kwargs):
        raise ResourceValidationError('simulated failed consumption')
    monkeypatch.setattr(client.app.state.services.world, 'delete_cards', failed_delete)
    assert client.post(url, json=request).status_code == 422
    assert client.get(f"/api/nodes/{agent['id']}").json()['minister'] is None
    assert client.get(f"/api/nodes/{role['id']}").status_code == 200


def test_deck_role_applies_without_creating_a_standalone_agent(client):
    agent = create_node(client, 'agent')
    before = {card['id'] for card in client.get('/api/nodes').json()}
    assert client.post(f"/api/ministers/{agent['id']}/appoint", json={'expected_revision': agent['revision']}).status_code == 200
    assert {card['id'] for card in client.get('/api/nodes').json()} == before


def test_promotion_preserves_identity_and_adds_to_existing_tools(client):
    agent = create_node(client, 'agent', name='My worker', config={'system_instruction': 'Keep my instructions.', 'model': 'my-model'})
    note = create_node(client, 'text')
    client.post('/api/edges', json={'source': agent['id'], 'target': note['id'], 'relationship': 'read'})
    services = client.app.state.services
    original = services.run_manager._agent_config(services.world.get_card(agent['id']))
    normal = {cap.id for cap in services.capabilities.derive(agent['id']).capabilities}
    assert normal and not any(key.startswith('minister.') for key in normal)
    assert client.get(f"/api/ministers/{agent['id']}/world").status_code == 403
    promoted = client.patch(f"/api/nodes/{agent['id']}", json={'minister': {}}).json()
    assert (promoted['id'], promoted['type'], promoted['name'], promoted['config']) == (agent['id'], agent['type'], agent['name'], agent['config'])
    capabilities = {cap.id for cap in services.capabilities.derive(agent['id']).capabilities}
    assert normal < capabilities
    config = services.run_manager._agent_config(services.world.get_card(agent['id']))
    assert config.model == original.model and config.runtime_provider_id == original.runtime_provider_id
    assert original.system_instruction in config.system_instruction and INSTRUCTION in config.system_instruction
    assert client.patch(f"/api/nodes/{agent['id']}", json={'minister': None}).status_code == 200
    assert services.run_manager._agent_config(services.world.get_card(agent['id'])) == original
    assert {cap.id for cap in services.capabilities.derive(agent['id']).capabilities} == normal


def test_role_requires_an_agent_and_is_not_a_creatable_type(client):
    note = create_node(client, 'text')
    assert client.patch(f"/api/nodes/{note['id']}", json={'minister': {}}).status_code == 422
    assert client.post('/api/nodes', json={'type': 'text', 'minister': {}}).status_code == 422
    assert client.post('/api/nodes', json={'type': 'core.minister'}).status_code == 422
    assert 'core.minister' not in {item['id'] for item in client.get('/api/catalog').json()['node_types']}


def test_live_removal_revokes_cached_scope_and_preserves_chat(client):
    agent = create_minister(client, config={'control_radius': 650, 'allow_canvas_edits': False})
    services = client.app.state.services
    chat = client.post(f"/api/ministers/{agent['id']}/chat").json()
    facade = control(services, agent['id'])
    updated = client.patch(f"/api/nodes/{agent['id']}", json={'minister': {'allow_canvas_edits': True}}).json()
    assert updated['minister'] == {'control_radius': 650, 'allow_canvas_edits': True}
    client.patch(f"/api/nodes/{agent['id']}", json={'minister': None})
    with pytest.raises(PermissionDeniedError):
        call(client, facade.query)
    assert client.get(f"/api/nodes/{chat['conversation_id']}").status_code == 200
    client.patch(f"/api/nodes/{agent['id']}", json={'minister': {}})
    assert client.post(f"/api/ministers/{agent['id']}/chat").json() == chat


def test_role_does_not_enter_strict_plugin_configuration(client):
    class StrictConfig(BaseModel):
        model_config = ConfigDict(extra='forbid')
        runtime_provider_id: str = 'core.mock'
        model: str = 'plugin-owned-model'
        system_instruction: str = 'The plugin owns these instructions.'
    services = client.app.state.services
    from dataclasses import replace
    from backend.tests.plugin_support import install_test_plugin
    install_test_plugin(services.plugins, 'strict', lambda registration: registration.register_node_type(
        replace(services.plugins.node_type('agent'), id='strict.agent', config_model=StrictConfig, templateable=False, template_status=None, template_handler=None)))
    agent = create_node(client, 'strict.agent')
    promoted = client.patch(f"/api/nodes/{agent['id']}", json={'minister': {}})
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()['config'] == agent['config']
    assert client.get(f"/api/ministers/{agent['id']}/world").status_code == 200
    assert services.run_manager._agent_config(services.world.get_card(agent['id'])).model == 'plugin-owned-model'


def test_legacy_migration_preserves_ids_config_equipment_and_persists(tmp_path):
    settings = Settings.for_data_root(tmp_path / 'legacy-world')
    with TestClient(create_app(settings)) as client:
        agent = create_minister(client, config={'model': 'kept-model', 'system_instruction': LEGACY_INSTRUCTION})
        chat = client.post(f"/api/ministers/{agent['id']}/chat").json()
        services = client.app.state.services
        legacy_config = {**agent['config'], 'control_radius': 750, 'allow_canvas_edits': False}
        with services.database.transaction(immediate=True) as db:
            db.execute("UPDATE cards SET type='core.minister', config_json=?, minister_json=NULL WHERE id=?", (json.dumps(legacy_config), agent['id']))
        # Simulate the old persisted representation, then run the startup migration.
        migrate_legacy_ministers(services.database, services.plugins)
        first = services.world.get_card(agent['id'])
        migrate_legacy_ministers(services.database, services.plugins)
        assert services.world.get_card(agent['id']) == first  # idempotent
        assert first.type == 'agent' and first.config['model'] == 'kept-model'
        assert 'control_radius' not in first.config
    with TestClient(create_app(settings)) as restarted:
        saved = restarted.get(f"/api/nodes/{agent['id']}").json()
        assert saved['minister'] == {'control_radius': 750, 'allow_canvas_edits': False}
        assert saved['id'] == agent['id'] and saved['type'] == 'agent'
        assert restarted.post(f"/api/ministers/{agent['id']}/chat").json() == chat
