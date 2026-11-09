"""Current-source retrieval through the actual request pipeline, offline only."""
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool
from google.genai import types

from backend.agents.context import ContextBudget, ManagedContext
from backend.agents.request_recovery import recover_request
from backend.agents.request_window import ContextCapabilities, RequestWindow
from backend.sandbox.files import operate
from backend.sandbox.models import SandboxValidationError
from backend.tests.test_invocation_logs import native
from backend.tests.test_request_window import MemoryContext
from oaw_atomsculptor.execution_steps import source_reader_function, workflow_argument_error
from oaw_atomsculptor.runtime import _model_callbacks
from test_execution_steps import setup, prior


SOURCE = '# saved computation\n' + '# intermediate quantities remain in this file\n' * 1000 + (
    '@decorator\n'
    'def compute(value):\n'
    '    return value + 1\n\n'
    'class Inspector:\n'
    '    def measure(self):\n'
    '        return 2\n'
    'if __name__ == "__main__":\n'
    '    def conditional():\n'
    '        return 3\n')


def request(e, *contents):
    return LlmRequest(contents=[types.Content(role='user', parts=[types.Part(text=e.original_request)]), *contents],
                      config=types.GenerateContentConfig(max_output_tokens=512))


def attach_window(e, native, budget=100000):
    e.trace.invocations = native[-1].invocations
    window = RequestWindow(ContextBudget(budget, 512), e.trace, 'structure_builder')
    window.register_bounded_reader('read_modelling_source', identity_fields=('sandbox', 'path', 'version'))
    e.trace.context_windows['structure_builder'] = window
    return window


async def append_read(e, req, call_id, **selectors):
    args = dict(sandbox='box', script_path='step.py', **selectors)
    logs = e.trace.invocations
    identity = logs.start_tool('structure_builder', call_id, 0, 'read_modelling_source', args)
    value = await source_reader_function(e)(**args)
    logs.finish_tool(identity, value)
    req.contents.extend([
        types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(id=call_id, name='read_modelling_source', args=args))]),
        types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(id=call_id, name='read_modelling_source', response=value))])])
    return value


@pytest.mark.asyncio
async def test_locate_raw_source_by_lines_query_symbol_and_read_the_patched_version():
    e, calls, run = await setup()
    await run(source=SOURCE)
    read = source_reader_function(e)
    line = SOURCE[:SOURCE.index('@decorator')].count('\n') + 1
    located = await read('box', 'step.py', symbol='compute')
    assert located['text'] == '@decorator\ndef compute(value):\n    return value + 1\n'
    assert located['start_line'] == line and located['end_line'] == line + 2
    assert located['next_offset'] is None
    selected = await read('box', 'step.py', start_line=line, end_line=line + 2)
    assert selected['text'] == located['text']
    query = await read('box', 'step.py', query='return value + 1')
    assert query['matches'][0]['line'] == line + 2 and 'return value + 1' in query['text']
    assert 'def measure' in (await read('box', 'step.py', symbol='Inspector.measure'))['text']
    assert 'def conditional' in (await read('box', 'step.py', symbol='conditional'))['text']
    assert len(calls) == 2  # Reads and AST parsing dispatch no Sandbox commands.
    assert (await read('box', 'step.py', offset=len(SOURCE) + 1))['error']['code'] == 'source_range'
    assert (await read('box', 'step.py', query='missing text'))['error']['code'] == 'source_query'
    assert (await read('box', 'step.py', offset=1, symbol='compute'))['error']['code'] == 'source_selector'
    for field in ('start_line', 'end_line', 'query', 'symbol'):
        assert field in FunctionTool(read)._get_declaration().parameters_json_schema['properties']
    assert workflow_argument_error('read_modelling_source', dict(sandbox='box', script_path='step.py', symbol='compute')) is None
    await run(source='', edits=[{'old': 'return value + 1', 'new': 'return value + 2'}], previous_assessment=prior(e))
    patched = await read('box', 'step.py', symbol='compute')
    assert patched['version'] == located['version'] + 1
    assert 'return value + 2' in patched['text'] and 'return value + 1' not in patched['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('capacity,compact', [(100000, False), (16384, True)])
async def test_full_page_survives_host_callbacks_native_compaction_and_final_api_request(native, capacity, compact):
    e, _, run = await setup()
    await run(source=SOURCE)
    window = attach_window(e, native, capacity)
    older = types.Content(role='user', parts=[types.Part(text='previous settled detail ' * (18000 if compact else 3000))])
    req = request(e, older)
    page = await append_read(e, req, 'current-page', offset=0, limit=8000)
    sdk_history = deepcopy(req.contents)
    assert len(json.dumps(page)) > 8000
    summaries = []
    class SummaryModel:
        model = 'offline-summary'
        async def generate_content_async(self, submitted, stream=False):
            summaries.append(submitted)
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='Keep the task constraints. Current source is saved; edit its failing function.')]))
    manager = ManagedContext(MemoryContext(), 'agent', 'scope', 'run', SummaryModel(), e.original_request, budget=window.budget)
    manager.preserve_reasoning = True
    manager.trace, manager.trace_role = e.trace, 'structure_builder'
    async def reduce(submitted):
        reads = {id(c) for c in window.retrieval_context(submitted)}
        await manager.reduce_final_request(submitted, protected=e.protected_context(submitted, window),
                                           retain_protected=lambda c: id(c) in reads)
    window.reducer = reduce
    callbacks = _model_callbacks(e.trace, 'structure_builder', None, NS(types=types), evidence=e, output_limit=512,
                                 context_factory=lambda role, submitted, **kwargs: manager)
    await callbacks['before_model_callback'](NS(branch='one-assignment'), req)
    # This exceeds the retired 48k-character cap, but final token accounting
    # owns whether reduction is necessary. No plugin silently drops pages.
    assert 'previous settled detail' in req.model_dump_json()
    assert 'HOST working-context checkpoint' not in req.model_dump_json()
    sent = []
    class Provider:
        model = 'offline'
        async def generate_content_async(self, submitted, stream=False):
            sent.append(submitted)
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='continue')]))
    _ = [r async for r in recover_request(Provider(), req, e.trace, 'structure_builder')]
    assert bool(summaries) is compact
    wire = sent[0]
    response = next(p.function_response for c in wire.contents for p in c.parts if p.function_response)
    assert response.response == page and response.id == 'current-page'
    assert any(p.function_call and p.function_call.id == 'current-page' for c in wire.contents for p in c.parts)
    assert 'HOST STABLE CONTEXT' in wire.model_dump_json() and 'working_decision' in wire.model_dump_json()
    assert window.visible_reads[-1]['source']['version'] == e.latest_experiment().version
    assert window.last_stats['reduced_window'] is compact
    archived = next(r for r in e.trace.invocations.records('models').values() if r['role'] == 'structure_builder')
    assert archived['request']['contents'] == [c.model_dump(mode='json', exclude_none=True) for c in wire.contents]
    # ADK supplies its full history again. Native incremental ingestion must
    # not resurrect summarized contents or lose the explicitly pinned page.
    summary_count = len(summaries)
    second = LlmRequest(contents=sdk_history, config=types.GenerateContentConfig(max_output_tokens=512))
    latest = await append_read(e, second, 'function-page', symbol='compute')
    second.contents[-2].parts.insert(0, types.Part(text='Keep this concrete edit intention.', thought=True))
    await callbacks['before_model_callback'](NS(branch='one-assignment'), second)
    _ = [r async for r in recover_request(Provider(), second, e.trace, 'structure_builder')]
    assert len(summaries) == summary_count
    wire2 = sent[-1]
    responses = {p.function_response.id: p.function_response.response for c in wire2.contents for p in c.parts if p.function_response}
    assert responses['current-page'] == page and responses['function-page'] == latest
    assert any(p.thought and p.text == 'Keep this concrete edit intention.' for c in wire2.contents for p in c.parts)
    if compact:
        assert 'previous settled detail ' * 100 not in wire2.model_dump_json()
    # Validation/output recovery can invoke the callback on an already prepared
    # request. Derived HOST frames must not become persistent user turns.
    await callbacks['before_model_callback'](NS(branch='one-assignment'), second)
    known_frames = e._host_static_texts | e._host_evidence_texts
    assert not any(p.get('text') in known_frames for c in manager.checkpoint.contents for p in c.get('parts', []))
    for prefix in ('HOST RUN EVIDENCE', 'HOST STABLE CONTEXT'):
        assert sum((p.text or '').startswith(prefix) for c in second.contents for p in c.parts) == 1


@pytest.mark.asyncio
async def test_repeated_visible_reads_get_feedback_without_blocking_normal_retrieval_or_execution(native):
    e, _, run = await setup()
    await run(source=SOURCE)
    window = attach_window(e, native)
    req = request(e)
    def host():
        e.before_model('structure_builder', req, NS(types=types), 512)
        return e.working_decision()['retrieval_progress']
    host()
    await window.prepare(req)
    for name, offset, limit in [('first', 0, 1000), ('next-page', 500, 1000)]:
        await append_read(e, req, name, offset=offset, limit=limit)
        assert host() is None  # Overlap with genuinely new characters is progress.
        await window.prepare(req)
    await append_read(e, req, 'duplicate', offset=250, limit=250)
    assert host()['status'] == 'repeated_visible_read'
    await window.prepare(req)
    same_feedback = host()
    assert same_feedback['status'] == 'repeated_visible_read'  # Preparing twice is not another read.
    await append_read(e, req, 'duplicate-after-feedback', offset=300, limit=100)
    assert host()['status'] == 'needs_plan_review'
    assert e.block_reason is None and e.workflow.review_request is None
    assert not e.workflow.checkpoints  # Advisory evidence never accepts/rejects science.
    # Compacted-away pages can be restored, even with unchanged source bytes.
    req.contents = request(e).contents
    window.visible_reads = []
    await append_read(e, req, 'restore-page', offset=300, limit=100)
    assert host() is None
    await window.prepare(req)
    e.pending['native-operation'] = [('box', 'step.py')]
    await append_read(e, req, 'read-while-waiting', offset=300, limit=100)
    assert host() is None  # Waiting for a running command is not stuck.
    e.pending.clear()
    e.latest_experiment().version += 1
    await append_read(e, req, 'new-version', offset=300, limit=100)
    assert host() is None
    await window.prepare(req)
    from test_subtask_lifecycle import ACTION
    e.workflow.pending_experiment = {**ACTION, 'plan_revision': e.workflow.plan_revision,
                                     'stage': e.workflow.current, 'origin': 'local'}
    await append_read(e, req, 'new-experiment-question', offset=300, limit=100)
    assert host() is None  # An explicitly changed subtask can justify inspection.
    await window.prepare(req)
    window.set_scope('different assignment')
    await append_read(e, req, 'new-assignment', offset=300, limit=100)
    assert host() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('signed', [False, True])
async def test_native_specialist_context_preserves_protocol_required_reasoning(native, signed):
    e, _, run = await setup()
    await run(source=SOURCE)
    window = attach_window(e, native)
    window.capabilities = ContextCapabilities(preserve_tool_reasoning=not signed)
    req = request(e)
    await append_read(e, req, 'protocol-read', offset=0, limit=100)
    full = 'Provider-required original reasoning. ' * 1000
    req.contents[-2].parts.insert(0, types.Part(text=full, thought=True, thought_signature=b'signed' if signed else None))
    prefix = [c.model_dump(mode='json', exclude_none=True) for c in req.contents]
    manager = ManagedContext(MemoryContext(), 'agent', 'scope', 'run', NS(model='offline'), e.original_request, budget=window.budget)
    manager.trace, manager.trace_role, manager.preserve_reasoning = e.trace, 'structure_builder', True
    callbacks = _model_callbacks(e.trace, 'structure_builder', None, NS(types=types), evidence=e, output_limit=512,
                                 context_factory=lambda role, submitted, **kwargs: manager)
    await callbacks['before_model_callback'](NS(branch='protocol-assignment'), req)
    await window.prepare(req)
    assert any(p.thought and p.text == full for c in req.contents for p in c.parts)
    if signed:
        assert [c.model_dump(mode='json', exclude_none=True) for c in req.contents[:len(prefix)]] == prefix


def test_patch_feedback_identifies_edit_and_match_locations_without_writing(tmp_path):
    path = tmp_path / 'step.py'
    original = 'value = 1\nprint(value)\nprint(value)\n'
    path.write_text(original)
    with pytest.raises(SandboxValidationError, match=r'Edit 2: old text matched 2 times.*\[2, 3\]'):
        operate(tmp_path, 'step.py', 'edit', edits=[{'old': 'value = 1', 'new': 'value = 2'},
                                                  {'old': 'print(value)', 'new': 'print(2)'}])
    assert path.read_text() == original
    with pytest.raises(SandboxValidationError, match='Edit 1: old text matched 0 times'):
        operate(tmp_path, 'step.py', 'edit', edits=[{'old': 'missing', 'new': 'replacement'}])
    assert path.read_text() == original
