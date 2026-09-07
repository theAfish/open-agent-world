from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.tests.test_summoning import equip, stock, invoke, settle
from backend.errors import PluginCompatibilityError


def test_equipped_conversation_has_live_participation_and_fresh_copied_sessions(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path), agent_runtime='core.mock')
    with TestClient(create_app(settings)) as client:
        owner = create_node(client, 'agent')
        room = equip(client, create_node(client, 'conversation'), owner)
        summary = client.get(f"/api/conversations/{room['id']}").json()
        assert any(agent['id'] == owner['id'] and agent['connected'] for agent in summary['agents'])
        session = client.post(f"/api/conversations/{room['id']}/sessions", json={'title': 'Private work', 'participant_ids': [owner['id']]})
        assert session.status_code == 201, session.text
        messages = f"/api/conversations/{room['id']}/sessions/{session.json()['id']}/messages"
        response = client.post(messages, json={'content': 'Saved private history'})
        assert response.status_code == 202, response.text
        copied = client.post(f"/api/nodes/{owner['id']}/duplicate")
        assert copied.status_code == 200, copied.text
        new_room = next(card for card in copied.json()['nodes'] if card['type'] == 'conversation')
        fresh = client.get(f"/api/conversations/{new_room['id']}").json()
        assert len(fresh['sessions']) == 1 and fresh['sessions'][0]['title'] == 'General'
        assert client.get(f"/api/conversations/{new_room['id']}/sessions/{fresh['sessions'][0]['id']}/messages").json() == []
        assert client.patch(f"/api/nodes/{room['id']}", json={'equipment': None}).status_code == 200
        denied = client.post(messages, json={'content': 'Revoked', 'mention_agent_ids': [owner['id']]})
        assert denied.status_code == 403, denied.text
        assert not client.get(f"/api/agents/{owner['id']}/capabilities").json()['capabilities']


@pytest.mark.asyncio
async def test_plugin_connection_rules_alone_enable_equipment_even_without_templates(tmp_path):
    from backend.plugins import RelationshipDefinition
    from backend.plugins.loader import load_plugin_registry
    from backend.tests.plugin_support import install_test_plugin
    from backend.world.models import CardCreate
    registry = load_plugin_registry()
    node = replace(registry.node_type('text'), id='example.device', traits=frozenset({'example.device'}),
                   templateable=False, template_handler=None, lifecycle=None, creation_fields=frozenset())
    def register(registration):
        registration.register_node_type(node)
        registration.register_relationship(RelationshipDefinition(id='example.signal', label='Signal', short_label='signal',
            description='A reverse-oriented plugin relationship', source_types=frozenset({node.id}),
            target_types=frozenset({'agent'}), directions=frozenset({'bidirectional'})))
    install_test_plugin(registry, 'example.device', register)
    services = create_services(Settings.for_data_root(tmp_path), plugins=registry)
    try:
        owner = await services.create_card(CardCreate(type='agent'))
        resource = await services.create_card(CardCreate(type=node.id, equipment={'owner_id': owner.id}))
        connection = services.world.connections_to(owner.id)[0]
        assert connection.source == resource.id and connection.relationship == 'example.signal'
        assert connection.direction == 'bidirectional'
        assert 'equipment' not in registry.node_type(node.id).catalog_item('example.device').model_dump()
    finally:
        services.close()


def test_equipment_persistence_duplicate_documents_and_private_workspaces(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path / 'world'), agent_runtime='core.mock', sandbox_runtime='auto')
    with TestClient(create_app(settings)) as client:
        agent = create_node(client, 'agent', config={'model': 'test-model', 'system_instruction': 'Private worker'})
        note = equip(client, create_node(client, 'text', content='private definition'), agent)
        sandbox = equip(client, create_node(client, 'sandbox'), agent)
        shared = create_node(client, 'text', content='shared')
        client.post('/api/edges', json={'source': agent['id'], 'target': shared['id'], 'relationship': 'read'})
        original_path = client.get(f"/api/sandboxes/{sandbox['id']}").json()['workspace']
        Path(original_path).mkdir(parents=True, exist_ok=True)
        Path(original_path, 'active.txt').write_text('do not copy', encoding='utf-8')
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/nodes/{note['id']}").json()['equipment']['owner_id'] == agent['id']
        result = client.post(f"/api/nodes/{agent['id']}/duplicate")
        assert result.status_code == 200, result.text
        nodes = result.json()['nodes']
        copied_agent = next(n for n in nodes if n['type'] == 'agent')
        copied_note = next(n for n in nodes if n['type'] == 'text')
        copied_sandbox = next(n for n in nodes if n['type'] == 'sandbox')
        assert copied_agent['config']['model'] == 'test-model'
        assert copied_agent['config']['system_instruction'] == 'Private worker'
        assert copied_note['equipment']['owner_id'] == copied_agent['id']
        services = client.app.state.services
        assert services.capabilities.read_text(copied_agent['id'], copied_note['id']).content == 'private definition'
        assert {c.target_id for c in services.capabilities.derive(copied_agent['id']).capabilities} == {copied_note['id'], copied_sandbox['id'], shared['id']}
        fresh = client.get(f"/api/sandboxes/{copied_sandbox['id']}").json()['workspace']
        assert fresh != original_path and not Path(fresh, 'active.txt').exists()
        assert client.delete(f"/api/nodes/{copied_agent['id']}").status_code == 200
        assert client.get(f"/api/nodes/{copied_note['id']}").status_code == 404
        assert not Path(fresh).exists()
        assert client.get(f"/api/nodes/{shared['id']}").status_code == 200


def test_equipment_rejects_invalid_ownership_and_batch_cycles(client):
    agent = create_node(client, 'agent')
    other = create_node(client, 'agent')
    toolbox = create_node(client, 'oaw.skills')
    resource = create_node(client, 'text')
    assert client.patch(f"/api/nodes/{agent['id']}", json={'equipment': {'owner_id': agent['id']}}).status_code == 422
    assert client.patch(f"/api/nodes/{resource['id']}", json={'equipment': {'owner_id': toolbox['id'], 'relationship':'read_edit'}}).status_code == 422
    assert client.patch(f"/api/nodes/{resource['id']}", json={'equipment': {'owner_id': agent['id'], 'relationship':'execute'}}).status_code == 422
    box = create_node(client, 'oaw.barracks')
    assert client.patch(f"/api/nodes/{agent['id']}", json={'parent_id':box['id']}).status_code == 200
    assert client.patch(f"/api/nodes/{resource['id']}", json={'parent_id':box['id'], 'equipment': {'owner_id': agent['id'], 'relationship':'read_edit'}}).status_code == 422


def test_equipment_cannot_connect_to_owner_but_can_connect_to_other_agents(client):
    owner = create_node(client, 'agent')
    other = create_node(client, 'agent')
    sandbox = equip(client, create_node(client, 'sandbox'), owner)
    toolbox = equip(client, create_node(client, 'oaw.skills'), owner)
    skill = create_node(client, 'oaw.skills.skill', parent_id=toolbox['id'])
    for resource, relationship in [(sandbox, 'execute'), (skill, 'oaw.skills.use')]:
        for source, target in [(owner, resource), (resource, owner)]:
            response = client.post('/api/edges', json={'source': source['id'], 'target': target['id'], 'relationship': relationship})
            assert response.status_code == 422
            assert 'already belongs' in response.text
    response = client.post('/api/edges', json={'source': other['id'], 'target': sandbox['id'], 'relationship': 'execute'})
    assert response.status_code == 201, response.text


def test_equipped_toolbox_restores_member_documents(client):
    agent = create_node(client, 'agent')
    toolbox = equip(client, create_node(client, 'oaw.skills'), agent)
    skill = create_node(client, 'oaw.skills.skill', parent_id=toolbox['id'])
    services = client.app.state.services
    from backend.node_documents import read_document, write_document
    current = read_document(services, skill['id'])
    write_document(services, skill['id'], {**current['value'], 'name':'Research', 'instructions':'Read primary sources'}, current['revision'])
    response = client.post(f"/api/nodes/{agent['id']}/duplicate")
    assert response.status_code == 200, response.text
    nodes = response.json()['nodes']
    new_box = next(n for n in nodes if n['type'] == 'oaw.skills')
    new_skill = next(n for n in nodes if n['type'] == 'oaw.skills.skill')
    assert new_skill['parent_id'] == new_box['id']
    assert read_document(services, new_skill['id'])['value']['instructions'] == 'Read primary sources'


def test_chunk_snapshot_follows_private_ownership(client):
    agent = create_node(client, 'agent', position={'x':100,'y':100})
    resource = equip(client, create_node(client, 'text', position={'x':50000,'y':50000}), agent)
    snapshot = client.get('/api/world?chunks=0:0').json()
    assert {agent['id'], resource['id']} <= {n['id'] for n in snapshot['nodes']}


def test_reclaim_uses_current_ownership_and_preserves_unequipped_cards(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path), agent_runtime='core.mock')
    with TestClient(create_app(settings)) as client:
        box = create_node(client, 'oaw.barracks')
        agent = stock(client, box, create_node(client, 'agent'))
        equip(client, create_node(client, 'text', content='private'), agent)
        shared = create_node(client, 'text')
        client.post('/api/edges', json={'source':agent['id'],'target':shared['id'],'relationship':'read'})
        instance = settle(client, box, invoke(client, box, action='summon', agent_id=agent['id'], prompt='Work'))
        detached = next(key for key in instance['node_ids'] if key != instance['entry_agent_id'])
        assert client.patch(f'/api/nodes/{detached}', json={'equipment':None}).status_code == 200
        extra = equip(client, create_node(client, 'text'), {'id':instance['entry_agent_id']})
        invoke(client, box, action='reclaim', instance_id=instance['id'])
        assert client.get(f"/api/nodes/{extra['id']}").status_code == 404
        assert client.get(f'/api/nodes/{detached}').status_code == 200
        assert client.get(f"/api/nodes/{shared['id']}").status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('templates', [[], [{'name': 'Saved worker'}], None])
async def test_startup_normalizes_only_empty_legacy_libraries(tmp_path, templates):
    from backend.node_documents import read_document
    from backend.world.models import CardCreate
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings)
    box = await services.create_card(CardCreate(type='oaw.barracks'))
    agent = await services.create_card(CardCreate(type='agent', parent_id=box.id))
    document = read_document(services, box.id)['value']
    scope = services.state.ensure_scope('node_document', box.id, schema_id='core.node_document')
    services.state.set(scope, 'document', {**document, 'templates': templates})
    services.close()
    if templates != []:
        with pytest.raises(PluginCompatibilityError, match='requires migration'):
            create_services(settings)
        with sqlite3.connect(settings.database_path) as db:
            import json
            raw = db.execute("SELECT value_json FROM state_values WHERE scope_id=? AND key='document'", (scope.scope_id,)).fetchone()[0]
            assert json.loads(raw) == {**document, 'templates': templates}
        return
    for _ in range(2):
        services = create_services(settings)
        try:
            assert read_document(services, box.id)['value'] == document
            assert services.world.get_card(agent.id).parent_id == box.id
        finally:
            services.close()


@pytest.mark.asyncio
async def test_legacy_single_agent_migration_is_explicit_and_preserves_source(tmp_path):
    from backend.migrations.barracks import migrate
    from backend.world.models import CardCreate
    source = tmp_path / 'legacy'
    settings = Settings.for_data_root(source)
    services = create_services(settings)
    box = await services.create_card(CardCreate(type='oaw.barracks'))
    entry = await services.create_card(CardCreate(type='agent', name='Archived worker'))
    resource = await services.create_card(CardCreate(type='text', content='Saved private text', equipment={'owner_id':entry.id,'relationship':'read_edit'}))
    blueprint, entry_key, _ = await services.summoning.definition(entry)
    # Reconstruct an actual old document: private resources were ordinary copied nodes.
    raw_blueprint = blueprint.model_dump(mode='json')
    for node in raw_blueprint['nodes']:
        node.pop('owner_key'); node.pop('equipment_relationship')
    legacy = await services.create_card(CardCreate(type='agent', name='Saved worker', parent_id=box.id))
    document = {'id':'legacy-template', 'node_id':None, 'name':'Saved worker', 'description':'Useful worker', 'entry_agent_key':entry_key,
                'blueprint':raw_blueprint, 'bindings':[], 'policy':{}}
    scope = services.state.ensure_scope('node_document', legacy.id, schema_id='core.node_document')
    services.state.set(scope, 'document', document)
    with services.database.transaction() as db:
        db.execute("UPDATE cards SET type='oaw.barracks.template',plugin_id='oaw.barracks',config_json='{}' WHERE id=?",(legacy.id,))
    services.close()
    with pytest.raises(PluginCompatibilityError, match='requires migration'):
        create_services(settings)
    output = tmp_path / 'migrated'
    await migrate(source, output)
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT type FROM cards WHERE id=?',(legacy.id,)).fetchone()[0] == 'oaw.barracks.template'
    migrated = create_services(Settings.for_data_root(output))
    try:
        assert migrated.world.get_card(legacy.id).type == 'agent'
        assert migrated.world.get_card(legacy.id).parent_id == box.id
        items = migrated.world.equipment_for(legacy.id)
        assert len(items) == 1
        assert migrated.capabilities.read_text(legacy.id, items[0].id).content == 'Saved private text'
        assert (output / 'legacy-barracks-archive.json').exists()
        assert not (output / 'MIGRATION_INCOMPLETE').exists()
    finally:
        migrated.close()
