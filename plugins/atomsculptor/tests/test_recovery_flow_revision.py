"""Continuation readiness, native evidence reads and optional action refinement."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator
from google.adk.models.lite_llm import _get_completion_inputs

from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.execution_steps import workflow_argument_error
from test_candidate_first_workflow import direct_plan, start_direct
from test_execution_actions import declarations, prepare, request
from test_execution_steps import prior, setup
from test_workflow import evidence, tool


NAMES = {"inspect_sandbox", "start_sandbox", "read_skills", "run_skill_script",
         "write_sandbox_text_file", "execute_command", "wait_sandbox_operation",
         "read_sandbox_file", "run_modelling_step", "finish_task", "advance_modelling_stage"}


def configure(e):
    e.configure_tools([ScopedToolDefinition("operation:" + name, name, "Native fixture") for name in NAMES])
    e.steps.available = True


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [(None, {"inspect_sandbox"}), ("stopped", {"start_sandbox"}),
    ("ready", {"run_skill_script", "write_sandbox_text_file", "execute_command", "run_modelling_step"})])
async def test_restored_action_checks_readiness_then_retains_native_execution_routes(state, expected):
    old = await start_direct()
    e = evidence()
    assert e.restore_continuation(old.closeout_checkpoint("interrupted"), "prior-run")
    configure(e)
    if state is not None:
        e.actions.sandbox_states["box"] = state
    req = request(NAMES | {"read_modelling_source"})
    prepare(e, req)
    actual = declarations(req)
    assert "read_modelling_source" not in actual
    assert expected <= actual
    if state != "ready":
        assert "run_modelling_step" not in actual and "execute_command" not in actual


@pytest.mark.asyncio
async def test_pending_dispatch_still_hides_new_writes_even_when_a_plan_is_restored():
    e = evidence()
    old = await start_direct()
    assert e.restore_continuation(old.closeout_checkpoint("lost response"), "prior-run")
    configure(e)
    e.actions.sandbox_states["box"] = "ready"
    e.executions["unknown-call"] = [(('box', 'build.py'), 1)]
    req = request(NAMES)
    prepare(e, req)
    assert not {"run_skill_script", "run_modelling_step", "write_sandbox_text_file", "execute_command"} & declarations(req)


@pytest.mark.asyncio
async def test_inline_preview_has_completeness_and_native_receipt_reference_across_restores():
    old = await start_direct()
    full = "result\n" + "detail\n" * 300
    await tool(old, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print('result')"]},
               {"exit_code": 0, "command_id": "old-command", "stdout": full, "stderr": ""})
    row = old.actions.inline_measurements[-1]
    assert row["stdout"] == full[:1000] and row["stdout_chars"] == len(full) and row["stdout_truncated"]
    assert row["stderr_chars"] == 0 and row["stderr_truncated"] is False
    assert row["result_reference"] == {"reader": "wait_sandbox_operation", "arguments": {
        "sandbox": "box", "operation_id": "old-command", "wait_seconds": 0}}
    checkpoint = old.closeout_checkpoint("network interruption")
    legacy = deepcopy(checkpoint)
    for key in ("stdout_chars", "stdout_truncated", "stderr_chars", "stderr_truncated", "result_reference"):
        legacy["inline_measurements"][0].pop(key)
    for source in (checkpoint, legacy):
        e = evidence()
        assert e.restore_continuation(source, "original-run")
        configure(e)
        e.actions.sandbox_states["box"] = "ready"
        view = e.actions.inline_measurements[-1]
        assert view["source_run_id"] == "original-run" and "not_scientifically_validated" in view["evidence_kind"]
        assert view["stdout_truncated"] == (True if source is checkpoint else None)
        assert view["result_reference"] == row["result_reference"]
        req = request(NAMES)
        prepare(e, req)
        assert "wait_sandbox_operation" in declarations(req)
        before_reads = deepcopy(e.actions.inline_measurements)
        result = await tool(e, "wait_sandbox_operation", view["result_reference"]["arguments"],
            {"command_id": "old-command", "exit_code": 0, "stdout": full,
             "argv": ["python3", "-c", "print('result')"]})
        assert result["stdout"] == full and result["argv"][1] == "-c"
        assert e.actions.inline_measurements == before_reads
        again = evidence()
        assert again.restore_continuation(e.closeout_checkpoint("again"), "second-run")
        assert again.actions.inline_measurements[-1]["source_run_id"] == "original-run"
        assert not again.scripts and not again.measurement_reports and not again.outputs


def action():
    return {"scope": "input_measurement", "artifact": "step.py", "question": "Measure the first input",
            "method": "Compute one input and retain it", "expected_output": "One measured input",
            "stop_condition": "One finite measurement"}


@pytest.mark.asyncio
async def test_refinement_preserves_acceptance_and_executes_the_narrowed_action():
    e, calls, run = await setup()
    before = deepcopy(e.workflow.plan)
    ids = e.workflow.requirements()
    req = request({"advance_modelling_stage"})
    prepare(e, req)
    _, wire_tools, _, _, _ = await _get_completion_inputs(req, req.model)
    schema = next(t["function"]["parameters"] for t in wire_tools if t["function"]["name"] == "advance_modelling_stage")
    args = {"stage": "refine", "reason": "Separate the next input from the final stage goal", "next_experiment": action()}
    Draft202012Validator(schema).validate(args)
    for field in ("reason", "next_experiment"):
        missing = {key: value for key, value in args.items() if key != field}
        assert not Draft202012Validator(schema).is_valid(missing)
        assert workflow_argument_error("advance_modelling_stage", missing)["error"]["missing_fields"] == [field]
    result = e.workflow.advance(e, **args)
    assert result["ok"] and result["action_refined"] and not result["stage_completed"] and not result["task_complete"]
    assert e.workflow.plan == before and e.workflow.requirements() == ids and e.workflow.plan_revision == 1
    assert not calls and not e.scripts and not e.workflow.review_request and not e.workflow.subtask_history
    pending = deepcopy(e.workflow.pending_experiment)
    assert e.workflow.advance(e, **args)["action_refined"] is False
    assert e.workflow.pending_experiment == pending
    restored = evidence()
    assert restored.restore_continuation(e.closeout_checkpoint("interrupted before execution"), "source-run")
    assert restored.workflow.pending_experiment["artifact"] == "step.py"
    assert restored.working_decision()["stage_goal"] == before[0]["goal"]
    assert (await run(source="print('measured input')"))["step"]["state"] == "finished"
    assert [name for name, _ in calls] == ["write_sandbox_text_file", "execute_command"]
    assert e.workflow.advance(e, **args)["error"]["code"] == "refinement_requires_unstarted_action"
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    req = request({"advance_modelling_stage"})
    prepare(e, req)
    schema = req.config.tools[0].function_declarations[0].parameters_json_schema
    assert "refine" not in schema["properties"]["stage"]["enum"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["executions", "pending", "inline_executions", "inline_pending",
                                      "skill_operations", "delivery_uncertainty", "historical_unknown", "review"])
async def test_refinement_cannot_discard_pending_effects_or_review(blocker):
    e, calls, _ = await setup()
    if blocker == "historical_unknown":
        e.continuation = {"unknown_executions": ["unresolved"]}
    elif blocker == "review":
        e.workflow.review_request = {"reason": "Current scientific review"}
    else:
        getattr(e, blocker)["unresolved"] = {}
    before = deepcopy(e.workflow.pending_experiment)
    rejected = e.workflow.advance(e, "refine", reason="Narrow an action", next_experiment=action())
    assert rejected["error"]["code"] == "refinement_unavailable"
    assert e.workflow.pending_experiment == before and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"checks": []}, {"scope": "candidate_work"}, {"input_paths": ["unbound.extxyz"]}])
async def test_refinement_reuses_action_and_dependency_validation(change):
    e, calls, _ = await setup()
    before = deepcopy(e.workflow.pending_experiment)
    rejected = e.workflow.advance(e, "refine", reason="Narrow an action", next_experiment={**action(), **change})
    assert rejected["ok"] is False and e.workflow.pending_experiment == before and not calls


@pytest.mark.asyncio
async def test_method_diagnostics_are_preserved_without_becoming_acceptance_ids():
    proposed = direct_plan()
    proposed["stages"][0]["method_checks"] = ["Inspect the chosen checker's numerical sensitivity"]
    proposed["stages"][0]["experiment"]["objective"] = {"name": "cost", "direction": "minimize"}
    e = evidence()
    assert await tool(e, "finish_task", {"result": proposed}, "Task completed.", role="planner") == "Task completed."
    assert e.workflow.plan[0]["method_checks"] == proposed["stages"][0]["method_checks"]
    requirements = e.workflow.requirements()
    assert [r["check"] for r in requirements] == [c for s in proposed["stages"] for c in s["checks"]]
    assert all(r["source"] == "planner_acceptance_interpretation" for r in requirements)
    req = request({"advance_modelling_stage"})
    prepare(e, req)
    stable = next(json.loads(part.text.split("\n", 1)[1]) for content in req.contents for part in content.parts
                  if part.text and part.text.startswith("HOST STABLE CONTEXT"))
    view = stable["modelling_plan_context"]["plan"][0]
    assert "experiment" not in view and view["goal"] == proposed["stages"][0]["goal"]
    assert view["optimization_objective"] == proposed["stages"][0]["experiment"]["objective"]
    assert "working_decision.current_subtask" in stable["modelling_plan_context"]["current_action_source"]
    merged, issues = e.workflow.merge_plan_revision({"status": "planned", "plan_revision": e.workflow.plan_revision,
        "stages": [{"id": "build", "method_checks": ["Revised diagnostic"]}]})
    assert not issues and merged["stages"][0]["checks"] == proposed["stages"][0]["checks"]
    assert merged["stages"][0]["method_checks"] == ["Revised diagnostic"]
    restored = evidence()
    assert restored.restore_continuation(e.closeout_checkpoint("interrupted"), "source-run")
    assert restored.workflow.plan[0]["method_checks"] == proposed["stages"][0]["method_checks"]
