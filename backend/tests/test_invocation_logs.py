"""Full native invocation persistence: no paid models or generated code execution."""
import asyncio
from dataclasses import dataclass
import json
from types import SimpleNamespace as NS

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.model_observation import ModelTrace
from backend.agents.request_recovery import recover_request
from backend.agents.tool_observation import archived_tool_callbacks
from backend.errors import NotFoundError, PermissionDeniedError
from backend.persistence.database import Database
from backend.plugins import create_builtin_registry
from backend.runs.invocation_logs import RunInvocationLogs, FIELDS
from backend.runs.model_checkpoints import RunModelCheckpoints
from backend.runs.store import RunStore
from backend.state import StateContext, StateStore


@pytest.fixture
def native(tmp_path):
    db = Database(tmp_path / 'world.sqlite3')
    state = StateStore(db, create_builtin_registry())
    run = RunStore(db).create(agent_id='test', runtime_provider_id='test', caller_kind='user')
    scope = state.ensure_scope('run', run.run_id)
    checkpoint = RunModelCheckpoints(db, run.run_id, api_key='sk-sensitive-key', state_store=state)
    yield db, state, run, scope, checkpoint
    db.close()


def request(text='hello'):
    return LlmRequest(model='fixture-model', contents=[types.Content(role='user', parts=[types.Part(text=text)])],
        config=types.GenerateContentConfig(system_instruction='keep system instruction', max_output_tokens=81920,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(name='execute_command',
                parameters_json_schema={'type': 'object', 'properties': {'argv': {'type': 'array', 'items': {'type': 'string'}}}})])]))


def response():
    return LlmResponse(content=types.Content(role='model', parts=[types.Part(text='model thinking', thought=True),
        types.Part(function_call=types.FunctionCall(id='real-call', name='execute_command', args={'argv': ['python3', 'probe.py']}))]),
        finish_reason='STOP', usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=42, candidates_token_count=13, thoughts_token_count=10, total_token_count=65))


def test_runtime_provenance_is_saved_once_and_provider_usage_is_not_guessed(native):
    *_, checkpoints = native
    logs = checkpoints.invocations
    provenance = {"model":{"gateway":"https://gateway.example/v1"},
                  "settings":{"api_key":"sk-sensitive-key"}, "code":{"working_source_sha256":"abc"}}
    first = logs.start_model("builder", 1, 0, 1, request(), provenance=provenance)
    second = logs.start_model("builder", 2, 0, 1, request(), provenance=provenance)
    rows = logs.records("models")
    assert rows[first]["provenance"]["settings"]["api_key"] == "[REDACTED]"
    assert rows[first]["provenance_complete"] and rows[second]["provenance"] is None
    assert rows[second]["provenance_ref"] == first
    logs.finish_model(first, [response()])
    row = logs.records("models")[first]
    assert row["reasoning_tokens"] == 10 and row["cached_input_tokens"] is None
    cached = response().model_copy(update={"usage_metadata":types.GenerateContentResponseUsageMetadata(
        prompt_token_count=42, candidates_token_count=13, cached_content_token_count=0)})
    logs.finish_model(second, [cached])
    row = logs.records("models")[second]
    assert row["cached_input_tokens"] == 0 and row["reasoning_tokens"] is None


@pytest.mark.asyncio
async def test_actual_boundary_snapshot_full_response_usage_and_host_isolation(native):
    db, state, run, scope, checkpoints = native
    req = request('large source ' * 30000 + ' sk-sensitive-key')
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    trace.begin('builder', req)
    # Final SDK changes after before_model must be retained at dispatch.
    req.config.labels = {'final': 'label'}
    class Provider:
        model = 'actual-model'
        async def generate_content_async(self, req, stream=False):
            yield response()
    output = [item async for item in recover_request(Provider(), req, trace, 'builder')]
    assert len(output) == 1
    archive = checkpoints.invocations
    row = next(iter(archive.records('models').values()))
    assert row['request']['config']['labels'] == {'final': 'label'}
    assert row['request']['config']['system_instruction'] == 'keep system instruction'
    assert row['request']['config']['tools'][0]['function_declarations'][0]['name'] == 'execute_command'
    assert row['request']['contents'][0]['parts'][0]['text'].endswith('[REDACTED]')
    assert len(row['request']['contents'][0]['parts'][0]['text']) > 256 * 1024
    assert row['request_complete'] and row['response_complete'] and not row['truncated']
    assert row['input_tokens'] == 42 and row['output_tokens'] == 13
    assert row['response'][0]['usage_metadata']['thoughts_token_count'] == 10
    assert row['response'][0]['content']['parts'][1]['function_call']['args']['argv'] == ['python3', 'probe.py']
    assert not checkpoints.latest('builder')['replayable']  # Recovery bound stays independent.
    db2 = Database(db.path)
    try:
        reopened = RunInvocationLogs(StateStore(db2, create_builtin_registry()), run.run_id)
        assert reopened.get('models', row['id']) == row
    finally:
        db2.close()
    api = state.api(StateContext((scope,)), permissions={'*'})
    for field in FIELDS.values():
        with pytest.raises(PermissionDeniedError): api.get(scope, field)
        with pytest.raises(PermissionDeniedError): api.patch(scope, field, {})
        with pytest.raises(PermissionDeniedError): api.delete(scope, field)
        with pytest.raises(NotFoundError): api.resolve(field)
        assert field not in api.snapshot().values


@pytest.mark.asyncio
async def test_retry_attempts_preserve_request_and_unknown_usage(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints, run_attempt=2)
    req = request(); trace.begin('planner', req)
    class Provider:
        count = 0
        async def generate_content_async(self, req, stream=False):
            self.count += 1
            if self.count == 1:
                yield LlmResponse(partial=True, content=types.Content(role='model', parts=[types.Part(thought=True, text='safe prefix')]))
                raise ConnectionError('gateway disconnected')
            yield response()
    provider = Provider()
    output = [item async for item in recover_request(provider, req, trace, 'planner', retry_delay=0)]
    assert len(output) == 1 and provider.count == 2
    rows = list(checkpoints.invocations.records('models').values())
    assert len(rows) == 2 and rows[0]['state'] == 'interrupted' and rows[1]['state'] == 'returned'
    assert rows[0]['error_type'] == 'ConnectionError' and rows[0]['input_tokens'] is None
    assert rows[0]['request'] == rows[1]['request'] and rows[0]['id'] != rows[1]['id']
    assert rows[0]['run_attempt'] == 2 and rows[1]['model_attempt'] == 2


@pytest.mark.asyncio
async def test_cancel_before_aggregate_retains_request_without_invented_usage(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    req = request(); trace.begin('agent', req)
    started = asyncio.Event()
    class Provider:
        async def generate_content_async(self, req, stream=False):
            yield LlmResponse(partial=True, content=types.Content(role='model', parts=[types.Part(thought=True, text='received thought')]))
            started.set(); await asyncio.Event().wait()
    async def consume():
        async for _ in recover_request(Provider(), req, trace, 'agent'): pass
    task = asyncio.create_task(consume()); await asyncio.wait_for(started.wait(), 2); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    row = next(iter(checkpoints.invocations.records('models').values()))
    assert row['state'] == 'interrupted' and row['input_tokens'] is None and not row['response_complete']
    assert next(iter(checkpoints.reasoning.records().values()))['text'] == 'received thought'
    checkpoints.invocations.interrupt()
    assert next(iter(checkpoints.invocations.records('models').values())) == row


@pytest.mark.asyncio
async def test_tool_original_and_effective_arguments_denial_error_and_large_result(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    async def before(tool, args, tool_context):
        args['timeout_seconds'] = 120
        return {'ok': False, 'error': {'code': 'denied'}} if args.get('deny') else None
    callbacks = archived_tool_callbacks(trace, 'builder', {'before_tool_callback': before})
    obj = NS(name='write_file'); ctx = NS(function_call_id='call/one')
    args = {'content': 'source ' * 20000, 'api_key': 'a-different-key', 'argv': ['python3', 'probe.py']}
    assert await callbacks['before_tool_callback'](obj, args, ctx) is None
    assert args['timeout_seconds'] == 120
    result = {'stdout': 'output ' * 20000 + ' sk-sensitive-key', 'exit_code': 0}
    assert await callbacks['after_tool_callback'](obj, args, ctx, result) is None
    row = next(iter(checkpoints.invocations.records('tools').values()))
    assert row['arguments']['api_key'] == '[REDACTED]' and 'timeout_seconds' not in row['arguments']
    assert row['effective_arguments']['timeout_seconds'] == 120
    assert row['response']['stdout'].endswith('[REDACTED]') and len(row['response']['stdout']) > 64000
    assert row['response_complete'] and row['arguments_complete']
    ctx = NS(function_call_id='denied'); args = {'deny': True}
    denial = await callbacks['before_tool_callback'](obj, args, ctx)
    await callbacks['after_tool_callback'](obj, args, ctx, denial)
    denied = checkpoints.invocations.records('tools')['tool-call:0:builder:denied']
    assert denied['overridden'] and denied['response']['error']['code'] == 'denied'
    ctx = NS(function_call_id='error')
    await callbacks['before_tool_callback'](obj, {}, ctx)
    assert await callbacks['on_tool_error_callback'](obj, {}, ctx, RuntimeError('sk-sensitive-key')) is None
    failed = checkpoints.invocations.records('tools')['tool-call:0:builder:error']
    assert failed['state'] == 'failed' and failed['error'] == '[REDACTED]'


def test_unsupported_values_are_explicit_and_pages_never_send_large_payloads(native):
    _, _, _, _, checkpoints = native
    archive = checkpoints.invocations
    archive.start_tool('builder', 'one', 0, 'tool', {'a': object(), 'content': 'x' * 100000})
    row = archive.records('tools')['tool-call:0:builder:one']
    assert not row['arguments_complete'] and row['unavailable_argument_paths'] == ['$.a']
    archive.interrupt()
    page = archive.page('tools', 0, 1)
    assert page['items'][0]['state'] == 'interrupted'
    assert 'arguments' not in page['items'][0] and len(page['items'][0]['arguments_preview']) <= 2000
    with pytest.raises(NotFoundError): archive.get('tools', 'other-run-call')


def test_aggregate_text_parts_redact_split_credentials_without_losing_structure(native):
    from backend.runs.invocation_logs import archive_value
    value = {'content': {'parts': [{'thought': True, 'text': 'safe sk-sensitive-'},
                                   {'thought': True, 'text': 'key safe tail'}]}}
    safe, missing = archive_value(value, ['sk-sensitive-key'])
    joined = ''.join(part['text'] for part in safe['content']['parts'])
    assert 'sk-sensitive-key' not in joined and 'sk-sensitive-' not in joined
    assert joined.startswith('safe ') and joined.endswith(' safe tail')
    assert len(safe['content']['parts']) == 2 and not missing


def test_circular_tool_results_preserve_other_fields_and_mark_incomplete(native):
    archive = native[-1].invocations
    identity = archive.start_tool('builder', 'cycle', 0, 'tool', {})
    result = {'stdout': 'successful execution', 'api_key': 'sk-sensitive-key'}
    result['self'] = result
    row = archive.finish_tool(identity, result)
    assert row['state'] == 'returned' and not row['response_complete']
    assert row['unavailable_response_paths'] == ['$.self']
    assert row['response']['stdout'] == 'successful execution'
    assert row['response']['api_key'] == '[REDACTED]'
    assert row['response']['self']['circular_reference']
    json.dumps(row)


def test_archive_cycles_in_dataclasses_and_lists_do_not_drop_shared_values():
    from backend.runs.invocation_logs import archive_value

    @dataclass
    class Node:
        child: object = None

    node = Node()
    node.child = node
    shared = {'text': 'keep both copies'}
    items = [node, shared, shared]
    items.append(items)
    safe, missing = archive_value(items)
    assert missing == ['$[0].child', '$[3]']
    assert safe[0]['child']['circular_reference']
    assert safe[1] == safe[2] == shared
    assert safe[3]['circular_reference']
    json.dumps(safe)


def test_sdk_serialization_recursion_is_explicit_without_privileged_repr():
    from backend.runs.invocation_logs import archive_value

    class RecursiveSDK:
        def model_dump(self, **kwargs):
            raise RecursionError('private credentials')

    safe, missing = archive_value({'sdk': RecursiveSDK(), 'stdout': 'done'})
    assert missing == ['$.sdk']
    assert safe['sdk']['serialization_failed'] and safe['stdout'] == 'done'
    assert 'private credentials' not in json.dumps(safe)


def test_invocation_api_run_scope_pagination_and_lazy_body(client):
    services = client.app.state.services
    run = RunStore(services.database).create(agent_id='a', runtime_provider_id='test', caller_kind='user')
    archive = RunInvocationLogs(services.state, run.run_id)
    for i in range(3):
        identity = archive.start_tool('agent', f'call/{i}', 0, 'execute', {'content': 'x' * 100000})
        archive.finish_tool(identity, {'stdout': 'done'})
    page = client.get(f'/api/runs/{run.run_id}/invocations/tools?limit=2').json()
    assert page['next_offset'] == 2 and len(page['items']) == 2
    assert 'arguments' not in page['items'][0]
    identity = page['items'][0]['id'].replace('/', '%2F')
    full = client.get(f'/api/runs/{run.run_id}/invocations/tools/{identity}').json()
    assert len(full['arguments']['content']) == 100000
    assert client.get('/api/runs/missing/invocations/tools').status_code == 404
    assert client.get(f'/api/runs/{run.run_id}/invocations/secrets').status_code == 404
    assert client.get(f'/api/runs/{run.run_id}/invocations/tools?limit=1000').status_code == 422


@pytest.mark.asyncio
async def test_startup_interrupts_pending_invocations_on_terminal_run(tmp_path):
    from backend.tests.test_runs import RecordingProvider, _services
    from backend.runs import RunStatus
    services = _services(tmp_path, RecordingProvider())
    try:
        run = services.run_manager.store.create(agent_id='fixture', runtime_provider_id='fixture', caller_kind='user')
        services.run_manager.store.update_status(run.run_id, RunStatus.FAILED)
        archive = RunInvocationLogs(services.state, run.run_id)
        identity = archive.start_model('builder', 1, 0, 1, request())
        tool_id = archive.start_tool('builder', 'pending', 0, 'tool', {})
        await services.run_manager.startup()
        assert archive.get('models', identity)['state'] == 'interrupted'
        assert archive.get('tools', tool_id)['state'] == 'interrupted'
        assert archive.get('models', identity)['input_tokens'] is None
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_manager_settles_pending_invocation_archives_on_run_exit(tmp_path, cancel):
    from backend.agents import AgentEvent, AgentEventType
    from backend.tests.test_runs import RecordingProvider, _services
    from backend.world.models import CardCreate
    class Provider(RecordingProvider):
        def __init__(self):
            super().__init__(); self.reasoning_store = None
        async def execute(self, config, context, runtime_input):
            archive = RunInvocationLogs(self.reasoning_store, context.run_id)
            archive.start_model('agent', 1, 0, 1, request())
            archive.start_tool('agent', 'pending-call', 0, 'tool', {})
            self.started.set()
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS, {'kind': 'status', 'text': 'Fixture active'})
            if cancel: await asyncio.Event().wait()
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {'text': 'Done'}, run_status='succeeded')
    provider = Provider(); services = _services(tmp_path, provider)
    try:
        card = await services.create_card(CardCreate(type='agent', name='Fixture'))
        run = await services.run_manager.start_run(card.id, 'fixture')
        await asyncio.wait_for(provider.started.wait(), 2)
        if cancel: await services.run_manager.cancel_run(run.run_id)
        settled = await services.run_manager.wait_execution(run.run_id)
        archive = RunInvocationLogs(services.state, run.run_id)
        assert settled.status.value == ('cancelled' if cancel else 'succeeded')
        assert next(iter(archive.records('models').values()))['state'] == 'interrupted'
        assert next(iter(archive.records('tools').values()))['state'] == 'interrupted'
    finally:
        services.close()


def test_generic_interruption_cleanup_preserves_previously_diagnosed_error(native):
    *_, checkpoints = native
    archive = checkpoints.invocations
    identity = archive.start_model('builder', 1, 0, 1, request())
    archive.interrupt_model(identity, error_type='RuntimeError')
    archive.interrupt_model(identity)
    archive.interrupt()
    row = archive.records('models')[identity]
    assert row['state'] == 'interrupted' and row['error_type'] == 'RuntimeError'
    assert row['request_complete'] and not row['response_complete'] and not row['usage_available']


@pytest.mark.asyncio
async def test_error_callback_replacement_is_saved_as_the_returned_result(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    async def recover(**kwargs):
        return {'ok': False, 'error': {'code': 'recoverable'}, 'next_action': 'Inspect receipt'}
    callbacks = archived_tool_callbacks(trace, 'builder', {'on_tool_error_callback': recover})
    tool, context = NS(name='import_file'), NS(function_call_id='recovered')
    await callbacks['before_tool_callback'](tool, {}, context)
    result = await callbacks['on_tool_error_callback'](tool, {}, context, TimeoutError('transport'))
    row = checkpoints.invocations.records('tools')['tool-call:0:builder:recovered']
    assert row['state'] == 'returned' and row['response'] == result
    checkpoints.invocations.interrupt()
    assert checkpoints.invocations.records('tools')[row['id']] == row


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_error_callback_failure_settles_archive_and_propagates(native, failure):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    async def recover(**kwargs):
        raise failure('observer failure')
    callbacks = archived_tool_callbacks(trace, 'builder', {'on_tool_error_callback': recover})
    tool, context = NS(name='import_file'), NS(function_call_id='observer-failed')
    await callbacks['before_tool_callback'](tool, {}, context)
    with pytest.raises(failure):
        await callbacks['on_tool_error_callback'](tool, {}, context, TimeoutError('original error'))
    row = checkpoints.invocations.records('tools')['tool-call:0:builder:observer-failed']
    assert row['state'] != 'started'
    assert row['error_type'] == failure.__name__
