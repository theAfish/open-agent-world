"""Regressions for the 2026-10 optimization paths P1-P7; no model API runs."""
import asyncio
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.harness import HarnessLimits
from oaw_atomsculptor.runtime import _ModelTrace, _trim_specialist_history
from oaw_atomsculptor.delivery import run_delivery_step
from test_workflow import PLAN, REQUEST, activate, evidence, execute, tool
from test_execution_actions import request
from test_execution_steps import prior, setup as step_setup


# ---------------------------------------------------------------- P1: host-filled receipts
@pytest.mark.asyncio
async def test_assessment_omitting_receipt_fields_is_filled_from_latest_execution():
    e = evidence(); await activate(e)
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"},
               {"written": 7})
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]},
               {"exit_code": 0, "command_id": "cmd-9", "stdout": "arc=2.13"})
    result = e.workflow.assess_previous(e, {"hypothesis": "One measured input", "measurement": "arc=2.13",
                                            "outcome": "continue", "next_step": "Use the measured arc"})
    assert result["ok"] and result["observation"]["script_path"] == "probe.py"
    assert result["observation"]["command_id"] == e.latest_experiment().command_id
    # The same execution cannot be assessed twice under one plan revision; the
    # host-fill resolves it to the identical script before rejecting.
    reassessed = e.workflow.advance(e, "observe", "", "", hypothesis="Boundary counted",
                                    measurement="counts=12", outcome="continue", next_step="Advance probe")
    assert reassessed["error"]["code"] == "observation_already_recorded"


@pytest.mark.asyncio
async def test_contradictory_receipt_field_rejects_whole_step_with_actual_values():
    e = evidence(); await activate(e)
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"},
               {"written": 7})
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]},
               {"exit_code": 0, "command_id": "cmd-9", "stdout": "arc=2.13"})
    result = e.workflow.assess_previous(e, {"command_id": "stale", "hypothesis": "h", "measurement": "m",
                                            "outcome": "continue", "next_step": "n"})
    assert result["error"]["code"] == "assessment_receipt"
    assert e.latest_experiment().command_id in result["error"]["message"]


# ---------------------------------------------------------------- P7: host-side stagnation
@pytest.mark.asyncio
async def test_ready_claim_after_unchanged_code_failure_cannot_complete_a_checkpoint():
    e = evidence(); await activate(e)
    for index in range(2):
        await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": f"probe{index}.py", "content": "print(1)"},
                   {"written": 7})
        await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", f"probe{index}.py"]},
                   {"exit_code": 1, "command_id": f"cmd{index}", "stderr": "boom"})
        assessed = e.workflow.advance(e, "observe", "box", f"probe{index}.py", hypothesis="Try again",
                                      measurement="exit 1", outcome="ready" if index == 1 else "continue",
                                      next_step="Claim readiness anyway" if index == 1 else "Patch the failure")
        assert assessed["ok"], assessed
    forced = assessed
    assert forced["ok"] and forced["checkpoint_status"] == "awaiting_evidence"
    assert forced["checkpoint_result"]["error"]["code"] == "execution_required"
    assert not e.workflow.checkpoints and e.workflow.host_signal is None


# ---------------------------------------------------------------- P3: bounded reasoning tail
def thought_contents(sizes):
    return [types.Content(role="model", parts=[
        types.Part(text="x" * size, thought=True)]) for size in sizes]


def trim(e, contents):
    req = NS(contents=contents)
    _trim_specialist_history(NS(types=types), _ModelTrace(asyncio.Queue()), "structure_builder", req, e)
    return req.contents


def test_unsigned_thoughts_keep_only_a_bounded_recent_tail():
    e = evidence()
    kept = trim(e, thought_contents([3000, 3000, 3000]))
    retained = sum(len(p.text or "") for c in kept for p in c.parts if p.thought)
    assert 0 < retained <= e.limits.retained_thought_chars
    assert kept  # the newest reasoning survives


def test_zero_budget_restores_full_omission_and_tool_parts_always_survive():
    e = evidence(); e.limits = HarnessLimits(retained_thought_chars=0)
    contents = thought_contents([10, 10])
    contents.append(types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
        id="c1", name="execute_command", args={}))]))
    kept = trim(e, contents)
    assert not any(p.thought for c in kept for p in c.parts)
    assert any(p.function_call for c in kept for p in c.parts)


# ---------------------------------------------------------------- P2: stable frame discipline
@pytest.mark.asyncio
async def test_stable_frame_is_sent_once_and_replaced_only_when_its_facts_change():
    e = evidence(); await activate(e)
    req = LlmRequest(contents=[], config=types.GenerateContentConfig(max_output_tokens=81920))
    e.before_model("structure_builder", req, NS(types=types), 81920)

    def stable(req):
        return [p.text for c in req.contents for p in c.parts if p.text and p.text.startswith("HOST STABLE CONTEXT")]

    first = stable(req)
    assert len(first) == 1
    e.before_model("structure_builder", req, NS(types=types), 81920)
    assert stable(req) == first  # unchanged facts: no duplicate, no rewrite
    await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "ready",
               "python_environment": {"python_version": "3.12.14"}})
    e.before_model("structure_builder", req, NS(types=types), 81920)
    replaced = stable(req)
    assert len(replaced) == 1 and replaced != first


# ---------------------------------------------------------------- P4: incremental edit hint
@pytest.mark.asyncio
async def test_incremental_contract_requires_patch_or_explicit_cas_rewrite_every_time():
    e, calls, run = await step_setup()
    await run()
    for _ in range(2):
        result = await run(source="print('next full version')", previous_assessment=prior(e))
        assert result["error"]["code"] == "step_patch_required"
        assert len(calls) == 2
    accepted = await run(source="print('next full version')", previous_assessment=prior(e),
                         action_mode="rewrite")
    assert accepted["ok"] and accepted["step"]["bytes_changed"]


# ---------------------------------------------------------------- P5: delivery pipeline
async def delivery_setup(*, failing=False, unvalidated=False):
    e = evidence(); await activate(e)  # delivery=document
    await execute(e, "probe.py", result={"exit_code": 0, "stdout": "arc=2.13"})
    assert e.workflow.advance(e, "probe", "box", "probe.py")["ok"]
    await execute(e, "build.py", result={"exit_code": 0, "command_id": "b1", "stdout": "built"})
    from test_workflow import validate
    await validate(e, skill="skill-node")
    await tool(e, "run_skill_script", {"sandbox": "box", "script": "scripts/structure_inspect.py",
        "argv": ["to_atomsculptor_document"]},
        {"exit_code": 0, "stdout": json.dumps({"input_structure_file": "cnt.extxyz", 
            "output_document_file": "cnt.json"})})
    advanced = e.workflow.advance(e, "build", "box", "build.py", structure_path="cnt.extxyz")
    assert advanced["ok"], advanced
    assert e.workflow.current == "deliver"
    definitions = [ScopedToolDefinition("operation:" + name, name, "Native operation") for name in
                   ("run_skill_script", "import_atom_structure_file", "inspect_atom_structure",
                    "wait_sandbox_operation", "write_sandbox_text_file", "execute_command")]
    e.configure_tools(definitions)
    e.steps.available = True
    calls = []

    async def invoke(capability, args):
        name = capability.removeprefix("operation:")
        calls.append((name, dict(args)))
        return await result_for(name, args)

    async def result_for(name, args):
        if name == "run_skill_script" and args["argv"][0] == "validate_structure":
            if failing:
                return {"exit_code": 1, "command_id": "v1", "stdout": json.dumps({"passed": False, "file": "cnt.extxyz"})}
            return {"exit_code": 0, "command_id": "v1", "stdout": json.dumps(
                {"passed": True, "file": "cnt.extxyz"})}
        if name == "run_skill_script":
            return {"exit_code": 0, "command_id": "c1", "stdout": json.dumps(
                {"input_structure_file": "cnt.extxyz", 
                 "output_document_file": "cnt.json"})}
        if name == "inspect_atom_structure":
            revision = 2 if any(n == "import_atom_structure_file" for n, _ in calls[:-1]) else 1
            return {"revision": revision,  "summary": {"atom_count": 80}}
        if name == "import_atom_structure_file":
            return {"revision": 2}
        return {}

    stopped = asyncio.Event()
    return e, definitions, invoke, stopped, calls


@pytest.mark.asyncio
async def test_delivery_pipeline_runs_all_receipt_checked_steps_in_one_action():
    e, definitions, invoke, stopped, calls = await delivery_setup()
    result = await run_delivery_step(e, definitions, invoke, stopped, call_id="deliver-call",
                                     sandbox="box", skill="skill-node", structure_path="cnt.extxyz",
                                     target="structure", validate={"expected_count": 80, "min_vacuum": 8})
    assert result["ok"], result
    assert [name for name, _ in calls] == ["run_skill_script", "run_skill_script",
                                           "inspect_atom_structure", "import_atom_structure_file",
                                           "inspect_atom_structure"]
    assert calls[0][1]["argv"][:5] == ["validate_structure", "--folder", ".", "--file-name", "cnt.extxyz"]
    assert calls[1][1]["argv"][:5] == ["to_atomsculptor_document", "--folder", ".", "--file-name", "cnt.extxyz"]
    assert "expected_file_sha256" not in calls[3][1] and calls[3][1]["expected_revision"] == 1
    assert result["revision"] == 2 and e.workflow.current == "complete"
    assert e.confirmed["structure"] == 2


@pytest.mark.asyncio
async def test_failed_validation_stops_the_pipeline_before_any_conversion_or_import():
    e, definitions, invoke, stopped, calls = await delivery_setup(failing=True)
    result = await run_delivery_step(e, definitions, invoke, stopped, call_id="deliver-call",
                                     sandbox="box", skill="skill-node", structure_path="cnt.extxyz",
                                     # Stronger parameters require a fresh validation;
                                     # the earlier bare validation is reusable only as-is.
                                     target="structure", validate={"expected_count": 80})
    assert result["error"]["code"] == "delivery_validation_failed"
    assert [name for name, _ in calls] == ["run_skill_script"]
    assert e.workflow.current == "deliver"
