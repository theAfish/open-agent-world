import json

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.tests.conftest import create_node


def document(client, node):
    return client.get(f"/api/nodes/{node}/document").json()


def edit(client, node, action, arguments, revision=None):
    response = client.post(f"/api/nodes/{node}/actions/{action}", json={
        "arguments": arguments, "expected_revision": document(client, node)["revision"] if revision is None else revision})
    return response


def test_research_preset_deploys_complete_independent_workspaces_and_can_be_saved(client):
    presets = client.get('/api/legions/presets').json()
    preset = next(item for item in presets if item['id'] == 'matcreator.research')
    assert preset['preset'] and not preset['starter'] and preset['compatible'], preset
    instances = []
    for _ in range(2):
        response = client.post('/api/legions/presets/matcreator.research/instances', json={})
        assert response.status_code == 201, response.text
        instances.append(response.json())
    first, second = instances
    a, b = first['node_ids'], second['node_ids']
    assert set(a.values()).isdisjoint(b.values())
    nodes = {node['id']: node for node in first['nodes']}
    assert all(nodes[a[key]]['parent_id'] == a['group'] for key in a if key not in {'group', 'executor', 'summoning'})
    assert nodes[a['executor']]['parent_id'] == a['barracks']
    assert nodes[a['summoning']]['equipment']['owner_id'] == a['agent']
    assert nodes[a['sandbox']]['status'] == 'stopped'
    assert nodes[a['agent']]['config']['model'] == 'oaw:default'
    assert 'task board' in nodes[a['agent']]['config']['system_instruction']
    layout = nodes[a['group']]['config']['workspace_layout']
    layout_text = json.dumps(layout)
    assert all(a[key] in layout_text for key in ['conversation', 'sandbox', 'tasks', 'knowledge'])
    assert all(section in layout_text for section in ['sessions', 'files', 'conversation', 'preview'])
    assert {'card_id': a['sandbox']} in _views(layout['root'])
    assert nodes[a['structure']]['type'] == 'science.structure-viewer'
    lower_right = layout['root']['second']['second']['second']
    assert lower_right['kind'] == 'tabs'
    assert lower_right['views'] == [{'card_id': a['sandbox']}, {'card_id': a['structure']}]
    assert {(edge['source'], edge['target'], edge['relationship']) for edge in first['edges']
            if edge['source'] == a['structure']} == {
        (a['structure'], a[target], 'core.file-preview') for target in ('conversation', 'sandbox')}
    assert len(first['edges']) == 9
    assert not {'core', 'simulation', 'ai', 'research'} & a.keys()
    graph = document(client, a['knowledge'])['value']
    assert len(graph['snapshots']) == 4
    assert graph['skills']
    assert all(client.get(f"/api/nodes/{skill['node_id']}").json()['parent_id'] == a['knowledge'] for skill in graph['skills'])
    assert {skill['node_id'] for skill in graph['skills']}.isdisjoint(
        skill['node_id'] for skill in document(client, b['knowledge'])['value']['skills'])
    created = edit(client, a['tasks'], 'create_plan', {'title': 'Copper', 'session_id': 'source-session', 'tasks': [
        {'id': 'build', 'title': 'Build copper', 'status': 'done', 'result': '32 atoms', 'outputs': ['copper.xyz']}]})
    assert created.status_code == 200, created.text
    assert document(client, b['tasks'])['value']['plans'] == []
    saved = client.post('/api/legions', json={'name': 'My materials project', 'node_ids': list(a.values())})
    assert saved.status_code == 201, saved.text
    copy = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
    assert copy.status_code == 201, copy.text
    copied_graph = next(node for node in copy.json()['nodes'] if node['type'] == 'matcreator.kdg')
    copied_value = document(client, copied_graph['id'])['value']
    assert len(copied_value['skills']) == len(graph['skills'])
    assert copied_value['snapshots'] == graph['snapshots']
    assert {s['node_id'] for s in copied_value['skills']}.isdisjoint(s['node_id'] for s in graph['skills'])
    board = next(node for node in copy.json()['nodes'] if node['type'] == 'matcreator.tasks')
    plan = document(client, board['id'])['value']['plans'][0]
    assert plan['session_id'] == '' and plan['tasks'][0]['status'] == 'pending'
    assert plan['tasks'][0]['result'] == '' and plan['tasks'][0]['outputs'] == []
    group = next(node for node in copy.json()['nodes'] if node['type'] == 'legion')
    assert board['id'] in json.dumps(group['config']['workspace_layout'])
    viewer = next(node for node in copy.json()['nodes'] if node['type'] == 'science.structure-viewer')
    assert viewer['id'] in json.dumps(group['config']['workspace_layout'])
    assert len([edge for edge in copy.json()['edges'] if edge['source'] == viewer['id']]) == 2
    assert not any(node_id in json.dumps(group['config']['workspace_layout']) for node_id in a.values())


def _views(node):
    if node['kind'] == 'split':
        return _views(node['first']) + _views(node['second'])
    return node['views'] if node['kind'] == 'tabs' else [node['view']]


def test_task_dependencies_results_and_conflicts_are_enforced_atomically(client):
    board = create_node(client, 'matcreator.tasks')['id']
    response = edit(client, board, 'create_plan', {'title': 'Research', 'tasks': [
        {'id': 'build', 'title': 'Build structure'}, {'id': 'verify', 'title': 'Verify', 'depends_on': ['build']}]})
    assert response.status_code == 200, response.text
    plan_id = response.json()['value']['plans'][0]['id']
    before = document(client, board)
    for args in [
        {'task_id': 'verify', 'status': 'running'},
        {'task_id': 'build', 'depends_on': ['verify']},
        {'task_id': 'build', 'depends_on': ['missing']},
        {'task_id': 'build', 'status': 'done'},
    ]:
        result = edit(client, board, 'update_task', {'plan_id': plan_id, **args})
        assert result.status_code == 422, result.text
        assert document(client, board) == before
    assert edit(client, board, 'remove_task', {'plan_id': plan_id, 'task_id': 'build'}).status_code == 422
    assert edit(client, board, 'update_task', {'plan_id': plan_id, 'task_id': 'build', 'status': 'done',
        'result': 'Read output structure: 32 atoms', 'outputs': ['copper/structure.xyz']}).status_code == 200
    assert edit(client, board, 'update_task', {'plan_id': plan_id, 'task_id': 'verify', 'status': 'running'}, before['revision']).status_code == 409
    assert edit(client, board, 'update_task', {'plan_id': plan_id, 'task_id': 'verify', 'status': 'running'}).status_code == 200


def test_task_tools_respect_read_only_and_live_revocation(client):
    board = create_node(client, 'matcreator.tasks')['id']
    agent = create_node(client, 'agent')['id']
    edge = client.post('/api/edges', json={'source': agent, 'target': board, 'relationship': 'matcreator.tasks.view'}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    def invoke(name, args):
        return client.portal.call(provider.invoke_tool, agent, 'operation:task_board_' + name, {'board': board, **args})
    result = invoke('read', {})
    assert result['value']['plans'] == []
    with pytest.raises(PermissionDeniedError):
        invoke('create_plan', {'title': 'No grant', 'expected_revision': result['revision']})
    client.delete('/api/edges/' + edge['id'])
    edge = client.post('/api/edges', json={'source': agent, 'target': board, 'relationship': 'matcreator.tasks.manage'}).json()
    result = invoke('create_plan', {'title': 'Authorized research', 'expected_revision': result['revision']})
    assert result['value']['plans'][0]['title'] == 'Authorized research'
    client.delete('/api/edges/' + edge['id'])
    with pytest.raises(PermissionDeniedError):
        invoke('read', {})


def test_plugin_preset_is_available_in_decks_and_disabled_with_its_plugin(client):
    from backend.card_library import LibraryEdit
    services = client.app.state.services
    library = services.card_library
    state = library.read()
    state = library.edit(LibraryEdit(action='update_deck', id=state.active_deck_id, expected_revision=state.revision,
        entries=[{'kind': 'legion', 'id': 'matcreator.research'}]))
    assert any(e.id == 'matcreator.research' for d in state.decks for e in d.entries)
    services.plugins.set_enabled('matcreator', False)
    assert 'matcreator.research' not in {item['id'] for item in client.get('/api/legions/presets').json()}
    assert client.post('/api/legions/presets/matcreator.research/instances', json={}).status_code == 404


def test_invalid_plugin_presets_do_not_partially_install():
    from open_agent_world.plugin_api import LegionPresetDefinition, PresetNode, PresetEdge, PluginDefinition, PluginDescriptor
    from backend.plugins import create_builtin_registry
    registry = create_builtin_registry()
    def register(registration):
        registration.register_legion_preset(LegionPresetDefinition(id='example.research', name='Bad plan', nodes=(
            PresetNode(key='group', type='legion', name='Group', parent_key=None),
            PresetNode(key='agent', type='agent', name='Agent'),
        ), edges=(PresetEdge(source='agent', target='missing', relationship='participate'),)))
    with pytest.raises(ValueError, match='reference preset nodes'):
        registry.install(PluginDefinition(PluginDescriptor(id='example', version='1', plugin_api_version='1.18'), register))
    assert not registry.has_plugin('example')
    assert registry.legion_presets() == ()


def test_research_preset_requires_enabled_structure_viewer(client):
    services = client.app.state.services
    services.plugins.set_enabled('science.structure-viewer', False)
    assert 'matcreator.research' not in {item['id'] for item in client.get('/api/legions/presets').json()}
    services.plugins.set_enabled('science.structure-viewer', True)
    assert 'matcreator.research' in {item['id'] for item in client.get('/api/legions/presets').json()}


def test_research_board_survives_restart_and_scoped_tool_projection(tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.config import Settings
    from backend.agents.tools import build_scoped_tool_callables
    settings = Settings.for_data_root(tmp_path / 'research')
    with TestClient(create_app(settings)) as client:
        agent = create_node(client, 'agent')['id']
        board = create_node(client, 'matcreator.tasks')['id']
        client.post('/api/edges', json={'source': agent, 'target': board, 'relationship': 'matcreator.tasks.manage'})
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        definitions = client.portal.call(provider.list_tools, agent)
        callables = {tool.__name__: tool for tool in build_scoped_tool_callables(provider, agent, definitions)}
        revision = client.portal.call(lambda: callables['task_board_read'](board=board))['revision']
        result = client.portal.call(lambda: callables['task_board_create_plan'](board=board, title='Restart study',
            tasks=[{'id': 'inspect', 'title': 'Inspect inputs'}], expected_revision=revision))
        assert 'value' in result, result
        assert result['value']['plans'][0]['title'] == 'Restart study'
        assert client.portal.call(lambda: callables['task_board_read'](board=board))['value']['plan']['tasks'][0]['id'] == 'inspect'
    with TestClient(create_app(settings)) as client:
        assert document(client, board)['value']['plans'][0]['title'] == 'Restart study'
