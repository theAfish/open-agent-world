"""Generic assessment -> repair -> execute loops; native capabilities are mocked."""
import copy
import json

import pytest
from google.genai import types

from oaw_atomsculptor.harness import HarnessLimits, HarnessStopped
from test_execution_actions import prepare, request, declarations
from test_execution_steps import setup, prior
from test_harness import evidence, invoke
from test_workflow import tool, PLAN


async def observed():
    e, calls, run = await setup()
    assert (await run())["ok"]
    assert e.current_experiment()["assessment_status"] == "pending"
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    assert e.current_experiment()["assessment_status"] == "recorded"
    return e, calls, run


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["patch", "rewrite", "rerun", "new"])
@pytest.mark.parametrize("redundant", [False, True])
async def test_recorded_assessment_is_consumed_once_by_every_modelling_action(mode, redundant):
    e, calls, run = await observed()
    original = copy.deepcopy(e.experiment_history)
    item = e.latest_experiment()
    assessment = prior(e, measurement="Different redundant wording must not replace the native record",
                       plan_revision=e.workflow.plan_revision)
    args = {"action_mode": mode}
    if mode == "patch":
        args.update(source="", edits=[{"old": "measurement", "new": "next fact"}])
    elif mode == "rewrite":
        args["source"] = "print('next fact')"
    elif mode == "rerun":
        args.update(source="", argv=["--next-input"])
    else:
        args.update(script_path="next.py", source="print('next fact')")
    if redundant:
        args["previous_assessment"] = assessment
    result = await run(**args)
    assert result["ok"] and result["assessment_reused"]
    assert len(calls) == (3 if mode == "rerun" else 4) and e.execution_sequence == 2
    assert e.experiment_history == original
    assert e.current_experiment()["assessment_status"] == "pending"
    assert e.current_experiment()['script_version'] == result["step"]['source_version']


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [{"command_id": "older"}, {"sandbox": "other"}, {"script_path": "other.py"},
                                   {"plan_revision": 0}, {"plan_revision": True}])
async def test_reuse_never_accepts_a_stale_explicit_execution_identity(stale):
    e, calls, run = await observed()
    result = await run(source="", edits=[{"old": "measurement", "new": "next fact"}],
                        previous_assessment=prior(e, **stale))
    assert result["error"]["code"] == "assessment_receipt"
    assert len(calls) == 2 and len(e.experiment_history) == 1


@pytest.mark.asyncio
async def test_direct_reassessment_remains_immutable_while_composite_reuse_is_idempotent():
    e, calls, _ = await observed()
    a = prior(e)
    assert e.workflow.assess_previous(e, a)["error"]["code"] == "observation_already_recorded"
    for _ in range(3):
        result = e.workflow.assess_previous(e, a, reuse_recorded=True)
        assert result["ok"] and result["assessment_reused"]
    assert len(e.experiment_history) == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_redundant_objective_cannot_replace_or_alias_stored_measurements():
    metric = {"name": "residual", "value": 3.0, "direction": "minimize"}
    e, _, run = await setup(stdout=json.dumps({"oaw_measurements": {"objective": metric}}))
    await run()
    assert e.workflow.assess_previous(e, prior(e, objective=metric))["ok"]
    result = e.workflow.assess_previous(e, prior(e, objective={**metric, "value": 999.0}), reuse_recorded=True)
    assert result["ok"] and result["observation"]["objective"]["value"] == 3.0
    result["observation"]["objective"]["value"] = -1.0
    assert e.experiment_history[0]["objective"]["value"] == 3.0


@pytest.mark.asyncio
@pytest.mark.parametrize("contradiction", [{"assumption_invalid": True}, {"outcome": "needs_plan_review"}, {"outcome": "blocked"}])
async def test_new_contradictions_do_not_get_discarded_as_redundant_assessment(contradiction):
    e, calls, run = await observed()
    args = dict(sandbox="box", script_path="step.py", source="print('next fact')",
                 previous_assessment=prior(e, **contradiction),
                question="One repair", expected_output="Print a measurement", stop_condition="One case")
    result = await run(action_mode="rewrite", **{k: v for k, v in args.items() if k not in {"sandbox", "script_path"}})
    assert result["error"]["code"] == "assessment_conflict" and len(calls) == 2
    await tool(e, "rewrite_modelling_step", args, result)
    prepare(e, req := request())
    assert declarations(req) == {"finish_task"} and e.block_reason is None
    assert e.experiment_history[0]["outcome"] == "continue"


@pytest.mark.asyncio
async def test_recorded_plan_review_cannot_be_bypassed_by_a_redundant_ready_claim():
    e, calls, run = await setup()
    await run()
    assert e.workflow.assess_previous(e, prior(e, assumption_invalid=True, outcome="needs_plan_review"))["ok"]
    result = await run(action_mode="rewrite", source="print('next fact')",
                        previous_assessment=prior(e, outcome="ready"))
    assert result["error"]["code"] == "plan_review_required" and len(calls) == 2
    assert e.experiment_history[0]["outcome"] == "needs_plan_review"


@pytest.mark.asyncio
async def test_planner_review_clears_old_correction_and_allows_assessing_under_new_plan():
    e, _, run = await observed()
    args = dict(sandbox="box", script_path="step.py", source="print('next fact')",
                 previous_assessment=prior(e, assumption_invalid=True),
                question="One repair", expected_output="A measurement", stop_condition="One case")
    rejected = await run(action_mode="rewrite", **args)
    await tool(e, "rewrite_modelling_step", args, rejected)
    assert e.correction_feedback["kind"] == "plan_review"
    item = e.latest_experiment()
    await tool(e, "finish_task", {"result": {"status": "needs_plan_review", "reason": "Measured contradiction",
               "sandbox": item.sandbox, "script_path": item.path}}, "Task completed.")
    assert e.workflow.review_request is not None
    revised = {**copy.deepcopy(PLAN), "delivery": e.workflow.delivery}
    await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner")
    assert e.workflow.plan_revision == 2 and e.correction_feedback is None
    prepare(e, req := request({"run_modelling_step", "advance_modelling_stage", "finish_task"}))
    assert "advance_modelling_stage" in declarations(req) and "run_modelling_step" in declarations(req)
    assert e.current_experiment()["assessment_status"] == "pending"


@pytest.mark.asyncio
async def test_reused_complete_stage_still_requires_independent_build_validation():
    e, calls, run = await observed()
    assert e.workflow.advance(e, "probe", "box", "step.py")["ok"]
    e.workflow.delivery = "document"
    assert (await run(script_path="build.py"))["ok"]
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    result = await run(script_path="next.py", previous_assessment=prior(e, complete_stage=True, structure_path="candidate.xyz"))
    assert result["error"]["code"] == "validation_required" and len(calls) == 4
    assert e.workflow.current == "build"


@pytest.mark.asyncio
async def test_protocol_rejections_keep_recovery_receipts_and_detect_unchanged_action():
    e, calls, _ = await observed()
    a = {"stage": "observe", **{k: v for k, v in prior(e).items() if k != "command_id"}}
    for index in range(2):
        rejected = e.workflow.advance(e, **a)
        await tool(e, "advance_modelling_stage", a, rejected)
        assert rejected["error"]["kind"] == "workflow_state"
        assert rejected["error"]["command_dispatched"] is False
        assert rejected["error"]["recovery"]["current_experiment"]["assessment_status"] == "recorded"
        if index == 0:
            prepare(e, request())  # Offer the native corrective receipt once.
    assert not e.failures and "unchanged rejected" in e.block_reason and len(calls) == 2
    assert "rejected before command dispatch" in e.actions.last_action
    with pytest.raises(HarnessStopped, match="unchanged rejected advance_modelling_stage"):
        prepare(e, request())


@pytest.mark.asyncio
async def test_step_contract_rejections_are_not_dependency_failures_and_do_not_reset_progress():
    e, calls, run = await observed()
    for _ in range(3):
        result = await run(action_mode="rewrite", source="print('measurement')")
        assert result["error"]["code"] == "step_no_change"
        await tool(e, "rewrite_modelling_step", {"sandbox": "box", "script_path": "step.py",
                   "source": "print('measurement')", 
                   "question": "Next fact", "expected_output": "A measurement", "stop_condition": "One case"}, result)
    assert not e.failures and e.block_reason is None and len(calls) == 2


@pytest.mark.asyncio
async def test_dynamic_context_and_schema_agree_on_recorded_assessment_and_selected_limit():
    e, _, _ = await observed()
    req = request({"patch_modelling_step", "rewrite_modelling_step", "finish_task", "advance_modelling_stage"})
    e.before_model("structure_builder", req, type("Bindings", (), {"types": types}), 81920)
    schemas = [d.parameters_json_schema for t in req.config.tools for d in t.function_declarations
               if d.name in {"patch_modelling_step", "rewrite_modelling_step"}]
    assert all("previous_assessment" not in s["required"] for s in schemas)
    assert schemas and all("previous_assessment" not in s["properties"] for s in schemas)
    snapshot = next(json.loads(p.text.split("\n", 1)[1]) for c in req.contents for p in c.parts
                    if p.text and p.text.startswith("HOST RUN EVIDENCE"))
    assert snapshot["current_experiment"]["assessment_status"] == "recorded"
    assert snapshot["current_experiment"]['script_version'] == e.latest_experiment().version
    assert req.config.max_output_tokens == 81920


@pytest.mark.asyncio
async def test_real_dependency_repetition_is_bounded_and_environment_change_has_a_new_identity():
    e = evidence(repeated_error_limit=3)
    args = {"sandbox": "box", "script": "tool.py", "argv": ["validate"]}
    failure = {"exit_code": 71, "stderr": "executable unavailable"}
    for _ in range(2):
        await invoke(e, "run_skill_script", args, failure)
    await invoke(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    await invoke(e, "run_skill_script", args, failure)
    assert e.block_reason is None
    await invoke(e, "run_skill_script", args, failure)
    await invoke(e, "run_skill_script", args, failure)
    assert "same dependency error (3 attempts)" in e.block_reason


@pytest.mark.asyncio
async def test_changed_native_inputs_are_observed_progress_without_rewriting_source():
    e, _, run = await observed()
    e.limits = HarnessLimits(model_requests_without_progress=3)
    for i in range(8):
        result = await run(action_mode="rerun", source="", 
                           argv=[str(i)])
        assert result["ok"]
        assert e.workflow.assess_previous(e, prior(e))["ok"]
        prepare(e, request())
        assert e.actions.requests_without_progress == 1


@pytest.mark.asyncio
async def test_identical_successful_reruns_do_not_create_new_progress_forever():
    e, _, run = await observed()
    e.limits = HarnessLimits(model_requests_without_progress=3)
    prepare(e, request())
    for _ in range(2):
        await run(action_mode="rerun", source="")
        assert e.workflow.assess_previous(e, prior(e), reuse_recorded=True)["ok"]
        prepare(e, request())
    await run(action_mode="rerun", source="")
    assert e.workflow.assess_previous(e, prior(e), reuse_recorded=True)["ok"]
    with pytest.raises(HarnessStopped, match="no new execution/assessment/checkpoint"):
        prepare(e, request())
