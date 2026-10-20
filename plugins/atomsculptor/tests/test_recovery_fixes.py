"""Regressions for the 2026-10-15 blocked-run recovery fixes (six items)."""
import asyncio
import hashlib
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock  # noqa: F401  (kept for future mocks)

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from oaw_atomsculptor.execution_steps import source_reader_function
from oaw_atomsculptor.harness import HarnessLimits
from test_workflow import PLAN, REQUEST, activate, evidence, tool


async def _saved_script(e, path="probe.py", code="print('measurement')"):
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": path, "content": code},
               {"written": len(code), "sha256": hashlib.sha256(code.encode()).hexdigest()})
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", path]},
               {"exit_code": 0, "command_id": "cmd-" + path, "stdout": "ok"})
    assessed = e.workflow.advance(e, "observe", "box", path, hypothesis="measured",
                                  measurement="ok", outcome="continue", next_step="advance")
    assert assessed["ok"], assessed


# ------------------------------------------------ 1. source_snapshot lists valid paths
@pytest.mark.asyncio
async def test_source_snapshot_error_lists_available_paths_and_explains_helper():
    e = evidence(); await activate(e)
    await _saved_script(e)
    reader = source_reader_function(e)
    result = await reader(sandbox="box", script_path="missing.py")
    assert result["error"]["code"] == "source_snapshot"
    assert result["error"]["available_paths"] == ["probe.py"]
    assert "probe.py" in result["error"]["message"]
    helper = await reader(sandbox="box", script_path="_oaw_modelling_abc.py")
    assert "host staging machinery" in helper["error"]["message"]


# ------------------------------------------------ 2. read-only usage errors never fuse
@pytest.mark.asyncio
async def test_three_identical_reader_errors_do_not_block_the_run():
    e = evidence(); await activate(e)
    await _saved_script(e)
    rejection = {"ok": False, "error": {"code": "source_snapshot", "message": "nope"}}
    for _ in range(3):
        await tool(e, "read_modelling_source", {"sandbox": "box", "script_path": "_oaw_modelling_x.py"},
                   rejection)
    assert e.block_reason is None
    # The same count on a mutating dependency still fuses (unchanged behaviour).
    for _ in range(3):
        await tool(e, "install_python_packages", {"sandbox": "box", "requirements": ["scipy"]},
                   {"ok": False, "error": {"code": "invalid_package", "message": "bad"}})
    assert e.block_reason is not None and "install_python_packages" in e.block_reason


# ------------------------------------------------ 3. blocked collapses tools to finish_task
@pytest.mark.asyncio
async def test_blocked_workflow_advertises_only_finish_task():
    e = evidence(); await activate(e)
    e.block_reason = "read_modelling_source repeatedly returned the same dependency error (3 attempts)"
    req = LlmRequest(contents=[], config=types.GenerateContentConfig(tools=[types.Tool(
        function_declarations=[
            types.FunctionDeclaration(name=n) for n in
            ("finish_task", "run_modelling_step", "resume_modelling_step", "read_skills")])]))
    e.actions.prepare(e, "structure_builder", req, NS(types=types))
    names = {d.name for t in req.config.tools for d in t.function_declarations}
    assert names == {"finish_task"}
    assert req.config.tool_config.function_calling_config.mode is not None
    assert "finish_task" in e.actions.last_action and "workflow is blocked" in e.actions.last_action


# ------------------------------------------------ 4. subtask_inputs names paths and routes
@pytest.mark.asyncio
async def test_missing_input_paths_error_lists_paths_and_three_routes():
    e = evidence(); await activate(e)
    await _saved_script(e)
    action = {"input_paths": ["probe_out.json"], "depends_on": [], "evidence_refs": []}
    rejected = e.workflow.validate_dependencies(action, e, "box")
    assert rejected["error"]["code"] == "subtask_inputs"
    message = rejected["error"]["message"]
    assert "probe_out.json" in message
    assert "oaw_measurements" in message and "emit" in message.lower()
    assert "needs_plan_review" in message
    assert "Inspecting files cannot create a receipt" in message


# ------------------------------------------------ 5. helper contract documents machinery
def test_helper_contract_forbids_reading_the_helper_source():
    from oaw_atomsculptor.execution_steps import ExecutionSteps
    steps = ExecutionSteps()
    steps.enable_helpers()
    contract = steps.helper_contract()
    assert "never read, reprint or validate its source" in contract["usage"]


# ------------------------------------------------ 6. producer nudge fires at execution time
@pytest.mark.asyncio
async def test_execution_without_artifact_records_hints_about_planned_inputs():
    e = evidence()  # submit the input-consuming plan directly (no default activate)
    plan = json.loads(json.dumps(PLAN))
    plan["stages"][1]["experiment"]["input_paths"] = ["probe_out.json"]
    await tool(e, "finish_task", {"result": plan}, "Task completed.", role="planner")
    assert e.workflow.before_delegation("structure_builder") is None
    assert e.workflow.plan[1]["experiment"]["input_paths"] == ["probe_out.json"]

    from backend.agents.models import ScopedToolDefinition
    definitions = [ScopedToolDefinition("operation:" + name, name, "Native operation",
                                        input_schema={"properties": {"timeout_seconds": {"type": "number"}}}
                                        if name == "execute_command" else None)
                   for name in ("write_sandbox_text_file", "execute_command")]
    e.configure_tools(list(definitions) + [ScopedToolDefinition("operation:run_modelling_step",
                                                                "run_modelling_step", "Native operation")])

    async def invoke(capability, args):
        name = capability.removeprefix("operation:")
        if name == "write_sandbox_text_file":
            return {"written": 1, "sha256": "a" * 64}
        return {"exit_code": 0, "command_id": "cmd-build", "stdout": "built"}

    # The producer (probe) step runs fine, but registers no artifact records
    # while the planned build stage consumes one: the hint must fire here.
    result = await e.steps.run(e, definitions, invoke, asyncio.Event(), call_id="c2",
                               sandbox="box", script_path="probe.py", source="print(1)",
                               question="q", expected_output="o", stop_condition="s")
    assert result["ok"], result
    assert "probe_out.json" in result["next_action"]
    assert "artifact records" in result["next_action"]


# ------------------------------------------------ 7. consumed review is valid prior evidence
@pytest.mark.asyncio
async def test_review_consumed_by_revision_is_a_valid_prerequisite():
    e = evidence(); await activate(e)
    await _saved_script(e)
    # A second measured execution triggers the review and is assessed as such.
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe2.py", "content": "print(2)"},
               {"written": 7, "sha256": "b" * 64})
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe2.py"]},
               {"exit_code": 0, "command_id": "cmd-2", "stdout": "measured"})
    e.measurement_reports[2] = {"kind": "baseline", "values": {"n": 2}}
    assessed = e.workflow.advance(e, "observe", "box", "probe2.py", hypothesis="contradiction",
                                  measurement="measured mismatch", outcome="needs_plan_review",
                                  next_step="review")
    assert assessed["ok"], assessed
    row = e.experiment_history[-1]
    assert row["outcome"] == "needs_plan_review" and row["execution_sequence"] == 2
    await tool(e, "finish_task", {"result": {"status": "needs_plan_review", "reason": "metric contradicted",
        "sandbox": "box", "script_path": "probe2.py"}}, "Task completed.")
    assert e.workflow.review_request is not None
    revised = json.loads(json.dumps(PLAN))
    revised["plan_revision"] = 1
    revised["stages"] = [{"id": "probe", "goal": "Revised probe", "checks": ["Recheck"],
        "experiment": {"scope": "input_measurement", "artifact": "probe2.py", "question": "q",
                       "method": "m", "stop_condition": "s", "evidence_refs": ["execution:2"]}}]
    await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner")
    assert e.workflow.plan_revision == 2
    # The consumed review is exactly the evidence revision 2 is based on.
    rejected = e.workflow.validate_dependencies({"input_paths": [], "depends_on": [],
                                                 "evidence_refs": ["execution:2"]}, e, "box")
    assert rejected is None, rejected
    assert "execution:2" in e.workflow.dependency_registry(e)["execution_refs"]


# ------------------------------------------------ 8. unconsumed review still rejected, with the reason
@pytest.mark.asyncio
async def test_unconsumed_review_ref_names_the_failed_condition():
    e = evidence(); await activate(e)
    await _saved_script(e)
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe2.py", "content": "print(2)"},
               {"written": 7, "sha256": "b" * 64})
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe2.py"]},
               {"exit_code": 0, "command_id": "cmd-2", "stdout": "measured"})
    e.measurement_reports[2] = {"kind": "baseline", "values": {"n": 2}}
    assessed = e.workflow.advance(e, "observe", "box", "probe2.py", hypothesis="h",
                                  measurement="m", outcome="needs_plan_review", next_step="n")
    assert assessed["ok"], assessed
    rejected = e.workflow.validate_dependencies({"input_paths": [], "depends_on": [],
                                                 "evidence_refs": ["execution:2"]}, e, "box")
    assert rejected["error"]["code"] == "subtask_evidence"
    message = rejected["error"]["message"]
    assert "needs_plan_review" in message and "already consumed" in message
    missing = e.workflow.validate_dependencies({"input_paths": [], "depends_on": [],
                                                "evidence_refs": ["execution:99"]}, e, "box")
    assert "no assessed execution" in missing["error"]["message"]


# ------------------------------------------------ 9. unsatisfiable refs stripped at scheduling
@pytest.mark.asyncio
async def test_schedule_strips_never_valid_refs_and_records_them():
    e = evidence(); await activate(e)
    experiment = {"scope": "input_measurement", "artifact": "next.py", "question": "q",
                  "method": "m", "stop_condition": "s", "evidence_refs": ["execution:99", "typo_ref"]}
    e.workflow.schedule_experiment(experiment, origin="assessment", evidence=e)
    pending = e.workflow.pending_experiment
    assert pending["evidence_refs"] == []
    assert pending["stripped_evidence_refs"] == ["execution:99", "typo_ref"]
