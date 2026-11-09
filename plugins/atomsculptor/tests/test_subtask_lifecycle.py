"""Measured local returns cannot confer global acceptance or replay a side effect."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.genai import types
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.models.lite_llm import _get_completion_inputs
from jsonschema import Draft202012Validator
from backend.agents.models import ScopedToolDefinition
from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
from oaw_atomsculptor.runtime import AtomSculptorRuntime
from test_execution_steps import setup, prior
from test_workflow import PLAN, REQUEST, tool
from test_execution_actions import prepare, request

ACTION = {"scope": "input_measurement", "artifact": "next.py", "question": "What is the remaining input count?",
          "method": "Read the retained inputs and print the count", "expected_output": "One measured count",
          "stop_condition": "One input measurement", "input_paths": []}
MEASURED = json.dumps({"oaw_measurements": {"kind": "diagnostic", "values": {"count": 4}}})


@pytest.mark.asyncio
async def test_normal_plan_and_next_stage_have_current_contracts():
    e, _, run = await setup(stdout="counts=4")
    pending = deepcopy(e.workflow.pending_experiment)
    assert pending['origin'] == 'stage' and pending['artifact'] == 'probe.py'
    assert e.working_decision()['current_subtask']['subtask_id'] == pending['subtask_id']
    await run()
    assert e.workflow.assess_previous(e, prior(e))['ok']
    assert e.workflow.advance(e, 'probe', 'box', 'step.py')['ok']
    assert e.workflow.pending_experiment['stage'] == 'build'
    assert e.workflow.pending_experiment['subtask_id'] != pending['subtask_id']


@pytest.mark.asyncio
async def test_structured_next_step_is_committed_with_assessment_and_blocks_wrong_target():
    e, calls, run = await setup(stdout=MEASURED)
    await run()
    result = e.workflow.assess_previous(e, prior(e, next_experiment=ACTION))
    assert result['ok'] and e.workflow.pending_experiment['artifact'] == 'next.py'
    before = len(calls)
    denied = await run(script_path='unrelated.py')
    assert denied['error']['code'] == 'planned_experiment_required' and len(calls) == before
    assert (await run(script_path='next.py'))['ok']


@pytest.mark.asyncio
async def test_local_checkpoint_only_commits_after_sdk_success_and_never_advances_stage():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    assert e.workflow.assess_previous(e, prior(e))['ok']
    args = {'result': {'status': 'subtask_complete', 'next_experiment': ACTION}}
    returned = await tool(e, 'finish_task', deepcopy(args), {'error': 'SDK completion failed'})
    assert returned['error'] and not e.workflow.subtask_history
    assert await tool(e, 'finish_task', deepcopy(args), 'Task completed.') == 'Task completed.'
    checkpoint = e.workflow.subtask_history[-1]
    assert checkpoint['measurements']['values']['count'] == 4
    assert not checkpoint['task_complete'] and not checkpoint['stage_completed']
    assert e.workflow.current == 'probe' and not e.workflow.checkpoints
    assert e.workflow.pending_experiment['origin'] == 'subtask'
    assert e.workflow.pending_experiment['artifact'] == 'next.py'
    again = await tool(e, 'finish_task', deepcopy(args), 'Task completed.')
    assert again['error']['code'] == 'subtask_duplicate' and len(e.workflow.subtask_history) == 1


@pytest.mark.asyncio
async def test_local_finish_wire_schema_and_native_receipt_agree_on_result_fields():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    req = request({"finish_task"})
    prepare(e, req)
    _, wire_tools, _, _, _ = await _get_completion_inputs(req, req.model)
    schema = next(t["function"]["parameters"] for t in wire_tools if t["function"]["name"] == "finish_task")
    validator = Draft202012Validator(schema)
    result = {"status": "subtask_complete", "next_experiment": ACTION}
    validator.validate({"result": result})
    for field, value in {"reason": "finished", "sandbox": "box", "script_path": "step.py",
                         "limitations": [], "files": [], "measurements": {}, "document": None}.items():
        assert not validator.is_valid({"result": {**result, field: value}}), field
        assert e.workflow.prepare_subtask_finish({**result, field: value}, e)["error"]["code"] == "subtask_fields"
    assert not validator.is_valid({"result": {"status": "subtask_complete"}})
    assert not validator.is_valid({"result": {"status": "needs_plan_review"}})
    validator.validate({"result": {"status": "blocked", "limitations": ["Remaining work"]}})
    assert await tool(e, "finish_task", {"result": result}, "Task completed.") == "Task completed."
    assert e.workflow.subtask_history[-1]["measurements"]["values"]["count"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('issue', ['unassessed', 'failed', 'unknown', 'stale', 'missing_measurement', 'empty_measurement', 'invented_input', 'new_acceptance'])
async def test_local_checkpoint_rejects_unconfirmed_or_changed_claims(issue):
    e, _, run = await setup(stdout='ordinary log' if issue == 'missing_measurement' else json.dumps({'oaw_measurements': {'kind': 'diagnostic'}}) if issue == 'empty_measurement' else MEASURED, exit_code=1 if issue == 'failed' else 0)
    await run()
    if issue != 'unassessed':
        assert e.workflow.assess_previous(e, prior(e))['ok']
    args = {'status': 'subtask_complete', 'next_experiment': deepcopy(ACTION)}
    if issue == 'unknown': e.executions['unknown-dispatch'] = []
    if issue == 'stale': args['command_id'] = 'old-command'
    if issue == 'invented_input': args['next_experiment']['input_paths'] = ['invented.npz']
    if issue == 'new_acceptance': args['next_experiment']['checks'] = ['discard previous checks']
    rejected = await tool(e, 'finish_task', {'result': args}, 'Task completed.')
    assert rejected['ok'] is False and not e.workflow.subtask_history and not e.workflow.checkpoints
    assert e.workflow.plan == PLAN['stages']


@pytest.mark.asyncio
async def test_patch_preserves_subtask_identity_across_repair():
    e, _, run = await setup(stdout=MEASURED, exit_code=1)
    first = await run()
    repaired = await run(source='', edits=[{'old': 'measurement', 'new': 'fixed'}],
                          previous_assessment=prior(e))
    assert repaired['step']['subtask_id'] == first['step']['subtask_id']


@pytest.mark.asyncio
async def test_real_adk_local_return_resumes_in_isolated_builder_branch():
    root_turns = 0
    builder_turns = 0
    scripts = {}
    native_calls = []
    received_local = None
    inspected_fresh = False

    class Fixture(BaseLlm):
        model: str = 'openai/subtask-fixture'
        async def generate_content_async(self, request, stream=False):
            nonlocal root_turns, builder_turns, received_local, inspected_fresh
            hosts = [json.loads(p.text.split('\n', 1)[1]) for c in request.contents for p in c.parts
                     if p.text and p.text.startswith('HOST RUN EVIDENCE')]
            host = hosts[-1]
            role = host['execution_role']
            if role == 'atom_sculptor':
                root_turns += 1
                if root_turns == 1: name, args = 'planner', {'request': REQUEST}
                elif root_turns in (2, 3): name, args = 'structure_builder', {'request': REQUEST}
                else:
                    yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='Remaining modelling checks are unmet.')]))
                    return
            elif role == 'planner': name, args = 'finish_task', {'result': PLAN}
            else:
                builder_turns += 1
                if builder_turns == 4:
                    for c in request.contents:
                        for p in c.parts:
                            if p.function_call: assert p.function_call.id != 'builder1-step'
                            if p.function_response: assert p.function_response.id != 'builder1-step'
                    assert host['working_decision']['current_subtask']['artifact'] == 'next.py'
                    assert host['modelling_workflow']['local_checkpoints'][-1]['execution_sequence'] == 1
                    inspected_fresh = True
                if builder_turns in (1, 4):
                    path = 'first.py' if builder_turns == 1 else 'next.py'
                    name, args = 'run_modelling_step', dict(sandbox='box', script_path=path, source="print('measured')",
                        question='One input count', expected_output='A measured count', stop_condition='One case')
                elif builder_turns in (2, 5):
                    name, args = 'advance_modelling_stage', dict(stage='observe', hypothesis='Measure input count', measurement='count=4',
                        outcome='continue', next_step='Measure the next unknown input')
                elif builder_turns == 3: name, args = 'finish_task', {'result': {'status': 'subtask_complete', 'next_experiment': ACTION}}
                else: name, args = 'finish_task', {'result': {'status': 'blocked', 'limitations': ['Remaining independent checks unmet.']}}
            call_id = 'builder1-step' if role == 'structure_builder' and builder_turns == 1 else f'{role}-{root_turns}-{builder_turns}'
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args))]))

    class Provider:
        async def list_tools(self, agent_id):
            return [ScopedToolDefinition('operation:'+name, name, 'native fixture')
                    for name in ('write_sandbox_text_file', 'execute_command', 'run_skill_script',
                                 'read_skill', 'import_atom_structure_file', 'inspect_atom_structure')]
        async def invoke_tool(self, agent_id, capability, args):
            import hashlib
            name = capability.removeprefix('operation:')
            native_calls.append(name)
            if name == 'write_sandbox_text_file':
                scripts[args['path']] = args['content']
                return {'written': len(args['content'].encode())}
            return {'exit_code': 0, 'command_id': args['argv'][1], 'stdout': MEASURED}
    resolver = NS(resolve_runtime=lambda _: RuntimeModelConnection('openai', 'fixture', 'https://model.invalid', None),
                  context_limits=lambda _: (128000, 81920))
    runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=resolver)
    runtime._model = lambda *_: Fixture()
    config = AgentConfig('fixture', 'Fixture')
    await runtime.create_agent(config)
    events = [event async for event in runtime.execute(config, NS(run_id='subtask-run', context_id='fixture'), NS(prompt=REQUEST, task_prompt=REQUEST))]
    checkpoints = [ev.payload for ev in events if ev.payload.get('workflow_event') == 'modelling_subtask']
    assert len(checkpoints) == 1 and not checkpoints[0]['task_complete']
    assert inspected_fresh and builder_turns == 6 and root_turns == 4
    assert native_calls == ['write_sandbox_text_file'] + ['write_sandbox_text_file', 'execute_command'] * 2
    assert events[-1].run_status == 'failed'  # Local progress cannot report global success.


@pytest.mark.asyncio
async def test_pending_assessment_schema_advertises_optional_next_experiment():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    declaration = types.FunctionDeclaration(name='advance_modelling_stage')
    request = NS(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[declaration])]))
    e.workflow.prepare_request('structure_builder', request, e)
    schema = request.config.tools[0].function_declarations[0].parameters_json_schema
    assert schema['properties']['stage']['enum'] == ['observe']
    assert 'next_experiment' in schema['properties'] and 'next_experiment' not in schema['required']


@pytest.mark.asyncio
async def test_host_filled_local_result_survives_model_plan_text_bound():
    stdout = json.dumps({'oaw_measurements': {'kind': 'diagnostic', 'values': {f'metric{i}': 'x' * 1000 for i in range(13)}}})
    e, _, run = await setup(stdout=stdout)
    await run()
    assert e.workflow.assess_previous(e, prior(e))['ok']
    args = {'result': {'status': 'subtask_complete', 'next_experiment': ACTION}}
    assert await tool(e, 'finish_task', args, 'Task completed.') == 'Task completed.'
    assert len(stdout) > 12000 and len(args['result']) < 12000 and len(e.workflow.subtask_history) == 1
    assert e.workflow.pending_experiment['artifact'] == 'next.py' and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_redundant_assessment_cannot_replace_next_subtask():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    assert e.workflow.assess_previous(e, prior(e, next_experiment=ACTION))['ok']
    changed = {**ACTION, 'artifact': 'different.py'}
    result = e.workflow.assess_previous(e, prior(e, next_experiment=changed), reuse_recorded=True)
    assert result['error']['code'] == 'assessment_conflict'
    assert e.workflow.pending_experiment['artifact'] == 'next.py'


@pytest.mark.asyncio
async def test_manual_native_tools_keep_the_same_subtask_receipt_after_assessment():
    import hashlib
    from test_workflow import evidence, activate
    e = evidence()
    await activate(e)
    identity = e.workflow.pending_experiment['subtask_id']
    source = "print('fixture')"
    await tool(e, 'write_sandbox_text_file', {'sandbox': 'box', 'path': 'probe.py', 'content': source},
               {'written': len(source)}, call_id='save')
    await tool(e, 'execute_command', {'sandbox': 'box', 'argv': ['python3', 'probe.py']},
               {'exit_code': 0, 'command_id': 'manual', 'stdout': MEASURED}, call_id='execute')
    assert e.workflow.assess_previous(e, prior(e))['ok']
    assert e.working_decision()['current_subtask']['subtask_id'] == identity
    assert await tool(e, 'finish_task', {'result': {'status': 'subtask_complete', 'next_experiment': ACTION}}, 'Task completed.') == 'Task completed.'
    assert e.workflow.subtask_history[-1]['subtask_id'] == identity


@pytest.mark.asyncio
async def test_sdk_return_for_changed_result_cannot_commit_prepared_checkpoint():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    assert e.workflow.assess_previous(e, prior(e))['ok']
    args = {'result': {'status': 'subtask_complete', 'next_experiment': ACTION}}
    assert e.workflow.before_tool('structure_builder', 'finish_task', args, e) is None
    e.workflow.observe_finish('structure_builder', {'result': {'status': 'subtask_complete'}}, 'Task completed.')
    assert not e.workflow.subtask_history and not e.workflow.checkpoints
