import json
from types import SimpleNamespace

import pytest

from backend.card_state import state_session
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.tests.conftest import create_node
from plugins.matcreator.oaw_matcreator.native_runtime import MatCreatorRuntime


def deploy(client):
    response = client.post('/api/legions/presets/matcreator.native-research/instances', json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_native_preset_is_the_only_research_legion_and_deploys_without_upstream(client):
    presets = {p['id']: p for p in client.get('/api/legions/presets').json()}
    assert 'matcreator.research' not in presets
    assert presets['matcreator.native-research']['revision'] == 4
    catalog = client.get('/api/catalog').json()
    assert 'matcreator.tasks' not in {node['id'] for node in catalog['node_types']}
    assert presets['matcreator.native-research']['compatible']
    instance = deploy(client)
    ids = instance['node_ids']
    nodes = {n['id']: n for n in instance['nodes']}
    assert set(ids) == {'group', 'agent', 'conversation', 'sandbox', 'knowledge', 'structure', 'artifacts', 'execution', 'jobs'}
    assert nodes[ids['jobs']]['type'] == 'core.jobs'
    assert nodes[ids['jobs']]['state_scope'] == 'session'
    assert any(edge['source'] == ids['jobs'] and edge['target'] == ids['agent']
               and edge['relationship'] == 'core.jobs-manage' for edge in instance['edges'])
    agent = nodes[ids['agent']]
    assert agent['type'] == 'matcreator.agent'
    assert agent['config']['runtime_provider_id'] == 'matcreator.native'
    assert agent['config']['model'] == 'oaw:default'
    assert nodes[ids['sandbox']]['status'] == 'stopped'
    assert nodes[ids['execution']]['state_scope'] == 'session'
    info = client.get(f"/api/agents/{agent['id']}")
    assert info.status_code == 200, info.text
    assert info.json()['details']['engine'] == 'Pack-owned MatCreator'
    saved = client.post('/api/legions', json={'name': 'Native copy', 'node_ids': list(ids.values())})
    assert saved.status_code == 201, saved.text
    copy = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
    assert copy.status_code == 201, copy.text
    assert set(ids.values()).isdisjoint(n['id'] for n in copy.json()['nodes'])


def test_native_projection_is_session_scoped_and_bridge_rechecks_revocation(client):
    instance = deploy(client)
    ids = instance['node_ids']
    conversation = client.get('/api/conversations/' + ids['conversation']).json()
    a = conversation['sessions'][0]['id']
    b = client.post('/api/conversations/' + ids['conversation'] + '/sessions', json={'title': 'B'}).json()['id']
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    runtime = MatCreatorRuntime(provider)

    async def project():
        from backend.runs.manager import _current_invocation
        manager = client.app.state.services.run_manager
        run = manager.store.create(agent_id=ids['agent'], runtime_provider_id='matcreator.native',
                                   caller_kind='conversation', caller_id=ids['conversation'], context_id=a)
        token = _current_invocation.set(SimpleNamespace(agent_id=ids['agent'], run_id=run.run_id))
        with state_session(a):
            try:
                async with client.app.state.services.events.subscribe() as notifications:
                    await runtime._snapshot(ids['agent'], run.run_id, {'phase': 'running', 'goal': 'Copper', 'graph': {}, 'agents': []})
                    event = notifications.get_nowait()
                    assert event.type == 'execution_updated'
                    assert (event.node_id, event.run_id, event.session_id, event.conversation_id) == (ids['agent'], run.run_id, a, ids['conversation'])
                    # Notifications carry invalidation metadata, never graph content.
                    assert set(event.payload) == {'history_id', 'revision'}
                    assert event.payload['revision'] == 1
                    assert manager.get_run(run.run_id).lifecycle['execution_graph']['title'] == 'Copper'
            finally:
                _current_invocation.reset(token)

    client.portal.call(project)
    url = '/api/nodes/' + ids['execution'] + '/execution-sources/' + ids['agent']
    assert client.get(url, headers={'X-OAW-State-Session': a}).json()['title'] == 'Copper'
    assert client.get(url, headers={'X-OAW-State-Session': b}).json()['title'] == ''
    edge = next(e for e in instance['edges'] if e['source'] == ids['execution'])
    assert client.delete('/api/edges/' + edge['id']).status_code == 200
    assert client.get(url, headers={'X-OAW-State-Session': a}).status_code == 403
    tools = client.portal.call(runtime._bridge, ids['agent'], {'method': 'list_tools'})
    assert 'matcreator_execution_snapshot' not in {t['name'] for t in tools}
    knowledge = next(t for t in tools if t['name'] == 'knowledge_search')
    edge = next(e for e in instance['edges'] if e['target'] == ids['knowledge'])
    assert client.delete('/api/edges/' + edge['id']).status_code == 200
    result = client.portal.call(runtime._bridge, ids['agent'], {'method': 'invoke_tool',
        'capability_id': knowledge['capability_id'], 'arguments': {'knowledge': ids['knowledge'], 'query': 'copper'}})
    assert result['ok'] is False
    assert result['error']['code'] == 'permission_denied'


def test_native_template_drops_machine_paths_and_snapshot(client):
    instance = deploy(client)
    ids = instance['node_ids']
    patched = client.patch('/api/nodes/' + ids['agent'], json={'config': {
        'python_executable': 'D:/private/python.exe', 'source_directory': 'D:/private/MatCreator', 'agent_mode': 'flash'}})
    assert patched.status_code == 200, patched.text
    saved = client.post('/api/legions', json={'name': 'Portable', 'node_ids': list(ids.values())})
    assert saved.status_code == 201, saved.text
    assert 'D:/private' not in json.dumps(saved.json())
    copy = client.post('/api/legions/' + saved.json()['id'] + '/instances', json={}).json()
    agent = next(n for n in copy['nodes'] if n['type'] == 'matcreator.agent')
    assert 'python_executable' not in agent['config']
    assert 'source_directory' not in agent['config']
    assert agent['config']['agent_mode'] == 'flash'


def test_runtime_model_connection_is_resolved_in_memory_and_directory_is_host_owned(client):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    card = create_node(client, 'matcreator.agent')
    path = provider.runtime_state_directory(card['id'])
    assert path.is_relative_to(client.app.state.services.settings.data_root)
    with pytest.raises(Exception, match='Choose a default model'):
        provider.resolve_model('oaw:default')
    connection = provider.resolve_model('openai/test')
    assert connection.model == 'openai/test'
    assert 'api_key' not in repr(connection)


@pytest.mark.asyncio
async def test_native_thinking_reaches_run_history_and_survives_restart(tmp_path):
    from google.adk.events import Event
    from google.genai import types
    from backend.agents import AgentEvent, AgentEventType
    from backend.runs.history import RunActivityHistory
    from backend.runs.reasoning import RunReasoning
    from backend.tests.test_runs import RecordingProvider, _services
    from backend.world.models import CardCreate
    from plugins.matcreator.oaw_matcreator.native_events import public_events

    actors = [('thinking_agent', False), ('execution_orchestrator', False), ('step_executor', True)]
    text = 'Provider-exposed thinking. ' * 200

    class Provider(RecordingProvider):
        async def execute(self, config, context, runtime_input):
            for index, (author, child) in enumerate(actors):
                native = Event(id=f'response-{index}', author=author, content=types.Content(role='model', parts=[
                    types.Part(text=text, thought=True)]))
                for message in public_events(native, child=child):
                    yield AgentEvent(context.agent_id, context.run_id, AgentEventType(message['event']), message['payload'])
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                             {'text': 'Done'}, run_status='succeeded')

    services = _services(tmp_path, Provider())
    try:
        agent = await services.create_card(CardCreate(type='agent', name='MatCreator event fixture'))
        run = await services.run_manager.start_run(agent.id, 'Check thinking')
        settled = await services.run_manager.wait_execution(run.run_id)
        assert settled.status.value == 'succeeded'
        assert services.run_manager.final_text(run.run_id) == 'Done'
        assert text not in json.dumps(settled.lifecycle)
    finally:
        services.close()

    restored = _services(tmp_path, RecordingProvider())
    try:
        directory = RunActivityHistory(restored.database, run.run_id).page()
        assert directory['total'] == 3
        assert all(row['kind'] == 'model_reasoning' and 'text' not in row for row in directory['items'])
        assert [row['role'] for row in directory['items']] == [author for author, _ in actors]
        for index, (author, _) in enumerate(actors):
            row = RunReasoning(restored.state, run.run_id).get(f'response-{index}:0')
            assert row['role'] == author and row['text'] == text and row['streaming'] is False
    finally:
        restored.close()
