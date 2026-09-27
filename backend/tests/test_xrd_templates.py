from oaw_xrd.templates import remap_config, capture_harness, remap_harness
from oaw_xrd import LibraryDocument, InputDocument
from backend.tests.test_legions import _create_node, _create_edge


def test_xrd_template_restores_fresh_references_and_keeps_configuration(client):
    owner = _create_node(client, 'xrd.match', config={'workflow_match_run_id': 'old-run', 'selected_candidate_ids': ['COD1'], 'iterations': 17})
    canvas = _create_node(client, 'xrd.spectrum-canvas', config={'source_node_id': owner['id']})
    agent = _create_node(client, 'agent')
    harness = _create_node(client, 'xrd.multiphase-harness', config={'owner_node_id': owner['id'], 'agent_node_id': agent['id'], 'next_options': {'budget': 10, 'max_phases': 3, 'evaluate_baseline': False, 'agent_node_id': agent['id']}})
    _create_edge(client, owner['id'], canvas['id'], 'xrd.frames')
    _create_edge(client, owner['id'], harness['id'], 'xrd.multiphase-control')
    _create_edge(client, agent['id'], harness['id'], 'xrd.multiphase-tools')
    response = client.post('/api/legions', json={'name': 'XRD portable', 'node_ids': [n['id'] for n in (owner, canvas, agent, harness)]})
    assert response.status_code == 201, response.text
    template = response.json()
    assert template['compatible']
    copies = []
    for _ in range(2):
        response = client.post(f"/api/legions/{template['id']}/instances", json={})
        assert response.status_code == 201, response.text
        nodes = {n['type']: n for n in response.json()['nodes']}
        fresh = nodes['xrd.match']
        assert fresh['config']['workflow_match_run_id'] == ''
        assert fresh['config']['selected_candidate_ids'] == []
        assert fresh['config']['iterations'] == 17
        assert nodes['xrd.spectrum-canvas']['config']['source_node_id'] == fresh['id']
        config = nodes['xrd.multiphase-harness']['config']
        assert config['owner_node_id'] == fresh['id']
        assert config['agent_node_id'] == nodes['agent']['id']
        assert config['next_options']['agent_node_id'] == nodes['agent']['id']
        assert config['next_options']['budget'] == 10
        assert config['next_options']['evaluate_baseline'] is False
        copies.append(fresh['id'])
    assert copies[0] != copies[1] != owner['id']


def test_harness_capture_discards_runtime_payload_and_remaps_options():
    value = {'run_id': 'old', 'state': {'status': 'completed', 'trials': [1]}, 'options': {'path': 'C:/local'},
             'next_options': {'budget': 10, 'max_phases': 3, 'agent_node_id': 'a', 'path': 'C:/private'}}
    captured = capture_harness(value)
    assert captured['run_id'] == '' and captured['state'] == {'status': 'idle'}
    assert captured['options'] == {} and 'path' not in captured['next_options']
    assert remap_harness(captured, {'a': 'new'})['next_options']['agent_node_id'] == 'new'
    assert value['run_id'] == 'old'


def test_documents_clear_machine_mounts_and_inputs(client):
    registry = client.app.state.services.plugins
    for kind, value in [('xrd.library', {'path': 'C:/private/library.db', 'slots': [{'path': 'C:/private'}]}),
                        ('xrd.pattern', {'kind': 'pattern', 'source_base64': 'private', 'filename': 'experiment.csv'})]:
        document = registry.node_type(kind).document
        captured = document.model.model_validate(document.capture(value)).model_dump()
        assert not captured.get('path') and not captured.get('slots') and not captured.get('source_base64')


def test_inputs_are_owned_by_canvas_and_match_and_legacy_cards_are_not_creatable(client, monkeypatch):
    import base64
    import oaw_xrd
    owner = _create_node(client, 'xrd.match')
    canvas = _create_node(client, 'xrd.spectrum-canvas', config={'source_node_id': owner['id']})
    doc = client.get(f"/api/nodes/{canvas['id']}/document").json()
    result = client.post(f"/api/nodes/{canvas['id']}/actions/import", json={'expected_revision':doc['revision'], 'arguments':{'filename':'pattern.csv', 'source_base64':base64.b64encode('\n'.join(f'{10+i},4' for i in range(25)).encode()).decode()}})
    assert result.status_code == 200, result.text
    assert len(result.json()['value']['points']) == 25
    monkeypatch.setattr(oaw_xrd, 'inspect_library', lambda path: {'kind':'library','path':path,'count':12,'filename':'library.db'})
    doc = client.get(f"/api/nodes/{owner['id']}/document").json()
    result = client.post(f"/api/nodes/{owner['id']}/actions/configure", json={'expected_revision':doc['revision'],'arguments':{'path':'user-library.db'}})
    assert result.status_code == 200, result.text
    assert result.json()['value']['slots'][0]['count'] == 12
    assert result.json()['value']['structure'] is None
    for kind in ('xrd.pattern','xrd.library'):
        assert client.post('/api/nodes',json={'type':kind}).status_code == 422


def test_runtime_reads_canvas_via_grant_and_only_its_own_library(client, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pytest
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    owner = _create_node(client, 'xrd.match')
    canvas = _create_node(client, 'xrd.spectrum-canvas')
    _create_edge(client, owner['id'], canvas['id'], 'xrd.frames')
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    monkeypatch.setattr(type(services), '_require_run_manager', lambda self: SimpleNamespace(current_context=SimpleNamespace(agent_id=owner['id'])))
    document = asyncio.run(provider.read_own_document(owner['id']))
    assert document['value']['kind'] == 'library'
    with pytest.raises(Exception, match='current agent run'):
        asyncio.run(provider.read_own_document(canvas['id']))
    # Use the public broker projection: spectrum access comes from the frame link.
    tools = asyncio.run(provider.list_tools(owner['id']))
    assert any(t.name == 'read_xrd_input' for t in tools)
    monkeypatch.undo()
