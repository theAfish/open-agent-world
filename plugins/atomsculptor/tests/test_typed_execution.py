"""Required executable payloads at the actual ADK boundary; no model API calls."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from google.adk.tools import FunctionTool
from google.genai import types
from jsonschema import Draft202012Validator

from backend.agents.models import ScopedToolDefinition
from backend.sandbox.text_edits import apply_text_edits
from oaw_atomsculptor.execution_steps import typed_step_functions, STEP_TOOL_MODES
from oaw_atomsculptor.harness import HarnessStopped
from test_execution_actions import request, prepare, declarations
from test_execution_steps import prior, setup
from test_workflow import evidence, activate, tool, PLAN, execute


COMMON = dict(sandbox="box", script_path="probe.py", question="Measure one input",
              expected_output="A native numeric result", stop_condition="One execution")


@pytest.mark.parametrize('extra', [
    {'complete_stage': True},
    {'hypothesis': 'Same measurement', 'measurement': 'Real stdout', 'outcome': 'continue', 'next_step': 'Patch one field'},
])
def test_misplaced_assessment_feedback_distinguishes_unexpected_from_missing(extra):
    from oaw_atomsculptor.execution_steps import argument_error
    args = dict(COMMON, edits=[{'old': 'a', 'new': 'b'}],  **extra)
    rejected = argument_error('patch_modelling_step', args)
    error = rejected['error']
    assert error['missing_fields'] == error['invalid_fields'] == []
    assert error['unexpected_fields'] == sorted(extra)
    assert error['assessment_field_locations'] == {key: 'previous_assessment.' + key for key in extra}
    assert not error['command_dispatched']
    corrected = {k: v for k, v in args.items() if k not in extra}
    corrected['previous_assessment'] = {'hypothesis': 'Previous measured input', 'measurement': 'Actual stdout',
                                        'outcome': 'ready', 'next_step': 'Next bounded experiment', **extra}
    from oaw_atomsculptor.execution_steps import step_tool_schema
    Draft202012Validator(step_tool_schema('patch_modelling_step')).validate(corrected)
    assert argument_error('patch_modelling_step', corrected) is None


@pytest.mark.asyncio
async def test_required_payloads_survive_sdk_declaration_and_host_projection():
    e, _, run = await setup()
    await run()
    sdk = [FunctionTool(f) for f in typed_step_functions(e, [], None, asyncio.Event())]
    req = request()
    req.config.tools = [types.Tool(function_declarations=[s._get_declaration() for s in sdk])]
    prepare(e, req)
    by_name = {d.name: d.parameters_json_schema for t in req.config.tools for d in t.function_declarations}
    required = {"run_modelling_step": {"source"}, "patch_modelling_step": {"edits"},
                "rewrite_modelling_step": {"source"}, "rerun_modelling_step": {"execution_scope", "argv"}}
    for name, fields in required.items():
        schema = by_name[name]
        assert "sha256" not in json.dumps(schema) and "digest" not in json.dumps(schema)
        assert fields <= set(schema["required"])
        Draft202012Validator.check_schema(schema)
        assert not Draft202012Validator(schema).is_valid(COMMON)
    assert "source" not in by_name["patch_modelling_step"]["properties"]
    assert "edits" not in by_name["run_modelling_step"]["properties"]


async def native_sdk_fixture(*, write_error=False):
    e = evidence()
    defs = [ScopedToolDefinition("operation:" + n, n, "Native fixture")
            for n in ("write_sandbox_text_file", "execute_command")]
    e.configure_tools(defs); await activate(e, delivery="report"); e.steps.available = True
    files, calls = {}, []
    async def native(capability, args):
        calls.append((capability, dict(args)))
        if capability.endswith("write_sandbox_text_file"):
            if write_error:
                return {"ok": False, "error": "native write permission rejected"}
            path = args["path"]
            files[path] = apply_text_edits(files[path].encode(), args["edits"]).decode() if args.get("edits") else args["content"]
            return {"written": len(files[path].encode())}
        return {"exit_code": 0, "command_id": f"cmd-{len(calls)}", "stdout": "measured=1"}
    sdk = {f.__name__: FunctionTool(f) for f in typed_step_functions(e, defs, native, asyncio.Event())}
    async def invoke(name, args):
        hooks = e.tool_callbacks("structure_builder"); context = NS(function_call_id=f"sdk-{len(calls)}")
        response = await hooks["before_tool_callback"](sdk[name], args, context)
        if response is None:
            response = await sdk[name].run_async(args=args, tool_context=context)
        await hooks["after_tool_callback"](sdk[name], args, context, response)
        return response
    return e, files, calls, invoke


@pytest.mark.asyncio
async def test_missing_payload_correction_then_real_patch_and_saved_search():
    e, files, calls, invoke = await native_sdk_fixture()
    bad = await invoke("run_modelling_step", dict(COMMON))
    assert bad["error"]["fields"] == ["source"] and not calls
    req = request(set(STEP_TOOL_MODES) | {"finish_task", "advance_modelling_stage"})
    prepare(e, req)
    assert declarations(req) & set(STEP_TOOL_MODES) == {"run_modelling_step"}
    assert "source" in e.actions.last_action
    made = await invoke("run_modelling_step", dict(COMMON, source="print(1)"))
    assert made["ok"] and e.steps.argument_issue is None and len(calls) == 2
    patched = await invoke("patch_modelling_step", dict(COMMON, edits=[{"old": "1", "new": "2"}],
         previous_assessment=prior(e)))
    assert patched["ok"] and files["probe.py"] == "print(2)" and len(calls) == 4
    searched = await invoke("rerun_modelling_step", dict(COMMON, execution_scope="search", argv=["--search"],
         previous_assessment=prior(e)))
    assert searched["ok"] and calls[-1][1]["argv"] == ["python3", "probe.py", "--search"]
    assert len(calls) == 5 and files["probe.py"] == "print(2)"


@pytest.mark.asyncio
async def test_finished_step_inherits_full_fields_and_schema_matches_actual_patch_dispatch():
    from oaw_atomsculptor.execution_steps import step_field_defaults
    e, files, calls, invoke = await native_sdk_fixture()
    question = 'Measured question ' * 100
    assert (await invoke('run_modelling_step', dict(COMMON, source='print(1)', question=question)))['ok']
    assert e.workflow.pending_experiment is None
    assert e.working_decision()['current_subtask']['question'] == question[:600]
    sdk = [FunctionTool(f) for f in typed_step_functions(e, [], None, asyncio.Event())]
    req = request(); req.config.tools = [types.Tool(function_declarations=[s._get_declaration() for s in sdk])]
    prepare(e, req)
    schema = next(d.parameters_json_schema for t in req.config.tools for d in t.function_declarations if d.name == 'patch_modelling_step')
    args = {'sandbox': 'box', 'script_path': 'probe.py', 'edits': [{'old': '1', 'new': '2'}],
            'previous_assessment': {k: v for k, v in prior(e).items() if k not in {'command_id', 'sandbox', 'script_path'}}}
    Draft202012Validator(schema).validate(args)
    result = await invoke('patch_modelling_step', args)
    assert result['ok'] and result['step']['question'] == question and files['probe.py'] == 'print(2)'
    assert len(calls) == 4
    assert step_field_defaults(e, 'other.py', 'box') == {}
    assert step_field_defaults(e, 'probe.py', 'other-box') == {}
    e.workflow.plan_revision += 1
    assert step_field_defaults(e, 'probe.py', 'box') == {}


@pytest.mark.asyncio
async def test_correct_arguments_clear_old_issue_even_if_native_write_is_rejected():
    e, files, calls, invoke = await native_sdk_fixture(write_error=True)
    assert (await invoke("run_modelling_step", dict(COMMON)))["error"]["code"] == "step_arguments"
    assert e.steps.argument_issue
    failed = await invoke("run_modelling_step", dict(COMMON, source="print(1)"))
    assert not failed["ok"] and len(calls) == 1
    assert e.steps.argument_issue is None and e.steps.argument_failures == 0
    assert not files and e.execution_sequence == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [123, [], "   ", None])
async def test_source_type_or_empty_value_is_a_parameter_error_without_native_execution(source):
    e, files, calls, invoke = await native_sdk_fixture()
    result = await invoke("run_modelling_step", dict(COMMON, source=source))
    assert result["error"]["fields"] == ["source"]
    assert result["error"]["kind"] == "argument_validation" and not calls


@pytest.mark.asyncio
async def test_repeated_argument_errors_never_become_scientific_planner_review():
    e, files, calls, invoke = await native_sdk_fixture()
    for _ in range(3):
        response = await invoke("run_modelling_step", dict(COMMON))
        assert response["error"]["kind"] == "argument_validation"
    assert not calls and not files and e.execution_sequence == 0
    assert e.workflow.host_signal is None and e.workflow.review_request is None
    with pytest.raises(HarnessStopped, match="argument error, not a script or dependency failure"):
        prepare(e, request())
    assert e.closeout_checkpoint("argument failure")["argument_issue"]["fields"] == ["source"]


@pytest.mark.asyncio
async def test_planner_assumptions_have_explicit_path_and_persist_when_corrected():
    e = evidence()
    bad = await tool(e, "finish_task", {"result": PLAN, "assumptions": ["Test the inferred cutoff"]}, "Task completed.", role="planner")
    assert bad["error"]["code"] == "plan_arguments" and "result.assumptions" in bad["error"]["message"]
    assert e.workflow.plan is None and e.workflow.plan_rejections == 1
    corrected = {**PLAN, "assumptions": ["Test the inferred cutoff"]}
    assert await tool(e, "finish_task", {"result": corrected}, "Task completed.", role="planner") == "Task completed."
    assert e.workflow.assumptions == corrected["assumptions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,declared,reported", [
    ("probe", None, {"name": "atom count", "direction": "minimize"}),
    ("build", None, {"name": "atom count", "direction": "minimize"}),
    ("build", {"name": "residual", "direction": "minimize"}, {"name": "atom count", "direction": "minimize"}),
])
async def test_ordinary_measurements_do_not_count_as_optimization_progress(stage, declared, reported):
    e, calls, run = await setup(stdout=json.dumps({"oaw_measurements": {"objective": {**reported, "value": 320}}}))
    if stage == "build":
        await execute(e, "input.py")
        assert e.workflow.advance(e, "probe", "box", "input.py")["ok"]
        if declared:
            e.workflow.plan[1]["experiment"]["objective"] = declared
    for i in range(2 if declared else 4):
        assert (await run(script_path=f"measurement{i}.py"))["ok"]
        assert e.workflow.assess_previous(e, prior(e))["ok"]
    assert not e.workflow._progress and e.workflow.review_request is None
    if stage == "build" and declared:
        # A metric mismatch is not numeric improvement. The same missing
        # comparable evidence routes to Planner without a diagnostic quota.
        assert "same unresolved diagnostic" in e.workflow.host_signal["reason"]



def test_plan_objective_is_optional_and_only_build_optimization_can_declare_it():
    from oaw_atomsculptor.workflow import validate_plan
    ordinary, issues = validate_plan(PLAN)
    assert ordinary and not issues and "objective" not in ordinary["stages"][1]["experiment"]
    revised = copy.deepcopy(PLAN)
    revised["stages"][1]["experiment"]["objective"] = {"name": "residual", "direction": "minimize", "unit": "angstrom"}
    optimized, issues = validate_plan(revised)
    assert not issues and optimized["stages"][1]["experiment"]["objective"]["name"] == "residual"
    revised["stages"][0]["experiment"]["objective"] = {"name": "atom count", "direction": "minimize"}
    assert validate_plan(revised)[1][0]["field"].endswith("experiment.objective")


@pytest.mark.parametrize("value,direction", [(10 ** 1000, "minimize"), (0, []), (True, "minimize"), (float("inf"), "maximize")])
def test_malformed_objective_never_crashes_the_native_receipt_observer(value, direction):
    from oaw_atomsculptor.harness import RunEvidence
    metric = {"name": "residual", "value": value, "direction": direction}
    assert RunEvidence._stdout_objective(json.dumps({"oaw_measurements": {"objective": metric}})) is None
