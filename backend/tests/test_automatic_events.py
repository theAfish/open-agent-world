import asyncio
from dataclasses import replace
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins import CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin


def test_task_board_executor_contract_is_discovered_without_granting_tools(client):
    agent = create_node(client, "agent")
    board = create_node(client, "oaw.tasks", name="Plan")
    path = f"/api/state-machines/events?card_id={agent['id']}"
    edge = client.post('/api/edges', json={"source": board['id'], "target": agent['id'], "relationship": "oaw.tasks.executor"}).json()
    catalog = client.get(path).json()
    assert catalog['operations'] == []
    run_source = next(source for source in catalog['sources'] if source.get('kind') == 'run')
    assert {event['key'] for event in run_source['events']} >= {'run.started', 'run.completed', 'run.interrupted'}
    source = next(source for source in catalog['sources'] if source['id'].startswith('execution:'))
    assert source['target_card_id'] == board['id']
    assert source['capability'] is None
    assert {event['key'] for event in source['events']} == {
        'execution.started', 'execution.succeeded', 'execution.failed', 'execution.cancelled', 'execution.interrupted'}
    assert all(event['runtime_bound'] for event in source['events'])
    assert source in client.get(f"/api/state-machines/events?card_id={board['id']}").json()['sources']
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    assert not any(source['id'].startswith('execution:') for source in client.get(path).json()['sources'])
    assert run_source in client.get(path).json()['sources']
    assert client.post('/api/edges', json={"source": agent['id'], "target": board['id'], "relationship": "oaw.tasks.progress"}).status_code == 201
    sources = [source for source in client.get(path).json()['sources'] if source.get('kind') == 'capability']
    assert {source['capability'] for source in sources} == {'oaw.tasks.read', 'oaw.tasks.progress'}
    assert {source['label'] for source in sources} == {'Read tasks', 'Update task progress'}
    assert all(source['default_event'] == 'capability.succeeded' for source in sources)


def register_unknown_tool(client, handler, *, legacy=False):
    def configure(registration):
        kind = 'example.autoevents.polish'
        if legacy:
            registration.register_capability_handler(kind, handler)
            grant = CapabilityGrantDefinition(kind, 'polish_surface', 'Polish the selected surface.', {'type': 'object'})
        else:
            registration.register_capability(CapabilityDefinition(kind, 'polish_surface', 'Polish the selected surface.'), handler)
            grant = CapabilityGrantDefinition(kind)
        registration.register_relationship(RelationshipDefinition(id='example.autoevents.polish', label='Polish', short_label='polish',
            description='Use this tool.', source_traits=frozenset({'core.agent'}), target_traits=frozenset({'core.text'}), capabilities=(grant,)))
    install_test_plugin(client.app.state.services.plugins, 'example.autoevents', configure)
    agent, target = create_node(client, 'agent'), create_node(client, 'text', name='Sample')
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': target['id'], 'relationship': 'example.autoevents.polish'}).json()
    return agent, target, edge


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('outcome', ['succeeded', 'failed', 'rejected', 'cancelled', 'timed_out'])
def test_unknown_plugin_discovery_and_real_invocation_share_lifecycle_without_event_registration(client, legacy, outcome):
    async def handler(context, capability, arguments):
        assert arguments == {'private': 'not event data'}
        if outcome == 'rejected':
            raise ResourceValidationError('Rejected')
        if outcome == 'cancelled':
            raise asyncio.CancelledError()
        if outcome == 'timed_out':
            raise TimeoutError()
        return {'ok': outcome == 'succeeded', 'private_result': 'not event data'}
    agent, target, edge = register_unknown_tool(client, handler, legacy=legacy)
    source = client.get(f"/api/state-machines/events?card_id={agent['id']}").json()['sources'][0]
    assert source['label'] == 'Polish surface'
    assert source['capability'] == 'example.autoevents.polish'
    provider = WorldAgentCapabilityProvider(client.app.state.services)

    async def invoke():
        async with client.app.state.services.events.subscribe() as queue:
            try:
                await provider.invoke_tool(agent['id'], 'operation:polish_surface', {'target': target['id'], 'private': 'not event data'})
            except (ResourceValidationError, asyncio.CancelledError, TimeoutError):
                pass
            return [queue.get_nowait() for _ in range(queue.qsize())]
    events = client.portal.call(invoke)
    retained = {event.id for _, event in client.app.state.services.operation_events.after(0, limit=10000)}
    assert {event.id for event in events} <= retained
    expected = 'failed' if outcome == 'rejected' else outcome
    assert [event.payload['event'] for event in events] == ['capability.started', f'capability.{expected}']
    assert events[0].payload['invocation_id'] == events[1].payload['invocation_id']
    for event in events:
        assert event.agent_id == agent['id']
        assert event.node_id == target['id']
        assert event.payload['capability'] == source['capability']
        assert event.payload['target_card_id'] == source['target_card_id']
        assert event.payload['event'] in {phase['key'] for phase in source['events']}
        assert 'not event data' not in event.model_dump_json()
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200

    async def denied():
        async with client.app.state.services.events.subscribe() as queue:
            with pytest.raises(PermissionDeniedError):
                await provider.invoke_tool(agent['id'], 'operation:polish_surface', {'target': target['id']})
            assert queue.empty()
    client.portal.call(denied)


def test_work_contract_publishes_real_lifecycle_with_executor_and_target(client, monkeypatch):
    from backend.tests.test_node_execution import setup_board, start, settled
    node, url, agent, _ = setup_board(client, [{'id': 'a', 'title': 'Work'}])
    received = []
    hub = client.app.state.services.events
    original = hub.publish_event_nowait

    def capture(event):
        if event.type.value.startswith('work_'):
            received.append(event)
        original(event)
    # Capture actual dispatch, not manually fabricated events or preview calls.
    monkeypatch.setattr(hub, 'publish_event_nowait', capture)
    assert start(client, url).status_code == 200
    settled(client, node)
    received = list({event.id: event for event in received}.values())
    assert [event.payload['event'] for event in received] == ['execution.started', 'execution.succeeded']
    assert received[0].run_id == received[1].run_id
    for event in received:
        assert event.agent_id == agent['id']
        assert event.node_id == event.payload['target_card_id'] == node['id']
        assert event.payload['item_id'] == 'a'


@pytest.mark.parametrize('context_source', ['run_operation', 'operation_fallback', 'run_caller', 'operation_only'])
def test_shared_work_outcome_keeps_dispatch_conversation_and_session(client, context_source):
    from backend.capabilities.events import publish_work_outcome
    from backend.plugins.execution import WorkOutcome
    from backend.plugins.state import ScopedStateSpec

    services = client.app.state.services
    def configure(registration):
        base = services.plugins.node_type('oaw.tasks')
        registration.register_node_type(replace(base, id='example.shared_work', label='Shared work',
            document=replace(base.document, actions={}), state=ScopedStateSpec(),
            traits=frozenset({'example.shared_work'}), execution=None))
    install_test_plugin(services.plugins, 'example.shared_work', configure)
    node = create_node(client, 'example.shared_work')
    agent = create_node(client, 'agent')
    conversation = create_node(client, 'conversation')
    session = services.conversations.list_sessions(conversation['id'])[0]
    assert services.card_state.identity(node['id']) == ('shared', '*')
    operation = {'invocation_id': 'shared-dispatch', 'context_id': session.id,
                 'conversation_id': conversation['id']}
    record = None
    if context_source != 'operation_only':
        record = services.run_manager.store.create(
            agent_id=agent['id'], runtime_provider_id='core.mock',
            caller_kind='conversation' if context_source == 'run_caller' else 'work',
            caller_id=conversation['id'] if context_source == 'run_caller' else 'batch',
            context_id=None if context_source == 'operation_fallback' else session.id,
            initial_lifecycle={} if context_source == 'run_caller' else {'operation': operation})
    sequence = services.operation_events.max_sequence()
    # Completion may be recovered after its original provider context has ended.
    for _ in range(2):
        publish_work_outcome(services, node['id'], WorkOutcome(
            item_id='task', status='succeeded', run_id=record.run_id if record else None),
            operation=operation if context_source == 'operation_only' else None,
            execution_id='shared-work')
    event, = [event for _, event in services.operation_events.after(sequence)]
    assert event.payload['event'] == 'execution.succeeded'
    assert event.payload['state_scope'] == 'shared'
    assert event.payload['state_scope_id'] == '*'
    assert event.session_id == session.id
    assert event.conversation_id == conversation['id']


@pytest.mark.parametrize('explicit_actor', [False, True])
def test_shared_state_mutation_preserves_capability_context_and_authoritative_fields(client, explicit_actor):
    from backend.operation_associations import operation_context

    services = client.app.state.services
    async def handler(context, capability, arguments):
        if explicit_actor:
            services.state.set(services.card_state.scope(capability.target_id), 'data', {'observed': True},
                               actor_id='mutation-actor', run_id='mutation-run')
        else:
            context.state.set({'observed': True})
        return {'ok': True}

    agent, target, _ = register_unknown_tool(client, handler)
    conversation = create_node(client, 'conversation')
    session = services.conversations.list_sessions(conversation['id'])[0]
    assert services.card_state.identity(target['id']) == ('shared', '*')
    sequence = services.operation_events.max_sequence()
    provider = WorldAgentCapabilityProvider(services)
    async def invoke():
        with operation_context('shared-state-call', caller_object_id=agent['id'], target_object_id=target['id'],
                               run_id='invoking-run', context_id=session.id, conversation_id=conversation['id']):
            await provider.invoke_tool(agent['id'], 'operation:polish_surface', {'target': target['id']},
                                       request_id='shared-state-call')
    client.portal.call(invoke)
    event, = [event for _, event in services.operation_events.after(sequence)
              if event.payload.get('event') in {'state.created', 'state.updated'} and event.node_id == target['id']]
    assert event.session_id == session.id
    assert event.conversation_id == conversation['id']
    assert event.agent_id == event.payload['actor_id'] == ('mutation-actor' if explicit_actor else agent['id'])
    assert event.run_id == event.payload['run_id'] == ('mutation-run' if explicit_actor else 'invoking-run')
    assert event.payload['invocation_id'] == 'shared-state-call'
    assert event.payload['target_card_id'] == target['id']
    assert event.payload['key'] == 'data'
    assert event.payload['value'] == {'observed': True}
    assert event.payload['revision'] == services.card_state.bind(target['id']).get()['revision']

    sequence = services.operation_events.max_sequence()
    services.card_state.bind(target['id']).set({'observed': False})
    unrelated, = [event for _, event in services.operation_events.after(sequence)]
    assert 'invocation_id' not in unrelated.payload
    assert unrelated.agent_id is unrelated.run_id is unrelated.session_id is unrelated.conversation_id is None


def test_unknown_work_source_uses_its_declared_execution_relationship(client):
    registry = client.app.state.services.plugins
    def configure(registration):
        base = registry.node_type('oaw.tasks')
        registration.register_node_type(replace(base, id='example.workflow', label='Workflow', document=replace(base.document, actions={}),
            traits=frozenset({'example.workflow'}), execution=replace(base.execution, executor_relationship='example.workflow.assign', control_capability_kind=None)))
        registration.register_relationship(RelationshipDefinition(id='example.workflow.assign', label='Assign', short_label='assign',
            description='Assign work to an executor.', source_traits=frozenset({'example.workflow'}), target_traits=frozenset({'core.agent'})))
    install_test_plugin(registry, 'example.workflow', configure)
    agent, workflow = create_node(client, 'agent'), create_node(client, 'example.workflow')
    assert client.post('/api/edges', json={'source': workflow['id'], 'target': agent['id'], 'relationship': 'example.workflow.assign'}).status_code == 201
    sources = [source for source in client.get(f"/api/state-machines/events?card_id={agent['id']}").json()['sources']
               if source['id'].startswith('execution:')]
    assert len(sources) == 1
    assert sources[0]['target_card_id'] == workflow['id']
    assert sources[0]['default_event'] == 'execution.succeeded'


