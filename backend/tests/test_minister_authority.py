"""Real services and scoped runtime tools; no production model calls."""
import pytest

from backend.errors import PermissionDeniedError, RevisionConflictError
from backend.minister import MINISTER_TYPE, INSTRUCTION, PREVIOUS_INSTRUCTION, runtime_instruction
from backend.tests.conftest import create_node
from backend.tests.test_minister import invoke


def versions(client, minister):
    return invoke(client, minister, 'inspect')['versions']


def approve(client, minister, proposal, expected=200):
    response = client.post(f"/api/ministers/{minister['id']}/proposals/{proposal['proposal_id']}", json={'approve': True})
    assert response.status_code == expected, response.text
    return response.json()


def test_normal_agent_administration_and_batch_are_autonomous(client):
    minister = create_node(client, MINISTER_TYPE)
    assert minister['config']['allow_canvas_edits'] is True
    agent = invoke(client, minister, 'create', type='agent', name='Chat helper', position={'x': 130, 'y': 0},
                   config={'system_instruction': 'Help the user with their questions.'}, versions=versions(client, minister))
    assert agent['type'] == 'agent' and 'id' in agent
    chat = invoke(client, minister, 'create', type='conversation', name='Chat area', position={'x': -240, 'y': 0},
                  config={'description': 'Chat here'}, versions=versions(client, minister))
    updated = invoke(client, minister, 'update', updates=[
        {'node_id': agent['id'], 'patch': {'size': {'width': 120, 'height': 120}, 'position': {'x': 200, 'y': 40}, 'config': {'system_instruction': 'Be brief.'}}},
        {'node_id': chat['id'], 'patch': {'name': 'Ready chat'}}], versions=versions(client, minister))
    assert len(updated) == 2
    pair = invoke(client, minister, 'inspect', source_id=agent['id'], target_id=chat['id'])
    assert pair['connection_options'][0]['risk'] == 'ALLOW'
    invoke(client, minister, 'connect', source=agent['id'], target=chat['id'], relationship='participate', versions=pair['versions'])
    view = invoke(client, minister, 'inspect', query=chat['id'])
    assert view['nodes'][0]['chat_readiness']['routing_ready']
    assert not view['nodes'][0]['chat_readiness']['reply_observed']
    assert runtime_instruction(PREVIOUS_INSTRUCTION) == INSTRUCTION
    assert 'Before changing the canvas' in PREVIOUS_INSTRUCTION
    assert 'You cannot change your own' in PREVIOUS_INSTRUCTION


def test_delete_requires_actual_effect_review_and_one_use_approval(client):
    minister = create_node(client, MINISTER_TYPE)
    agent = create_node(client, 'agent', size={'width': 96, 'height': 96})
    note = create_node(client, 'text', name='Attached notes', size={'width': 96, 'height': 96},
                       equipment={'owner_id': agent['id'], 'relationship': 'read'})
    chat = create_node(client, 'conversation', size={'width': 96, 'height': 96})
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': chat['id'], 'relationship': 'participate'}).json()
    proposal = invoke(client, minister, 'delete', node_ids=[agent['id']], versions=versions(client, minister))
    assert proposal['status'] == 'confirmation_required'
    assert {item['name'] for item in proposal['changes']} == {agent['name'], note['name']}
    assert any(item['target'] == chat['id'] for item in proposal['connections'])
    assert client.get(f"/api/nodes/{agent['id']}").status_code == 200
    approve(client, minister, proposal)
    assert client.get(f"/api/nodes/{agent['id']}").status_code == 404
    assert client.get(f"/api/nodes/{note['id']}").status_code == 404
    assert client.get(f"/api/nodes/{chat['id']}").status_code == 200
    approve(client, minister, proposal, 409)


@pytest.mark.parametrize('change', ['human_edit', 'new_edge', 'scope', 'pause', 'other_minister'])
def test_approval_revalidates_scope_graph_and_revisions(client, change):
    minister = create_node(client, MINISTER_TYPE)
    note = create_node(client, 'text', size={'width': 96, 'height': 96})
    agent = create_node(client, 'agent', size={'width': 96, 'height': 96})
    proposal = invoke(client, minister, 'delete', node_ids=[note['id']], versions=versions(client, minister))
    if change == 'human_edit':
        client.patch(f"/api/nodes/{note['id']}", json={'name': 'Keep my work'})
    elif change == 'new_edge':
        client.post('/api/edges', json={'source': agent['id'], 'target': note['id'], 'relationship': 'read'})
    elif change in {'scope', 'pause'}:
        client.patch(f"/api/nodes/{minister['id']}", json={'config': {'control_radius': 450} if change == 'scope' else {'allow_canvas_edits': False}})
    else:
        other = create_node(client, MINISTER_TYPE)
        invoke(client, other, 'rename', node_id=note['id'], name='Another actor', versions=versions(client, other))
    approve(client, minister, proposal, 409)
    assert client.get(f"/api/nodes/{note['id']}").status_code == 200


def test_sensitive_configuration_and_capability_grants_are_confirmable(client):
    minister = create_node(client, MINISTER_TYPE)
    agent = create_node(client, 'agent', size={'width': 96, 'height': 96})
    sandbox = create_node(client, 'sandbox', size={'width': 96, 'height': 96})
    proposal = invoke(client, minister, 'update', updates=[{'node_id': sandbox['id'], 'patch': {'config': {'network_enabled': True}}}], versions=versions(client, minister))
    assert proposal['risk'] == 'CONFIRM'
    assert not client.get(f"/api/nodes/{sandbox['id']}").json()['config']['network_enabled']
    approve(client, minister, proposal)
    assert client.get(f"/api/nodes/{sandbox['id']}").json()['config']['network_enabled']
    view = invoke(client, minister, 'inspect', source_id=agent['id'], target_id=sandbox['id'])
    assert all(item['risk'] == 'CONFIRM' for item in view['connection_options'])
    proposal = invoke(client, minister, 'connect', source=agent['id'], target=sandbox['id'], relationship='execute_manage', versions=view['versions'])
    assert any('start' in item.lower() for item in proposal['connections'][0]['capabilities'])
    assert not client.app.state.services.world.connections_from(agent['id'])
    approve(client, minister, proposal)
    assert client.app.state.services.world.connections_from(agent['id'])[0].relationship == 'execute_manage'


def test_no_secret_or_authority_bypass_even_with_confirmation_arguments(client):
    minister = create_node(client, MINISTER_TYPE)
    agent = create_node(client, 'agent', size={'width': 96, 'height': 96}, config={'api_key': 'stored-secret'})
    other = create_node(client, MINISTER_TYPE)
    for target, patch in [(agent, {'config': {'api_key': 'submitted-secret'}}),
                          (agent, {'config': {'status': 'running'}}),
                          (minister, {'config': {'control_radius': 3000}}),
                          (other, {'config': {'allow_canvas_edits': True, 'control_radius': 2500}}),
                          (other, {'position': {'x': 200, 'y': 200}})]:
        with pytest.raises(PermissionDeniedError):
            invoke(client, minister, 'update', updates=[{'node_id': target['id'], 'patch': patch}], versions=versions(client, minister))
    with pytest.raises(PermissionDeniedError):
        invoke(client, minister, 'create', type=MINISTER_TYPE, name='Escalation', position={}, versions=versions(client, minister))
    assert 'stored-secret' not in str(invoke(client, minister, 'inspect'))
    assert client.get(f"/api/ministers/{minister['id']}/proposals").json() == []


def test_group_attach_detach_glue_and_scope_effects(client):
    minister = create_node(client, MINISTER_TYPE, config={'control_radius': 1500})
    agent = create_node(client, 'agent', size={'width': 96, 'height': 96}, position={'x': 0, 'y': 0})
    note = create_node(client, 'text', size={'width': 96, 'height': 96}, position={'x': 200, 'y': 0})
    invoke(client, minister, 'organize', operation='glue', node_ids=[agent['id']], target_id=note['id'], versions=versions(client, minister))
    glued = client.get('/api/canvas/glue').json()
    assert len(glued['bonds']) == 1
    observed = versions(client, minister)
    invoke(client, minister, 'move', node_id=agent['id'], position={'x': 300, 'y': 0}, versions=observed)
    assert client.get(f"/api/nodes/{note['id']}").json()['position']['x'] == 396
    with pytest.raises(RevisionConflictError):
        invoke(client, minister, 'move', node_id=agent['id'], position={}, versions=observed)
    invoke(client, minister, 'organize', operation='unglue', node_ids=[agent['id']], versions=versions(client, minister))
    assert not client.get('/api/canvas/glue').json()['bonds']
    invoke(client, minister, 'organize', operation='attach', node_ids=[note['id']], target_id=agent['id'], relationship='read', versions=versions(client, minister))
    assert client.get(f"/api/nodes/{note['id']}").json()['equipment']['owner_id'] == agent['id']
    invoke(client, minister, 'organize', operation='detach', node_ids=[note['id']], versions=versions(client, minister))
    assert client.get(f"/api/nodes/{note['id']}").json()['equipment'] is None
    group = invoke(client, minister, 'organize', operation='group', name='Chat team', node_ids=[agent['id'], note['id']], versions=versions(client, minister))
    assert group[0]['type'] == 'legion'
    assert client.get(f"/api/nodes/{agent['id']}").json()['parent_id'] == group[0]['id']
    invoke(client, minister, 'organize', operation='ungroup', node_ids=[agent['id'], note['id']], versions=versions(client, minister))
    assert client.get(f"/api/nodes/{agent['id']}").json()['parent_id'] is None


def test_review_detects_resource_edits_without_reading_content(client):
    minister = create_node(client, MINISTER_TYPE)
    note = create_node(client, 'text', content='Private content stays private', size={'width': 96, 'height': 96})
    proposal = invoke(client, minister, 'delete', node_ids=[note['id']], versions=versions(client, minister))
    assert proposal['resources'][0]['size_bytes'] == len('Private content stays private')
    assert 'Private content stays private' not in str(proposal)
    response = client.put(f"/api/resources/{note['id']}/text", json={'content': 'Keep this newer work'})
    assert response.status_code == 200, response.text
    approve(client, minister, proposal, 409)
    assert client.get(f"/api/resources/{note['id']}/text").json()['content'] == 'Keep this newer work'


def test_glue_scope_revision_and_resize_follow_all_peers(client):
    minister = create_node(client, MINISTER_TYPE)
    a = create_node(client, 'text', position={'x': 0, 'y': 0}, size={'width': 96, 'height': 96})
    b = create_node(client, 'text', position={'x': 200, 'y': 0}, size={'width': 96, 'height': 96})
    invoke(client, minister, 'organize', operation='glue', node_ids=[a['id']], target_id=b['id'], versions=versions(client, minister))
    invoke(client, minister, 'update', updates=[{'node_id': a['id'], 'patch': {'size': {'width': 140, 'height': 120}}}], versions=versions(client, minister))
    assert client.get(f"/api/nodes/{b['id']}").json()['position']['x'] == 244
    stale = versions(client, minister)
    glue = client.get('/api/canvas/glue').json()
    client.patch('/api/canvas/glue', json={'revision': glue['revision'], 'detach': [b['id']]})
    with pytest.raises(RevisionConflictError):
        invoke(client, minister, 'organize', operation='glue', node_ids=[a['id']], target_id=b['id'], versions=stale)
    # Reattach, then have the host move a peer beyond this scope. Neither a
    # layout nor a group/delete may silently change that external attachment.
    invoke(client, minister, 'organize', operation='glue', node_ids=[a['id']], target_id=b['id'], versions=versions(client, minister))
    client.patch(f"/api/nodes/{b['id']}", json={'position': {'x': 2000, 'y': 0}})
    for action, args in [('move', dict(node_id=a['id'], position={'x': 100, 'y': 0})), ('delete', dict(node_ids=[a['id']]))]:
        with pytest.raises(PermissionDeniedError):
            invoke(client, minister, action, **args, versions=versions(client, minister))


def test_validated_indirect_sensitive_changes_also_require_confirmation(client):
    from dataclasses import replace
    from pydantic import Field, model_validator
    from backend.world.models import AgentConfig
    from backend.tests.plugin_support import install_test_plugin
    services = client.app.state.services

    class Config(AgentConfig):
        host_permission: bool = Field(default=False, json_schema_extra={'privileged': True})

        @model_validator(mode='after')
        def computed_permission(self):
            if self.system_instruction == 'request elevated behavior':
                self.host_permission = True
            return self

    install_test_plugin(services.plugins, 'test.review', lambda registration: registration.register_node_type(
        replace(services.plugins.node_type('agent'), id='test.review.agent', config_model=Config)))
    minister = create_node(client, MINISTER_TYPE)
    proposal = invoke(client, minister, 'create', type='test.review.agent', name='Reviewed plugin Agent', position={},
        config={'system_instruction': 'request elevated behavior'}, versions=versions(client, minister))
    assert proposal['status'] == 'confirmation_required'
    assert proposal['changes'][0]['configuration']['host_permission'] is True
    assert not any(card.type == 'test.review.agent' for card in services.world.list_cards())
    approve(client, minister, proposal)
    assert next(card for card in services.world.list_cards() if card.type == 'test.review.agent').config['host_permission'] is True


def test_sandbox_creation_is_reviewable_and_shared_glue_survives_reload(tmp_path):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    settings = Settings.for_data_root(tmp_path / 'world')
    with TestClient(create_app(settings)) as client:
        minister = create_node(client, MINISTER_TYPE)
        proposal = invoke(client, minister, 'create', type='sandbox', name='Local workplace', position={}, versions=versions(client, minister))
        assert proposal['risk'] == 'CONFIRM'
        assert proposal['resources'][-1]['kind'] == 'sandbox defaults'
        approve(client, minister, proposal)
        a = create_node(client, 'text', position={'x': 100, 'y': 0}, size={'width': 96, 'height': 96})
        b = create_node(client, 'text', position={'x': 300, 'y': 0}, size={'width': 96, 'height': 96})
        invoke(client, minister, 'organize', operation='glue', node_ids=[a['id']], target_id=b['id'], versions=versions(client, minister))
        saved = client.get('/api/canvas/glue').json()
    with TestClient(create_app(settings)) as reopened:
        assert reopened.get('/api/canvas/glue').json() == saved
        assert reopened.get(f"/api/ministers/{minister['id']}/proposals").json() == []
