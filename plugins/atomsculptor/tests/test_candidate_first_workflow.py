"""Construction-first plans reuse the native phase and validation receipts."""
from copy import deepcopy

import pytest

from oaw_atomsculptor.workflow import PLAN_RESULT_SCHEMA, validate_plan
from test_workflow import PLAN, activate, evidence, tool, validate
from test_measurement_handoffs import execute, assess, record
from test_execution_steps import setup, prior


def direct_plan(delivery="report"):
    result = deepcopy(PLAN)
    result["delivery"] = delivery
    result["stages"] = result["stages"][1:]
    return result


async def start_direct(delivery="report"):
    e = evidence()
    assert await tool(e, "finish_task", {"result": direct_plan(delivery)}, "Task completed.", role="planner") == "Task completed."
    assert e.workflow.before_delegation("structure_builder") is None
    return e


def input_report(e, **changes):
    ids = [r["id"] for r in e.workflow.requirements() if r["stage"] == "probe"]
    return {"kind": "baseline", "coverage": {"input": 4}, "values": {"count": 4},
            "constraints": [{"name": "input_measured", "passed": True, "regions": ["input"], "check_ids": ids}], **changes}


def test_optional_probe_plan_schema_and_stage_order():
    assert PLAN_RESULT_SCHEMA["properties"]["stages"]["minItems"] == 2
    assert validate_plan(direct_plan())[0]["stages"][0]["id"] == "build"
    assert validate_plan(deepcopy(PLAN))[0]
    for stages in [[PLAN["stages"][2]], list(reversed(direct_plan()["stages"])),
                   [PLAN["stages"][1]] * 2, PLAN["stages"][:2]]:
        assert validate_plan({**PLAN, "stages": stages})[0] is None
    broken = direct_plan()
    broken["stages"][0]["experiment"]["scope"] = "input_measurement"
    assert validate_plan(broken)[0] is None


@pytest.mark.asyncio
async def test_construction_first_still_requires_candidate_validation_and_read_back():
    e = await start_direct("document")
    assert e.workflow.current == "build" and e.workflow.stage_ids == ("build", "deliver")
    assert e.workflow.snapshot(execution_focus=True)["remaining_stages"] == ["build", "deliver"]
    assert {r["stage"] for r in e.workflow.requirements()} == {"build", "deliver"}
    ids = [r["id"] for r in e.workflow.requirements() if r["stage"] == "build"]
    report = {"kind": "candidate", "measurement_revision": "object-v1", "coverage": {"object": 4}, "constraints": [
        {"name": "requested_object", "passed": True, "regions": ["object"], "check_ids": ids}],
        "artifacts": [{"path": "cnt.extxyz", "units": "angstrom", "components": {"C": 4},
            "frame": {"origin": [0, 0, 0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}}]}
    await execute(e, "build.py", record(**report))
    assert assess(e)["ok"]
    assert e.workflow.advance(e, "build", "box", "build.py", structure_path="cnt.extxyz")["error"]["code"] == "validation_required"
    await validate(e)
    assert e.workflow.advance(e, "build", "box", "build.py", structure_path="cnt.extxyz")["ok"]
    assert e.workflow.current == "deliver"
    assert e.workflow.advance(e, "deliver", target="doc")["error"]["code"] == "read_back_required"
    await tool(e, "import_atom_structure_file", {"target": "doc"}, {"revision": 2})
    assert e.workflow.advance(e, "deliver", target="doc")["error"]["code"] == "read_back_required"
    await tool(e, "inspect_atom_structure", {"target": "doc"}, {"revision": 2})
    assert e.workflow.advance(e, "deliver", target="doc")["ok"]
    assert e.workflow.current == "complete"
    assert [r["stage"] for r in e.workflow.checkpoints] == ["build", "deliver"]


@pytest.mark.asyncio
async def test_satisfied_input_checks_override_another_proposed_probe_without_erasing_plan():
    e = evidence(); await activate(e, delivery="report")
    accepted = deepcopy(e.workflow.plan)
    await execute(e, "probe.py", record(**input_report(e)))
    assert e.workflow.probe_ready(e)
    result = e.workflow.advance(e, "observe", hypothesis="Measured input", measurement="Native count=4",
        outcome="continue", next_step="Remeasure with a new checker", next_experiment={
            **accepted[1]["experiment"], "scope": "candidate_work"})
    assert result["ok"] and result["probe_completed"] and result["checkpoint_status"] == "completed"
    assert result["observation"]["outcome"] == "ready" and e.workflow.current == "build"
    assert e.workflow.plan == accepted
    assert e.workflow.pending_experiment["artifact"] == accepted[1]["experiment"]["artifact"]
    assert e.workflow.checkpoints[0]["requirement_evidence"][0]["status"] == "reported_pass"
    assert e.workflow.advance(e, "probe", "box", "probe.py")["checkpoint_reused"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_check", "false_check", "failed_control", "empty_population", "contradiction", "invalid_contract"])
async def test_exit_zero_and_incomplete_or_failed_evidence_cannot_complete_probe(failure):
    e = evidence(); await activate(e, delivery="report")
    report = input_report(e)
    if failure == "missing_check": report["constraints"][0]["check_ids"] = []
    if failure == "false_check": report["constraints"][0]["passed"] = False
    if failure == "failed_control": report["constraints"][0]["controls"] = [{"name": "control", "passed": False}]
    if failure == "empty_population": report["coverage"]["input"] = 0
    if failure == "contradiction": report.update(assumption_invalid=True, contradiction="Native evidence contradicts the assumed input")
    if failure == "invalid_contract": report["coverage"]["input"] = {"nested": 4}
    await execute(e, "probe.py", record(**report))
    assert not e.workflow.probe_ready(e)
    assert e.workflow.current == "probe" and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_combined_assessment_cannot_relabel_an_extra_probe_as_build_work():
    e, calls, run = await setup()
    report = input_report(e)
    # The fixture supplies stdout through the native command boundary.
    e, calls, run = await setup(stdout=record(**report))
    await run()
    before = len(calls)
    result = await run(script_path="another-probe.py", source="print('redundant')", previous_assessment=prior(e))
    assert result["ok"] and result["command_dispatched"] is False
    assert result["submitted_changes_saved"] is False and len(calls) == before
    assert e.workflow.current == "build" and ("box", "another-probe.py") not in e.scripts


@pytest.mark.asyncio
async def test_satisfied_probe_and_committed_candidate_can_share_one_tool_action():
    outputs = []
    e, calls, run = await setup(stdout=lambda: outputs.pop(0))
    outputs.append(record(**input_report(e)))
    await run()
    outputs.append(record(kind="diagnostic", values={"candidate_atoms": 4}))
    result = await run(script_path="build.py", source="print('candidate')", previous_assessment=prior(e))
    assert result["ok"] and result["step"]["stage"] == "build"
    assert e.workflow.current == "build" and e.execution_sequence == 2
    assert len(calls) == 4 and len(e.workflow.checkpoints) == 1
    assert calls[-1][1]["argv"] == ["python3", "build.py"]


@pytest.mark.asyncio
async def test_review_of_two_stage_plan_merges_only_its_remaining_stages():
    e = await start_direct()
    await execute(e, "build.py", record(kind="diagnostic", values={"count": 4}))
    assert assess(e)["ok"]
    assert await tool(e, "finish_task", {"result": {"status": "needs_plan_review", "reason": "Executed candidate diagnostic contradicts the placement assumption"}}, "Task completed.") == "Task completed."
    revised = {"status": "planned", "plan_revision": e.workflow.plan_revision,
        "stages": [{"id": "build", "experiment": {"method": "Construct using the measured alternative"}}]}
    assert await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner") == "Task completed."
    assert e.workflow.stage_ids == ("build", "deliver") and e.workflow.current == "build"
    assert e.workflow.plan[0]["experiment"]["method"] == "Construct using the measured alternative"
    assert e.workflow.plan[0]["checks"] == PLAN["stages"][1]["checks"]
