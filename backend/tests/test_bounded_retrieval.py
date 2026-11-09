"""Bounded reads survive native projection and final dispatch; no live API."""
import asyncio
import json

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.context import ContextBudget
from backend.agents.model_observation import ModelTrace
from backend.agents.models import AgentRuntimeError
from backend.agents.request_recovery import recover_request
from backend.agents.request_window import RequestWindow
from backend.tests.test_invocation_logs import native
from backend.tests.test_request_window import request


def exchange(logs, name, call_id, value):
    identity = logs.start_tool('builder', call_id, 0, name, {})
    logs.finish_tool(identity, value)
    return identity, [
        types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args={}))]),
        types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(id=call_id, name=name, response=value))])]


def page(text, **extra):
    return dict(text=text, offset=0, total_chars=len(text), next_offset=None,
                document='current.py', version=2, **extra)


@pytest.mark.asyncio
async def test_registered_reader_keeps_full_escaped_page_at_final_dispatch(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    text = ('value = "quoted"\n' * 800)[:8000]
    value = page(text)
    assert len(json.dumps(value)) > 8000
    identity, read = exchange(trace.invocations, 'fetch_saved_text', 'read-page', value)
    older_id, older = exchange(trace.invocations, 'compute', 'older', {'stdout': 'old output ' * 10000})
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    window.register_bounded_reader('fetch_saved_text', identity_fields=('document', 'version'))
    trace.context_windows['builder'] = window
    submitted = []
    class Provider:
        model = 'offline'
        async def generate_content_async(self, req, stream=False):
            submitted.append(req)
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='done')]))
    req = request(contents=[types.Content(role='user', parts=[types.Part(text='edit the current function')]), *older, *read])
    await trace.callbacks('builder', prepare_only=True)['before_model_callback'](None, req)
    _ = [r async for r in recover_request(Provider(), req, trace, 'builder')]
    responses = [p.function_response for c in submitted[0].contents for p in c.parts if p.function_response]
    assert responses[-1].response == value
    assert responses[0].response['context_projection']['native_invocation_id'] == older_id
    assert trace.invocations.get('tools', identity)['response'] == value
    assert trace.invocations.get('tools', older_id)['response']['stdout'] == 'old output ' * 10000
    assert window.visible_reads[-1]['end'] == 8000


@pytest.mark.asyncio
@pytest.mark.parametrize('registered,oversize,metadata', [(False, False, False), (True, True, False), (True, False, True)])
async def test_result_shape_cannot_opt_itself_out_or_bypass_reader_bounds(native, registered, oversize, metadata):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    value = page('x' * (8001 if oversize else 8000), **({'extra': 'y' * 5000} if metadata else {}))
    _, contents = exchange(trace.invocations, 'fetch_saved_text', 'page', value)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    if registered:
        window.register_bounded_reader('fetch_saved_text', identity_fields=('document', 'version'))
    req = request(contents=contents)
    assert window.project_results(req) == 1
    assert 'context_projection' in req.contents[-1].parts[0].function_response.response
    assert not window.read_pages(req)


@pytest.mark.asyncio
async def test_bounded_reads_still_obey_final_input_window(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    _, contents = exchange(trace.invocations, 'fetch_saved_text', 'page', page('not compressible by previews ' * 200))
    window = RequestWindow(ContextBudget(1024, 512), trace, 'builder')
    window.register_bounded_reader('fetch_saved_text', identity_fields=('document', 'version'))
    with pytest.raises(AgentRuntimeError, match='safe input window'):
        await window.prepare(request(contents=contents))


def test_latest_read_batch_is_pinned_as_a_complete_exchange_and_waits_are_not():
    window = RequestWindow(ContextBudget(100000, 512), ModelTrace(asyncio.Queue()), 'builder')
    window.register_bounded_reader('fetch_saved_text', identity_fields=('document', 'version'))
    call = types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
        id=str(i), name='fetch_saved_text', args={})) for i in range(2)])
    responses = [types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(
        id=str(i), name='fetch_saved_text', response=page('current page')))]) for i in range(2)]
    req = request(contents=[call, *responses, types.Content(role='user', parts=[types.Part(text='HOST facts')])])
    assert window.retrieval_context(req) == [call, *responses]
    req.contents.append(types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(
        id='waiting', name='wait_sandbox_operation', response={'status': 'running'}))]))
    assert window.retrieval_context(req) == []
    req.contents = [call, responses[-1]]  # Never pin a partial batch.
    assert window.retrieval_context(req) == []
