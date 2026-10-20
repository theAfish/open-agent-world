"""Structured experiment assessment uses receipts, not hard-coded geometry truth."""
import json
from types import SimpleNamespace as NS

import pytest
from google.genai import types
from google.adk.models.llm_request import LlmRequest
from oaw_atomsculptor.harness import RunEvidence, HarnessLimits
from test_workflow import evidence, activate, tool, PLAN
from test_execution_actions import request, prepare, declarations


async def finished(e, *, code='print(0)', result=None):
    await tool(e, 'write_sandbox_text_file', {'sandbox': 'box', 'path': 'build.py', 'content': code}, {'written': len(code)})
    await tool(e, 'execute_command', {'sandbox': 'box', 'argv': ['python3', 'build.py']}, result or {'exit_code': 0, 'stdout': 'cross_bonds=0'})


def assess(e, *, outcome='continue', measurement='cross_bonds=0; seam remains disconnected'):
    return e.workflow.advance(e, 'observe', 'box', 'build.py', hypothesis='A direct connection exists',
        measurement=measurement, outcome=outcome, next_step='Measure the actual boundary in a small script')


@pytest.mark.asyncio
async def test_exit_zero_requires_assessment_before_rewrite_validation_or_stage_advance():
    e = evidence(); await activate(e); await finished(e)
    for name, args in [('write_sandbox_text_file', {'sandbox': 'box', 'path': 'next.py', 'content': 'print(1)'}),
                       ('execute_command', {'sandbox': 'box', 'argv': ['python3', 'build.py']}),
                       ('run_skill_script', {'sandbox': 'box', 'argv': ['validate_structure']})]:
        assert (await tool(e, name, args, {}))['error']['code'] == 'experiment_observation_required'
    assert e.workflow.advance(e, 'probe', 'box', 'build.py')['error']['code'] == 'experiment_observation_required'
    req = request(); prepare(e, req)
    assert declarations(req) == {'finish_task', 'advance_modelling_stage'}
    schema = next(d.parameters_json_schema for t in req.config.tools for d in t.function_declarations
                  if d.name == 'advance_modelling_stage')
    assert schema['properties']['measurement']['maxLength'] == 12000
    assert 'needs_plan_review' in schema['properties']['outcome']['enum']
    assert assess(e)['ok']
    assert e.workflow.current == 'probe' and not e.outputs
    assert assess(e)['error']['code'] == 'observation_already_recorded'
    assert e.workflow.advance(e, 'probe', 'box', 'build.py')['ok']


@pytest.mark.asyncio
async def test_contradiction_requires_existing_planner_review_then_new_execution():
    e = evidence(); await activate(e); await finished(e)
    assert assess(e, outcome='needs_plan_review')['ok']
    assert (await tool(e, 'write_sandbox_text_file', {'sandbox': 'box', 'path': 'build.py', 'content': 'print(8)'}, {}))['error']['code'] == 'plan_review_required'
    req = request(); prepare(e, req)
    assert declarations(req) == {'finish_task'}
    args = {'result': {'status': 'needs_plan_review', 'reason': 'Actual cross_bonds=0 contradicts direct-seam assumption',
                       'sandbox': 'box', 'script_path': 'build.py'}}
    assert await tool(e, 'finish_task', args, 'Task completed.') == 'Task completed.'
    assert e.workflow.review_request and e.workflow.before_delegation('structure_builder')['error']['code'] == 'plan_review_required'
    assert await tool(e, 'finish_task', {'result': PLAN}, 'Task completed.', role='planner') == 'Task completed.'
    assert e.workflow.plan_revision == 2 and e.workflow.review_request is None
    assert e.workflow.before_delegation('structure_builder') is None
    # Reassess the actual measurement under the revised plan, without replaying tools.
    assert not e.workflow.advance(e, 'probe', 'box', 'build.py')['ok']
    assert assess(e, outcome='ready', measurement='cross_bonds=0; revised plan treats seam construction as build work')['ok']
    assert e.workflow.advance(e, 'probe', 'box', 'build.py')['ok']
    assert len(e.experiment_history) == 2 and e.execution_sequence == 1


@pytest.mark.asyncio
async def test_failed_execution_can_be_assessed_but_cannot_advance():
    e = evidence(); await activate(e); await finished(e, result={'exit_code': 1, 'stderr': 'bad assumption'})
    assert assess(e, measurement='stderr=bad assumption')['ok']
    assert not e.workflow.advance(e, 'probe', 'box', 'build.py')['ok']
    assert e.workflow.advance(e, 'observe', 'box', 'missing.py', hypothesis='a', measurement='b', outcome='ready', next_step='c')['error']['code'] == 'execution_required'


@pytest.mark.asyncio
async def test_invalid_assessment_cannot_skip_receipts_and_closeout_does_not_change_token_limit(monkeypatch):
    e = evidence(); e.limits = HarnessLimits(max_run_seconds=1500)
    await activate(e); await finished(e)
    assert assess(e, measurement='x' * 12001)['error']['code'] == 'observation_required'
    assert assess(e)['ok']
    monkeypatch.setattr('oaw_atomsculptor.harness.time.monotonic', lambda: e.started + 1380)
    req = LlmRequest(contents=[], config=types.GenerateContentConfig(max_output_tokens=81920))
    e.before_model('structure_builder', req, NS(types=types), 81920)
    part = req.contents[-1].parts[0].text
    assert e.time_budget()['phase'] == 'closeout' and e.time_budget()['remaining_seconds'] == 120
    assert 'closeout reserve has begun' in e.actions.last_action.lower()
    assert '"construction_seconds": 0' in part and 'experiment_observations' in part
    assert req.config.max_output_tokens == 81920
    short = RunEvidence(e.trace, 'another task', HarnessLimits(max_run_seconds=30))
    assert short.time_budget()['closeout_reserve_seconds'] == 6
