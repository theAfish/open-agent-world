"""Context focus, persistent counterexamples and local archive read-back."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from test_execution_steps import setup
from test_execution_cycle import history
from oaw_atomsculptor.harness import HarnessLimits, RunEvidence
from oaw_atomsculptor.runtime import _ModelTrace, _specialist_task_prompt
from backend.tests.test_invocation_logs import native


@pytest.mark.parametrize('content', [None, NS(), NS(role='user', parts=None),
    types.Content(role='model', parts=[types.Part(text='private reasoning', thought=True)]),
    types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(name='read', response={'text': 'tool data'}))]),
    types.Content(role='user', parts=[types.Part(text='   ')])])
def test_specialist_task_prompt_falls_back_without_using_thoughts_or_tool_results(content):
    request = NS(contents=[content] if content is not None else [])
    assert _specialist_task_prompt(request, 'original task') == 'original task'


def test_specialist_task_prompt_prefers_public_invocation_input_and_excludes_reasoning():
    initial = types.Content(role='user', parts=[types.Part(text='older history')])
    delegated = types.Content(role='user', parts=[types.Part(text='current delegation'),
        types.Part(text='private reasoning', thought=True), types.Part(text='signed', thought_signature=b'signed')])
    request = NS(contents=[initial])
    assert _specialist_task_prompt(request, 'original task', user_content=delegated) == 'current delegation'
    assert _specialist_task_prompt(request, 'original task') == 'older history'


@pytest.mark.asyncio
async def test_saved_source_reference_reads_exact_revision_and_never_labels_old_code_current(native):
    from backend.agents.context import ContextBudget
    from backend.agents.request_window import RequestWindow
    e, _, run = await setup()
    archive = native[-1].invocations
    e.trace.invocations = archive
    window = RequestWindow(ContextBudget(100000, 512), e.trace, 'structure_builder')
    e.trace.context_windows['structure_builder'] = window
    source = '# exact saved program\n' + 'print(1)\n' * 400
    identity = archive.start_tool('structure_builder', 'step-call', 0, 'run_modelling_step',
                                 {'sandbox': 'box', 'script_path': 'step.py', 'source': source})
    result = await run(source=source)
    def prepare():
        e.before_model('structure_builder', LlmRequest(contents=[], config=types.GenerateContentConfig()), NS(types=types), 81920)
        return e._current_source_head('structure_builder')
    assert 'source_reference' not in prepare()  # Still-running archive isn't readable.
    archive.finish_tool(identity, result)
    ref = prepare()['source_reference']
    assert ref['section'] == 'arguments' and ref['pointer'] == '/source'
    assert (await window.read_tool_result(ref['invocation_id'], ref['pointer'], section=ref['section'], limit=8000))['text'] == source
    assert ref['version'] == e.scripts[e.current_script_key].version
    # A later CAS revision cannot advertise the original full-source argument.
    item = e.scripts[e.current_script_key]
    item.version += 1
    e.script_sources[e.current_script_key] += '\n# changed source'
    assert 'source_reference' not in prepare()
    window.set_scope('another task')
    assert (await window.read_tool_result(identity, '/source', section='arguments'))['error']['code'] == 'permission_denied'


@pytest.mark.asyncio
@pytest.mark.parametrize('running', [False, True])
async def test_projected_measurement_can_read_complete_stdout_from_its_composite_archive(native, running):
    from backend.agents.context import ContextBudget
    from backend.agents.request_window import RequestWindow
    from test_measurement_handoffs import record
    raw = record(values={'diagnostic_json': 'x' * 3122}, constraints=[{'name': 'real_failure', 'passed': False}])
    e, _, run = await setup(stdout=raw, running=running)
    archive = native[-1].invocations
    e.trace.invocations = archive
    window = RequestWindow(ContextBudget(100000, 512), e.trace, 'structure_builder')
    e.trace.context_windows['structure_builder'] = window
    identity = archive.start_tool('structure_builder', 'step-call', 0, 'run_modelling_step', {})
    result = await run()
    assert e.execution_receipts[1]['call_id'] == 'step-call:execute_command'
    assert e.execution_receipts[1]['archive_call_id'] == 'step-call'
    assert e.measurement_reports[1]['constraints'][0]['passed'] is False
    # An incomplete parent archive is never a successful/readable receipt.
    assert e.current_experiment()['native_measurement_reference']['availability'] == 'unavailable'
    archive.finish_tool(identity, result)
    e.before_model('structure_builder', LlmRequest(contents=[], config=types.GenerateContentConfig()), NS(types=types), 81920)
    ref = e.current_experiment()['native_measurement_reference']
    assert ref['invocation_id'] == identity and ref['pointer'] == '/execution/stdout'
    recovered = await window.read_tool_result(identity, ref['pointer'])
    assert recovered['text'] == raw
    assert e.current_experiment()['measurements']['constraints'][0]['passed'] is False and not e.workflow.checkpoints
    assert len(archive.records('tools')) == 1  # No synthetic internal tool rows.
    window.set_scope('a different delegation')
    assert (await window.read_tool_result(identity, ref['pointer']))['error']['code'] == 'permission_denied'


@pytest.mark.asyncio
async def test_protocol_repair_source_excerpt_follows_the_record_builder():
    from test_measurement_handoffs import record
    source = ''.join(f'# computation line {i}\n' for i in range(150)) + (
        'flat_values = {"radius": 2.0}\n'
        'flat_values["diagnostic_json"] = json.dumps(diagnostics)\n'
        'record = {"oaw_measurements": {"values": flat_values}}\n'
        'print("OAW_MEASUREMENTS " + json.dumps(record))\n')
    e, _, run = await setup(stdout=record(values={'diagnostic_json': float('nan')}))
    await run(source=source)
    head = e._current_source_head('structure_builder')
    assert head['version'] == e.latest_experiment().version
    assert head['excerpt_reason'] == 'measurement_contract'
    assert 'flat_values = ' in head['source_excerpt'] and 'json.dumps(diagnostics)' in head['source_excerpt']
    assert head['producer_line'] == 151 and len(head['source_excerpt']) <= 2400


def test_specialist_preserves_full_unsigned_reasoning_when_adapter_requires_it():
    from backend.agents.context import ContextBudget
    from backend.agents.request_window import ContextCapabilities, RequestWindow
    from oaw_atomsculptor.runtime import _trim_specialist_history
    trace = _ModelTrace(asyncio.Queue())
    trace.context_windows["structure_builder"] = RequestWindow(ContextBudget(100000,512),trace,"structure_builder",
        capabilities=ContextCapabilities(preserve_tool_reasoning=True))
    full = "Required original reasoning. " * 1000
    req = LlmRequest(contents=[types.Content(role="model",parts=[types.Part(text=full,thought=True),
        types.Part.from_function_call(name="measure",args={})])])
    _trim_specialist_history(NS(types=types),trace,"structure_builder",req,NS(limits=HarnessLimits(retained_thought_chars=0)))
    assert req.contents[0].parts[0].text == full


@pytest.mark.asyncio
async def test_large_saved_script_does_not_refill_history_and_host_does_not_trim_it():
    e, _, run = await setup()
    source = "print('measured')\n" * 6000
    await run(source=source)
    e.limits = HarnessLimits(working_history_chars=8000)
    req = LlmRequest(contents=history(e.original_request), config=types.GenerateContentConfig())
    before = len(req.model_dump_json())
    e.before_model("structure_builder", req, NS(types=types), 81920)
    # Native accounting owns compaction. The HOST keeps a bounded excerpt,
    # rather than reinjecting the full 100K source or cutting history by chars.
    focused = len(req.model_dump_json())
    assert before < focused and source not in req.model_dump_json()
    assert 'measured-0' in req.model_dump_json() and 'measured-15' in req.model_dump_json()
    head = json.loads(req.contents[-1].parts[0].text.split("\n", 1)[1])["current_saved_source"]
    assert head["source_chars"] == len(source) and len(head["source_head"]) <= 800
    assert head["version"] == e.scripts[e.current_script_key].version
    for _ in range(3):
        e.before_model("structure_builder", req, NS(types=types), 81920)
    assert "HOST working-context checkpoint" not in req.model_dump_json()
    assert len(req.model_dump_json()) <= focused + 1000


def test_early_counterexamples_and_reviews_survive_many_newer_successes():
    e = RunEvidence(_ModelTrace(asyncio.Queue()), "Preserve original constraints")
    failure = {"measurement_revision": "revision-a", "kind": "candidate", "constraints": [
        {"name": "interface", "passed": False, "controls": [], "regions": []}]}
    e.measurement_reports[1] = failure
    for sequence in range(2, 35):
        e.measurement_reports[sequence] = {"measurement_revision": "revision-a", "constraints": [
            {"name": "interface", "passed": True, "controls": [], "regions": []}]}
    e.measurement_reports[35] = failure
    e.workflow.review_history = [{"reason": "Do not reuse the independently rejected approach",
        "stage": "build", "plan_revision": 1, "script_path": "early.py", 'script_version': "a" * 64}]
    e.workflow.review_history.extend({"reason": "later review", "stage": "deliver", "plan_revision": i,
        "script_path": f"later-{i}.py", 'script_version': "b" * 64} for i in range(2, 20))
    e.workflow.index = 1  # Current build contradictions outrank unrelated later delivery reviews.
    checkpoint = e.working_decision()["decision_checkpoint"]
    group = checkpoint["counterexamples"][0]
    assert group["first"]["execution_sequence"] == 1 and group["latest"]["execution_sequence"] == 35
    assert group["first"]["measurement_revision"] == "revision-a"
    assert checkpoint["rejected_methods"][0]["script_path"] == "early.py"
    assert "original measurement/plan revisions" in checkpoint["scope"]
    assert not any("validated" in row for row in checkpoint["counterexamples"])


@pytest.mark.asyncio
async def test_archive_reader_remains_declared_and_readable_while_review_blocks_new_execution():
    e, _, _ = await setup()
    e.workflow.review_request = {"reason": "Review measured failures"}
    e.workflow.host_signal = {"reason": "Review measured failures", "outcome": "needs_plan_review",
        "plan_revision": e.workflow.plan_revision}
    e.correction_feedback = {"role": "structure_builder", "plan_revision": e.workflow.plan_revision,
        "kind": "plan_review", "message": "Review measured failures"}
    req = LlmRequest(contents=[], config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[
        types.FunctionDeclaration(name="read_tool_result"), types.FunctionDeclaration(name="run_modelling_step"),
        types.FunctionDeclaration(name="finish_task")])]))
    e.before_model("structure_builder", req, NS(types=types), 81920)
    names = {d.name for t in req.config.tools for d in t.function_declarations}
    assert "read_tool_result" in names and "run_modelling_step" not in names
    hooks = e.tool_callbacks("structure_builder")
    assert await hooks["before_tool_callback"](NS(name="read_tool_result"), {"invocation_id": "issued-reference"}, NS()) is None
    blocked = await hooks["before_tool_callback"](NS(name="run_modelling_step"), {}, NS())
    assert blocked["error"]["code"] == "plan_review_required"


@pytest.mark.asyncio
async def test_new_source_advertises_only_new_mode_then_reopens_hash_based_repairs():
    from oaw_atomsculptor.execution_steps import STEP_TOOL_MODES, step_tool_schema
    from backend.agents.context import ContextBudget
    from backend.agents.request_window import RequestWindow, request_usage
    e, _, run = await setup()
    e.actions.sandbox_states['box'] = 'ready'
    # A large selected output reserve with sizable fixed instructions is valid
    # when only the current source mode is declared. No model call is needed.
    def req():
        return LlmRequest(contents=[types.Content(role='user', parts=[types.Part(text=e.original_request)])],
            config=types.GenerateContentConfig(max_output_tokens=81920,
                system_instruction='Current modelling instructions. ' * 1150,
                tools=[types.Tool(function_declarations=[types.FunctionDeclaration(name=name,
                    parameters_json_schema=step_tool_schema(name)) for name in STEP_TOOL_MODES])]))
    full = req()
    e.before_model('structure_builder', full, NS(types=types), 81920)
    ds = {d.name: d for t in full.config.tools for d in t.function_declarations}
    assert set(ds) == {'run_modelling_step'}
    assert 'previous_assessment' not in ds['run_modelling_step'].parameters_json_schema['properties']
    assert full.config.max_output_tokens == 81920
    window = RequestWindow(ContextBudget(128000, 81920), e.trace, 'structure_builder')
    await window.prepare(full)
    assert request_usage(full)['estimated_input_tokens'] < window.last_stats['input_capacity']
    await run()
    repaired = req()
    e.before_model('structure_builder', repaired, NS(types=types), 81920)
    ds = {d.name: d for t in repaired.config.tools for d in t.function_declarations}
    assert set(ds) == set(STEP_TOOL_MODES)
    assert all('previous_assessment' in d.parameters_json_schema['required'] for d in ds.values())
    assert repaired.config.max_output_tokens == 81920


@pytest.mark.asyncio
async def test_builder_view_reuses_committed_check_ids_without_dropping_other_stages():
    from copy import deepcopy
    from backend.agents.context import estimate
    e, _, _ = await setup()
    original = deepcopy(e.workflow.plan)
    for stage in e.workflow.plan:
        stage['checks'] = [f"{stage['id']} requirement {i}: inspect the measured populations and preserve the full acceptance conditions." for i in range(3)]
    original = deepcopy(e.workflow.plan)
    req = LlmRequest(contents=[types.Content(role='user', parts=[types.Part(text=e.original_request)])], config=types.GenerateContentConfig())
    def stable_frame():
        return json.loads(next(p.text.split('\n', 1)[1] for c in req.contents for p in c.parts
                              if p.text and p.text.startswith('HOST STABLE CONTEXT')))
    e.before_model('structure_builder', req, NS(types=types), 81920)
    frame = stable_frame(); context = frame['modelling_plan_context']
    mapping = {r['id']: r for r in context['requirements']}
    for stage in original:
        assert [mapping[identity]['check'] for identity in context['acceptance_checks'][stage['id']]] == stage['checks']
        assert all(mapping[identity]['stage'] == stage['id'] for identity in context['acceptance_checks'][stage['id']])
        for check in stage['checks']:
            assert json.dumps(frame).count(check) == 1
    legacy = deepcopy(frame)
    legacy['modelling_plan_context']['plan'] = [original[0]]
    legacy['modelling_plan_context']['acceptance_checks'] = {s['id']: s['checks'] for s in original}
    legacy['modelling_plan_context'].pop('check_text_source')
    assert estimate(frame) < estimate(legacy)
    assert e.workflow.plan == original  # Only the working request changed.
    ids = {r['check']: r['id'] for r in context['requirements']}
    e.workflow.index = 1
    e.workflow.plan_revision += 1
    e.workflow.plan[1]['checks'].reverse()
    e.before_model('structure_builder', req, NS(types=types), 81920)
    changed = stable_frame()['modelling_plan_context']
    assert changed['plan'][0]['id'] == 'build' and changed['plan_revision'] == e.workflow.plan_revision
    assert {r['check']: r['id'] for r in changed['requirements']} == ids
    assert len([p for c in req.contents for p in c.parts if p.text and p.text.startswith('HOST STABLE CONTEXT')]) == 1


@pytest.mark.asyncio
async def test_source_context_avoids_duplicate_excerpt_but_keeps_distinct_failure_lines():
    e, _, run = await setup()
    source = ''.join(f"print({i})\n" for i in range(150))
    await run(source=source)
    head = e._current_source_head('structure_builder')
    assert head['source_head'] == source[:800] and head['excerpt_source'] == 'source_head'
    assert 'source_excerpt' not in head
    key = e.current_script_key
    e.execution_observations[key] = f'File "{key[1]}", line 100\nValueError: bad measured input'
    failed = e._current_source_head('structure_builder')
    assert failed['error_line'] == 100 and failed['excerpt_start_line'] == 96
    assert 'print(99)' in failed['source_excerpt'] and failed['source_excerpt'] != failed['source_head']
    assert failed['version'] == e.scripts[key].version
