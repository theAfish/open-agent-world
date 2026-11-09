"""Checkpoint advice must agree with the accepted native state and evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from google.genai import types

from oaw_atomsculptor.measurements import Measurement, measurement_contract
from test_candidate_first_workflow import start_direct, input_report
from test_measurement_handoffs import execute, record
from test_workflow import activate, evidence, validate


def candidate(e, *, kind="candidate", artifacts=True):
    result = {"kind": kind, "measurement_revision": "fixture-v1", "coverage": {"sample": 4},
        "constraints": [{"name": "actual_check", "passed": True, "regions": ["sample"],
            "check_ids": [r["id"] for r in e.workflow.requirements() if r["stage"] == "build"]}]}
    if artifacts:
        result["artifacts"] = [{"path": "cnt.extxyz", "units": "angstrom", "components": {"C": 4},
            "frame": {"origin": [0, 0, 0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}}]
    return result


async def ready_build():
    e = await start_direct("document")
    await execute(e, "build.py", record(**candidate(e)))
    result = e.workflow.advance(e, "observe", hypothesis="Measured candidate", measurement="Native sample=4",
        outcome="ready", next_step="Validate and advance", structure_path="cnt.extxyz")
    assert result["checkpoint_result"]["error"]["code"] == "validation_required"
    return e


@pytest.mark.asyncio
async def test_ready_advice_previews_the_same_checkpoint_without_committing_it():
    e = await ready_build()
    before = deepcopy(e.workflow.snapshot())
    advice, _ = e.actions.next_action(e)
    assert "validate_modelling_artifact" in advice and "cnt.extxyz" in advice
    assert e.workflow.snapshot() == before
    await validate(e)
    before = deepcopy(e.workflow.snapshot())
    advice, _ = e.actions.next_action(e)
    assert "Call advance_modelling_stage" in advice
    assert '"structure_path": "cnt.extxyz"' in advice
    assert e.workflow.snapshot() == before and not e.workflow.checkpoints
    e.steps.available = True
    e.output_recoveries["structure_builder"] = 1
    request = SimpleNamespace(contents=[], config=types.GenerateContentConfig(max_output_tokens=81920))
    e.actions.prepare(e, "structure_builder", request, None)
    assert "Call advance_modelling_stage" in e.actions.last_action
    assert "Answer ONE current question with code" not in e.actions.last_action
    assert "continue with a saved, executable" not in e.actions.last_action
    assert request.config.max_output_tokens == 81920
    assert e.workflow.advance(e, "build", "box", "build.py", structure_path="cnt.extxyz")["ok"]
    assert e.workflow.ready_execution(e) is None and e.workflow.current == "deliver"


@pytest.mark.asyncio
async def test_ready_result_does_not_advertise_or_accept_a_continue_only_return():
    e = await ready_build()
    denied = e.workflow.prepare_subtask_finish({"status": "subtask_complete", "next_experiment": {}}, e)
    assert denied["error"]["actual_outcome"] == "ready"
    assert denied["error"]["expected_outcome"] == "continue"
    declaration = types.FunctionDeclaration(name="finish_task")
    request = SimpleNamespace(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[declaration])]))
    e.workflow.prepare_request("structure_builder", request, e)
    schema = request.config.tools[0].function_declarations[0].parameters_json_schema["properties"]["result"]
    assert "subtask_complete" not in schema["properties"]["status"]["enum"]
    assert not e.workflow.subtask_history and not e.workflow.checkpoints


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["diagnostic", "no_artifact", "missing_path", "wrong_path"])
async def test_checkpoint_errors_identify_the_actual_missing_field(problem):
    e = await start_direct("document")
    await execute(e, "build.py", record(**candidate(e, kind="diagnostic" if problem == "diagnostic" else "candidate",
                                                   artifacts=problem != "no_artifact")))
    e.workflow.advance(e, "observe", hypothesis="Measured candidate", measurement="Native sample=4",
        outcome="continue", next_step="Use existing candidate")
    path = "" if problem == "missing_path" else "other.extxyz" if problem == "wrong_path" else "cnt.extxyz"
    result = e.workflow.advance(e, "build", "box", "build.py", structure_path=path)
    error = result["error"]
    if problem == "diagnostic":
        assert error["missing_check_ids"] == [] and error["actual_kind"] == "diagnostic"
        assert [issue["field"] for issue in error["issues"]] == ["measurements.kind"]
    elif problem == "no_artifact":
        assert error["field"] == "measurements.artifacts" and error["artifact_paths"] == []
    else:
        assert error["field"] == "structure_path" and error["artifact_paths"] == ["cnt.extxyz"]
        assert error["code"] == ("structure_path_required" if problem == "missing_path" else "requirement_artifact")
    assert e.workflow.current == "build" and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_completed_probe_ready_does_not_leak_into_the_new_build_stage():
    e = evidence()
    await activate(e, delivery="report")
    await execute(e, "probe.py", record(**input_report(e)))
    result = e.workflow.advance(e, "observe", hypothesis="Measured input", measurement="Native input=4",
        outcome="ready", next_step="Construct candidate")
    assert result["checkpoint_status"] == "completed" and e.workflow.current == "build"
    assert e.workflow.ready_execution(e) is None


def test_candidate_example_is_valid_but_preserves_its_illustrative_failed_verdict():
    contract = measurement_contract()
    candidate = Measurement.model_validate(contract["candidate_example"]["oaw_measurements"])
    assert candidate.kind == "candidate" and candidate.artifacts and candidate.measurement_revision
    assert candidate.constraints[0].passed is False
    assert contract["example"]["oaw_measurements"]["kind"] == "diagnostic"


@pytest.mark.asyncio
async def test_absence_feedback_distinguishes_empty_test_from_a_completed_search():
    from oaw_atomsculptor.measurements import constraint_failures
    e = evidence()
    await activate(e, delivery='report')
    checks = [r['id'] for r in e.workflow.requirements() if r['stage'] == 'probe']
    empty = {'kind': 'diagnostic', 'measurement_revision': 'inventory-v1',
             'coverage': {'candidates': 0}, 'values': {'found_files': 0},
             'constraints': [{'name': 'candidate_property', 'passed': True,
                              'regions': ['candidates'], 'check_ids': checks}]}
    await execute(e, 'probe.py', record(**empty))
    rows = [r for r in e.workflow.requirement_coverage(e) if r['stage'] == 'probe']
    assert all(r['status'] == 'reported_fail' for r in rows)
    assert all(r['evidence_issues'][0]['code'] == 'empty_test_population' for r in rows)
    assert 'found_matches=0 in values' in constraint_failures(empty)[0]['next_action']
    assert not e.workflow.probe_ready(e)
    absent = {**empty, 'coverage': {'inspected_directories': 1}, 'constraints': [
        {'name': 'no_candidate_files', 'passed': True, 'regions': ['inspected_directories'], 'check_ids': checks}]}
    e = evidence()
    await activate(e, delivery='report')
    await execute(e, 'probe.py', record(**absent))
    assert not constraint_failures(absent)
    assert e.workflow.probe_ready(e)
    assert all('evidence_issues' not in r for r in e.workflow.requirement_coverage(e) if r['stage'] == 'probe')


@pytest.mark.asyncio
async def test_step_reply_next_action_uses_finished_or_running_native_state():
    from test_execution_steps import setup
    e, calls, run = await setup()
    finished = await run()
    assert finished['next_action'] == e.actions.next_action(e)[0]
    assert 'Collect running/unknown execution first' not in finished['next_action']
    e, calls, run = await setup(running=True)
    collected = await run()
    assert collected['step']['state'] == 'finished'  # Native waiter collected this fixture.
    assert 'Collect running/unknown' not in collected['next_action']
    e.pending['native-still-running'] = []
    advice, _ = e.actions.next_action(e)
    assert 'wait_sandbox_operation' in advice and 'do not edit or restart' in advice


def stage_schema(e):
    declaration = types.FunctionDeclaration(name='advance_modelling_stage')
    request = SimpleNamespace(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[declaration])]))
    e.workflow.prepare_request('structure_builder', request, e)
    return declaration.parameters_json_schema


@pytest.mark.asyncio
@pytest.mark.parametrize('supply_path', [True, False])
async def test_advertised_observe_schema_accepts_path_and_unique_binding_fills_omission(supply_path):
    from jsonschema import validate as validate_schema
    e = await start_direct('document')
    await execute(e, 'build.py', record(**candidate(e)))
    arguments = dict(stage='observe', hypothesis='Constructed candidate', measurement='Measured sample=4',
                     outcome='ready', next_step='Independently validate')
    if supply_path:
        arguments['structure_path'] = 'cnt.extxyz'
    schema = stage_schema(e)
    validate_schema(arguments, schema)
    assert 'structure_path' not in schema['required']
    result = e.workflow.advance(e, **arguments)
    assert result['ok'] and result['checkpoint_result']['error']['code'] == 'validation_required'
    assert 'validate_modelling_artifact' in result['next_action'] and 'cnt.extxyz' in result['next_action']
    assert e.workflow.current == 'build' and not e.workflow.checkpoints
    await validate(e)
    schema = stage_schema(e)
    assert schema['properties']['stage']['enum'] == ['build', 'blocked']
    assert not {'measurement', 'hypothesis', 'outcome', 'next_step'} & set(schema['properties'])
    args = dict(stage='build', sandbox='box', script_path='build.py', structure_path='cnt.extxyz')
    validate_schema(args, schema)
    assert e.workflow.advance(e, **args)['ok']
    assert e.workflow.current == 'deliver'


@pytest.mark.asyncio
async def test_multiple_current_artifacts_require_a_selection_without_new_computation():
    e = await start_direct('document')
    report = candidate(e)
    report['artifacts'].append({**report['artifacts'][0], 'path': 'other.extxyz'})
    await execute(e, 'build.py', record(**report))
    result = e.workflow.advance(e, 'observe', hypothesis='Two candidates', measurement='Both produced',
        outcome='ready', next_step='Select candidate')
    assert result['checkpoint_result']['error']['code'] == 'structure_path_required'
    assert set(result['checkpoint_result']['error']['artifact_paths']) == {'cnt.extxyz', 'other.extxyz'}
    assert not e.workflow.checkpoints


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,failed', [('candidate', False), ('diagnostic', False), ('candidate', True)])
async def test_candidate_advice_prefers_assessment_without_accepting_a_failed_or_diagnostic_result(kind, failed):
    e = await start_direct('document')
    report = candidate(e, kind=kind)
    report['constraints'][0]['passed'] = not failed
    await execute(e, 'build.py', record(**report))
    e.steps.available = True
    advice, allowed = e.actions.next_action(e)
    if kind == 'candidate' and not failed:
        assert 'Briefly assess' in advice and 'ready only if justified' in advice
        assert 'ONE next code action' not in advice and 'run_modelling_step' in allowed
    else:
        assert not advice.startswith('The execution returned a saved candidate')
    assert e.workflow.current == 'build' and not e.workflow.checkpoints
