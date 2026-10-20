"""Real HTTP visual fixtures with an offline MatCreator Sandbox.

No scientific process is started by this suite. Keep the production preset's
network intent intact and make the isolated browser profile portable to hosts
without the optional Windows network-policy broker.
"""
from backend.config import Settings
from backend.main import create_app
from backend.services import create_services

settings = Settings.from_environment()
services = create_services(settings)
for preset_id in ("matcreator.native-research",):
    preset = services.plugins._legion_presets[preset_id]
    services.plugins._legion_presets[preset.id] = preset.model_copy(update={
        "nodes": tuple(node.model_copy(update={"config": {**node.config, "network_enabled": False}})
                       if node.type == "sandbox" else node for node in preset.nodes),
    })
app = create_app(settings, services=services)


# Conversation integration fixture: real persistence/delivery and HTTP/WS,
# deterministic provider output, no external model or Sandbox process.
import asyncio
from backend.agents import AgentEvent, AgentEventType, MockAgentRuntime

_conversation_gates = {}
_conversation_original_runtime = None


@app.post('/api/test/conversation-input/install')
async def install_conversation_input_fixture():
    global _conversation_original_runtime
    _conversation_original_runtime = services.run_manager.provider_for_agent_configuration('core.mock')
    provider = WorldAgentCapabilityProvider(services)

    class PausedConversation(MockAgentRuntime):
        async def execute(self, config, context, runtime_input):
            gate = _conversation_gates[context.run_id] = asyncio.Event()
            try:
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {'text': 'I finished a. I am checking b now.', 'provider_message_id': 'before-b', 'final': True})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_STARTED,
                    {'name': 'b', 'call_id': 'tool-b', 'arguments': {'query': 'Check the intermediate result'}})
                await gate.wait()
                pending = await provider.pending_conversation_input(context.agent_id, context.run_id)
                assert any('Skip c' in message['text'] for message in pending)
                await provider.acknowledge_conversation_input(context.agent_id, context.run_id, [m['id'] for m in pending])
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_COMPLETED,
                    {'name': 'b', 'call_id': 'tool-b', 'response': {'result': 'b returned'}})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {'text': 'I received your update with the result of b. I skipped c.', 'provider_message_id': 'after-b', 'final': True})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status='succeeded')
            finally:
                _conversation_gates.pop(context.run_id, None)

    services.install_runtime_provider('core.mock', PausedConversation(provider), default=True)
    return {'ok': True}


@app.post('/api/test/conversation-input/restore')
async def restore_conversation_input_fixture():
    global _conversation_original_runtime
    if _conversation_original_runtime is not None:
        services.install_runtime_provider('core.mock', _conversation_original_runtime, default=True)
        _conversation_original_runtime = None
    return {'ok': True}


@app.post('/api/test/conversation-input/{run_id}/return-b')
async def return_conversation_tool(run_id: str):
    _conversation_gates[run_id].set()
    return {'ok': True}


_activity_gates = {}


@app.post('/api/test/conversation-activity/install')
async def install_conversation_activity_fixture(incremental: bool = False):
    global _conversation_original_runtime
    _conversation_original_runtime = services.run_manager.provider_for_agent_configuration('core.mock')

    class ActivityConversation(MockAgentRuntime):
        async def execute(self, config, context, runtime_input):
            gates = _activity_gates[context.run_id] = [[asyncio.Event() for _ in range(count)] for count in (12, 8)]
            try:
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {'text': 'I will read the project files, then check the changes.', 'provider_message_id': 'intro', 'final': True})
                for phase, count in enumerate([12, 8]):
                    # Real runtimes emit context/model lifecycle ticks without
                    # display text between calls. These must not become bubbles.
                    for tick in ({'phase': 'model', 'status': 'started'},
                                 {'kind': 'status', 'text': ' \n\t'},
                                 {'kind': 'context_usage', 'tokens': 2048}):
                        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS, tick)
                    if phase:
                        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS,
                            {'text': 'The files agree. I am checking the implementation next.', 'kind': 'reasoning_summary', 'provider_message_id': 'plan'})
                    for index in range(count):
                        arguments = {'summary': {'goal': 'Review the computation and record its evidence.',
                            'approach': 'Reuse the existing job, inspect its results, and summarize the findings.',
                            'key_decisions': ['Keep the approved plan.', 'Verify outputs before accepting the result.'],
                            'evidence': {'result': {'path': 'outputs/report.json', 'verified': True}}}} if index == 0 else {'path': f'src/module-{index}.ts'}
                        payload = {'name': 'oaw_invoke_tool' if phase == 0 else 'check_result', 'call_id': f'{phase}-{index}',
                            'arguments': ({'capability_id': 'operation:write_session_summary' if index == 0 else 'operation:read_file', 'arguments': arguments}
                                if phase == 0 else {'path': f'src/module-{index}.ts'})}
                        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_STARTED, payload)
                        if incremental or index == count - 1:
                            await gates[phase][index].wait()
                        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_COMPLETED,
                            {'name': payload['name'], 'call_id': payload['call_id'], 'response': {'exit_code': 1 if phase == 1 and index == 0 else 0,
                                'result': f'Checked module {index}',
                                **({'lines': [f'Line {line}: detailed inspection result' for line in range(30)]} if index == 0 else {})}})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {'text': 'Review complete. One check needs attention.', 'provider_message_id': 'answer', 'final': True})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status='succeeded')
            finally:
                _activity_gates.pop(context.run_id, None)

    services.install_runtime_provider('core.mock', ActivityConversation(WorldAgentCapabilityProvider(services)), default=True)
    return {'ok': True}


@app.post('/api/test/conversation-activity/{run_id}/advance/{phase}')
async def advance_conversation_activity(run_id: str, phase: int):
    for gate in _activity_gates[run_id][phase]:
        gate.set()
    return {'ok': True}


@app.post('/api/test/conversation-activity/{run_id}/step/{phase}/{index}')
async def step_conversation_activity(run_id: str, phase: int, index: int):
    _activity_gates[run_id][phase][index].set()
    return {'ok': True}


# Isolated browser fixtures only: seed a provider projection through its real
# public API, without a model request or mutable document on the graph viewer.
from open_agent_world.plugin_api import ExecutionSnapshot
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.runs.manager import _current_invocation
from types import SimpleNamespace


@app.post('/api/test/execution/{agent_id}/{session_id}')
async def execution_fixture(agent_id: str, session_id: str, value: ExecutionSnapshot):
    run = (services.run_manager.get_run(value.run_id) if value.run_id else
        services.run_manager.store.create(agent_id=agent_id, runtime_provider_id='matcreator.native',
            caller_kind='conversation', context_id=session_id))
    assert run.agent_id == agent_id and run.context_id == session_id
    token = _current_invocation.set(SimpleNamespace(agent_id=agent_id, run_id=run.run_id))
    try:
        await WorldAgentCapabilityProvider(services).publish_execution(agent_id, run.run_id, value.model_dump())
    finally:
        _current_invocation.reset(token)
    return {'run_id': run.run_id}


@app.post('/api/test/execution/{agent_id}/{session_id}/start')
async def execution_start_fixture(agent_id: str, session_id: str):
    from backend.runs.models import RunStatus
    run = services.run_manager.store.create(agent_id=agent_id, runtime_provider_id='matcreator.native',
        caller_kind='conversation', context_id=session_id)
    await services.run_manager.transition_run(run.run_id, RunStatus.RUNNING)
    return {'run_id': run.run_id}
