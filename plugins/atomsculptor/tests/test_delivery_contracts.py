"""Delivery routing and CLI contracts without model/network or generated execution."""
import ast
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types

from backend.agents.models import ScopedToolDefinition
from open_agent_world.plugin_api import AgentRuntimeError
from oaw_atomsculptor.delivery import DeliveryArguments, _validate_argv, _skill_argv, run_delivery_step
from oaw_atomsculptor.harness import HarnessLimits
from test_execution_actions import setup, request, prepare, declarations
from test_optimization_paths import delivery_setup
from test_workflow import activate, execute, tool

from skill_materialization import skill_scripts
SCRIPTS = skill_scripts("structure_inspect")


def signature_parser(command):
    # Use the production CLI and exact bundled function signature. Do not load
    # scientific imports or execute that function's body in backend tests.
    spec = importlib.util.spec_from_file_location('contract_cli', SCRIPTS / 'oaw_runtime.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    node = next(n for n in ast.parse((SCRIPTS / 'structure_inspect.py').read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == command)
    node.body = [ast.Pass()]
    tree = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    validation_spec = importlib.util.spec_from_file_location('contract_validation', SCRIPTS / 'structure_validation.py')
    validation = importlib.util.module_from_spec(validation_spec); validation_spec.loader.exec_module(validation)
    namespace = {'DistanceMode': validation.DistanceMode}
    exec(compile(tree, '<trusted signature only>', 'exec'), namespace)
    return module.build_cli_parser(prog='contract', description_lines=[], tool_functions={command: namespace[command]})


@pytest.mark.parametrize('boolean', [True, False, 'true', 'false'])
def test_actual_signature_parser_accepts_folder_and_explicit_boolean_values(boolean):
    argv = _validate_argv('folder/candidate.extxyz', {'expected_count': 80, 'nonperiodic': boolean,
                                                   'require_connected': False, 'folder': '.'})
    parsed = signature_parser('validate_structure').parse_args(argv)
    assert parsed.folder == '.' and parsed.file_name == 'folder/candidate.extxyz'
    assert parsed.nonperiodic == str(boolean).lower()
    assert parsed.require_connected == 'false'
    converted = _skill_argv('to_atomsculptor_document', {'folder': '.', 'file_name': 'folder/candidate.extxyz',
                                                      'output_name': 'candidate.json'})
    assert signature_parser('to_atomsculptor_document').parse_args(converted).output_name == 'candidate.json'


@pytest.mark.parametrize('values,field', [({'--folder': '.'}, '--folder'), ({'expected_count': True}, 'expected_count'),
    ({'expected_count': '80'}, 'expected_count'), ({'min_vacuum': float('nan')}, 'min_vacuum'),
    ({'min_vacuum': 10 ** 500}, 'min_vacuum'), ({'nonperiodic': 'maybe'}, 'nonperiodic'),
    ({'folder': []}, 'folder'), ({'folder': 'other'}, 'folder'), ({'file_name': 'other.xyz'}, 'file_name'),
    ({'unknown': 2}, 'unknown'), ({'distance_mode': 'minimum_image'}, 'distance_mode')])
def test_delivery_rejects_invalid_or_conflicting_arguments_before_any_dispatch(values, field):
    with pytest.raises(DeliveryArguments) as e:
        _validate_argv('candidate.extxyz', values)
    assert field in {issue['field'] for issue in e.value.issues}


def test_validation_enum_is_shared_by_host_schema_native_cli_and_validator():
    from typing import get_args
    from oaw_atomsculptor.delivery import validation_schema
    from test_geometry_validation import validation
    choices = get_args(validation.DistanceMode)
    assert tuple(validation_schema()['properties']['distance_mode']['enum']) == choices
    parser = signature_parser('validate_structure')
    for value in choices:
        argv = _validate_argv('candidate.extxyz', {'distance_mode': value})
        assert parser.parse_args(argv).distance_mode == value
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(['validate_structure', '--folder', '.', '--file-name', 'candidate.extxyz',
                           '--distance-mode', 'minimum_image'])
    assert caught.value.code == 2


@pytest.mark.asyncio
async def test_invalid_validation_enum_never_dispatches_and_native_input_failure_is_not_scientific_failure():
    from oaw_atomsculptor.delivery import validation_step_function
    from test_candidate_first_workflow import start_direct, candidate_report
    from test_measurement_handoffs import execute as measured_execute, record
    e = await start_direct('document')
    await measured_execute(e, 'build.py', record(**candidate_report(e)))
    definitions = [ScopedToolDefinition('operation:run_skill_script', 'run_skill_script', 'Native Skill')]
    e.configure_tools(definitions)
    calls = []
    async def invoke(capability, args):
        import json
        calls.append(args)
        return {'command_id': 'validator', 'exit_code': 1, 'stdout': json.dumps({
            'file': 'cnt.extxyz', 'passed': False, 'validation_status': 'unsupported',
            'error': 'Periodic distances require a finite nonsingular cell'})}
    validate = validation_step_function(e, definitions, invoke, asyncio.Event())
    result = await validate(sandbox='box', skill='inspector', structure_path='cnt.extxyz',
                            checks={'distance_mode': 'minimum_image'})
    assert result['error']['code'] == 'validation_arguments' and not calls
    assert result['error']['issues'][0]['allowed_values'] == ['auto', 'finite', 'periodic']
    result = await validate(sandbox='box', skill='inspector', structure_path='cnt.extxyz', checks={'distance_mode': 'periodic'})
    assert result['error']['code'] == 'validation_unavailable' and result['error']['command_dispatched']
    assert result['error']['kind'] == 'native_dependency'
    assert len(calls) == 1 and not e.outputs and 'checkpoint_action' not in result
    await e.tool_callbacks('structure_builder')['after_tool_callback'](SimpleNamespace(name='validate_modelling_artifact'),
        {'sandbox': 'box', 'structure_path': 'cnt.extxyz'}, SimpleNamespace(function_call_id='validation-result'), result)
    assert e.correction_feedback['kind'] == 'native_dependency'
    prepare(e, request({'advance_modelling_stage', 'finish_task'}))
    assert 'rejected before command dispatch' not in e.actions.last_action


@pytest.mark.asyncio
async def test_invalid_arguments_can_be_corrected_without_running_an_operation_or_replanning():
    e, definitions, invoke, stopped, calls = await delivery_setup()
    args = dict(call_id='delivery', sandbox='box', skill='skill', structure_path='cnt.extxyz', target='structure')
    rejected = await run_delivery_step(e, definitions, invoke, stopped, **args, validate={'--nonperiodic': True})
    assert rejected['error']['code'] == 'delivery_arguments' and not calls
    prepare(e, request({'run_delivery_step', 'finish_task', 'advance_modelling_stage'}))
    assert 'Correct only' in e.actions.last_action
    result = await run_delivery_step(e, definitions, invoke, stopped, **args, validate={'nonperiodic': True})
    assert result['ok'] and e.delivery_issue is None
    assert e.workflow.current == 'complete'
    assert sum(name == 'import_atom_structure_file' for name, _ in calls) == 1


@pytest.mark.asyncio
async def test_actual_cli_rejection_is_interface_blocker_and_does_not_retry_or_import():
    e, definitions, _, stopped, calls = await delivery_setup()
    async def invoke(capability, args):
        calls.append((capability, args))
        return {'exit_code': 2, 'command_id': 'parse-failed', 'stderr': 'required CLI parameter missing'}
    result = await run_delivery_step(e, definitions, invoke, stopped, call_id='delivery', sandbox='box',
                                    skill='skill', structure_path='cnt.extxyz', target='structure', validate={})
    assert result['error']['code'] == 'delivery_cli_error' and len(calls) == 1
    req = request({'run_delivery_step', 'finish_task', 'advance_modelling_stage'})
    prepare(e, req)
    assert declarations(req) == {'finish_task'}
    assert not e.imports and e.workflow.host_signal is None


@pytest.mark.asyncio
async def test_native_confirmed_target_can_fill_omission_but_multiple_targets_are_not_guessed():
    e, _, _, _, _ = await delivery_setup()
    e.imports['structure'] = e.confirmed['structure'] = 2
    result = e.workflow.advance(e, 'deliver')
    assert result['ok'] and result['checkpoint']['target'] == 'structure'
    e, _, _, _, _ = await delivery_setup()
    e.imports.update(one=2, two=3); e.confirmed.update(one=2, two=3)
    rejected = e.workflow.advance(e, 'deliver')
    assert rejected['error']['code'] == 'delivery_target_required' and rejected['error']['field'] == 'target'
    assert e.workflow.current == 'deliver'
    assert e.workflow.advance(e, 'deliver', target='two')['checkpoint']['revision'] == 3


@pytest.mark.asyncio
async def test_unknown_import_and_unconfirmed_revision_cannot_close_delivery():
    e, _, _, _, _ = await delivery_setup()
    e.imports['structure'] = 2
    rejected = e.workflow.advance(e, 'deliver')
    assert rejected['error']['code'] == 'read_back_required' and rejected['error']['target'] == 'structure'
    assert rejected['error']['imported_revision'] == 2
    e.confirmed['structure'] = 2; e.delivery_uncertainty['structure'] = {'file': 'unknown.json'}
    assert e.workflow.advance(e, 'deliver')['error']['code'] == 'delivery_import_unknown'


@pytest.mark.asyncio
async def test_confirmed_manual_delivery_closes_without_revalidation_conversion_or_import():
    e, definitions, invoke, stopped, calls = await delivery_setup()
    e.imports['structure'] = e.confirmed['structure'] = 2
    req = request({'run_delivery_step', 'advance_modelling_stage', 'finish_task'})
    prepare(e, req)
    assert declarations(req) == {'advance_modelling_stage', 'finish_task'}
    stage = next(d for t in req.config.tools for d in t.function_declarations if d.name == 'advance_modelling_stage')
    assert stage.parameters_json_schema['required'] == ['stage', 'target']
    assert stage.parameters_json_schema['properties']['stage']['enum'] == ['deliver']
    result = await run_delivery_step(e, definitions, invoke, stopped, call_id='delivery', sandbox='box',
                                    skill='skill', structure_path='cnt.extxyz', target='structure', validate={})
    assert result['ok'] and result['receipt_reused'] and not calls
    assert result['read_back']['revision'] == 2 and result['read_back']['summary'] is None
    assert e.workflow.current == 'complete'
    prepare(e, req := request({'run_delivery_step', 'advance_modelling_stage', 'finish_task'}))
    assert declarations(req) == {'finish_task'}


@pytest.mark.asyncio
@pytest.mark.parametrize('has_summary', [False, True])
async def test_delivery_exposes_native_read_back_scope_without_extra_inspection(has_summary):
    e, definitions, invoke, stopped, calls = await delivery_setup()
    from oaw_atomsculptor import _structure_overview
    from oaw_atomsculptor.structure import Atom, StructureDocument
    document = StructureDocument(atoms=[Atom(id=i, symbol='C', x=i, y=0, z=0) for i in range(80)]).model_dump(mode='json')
    async def native(capability, args):
        result = await invoke(capability, args)
        if capability == 'operation:inspect_atom_structure':
            result = _structure_overview(document, revision=result['revision'], detail='summary', offset=0, limit=10)
        if capability in {'operation:inspect_atom_structure', 'operation:import_atom_structure_file'}:
            result = dict(result)
            if has_summary:
                result['summary'] = {'atom_count': 80, 'selected_atom_count': 0}
            else:
                result.pop('summary', None)
        return result
    result = await run_delivery_step(e, definitions, native, stopped, call_id='delivery', sandbox='box',
                                    skill='skill-node', structure_path='cnt.extxyz', target='structure')
    assert result['ok'] and result['read_back']['revision'] == result['import']['revision'] == 2
    assert result['read_back']['source'] == 'native_inspect_atom_structure'
    assert result['read_back']['summary'] == ({'atom_count': 80, 'selected_atom_count': 0} if has_summary else None)
    assert result['read_back']['structure']['formula'] == 'C80'
    assert result['read_back']['structure']['atom_count'] == 80
    assert 'atoms' not in result['read_back']['structure']
    assert result['confirmation']['atom_count_match'] is (True if has_summary else None)
    assert 'not re-run' in result['confirmation']['scope']
    assert sum(n == 'inspect_atom_structure' for n, _ in calls) == 2
    assert sum(n == 'import_atom_structure_file' for n, _ in calls) == 1


@pytest.mark.asyncio
async def test_delivery_does_not_claim_confirmation_for_a_mismatched_read_back_count():
    e, definitions, invoke, stopped, calls = await delivery_setup()
    async def native(capability, args):
        result = await invoke(capability, args)
        if capability == 'operation:import_atom_structure_file':
            return {**result, 'summary': {'atom_count': 80}}
        if capability == 'operation:inspect_atom_structure' and e.imports:
            return {**result, 'summary': {'atom_count': 79}}
        return result
    result = await run_delivery_step(e, definitions, native, stopped, call_id='delivery', sandbox='box',
                                    skill='skill-node', structure_path='cnt.extxyz', target='structure')
    assert result['error']['code'] == 'delivery_read_back_required'
    assert e.workflow.current == 'deliver' and not e.confirmed
    assert sum(n == 'import_atom_structure_file' for n, _ in calls) == 1


@pytest.mark.asyncio
async def test_stagnation_closes_verified_delivery_once_then_allows_final_report():
    e, _, _, _, _ = await delivery_setup()
    e.limits = HarnessLimits(model_requests_without_progress=2)
    e.imports['structure'] = e.confirmed['structure'] = 2
    for _ in range(4):
        prepare(e, request({'advance_modelling_stage', 'finish_task'}))
    assert e.workflow.current == 'complete'
    assert len([x for x in e.workflow.checkpoints if x['stage'] == 'deliver']) == 1
    assert e.actions.allowed == {'finish_task'}
    prepare(e, request({'advance_modelling_stage', 'finish_task'}))
    with pytest.raises(AgentRuntimeError, match='checkpoints complete, but final reporting stalled'):
        prepare(e, request({'advance_modelling_stage', 'finish_task'}))
    assert e.workflow.current == 'complete'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['ambiguous', 'other_unconfirmed', 'unconfirmed', 'unknown'])
async def test_stagnation_never_fills_ambiguous_unconfirmed_or_unknown_delivery(state):
    e, _, _, _, _ = await delivery_setup()
    e.limits = HarnessLimits(model_requests_without_progress=2)
    e.imports['structure'] = 2
    if state != 'unconfirmed':
        e.confirmed['structure'] = 2
    if state == 'ambiguous':
        e.imports['other'] = e.confirmed['other'] = 3
    if state == 'other_unconfirmed':
        e.imports['other'] = 3
    if state == 'unknown':
        e.delivery_uncertainty['structure'] = {'file': 'unknown.json'}
    for _ in range(2):
        prepare(e, request({'advance_modelling_stage', 'finish_task'}))
    with pytest.raises(AgentRuntimeError, match='no new execution/assessment/checkpoint'):
        prepare(e, request({'advance_modelling_stage', 'finish_task'}))
    assert e.workflow.current == 'deliver'
    assert not any(c['stage'] == 'deliver' for c in e.workflow.checkpoints)


@pytest.mark.asyncio
async def test_deliver_schema_keeps_target_even_with_a_later_unassessed_script():
    e, _, _, _, _ = await delivery_setup()
    await execute(e, 'later.py', result={'exit_code': 0, 'stdout': 'later diagnostic'})
    e.experiment_records.pop((e.latest_experiment().execution_sequence, e.workflow.plan_revision))
    e.imports['structure'] = e.confirmed['structure'] = 2
    req = request({'advance_modelling_stage', 'finish_task'})
    prepare(e, req)
    schema = next(d for t in req.config.tools for d in t.function_declarations
                  if d.name == 'advance_modelling_stage').parameters_json_schema
    assert schema['required'] == ['stage', 'target']
    assert schema['properties']['stage']['enum'] == ['deliver']


@pytest.mark.asyncio
async def test_unconfirmed_deliver_can_assess_a_later_script_before_running_pipeline():
    e, _, _, _, _ = await delivery_setup()
    await execute(e, 'later.py', result={'exit_code': 0, 'stdout': 'later diagnostic'})
    e.experiment_records.pop((e.latest_experiment().execution_sequence, e.workflow.plan_revision))
    req = request({'advance_modelling_stage', 'finish_task'})
    prepare(e, req)
    schema = next(d for t in req.config.tools for d in t.function_declarations
                  if d.name == 'advance_modelling_stage').parameters_json_schema
    assert schema['properties']['stage']['enum'] == ['observe']
    assert 'hypothesis' in schema['required'] and 'target' not in schema['required']
    assert e.workflow.advance(e, 'observe', hypothesis='Check saved diagnostic', measurement='later diagnostic',
                              outcome='ready', next_step='Deliver the validated candidate')['ok']
    req = request({'advance_modelling_stage', 'finish_task'})
    prepare(e, req)
    schema = next(d for t in req.config.tools for d in t.function_declarations
                  if d.name == 'advance_modelling_stage').parameters_json_schema
    assert schema['required'] == ['stage', 'target']


@pytest.mark.asyncio
async def test_parallel_reads_do_not_require_any_computation_or_reset_progress():
    e = setup(names={'read_skills', 'preview_file'})
    parts = [types.Part(function_call=types.FunctionCall(name=n, args={}))
             for n in ('read_skills', 'preview_file')]
    assert not e.actions.dependent_batch(e, parts)
    parts.append(types.Part(function_call=types.FunctionCall(name='execute_command', args={})))
    assert e.actions.dependent_batch(e, parts)


@pytest.mark.asyncio
async def test_assessment_only_and_read_dependencies_remain_available_without_new_placeholder_script():
    names = {'write_sandbox_text_file', 'execute_command', 'advance_modelling_stage', 'finish_task',
             'read_skills', 'preview_file', 'run_skill_script', 'inspect_sandbox'}
    e = setup(names=names); await activate(e, delivery='report')
    e.steps.available = True
    await tool(e, 'inspect_sandbox', {'sandbox': 'box'}, {'state': 'ready'})
    await tool(e, 'write_sandbox_text_file', {'sandbox': 'box', 'path': 'probe.py', 'content': 'print(32)'}, {'written': 9})
    await tool(e, 'execute_command', {'sandbox': 'box', 'argv': ['python3', 'probe.py']},
               {'exit_code': 0, 'command_id': 'actual-probe', 'stdout': 'count=32'})
    req = request(names | {'run_modelling_step'}); prepare(e, req)
    assert {'read_skills', 'preview_file', 'advance_modelling_stage'} <= declarations(req)
    assert 'assessment-only' in e.actions.last_action
    before = e.execution_sequence
    assert await tool(e, 'read_skills', {}, {'instructions': 'Fixture guidance'}) == {'instructions': 'Fixture guidance'}
    assert e.execution_sequence == before and len(e.scripts) == 1
    observed = e.workflow.advance(e, 'observe', hypothesis='Count input', measurement='count=32',
                                  outcome='ready', next_step='Advance the probe checkpoint')
    assert observed['ok'] and e.execution_sequence == before


@pytest.mark.asyncio
async def test_real_adk_delivery_declaration_carries_typed_acceptance_parameters():
    from google.adk.tools.function_tool import FunctionTool
    from oaw_atomsculptor.delivery import delivery_step_function
    e, definitions, invoke, stopped, _ = await delivery_setup()
    declaration = FunctionTool(delivery_step_function(e, definitions, invoke, stopped))._get_declaration()
    req = request({'advance_modelling_stage', 'finish_task'})
    req.config.tools[0].function_declarations.append(declaration)
    prepare(e, req)
    schema = declaration.parameters_json_schema
    assert declaration.parameters is None
    assert schema['properties']['validate']['additionalProperties'] is False
    assert schema['properties']['validate']['properties']['nonperiodic']['type'] == 'boolean'
    assert schema['properties']['validate']['properties']['distance_mode']['enum'] == ['auto', 'finite', 'periodic']
    assert '--folder' not in schema['properties']['validate']['properties']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['tool_timeout', 'tool_execution_error', 'tool_cancelled', 'exception', 'cancel', 'unexpected'])
async def test_uncertain_import_cannot_be_replayed(failure):
    import asyncio
    e, definitions, invoke, stopped, calls = await delivery_setup()
    async def interrupted(capability, args):
        if capability.endswith(':import_atom_structure_file'):
            # Model the mutation being committed before its response is lost.
            await invoke(capability, args)
            if failure == 'exception':
                raise TimeoutError('response lost')
            if failure == 'cancel':
                raise asyncio.CancelledError()
            if failure == 'unexpected':
                raise RuntimeError('response lost after import')
            return {'ok': False, 'error': {'code': failure}}
        return await invoke(capability, args)
    args = dict(call_id='delivery', sandbox='box', skill='skill', structure_path='cnt.extxyz', target='structure')
    result = await run_delivery_step(e, definitions, interrupted, stopped, **args)
    assert result['error']['code'] == 'delivery_import_unknown'
    assert 'structure' in e.delivery_uncertainty
    again = await run_delivery_step(e, definitions, invoke, stopped, **args)
    assert again['error']['code'] == 'delivery_import_unknown'
    assert sum(name == 'import_atom_structure_file' for name, _ in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('raised', [False, True])
async def test_revision_conflict_remains_repairable_without_unknown_import_lock(raised):
    e, definitions, invoke, stopped, calls = await delivery_setup()
    async def conflict(capability, args):
        if capability.endswith(':import_atom_structure_file'):
            if raised:
                from backend.errors import RevisionConflictError
                raise RevisionConflictError('Live target revision changed')
            return {'ok': False, 'error': {'code': 'revision_conflict'}}
        return await invoke(capability, args)
    args = dict(call_id='delivery', sandbox='box', skill='skill', structure_path='cnt.extxyz', target='structure')
    result = await run_delivery_step(e, definitions, conflict, stopped, **args)
    assert result['error']['code'] == 'delivery_import_failed'
    assert not e.delivery_uncertainty
    assert (await run_delivery_step(e, definitions, invoke, stopped, **args))['ok']


@pytest.mark.asyncio
async def test_unknown_import_routes_to_inspection_and_blocked_report():
    e, _, _, _, _ = await delivery_setup()
    e.delivery_uncertainty['structure'] = {'file': 'cnt.json', 'expected_revision': 1}
    req = request({'run_delivery_step', 'import_atom_structure_file', 'inspect_atom_structure',
                   'inspect_sandbox', 'read_tool_result', 'advance_modelling_stage', 'finish_task'})
    prepare(e, req)
    assert declarations(req) == {'inspect_atom_structure', 'read_tool_result', 'finish_task'}
    assert 'unknown' in e.actions.last_action and 'do not replay' in e.actions.last_action
    # A changed live revision alone is not proof that the pending import committed.
    await tool(e, 'inspect_atom_structure', {'target': 'structure'}, {'revision': 2, 'summary': {'atom_count': 80}})
    assert 'structure' in e.delivery_uncertainty and not e.confirmed
    result = await tool(e, 'finish_task', {'result': {'status': 'blocked', 'reason': 'Import outcome unknown'}}, 'Task completed.')
    assert result == 'Task completed.'


@pytest.mark.asyncio
async def test_successful_import_with_failed_read_back_routes_only_to_inspection():
    e, definitions, invoke, stopped, calls = await delivery_setup()
    async def lose_read_back(capability, args):
        if capability.endswith(':inspect_atom_structure') and any(n == 'import_atom_structure_file' for n, _ in calls):
            return {'ok': False, 'error': {'code': 'tool_timeout'}}
        return await invoke(capability, args)
    args = dict(call_id='delivery', sandbox='box', skill='skill', structure_path='cnt.extxyz', target='structure')
    result = await run_delivery_step(e, definitions, lose_read_back, stopped, **args)
    assert result['error']['code'] == 'delivery_read_back_required' and not e.delivery_uncertainty
    req = request({'run_delivery_step', 'import_atom_structure_file', 'inspect_atom_structure',
                   'advance_modelling_stage', 'finish_task'})
    prepare(e, req)
    assert declarations(req) == {'inspect_atom_structure', 'finish_task'}
    assert await tool(e, 'inspect_atom_structure', {'target': 'structure'},
                      {'revision': 2})
    prepare(e, req := request({'run_delivery_step', 'inspect_atom_structure', 'advance_modelling_stage', 'finish_task'}))
    assert declarations(req) == {'advance_modelling_stage', 'finish_task'}
    assert (await run_delivery_step(e, definitions, invoke, stopped, **args))['receipt_reused']
    assert sum(name == 'import_atom_structure_file' for name, _ in calls) == 1


@pytest.mark.asyncio
async def test_unknown_native_replacement_blocks_both_structure_write_paths():
    e, _, _, _, _ = await delivery_setup()
    result = await tool(e, 'replace_atom_structure', {'target': 'structure', 'expected_revision': 1},
                        {'ok': False, 'error': {'code': 'tool_timeout'}}, call_id='replacement')
    assert result['error']['code'] == 'tool_timeout'
    assert e.delivery_uncertainty['structure']['call_id'] == 'replacement'
    for name in ('replace_atom_structure', 'import_atom_structure_file'):
        rejected = await tool(e, name, {'target': 'structure', 'expected_revision': 2}, {'revision': 3}, call_id='retry')
        assert rejected['error']['code'] == 'delivery_import_unknown'
    assert not e.imports and not e.confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['same', 'omitted', 'parameters', 'skill', 'environment', 'candidate'])
async def test_delivery_reuses_only_the_same_validation_identity(change):
    from oaw_atomsculptor.delivery import validation_identity
    e, definitions, invoke, stopped, calls = await delivery_setup()
    checks = {'expected_count': 80, 'nonperiodic': True, 'min_vacuum': 8.0}
    argv = _validate_argv('cnt.extxyz', checks)
    e._observe_skill('structure_builder', {'sandbox': 'box', 'skill': 'skill',
        'script': 'scripts/structure_inspect.py', 'argv': argv},
        {'exit_code': 0, 'command_id': 'prior-validation', 'stdout': '{"passed": true, "file": "cnt.extxyz"}'})
    args = dict(call_id='delivery', sandbox='box', skill='skill', structure_path='cnt.extxyz', target='structure')
    requested = dict(checks)
    if change == 'same':
        requested.update(nonperiodic='true', min_vacuum=8)  # CLI-equivalent spelling.
    if change == 'omitted':
        requested = None
    if change == 'parameters':
        requested['min_vacuum'] = 9
    if change == 'skill':
        args['skill'] = 'another-skill'
    if change == 'environment':
        e.environment_revisions['box'] = 1
    if change == 'candidate':
        e._invalidate_file('box', 'cnt.extxyz', 'written')
    result = await run_delivery_step(e, definitions, invoke, stopped, **args, validate=requested)
    assert result['ok'], result
    validations = [a for name, a in calls if name == 'run_skill_script' and a['argv'][0] == 'validate_structure']
    assert len(validations) == (0 if change in {'same', 'omitted'} else 1)
    assert result['steps'][0]['receipt_reused'] == (change in {'same', 'omitted'})
    assert sum(name == 'import_atom_structure_file' for name, _ in calls) == 1


def test_validation_identity_normalizes_defaults_and_does_not_cache_mutable_rules_file():
    from oaw_atomsculptor.delivery import validation_identity
    def identity(checks):
        return validation_identity({'script': 'scripts/structure_inspect.py', 'skill': 'skill',
                                    'argv': _validate_argv('candidate.extxyz', checks)}, 0)
    assert identity({}) == identity({'min_distance': 0.1, 'nonperiodic': False, 'distance_mode': 'auto'})
    assert identity({'region_checks_file': 'rules.json'}) is None
