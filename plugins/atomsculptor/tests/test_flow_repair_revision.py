"""Native preparation, local repair and anchored revisions, without paid model calls."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest
from google.genai import types
from jsonschema import Draft202012Validator

from oaw_atomsculptor.workflow import validate_plan
from test_execution_actions import prepare, request, declarations
from test_execution_steps import setup, prior
from test_workflow import tool, PLAN
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.tests.conftest import desktop_test_client_peer


def test_composite_step_prepares_empty_script_directory_using_real_native_files(runtime_client):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from test_workflow import evidence, activate
    client, backend, native = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client)
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    assert not (root / 'probe').exists()

    async def scenario():
        e = evidence()
        definitions = await provider.list_tools(agent['id'])
        e.configure_tools(definitions)
        await activate(e, delivery='report')
        e.steps.available = True
        e.steps.enable_helpers()
        async def invoke(capability, args):
            return await provider.invoke_tool(agent['id'], capability, args)
        result = await e.steps.run(e, definitions, invoke, asyncio.Event(), call_id='empty-dir',
            sandbox=sandbox['id'], script_path='probe/测量.py', source="print('native measurement')\n",
            question='One input count', expected_output='Measured count', stop_condition='One execution')
        assert result['step']['state'] == 'finished' and result['exit_code'] == 0
        assert set(e.scripts) == {(sandbox['id'], 'probe/测量.py')}
        assert e.execution_sequence == 1 and not e.workflow.checkpoints
        return e.steps.helper_module
    helper = client.portal.call(scenario)
    assert (root / 'probe/测量.py').is_file() and (root / 'probe' / (helper + '.py')).is_file()
    assert native.last_argv[-1] == 'probe/测量.py'


@pytest.mark.asyncio
@pytest.mark.parametrize('save_kind', ['validation', 'permission', 'transport'])
@pytest.mark.parametrize('helper', [False, True])
async def test_only_definite_native_save_rejection_retains_the_unexecuted_proposal(save_kind, helper):
    import hashlib
    from backend.agents.models import ScopedToolDefinition
    from backend.errors import ResourceValidationError, PermissionDeniedError
    from test_workflow import evidence, activate
    e = evidence()
    definitions = [ScopedToolDefinition('operation:'+n, n, 'Native') for n in ('write_sandbox_text_file','execute_command')]
    e.configure_tools(definitions); await activate(e, delivery='report'); e.steps.available = True
    if helper: e.steps.enable_helpers()
    calls, files = [], {}
    async def native(capability, args):
        calls.append((capability, args))
        if len(calls) == 1:
            raise {'validation': ResourceValidationError, 'permission': PermissionDeniedError,
                   'transport': ConnectionError}[save_kind]('save unavailable')
        if capability.endswith('write_sandbox_text_file'):
            files[args['path']] = args['content']
            return {'written':len(args['content'].encode())}
        return {'exit_code':0,'command_id':'recovered','stdout':'native count=1'}
    args = dict(sandbox='box', script_path='probe.py', source="print('retained')",
                question='One count',expected_output='Native count',stop_condition='One execution')
    rejected = await e.steps.run(e, definitions, native, asyncio.Event(), call_id='denied', **args)
    e.steps.retain_target_rejection(e,'run_modelling_step',args,rejected)
    recovery = e.steps.target_recovery(e)
    assert len(calls) == 1 and not e.scripts and not e.execution_sequence
    if save_kind == 'transport':
        assert recovery is None
        return
    assert recovery and not recovery['command_dispatched'] and not recovery['submitted_changes_saved']
    resumed = await e.steps.resume(e, definitions, native, asyncio.Event(), call_id='resume',
        subtask_id=recovery['subtask_id'], 
        script_path=recovery['expected_script_path'], reason='Native save is now available')
    assert resumed['ok'] and files['probe.py'] == args['source'] and e.execution_sequence == 1


@pytest.mark.asyncio
async def test_failed_commands_keep_model_judgments_but_cannot_complete_checkpoints():
    for change in [{'assumption_invalid': True}, {'outcome': 'needs_plan_review'}]:
        e, calls, run = await setup(exit_code=1)
        await run()
        result = e.workflow.assess_previous(e, prior(e, **change))
        assert result['observation']['outcome'] == change.get('outcome', 'continue')
        assert result['observation']['exit_code'] == 1 and len(calls) == 2
        assert e.workflow.advance(e, 'probe', 'box', 'step.py')['error']['code'] == 'execution_required'
        assert not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_metric_printed_before_a_code_failure_cannot_trigger_method_stagnation():
    metric = {'name':'residual','value':5,'direction':'minimize'}
    e, _, run = await setup(exit_code=1, stdout=json.dumps({'oaw_measurements': {'objective':metric}}))
    # Exercise the build observer without granting a probe/candidate checkpoint.
    e.workflow.index = 1
    e.workflow.plan[1]['experiment']['objective'] = {'name':'residual','direction':'minimize'}
    e.workflow.schedule_experiment(e.workflow.plan[1]['experiment'], origin='stage')
    await run(script_path='build.py')
    assert e.workflow.assess_previous(e,prior(e))['ok']
    await run(script_path='build.py', source='', 
              edits=[{'old':'measurement','new':'repaired'}])
    assert e.workflow.host_signal is None and e.workflow.local_repair(e)
    assert not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_code_failure_supports_explicit_rewrite_and_model_chosen_review():
    e, calls, run = await setup(exit_code=1)
    await run()
    req = request({'run_modelling_step','patch_modelling_step','rewrite_modelling_step','rerun_modelling_step',
                   'advance_modelling_stage','finish_task','read_modelling_source'})
    prepare(e, req)
    assert 'patch_modelling_step' in declarations(req) and 'run_modelling_step' not in declarations(req)
    rewrite = next(d for t in req.config.tools for d in t.function_declarations if d.name == 'rewrite_modelling_step')
    assert 'source' in rewrite.parameters_json_schema['required']
    assert 'rewrite_reason' not in rewrite.parameters_json_schema['required']
    replaced = await run(action_mode='rewrite', source="print('large rewrite')", previous_assessment=prior(e))
    assert replaced['step']['state'] == 'finished' and len(calls) == 4
    assert e.script_sources[('box','step.py')] == "print('large rewrite')" and not e.workflow.checkpoints
    for word in ['second','third']:
        assert (await run(source='', edits=[{'old':e.script_sources[('box','step.py')],'new':f"print('{word}')"}],
                           previous_assessment=prior(e)))['exit_code'] == 1
        assert e.workflow.host_signal is None
    review = await tool(e, 'finish_task', {'result': {'status': 'needs_plan_review', 'reason': 'Still a code error'}}, 'Task completed.')
    assert review == 'Task completed.' and e.workflow.review_request
    assert e.workflow.review_request['exit_code'] == 1
    blocked = await run(action_mode='rewrite', source="print('another rewrite')", previous_assessment=prior(e))
    assert blocked['error']['code'] == 'plan_review_required'
    assert not e.workflow.checkpoints


async def ready_for_review():
    def measurement():
        checks = [r['id'] for r in e.workflow.requirements() if r['stage'] == e.workflow.current]
        return json.dumps({'oaw_measurements': {'kind': 'baseline' if e.workflow.current == 'probe' else 'diagnostic',
            'coverage': {'tested': 3}, 'values': {'count': 3},
            'constraints': [{'name': 'measured', 'passed': True, 'regions': ['tested'], 'check_ids': checks}]}})
    e, _, run = await setup(stdout=measurement)
    await run(); assert e.workflow.assess_previous(e,prior(e))['ok']
    assert e.workflow.advance(e,'probe','box','step.py')['ok']
    await run(script_path='build.py')
    assert await tool(e,'finish_task',{'result':{'status':'needs_plan_review','reason':'Executed diagnostic disproves an assumption'}},'Task completed.')=='Task completed.'
    return e


@pytest.mark.asyncio
async def test_incremental_revision_supplies_frozen_stages_and_preserves_native_checkpoints():
    e = await ready_for_review(); frozen=deepcopy(e.workflow.plan[0]); checkpoints=deepcopy(e.workflow.checkpoints)
    revision=e.workflow.plan_revision
    delta={'status':'planned','plan_revision':revision,'stages':[{'id':'build','experiment':{'method':'x'*302}}]}
    assert await tool(e,'finish_task',{'result':delta},'Task completed.',role='planner')=='Task completed.'
    assert e.workflow.plan_revision==revision+1 and e.workflow.plan[0]==frozen
    assert e.workflow.plan[1]['experiment']['method']=='x'*302 and e.workflow.checkpoints==checkpoints
    assert e.workflow.plan[1]['checks']==PLAN['stages'][1]['checks']


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['stale','frozen','delivery','unknown_field'])
async def test_incremental_revision_rejects_invalid_changes_atomically(change):
    e = await ready_for_review(); old=deepcopy(e.workflow.plan)
    delta={'status':'planned','plan_revision':e.workflow.plan_revision,'stages':[{'id':'build','experiment':{'method':'New diagnostic'}}]}
    if change=='stale': delta['plan_revision']-=1
    if change=='frozen': delta['stages'][0]['id']='probe'
    if change=='delivery': delta['delivery']='document' # setup uses report
    if change=='unknown_field': delta['stages'][0]['pretend_complete']=True
    result=await tool(e,'finish_task',{'result':delta},'Task completed.',role='planner')
    assert result['error']['code']=='plan_required' and e.workflow.plan==old and e.workflow.plan_revision==1


@pytest.mark.asyncio
async def test_review_schema_offers_only_remaining_stages_and_real_dependency_ids():
    e=await ready_for_review()
    req=request({'finish_task'});e.before_model('planner',req,NS(types=types),81920)
    schema=req.config.tools[0].function_declarations[0].parameters_json_schema
    Draft202012Validator.check_schema(schema)
    root=schema['properties']['result'];stage=root['properties']['stages']['items']
    assert stage['properties']['id']['enum']==['build','deliver']
    native_id=e.workflow.checkpoints[0]['subtask_id']
    assert native_id in stage['properties']['experiment']['properties']['depends_on']['items']['enum']
    assert Draft202012Validator(schema).is_valid({'result':{'status':'planned','plan_revision':1,'stages':[{'id':'build'}]}})
    action={**e.workflow.plan[1]['experiment'],'depends_on':[native_id],'evidence_refs':['execution:1']}
    assert e.workflow.validate_dependencies(action,e,'box') is None
    assert e.workflow.validate_dependencies({**action,'depends_on':['fabricated']},e,'box')['error']['code']=='subtask_dependencies'


def test_short_prose_is_guidance_but_whole_payload_is_still_bounded():
    value=deepcopy(PLAN);value['stages'][1]['experiment']['method']='x'*302
    value['stages'][1]['goal']='y'*401
    parsed,issues=validate_plan(value);assert parsed and not issues
    value['stages'][1]['experiment']['method']='x'*13000
    assert validate_plan(value)[0] is None


def test_varying_invalid_counts_do_not_disguise_the_same_unresolved_plan_issue():
    from test_workflow import evidence
    e = evidence()
    for size in [13, 14, 15]:
        value = deepcopy(PLAN); value['stages'][1]['checks'] = ['same uncorrected field'] * size
        assert e.workflow.before_tool('planner','finish_task',{'result':value})['error']['code']=='plan_required'
    assert e.workflow.plan_rejections == 3 and e.workflow.repeated_plan_rejections == 3
