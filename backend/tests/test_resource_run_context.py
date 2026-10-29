"""Public native-resource contexts carry the host's current Run identity."""
import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from backend.agents.mock import MockAgentRuntime
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.services import create_services
from backend.tests.plugin_support import install_test_plugin
from backend.world.models import CardCreate, EdgeCreate
from open_agent_world.plugin_api import CapabilityDefinition, CapabilityGrantDefinition, NodeResourceAction, NodeResourceContext, NodeTypeDefinition, RelationshipDefinition


class ProbeConfig(BaseModel):
    pass


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / 'isolated-profile'))
    instance.run_manager.default_runtime_provider_id = 'core.mock'
    def register(registration):
        def probe(context, arguments):
            return dict(node_id=context.node_id, actor_id=context.actor_id, run_id=context.run_id,
                        argument_run_id=arguments.get('run_id'))
        async def invoke(context, capability, arguments):
            return await context.node_resource_action(capability, 'probe', arguments)
        registration.register_capability(CapabilityDefinition(
            'test.context.probe', 'context_probe', 'Inspect resource invocation context',
            input_schema={'type': 'object', 'properties': {'run_id': {'type': 'string'}}}, read_only=True), invoke)
        registration.register_node_type(NodeTypeDefinition(
            id='test.context.probe', label='Context probe', description='Fixture', icon='box', color='#888888',
            deck_id='test.context', deck_label='Fixture', deck_icon='box', default_name='Context probe',
            default_size=(300, 200), default_status='available', statuses=frozenset({'available'}), config_model=ProbeConfig,
            resource_actions={'probe': NodeResourceAction(probe, capability_kind='test.context.probe')}))
        registration.register_relationship(RelationshipDefinition(
            id='test.context.access', label='Probe', short_label='probe', description='Fixture',
            source_traits=frozenset({'core.agent'}), target_types=frozenset({'test.context.probe'}),
            capabilities=(CapabilityGrantDefinition('test.context.probe'),)))
    install_test_plugin(instance.plugins, 'test.context', register)
    yield instance
    instance.close()


async def setup(services):
    agent = await services.create_card(CardCreate(type='agent', config={'max_concurrent_runs': 2}))
    probe = await services.create_card(CardCreate(type='test.context.probe'))
    await services.create_edge(EdgeCreate(source=agent.id, target=probe.id, relationship='test.context.access'))
    return agent, probe


def test_existing_positional_context_constructor_remains_compatible(tmp_path):
    context = NodeResourceContext('card', tmp_path, Event(), 'agent', True, None, None)
    assert context.actor_id == 'agent' and context.confirmed is True and context.run_id is None
    resolver = lambda reference: reference
    upstream = NodeResourceContext('card', tmp_path, Event(), 'agent', False, None, None, resolver)
    assert upstream.resolve_model is resolver and upstream.run_id is None


@pytest.mark.asyncio
async def test_desktop_and_standalone_capability_calls_have_no_run_even_with_a_run_id_argument(services):
    agent, probe = await setup(services)
    result = await invoke_resource_action(services, probe.id, 'probe', ResourceActionRequest(arguments={'run_id': 'invented'}))
    assert result == dict(node_id=probe.id, actor_id=None, run_id=None, argument_run_id='invented')
    provider = WorldAgentCapabilityProvider(services)
    definition = (await provider.list_tools(agent.id))[0]
    result = await provider.invoke_tool(agent.id, definition.capability_id, dict(target=probe.id, run_id='invented'))
    assert result['actor_id'] == agent.id and result['run_id'] is None and result['argument_run_id'] == 'invented'


@pytest.mark.asyncio
async def test_unrelated_run_and_non_agent_resource_calls_do_not_receive_run_identity(services, monkeypatch):
    agent, probe = await setup(services)
    capability = services.capabilities.derive(agent.id).capabilities[0]
    with monkeypatch.context() as patch:
        patch.setattr(type(services.run_manager), 'current_context', property(lambda _: SimpleNamespace(
            run_id='unrelated-run', agent_id='another-agent', context_id=None)))
        result = await invoke_resource_action(services, probe.id, 'probe', ResourceActionRequest(), capability=capability)
        assert result['actor_id'] == agent.id and result['run_id'] is None
        result = await invoke_resource_action(services, probe.id, 'probe', ResourceActionRequest())
        assert result['actor_id'] is None and result['run_id'] is None


@pytest.mark.asyncio
async def test_real_concurrent_runs_on_one_agent_receive_distinct_trusted_ids_in_resource_workers(services, monkeypatch):
    agent, probe = await setup(services)
    original = MockAgentRuntime.execute
    ready = asyncio.Event()
    started = []
    observed = {}
    async def execute(self, config, context, runtime_input):
        started.append(context.run_id)
        if len(started) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        definition = next(t for t in await self._provider.list_tools(agent.id) if t.name == 'context_probe')
        result = await self._provider.invoke_tool(agent.id, definition.capability_id,
                                                 dict(target=probe.id, run_id='invented'))
        assert result['actor_id'] == agent.id and result['run_id'] == context.run_id
        assert result['argument_run_id'] == 'invented'
        observed[context.run_id] = result
        async for event in original(self, config, context, runtime_input):
            yield event
    monkeypatch.setattr(MockAgentRuntime, 'execute', execute)
    try:
        receipts = await asyncio.gather(services.run_agent(agent.id, 'First probe'), services.run_agent(agent.id, 'Second probe'))
        runs = await asyncio.wait_for(asyncio.gather(*(services.run_manager.wait_settled(r['run_id']) for r in receipts)), 10)
        assert all(str(run.status) == 'succeeded' for run in runs), runs
        assert set(observed) == {r['run_id'] for r in receipts} and len(observed) == 2
    finally:
        await services.shutdown()
