"""Exact native artifacts and unexecuted proposals remain useful across workflow gates."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest
from google.adk.tools import FunctionTool
from google.adk.models.lite_llm import _get_completion_inputs
from google.genai import types
from jsonschema import Draft202012Validator
from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.delivery import validation_step_function, is_independent_validation
from oaw_atomsculptor.execution_steps import source_reader_function
from test_execution_actions import prepare, request, declarations
from test_execution_steps import setup, prior
from test_measurement_handoffs import artifact, record
from test_subtask_lifecycle import ACTION, MEASURED
from test_target_recovery import correction
from test_typed_execution import native_sdk_fixture, COMMON


@pytest.mark.asyncio
async def test_current_native_measurement_can_be_assessed_and_depended_on_atomically():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    seq = e.latest_experiment().execution_sequence
    next_action = {**ACTION, 'evidence_refs': [f'execution:{seq}']}
    result = e.workflow.assess_previous(e, prior(e, next_experiment=next_action))
    assert result['ok'] and e.experiment_history[-1]['execution_sequence'] == seq
    assert e.workflow.pending_experiment['evidence_refs'] == [f'execution:{seq}']
    assert not e.workflow.checkpoints and e.workflow.current == 'probe'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['future', 'failed', 'missing_report', 'blocked', 'unknown_subtask'])
async def test_invalid_dependency_rejects_whole_assessment_without_partial_commit(case):
    e, _, run = await setup(stdout=MEASURED, exit_code=1 if case == 'failed' else 0)
    await run()
    seq = e.latest_experiment().execution_sequence
    action = {**ACTION, 'evidence_refs': [f'execution:{seq + (case == "future")}']}
    if case == 'missing_report': e.measurement_reports.clear()
    if case == 'unknown_subtask': action['depends_on'] = ['never-returned']
    pending = deepcopy(e.workflow.pending_experiment)
    result = e.workflow.assess_previous(e, prior(e, outcome='blocked' if case == 'blocked' else 'continue', next_experiment=action))
    assert not result['ok'] and not e.experiment_history and not e.experiment_observations
    assert e.workflow.pending_experiment == pending and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_local_proposal_and_misplaced_assessment_preserve_source_without_a_pending_contract():
    e, files, calls, invoke = await native_sdk_fixture()
    e.workflow.pending_experiment = None
    args = dict(COMMON, source='print("retained")', argv='wrong type', complete_stage=True)
    refused = await invoke('run_modelling_step', args)
    assert refused['error']['unexpected_fields'] == ['complete_stage'] and not calls
    recovery = e.steps.target_recovery(e)
    assert recovery and recovery['expected_script_path'] == 'probe.py'
    result = await invoke('resume_modelling_step', {**correction(recovery, 'probe.py'), 'corrections': {'argv': []}})
    assert result['ok'] and files['probe.py'] == args['source'] and len(calls) == 2
    assert not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_unassessed_execution_allows_retained_source_to_resume_with_explicit_assessment():
    e, files, calls, invoke = await native_sdk_fixture()
    await invoke('run_modelling_step', dict(COMMON, source='print(1)'))
    e.workflow.pending_experiment = None
    refused = await invoke('run_modelling_step', dict(COMMON, script_path='next.py', source='print(2)'))
    assert refused['error']['code'] == 'experiment_observation_required'
    recovery = e.steps.target_recovery(e)
    result = await invoke('resume_modelling_step', {**correction(recovery), 'corrections': {'previous_assessment': prior(e)}})
    assert result['ok'] and files['next.py'] == 'print(2)' and len(calls) == 4
    assert len(e.experiment_history) == 1 and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_source_reader_is_read_only_available_before_assessment_and_sha_bound():
    e, _, run = await setup()
    await run()
    item = e.latest_experiment()
    reader = source_reader_function(e)
    hooks = e.tool_callbacks('structure_builder')
    args = dict(sandbox='box', script_path='step.py',  offset=3, limit=5)
    assert await hooks['before_tool_callback'](NS(name='read_modelling_source'), args, NS()) is None
    value = await reader(**args)
    assert value['text'] == e.script_sources[('box', 'step.py')][3:8]
    assert not (await reader(**{**args, 'script_path': 'missing.py'}))['ok']
    assert not e.experiment_observations


@pytest.mark.asyncio
async def test_missing_stage_is_structured_before_sdk_and_does_not_commit_anything():
    e, _, _ = await setup()
    result = await e.tool_callbacks('structure_builder')['before_tool_callback'](
        NS(name='advance_modelling_stage'), {'hypothesis': 'One count'}, NS())
    assert result['error']['kind'] == 'argument_validation'
    assert result['error']['missing_fields'] == ['stage']
    assert result['error']['command_dispatched'] is False and not e.workflow.checkpoints


@pytest.mark.asyncio
@pytest.mark.parametrize('passed', [True, False])
async def test_independent_native_validation_visible_before_assessment_and_never_closes_stage(passed):
    a = artifact(path='candidate.extxyz')
    e, _, run = await setup(stdout=record(artifacts=[a]))
    await run()
    definitions = [ScopedToolDefinition('operation:run_skill_script', 'skill_run', 'native inspector')]
    e.configure_tools(definitions)
    calls = []
    async def invoke(capability, args):
        calls.append((capability, args))
        assert args['argv'][0] == 'validate_structure' and a['path'] in args['argv']
        return {'command_id': 'native-validation', 'exit_code': 0 if passed else 1,
                'stdout': json.dumps({'passed': passed, 'file': a['path']})}
    validate = validation_step_function(e, definitions, invoke, asyncio.Event())
    req = request({'validate_modelling_artifact', 'run_modelling_step', 'finish_task', 'advance_modelling_stage'})
    prepare(e, req)
    assert 'validate_modelling_artifact' in declarations(req)
    value = await validate('box', 'authorized-skill', a['path'], {'min_distance': 1.0})
    assert value['ok'] == passed and value['artifact_bound'] and len(calls) == 1
    assert not e.experiment_observations and not e.workflow.checkpoints
    wrong = await validate('box', 'authorized-skill', 'unknown.extxyz')
    assert not wrong['ok'] and len(calls) == 1
    e.executions['unknown'] = []
    assert not (await validate('box', 'authorized-skill', a['path']))['ok']
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_support_module_written_natively_once_without_becoming_a_science_experiment():
    e, calls, run = await setup()
    e.steps.enable_helpers()
    source = 'print("unaltered")'
    assert (await run(source=source))['ok']
    assert len(calls) == 3 and calls[0][1]['path'] == e.steps.helper_module + '.py'
    assert calls[1][1]['content'] == source and len(e.scripts) == 1 and e.execution_sequence == 1
    assert e.workflow.assess_previous(e, prior(e))['ok']
    assert (await run(script_path='next.py', source=source))['ok']
    assert len(calls) == 5 and len(e.scripts) == 2 and e.execution_sequence == 2
    assert e.steps.records[-1]['host_input_files'][e.steps.helper_module + '.py']
    assert e.workflow.assess_previous(e, prior(e))['ok']
    assert (await run(script_path='scripts/next.py', source=source))['ok']
    assert len(calls) == 8 and calls[-3][1]['path'] == 'scripts/' + e.steps.helper_module + '.py'
    assert e.steps.snapshot()['support_files'][-1]['path'].startswith('scripts/')


@pytest.mark.asyncio
async def test_read_and_validation_contracts_survive_the_actual_provider_wire():
    a = artifact(path='candidate.extxyz')
    e, _, run = await setup(stdout=record(artifacts=[a])); await run()
    functions = [validation_step_function(e, [], None, asyncio.Event()), source_reader_function(e)]
    req = request()
    req.config.tools = [types.Tool(function_declarations=[FunctionTool(f)._get_declaration() for f in functions])]
    prepare(e, req)
    _, wire, _, _, choice = await _get_completion_inputs(req, req.model)
    assert choice == 'required'
    schemas = {d['function']['name']:d['function']['parameters'] for d in wire}
    assert set(schemas) == {'validate_modelling_artifact', 'read_modelling_source'}
    for schema in schemas.values(): Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schemas['validate_modelling_artifact'])
    base = dict(sandbox='box', skill='skill', structure_path=a['path'])
    assert validator.is_valid({**base, 'checks': {'connectivity_cutoff':1.7, 'region_checks_file':'regions.json'}})
    assert not validator.is_valid({**base, 'checks': {'unknown_check': True}})


@pytest.mark.asyncio
async def test_stale_skill_cli_reports_interface_failure_instead_of_geometry_failure():
    a = artifact(path='candidate.extxyz')
    e, _, run = await setup(stdout=record(artifacts=[a])); await run()
    definitions = [ScopedToolDefinition('operation:run_skill_script','run_skill_script','native')]
    e.configure_tools(definitions)
    async def native(*_): return {'exit_code':2, 'stderr':'unrecognized arguments: --expected-sha256'}
    validate = validation_step_function(e, definitions, native, asyncio.Event())
    result = await validate('box','skill',a['path'])
    assert result['error']['code'] == 'validation_cli_error' and result['error']['command_dispatched']
    assert 'declared parameters' in result['error']['message'] and not e.workflow.checkpoints


@pytest.mark.parametrize('argv', [['validate_structure', {}, []], ['validate_structure', '--file-name', {}]])
def test_malformed_native_arguments_cannot_crash_validation_recognition(argv):
    assert is_independent_validation({'script':'scripts/structure_inspect.py', 'sandbox':'box', 'argv':argv}, NS()) is False
