"""Recover rejected model actions without dispatching them or hiding model errors."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.actions import SERIAL_ACTION_INSTRUCTION
from oaw_atomsculptor.harness import HarnessStopped
from oaw_atomsculptor.model_recovery import bounded_output_model
from oaw_atomsculptor.runtime import BUILDER_INSTRUCTION
from test_execution_actions import TOOLS, setup, request, prepare
from test_workflow import activate, tool


def response(*calls):
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(id=identity, name=name, args=args))
        for identity, name, args in calls]), finish_reason="STOP")


async def complete(e, model, req):
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    accepted = [item async for item in model.generate_content_async(req) if not item.partial]
    for item in accepted:
        await e.trace.callbacks("structure_builder")["after_model_callback"](None, item)
    return accepted


@pytest.mark.asyncio
async def test_mixed_batch_returns_per_call_feedback_without_regenerating_response():
    e = setup(); await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    turns = 0
    class Fixture(BaseLlm):
        model: str = "openai/action-feedback-fixture"
        async def generate_content_async(self, req, stream=False):
            nonlocal turns
            turns += 1
            if turns == 1:
                yield response(("write", "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}),
                               ("read", "inspect_sandbox", {"sandbox": "box"}))
            else:
                yield response(("write-alone", "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}))
    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, NS(types=types), 81920)
    prepare(e, req := request()); first = await complete(e, model, req)
    assert [p.function_call.id for p in first[0].content.parts] == ["write", "read"] and turns == 1
    write, read = [p.function_call for p in first[0].content.parts]
    rejected = await tool(e, write.name, write.args, {"written": 8}, call_id=write.id)
    returned = await tool(e, read.name, read.args, {"state": "ready"}, call_id=read.id)
    assert rejected["error"]["code"] == "non_readonly_tool_batch"
    assert returned == {"state": "ready"} and not e.scripts and not e.executions
    prepare(e, req := request()); second = await complete(e, model, req)
    call = second[0].content.parts[0].function_call
    assert await tool(e, call.name, call.args, {"written": 8}, call_id=call.id) == {"written": 8}
    assert e.scripts[("box", "probe.py")].phase == "written"
    assert turns == 2 and e.actions.recoveries == 0 and e.block_reason is None


@pytest.mark.asyncio
async def test_identical_rejected_calls_are_detected_after_feedback_with_new_ids():
    e = setup(); await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    turns = 0
    class Fixture(BaseLlm):
        model: str = "openai/action-feedback-fixture"
        async def generate_content_async(self, req, stream=False):
            nonlocal turns
            turns += 1
            yield response((f"save-{turns}", "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}),
                           (f"execute-{turns}", "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]}))
    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, NS(types=types), 81920)
    for _ in range(2):
        prepare(e, req := request()); accepted = await complete(e, model, req)
        for part in accepted[0].content.parts:
            call = part.function_call
            result = await tool(e, call.name, call.args, {}, call_id=call.id)
            assert result["error"]["code"] == "non_readonly_tool_batch"
    with pytest.raises(HarnessStopped, match="unchanged rejected .*non_readonly_tool_batch"):
        prepare(e, request())
    assert turns == 2 and e.actions.recoveries == 0 and not e.scripts and not e.executions


@pytest.mark.asyncio
async def test_a_changed_protocol_error_gets_its_own_call_feedback():
    e = setup(); await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    turns = 0
    class Fixture(BaseLlm):
        model: str = "openai/action-feedback-fixture"
        async def generate_content_async(self, req, stream=False):
            nonlocal turns
            turns += 1
            if turns == 1:
                yield response(("unknown", "unavailable_tool", {}))
            elif turns == 2:
                yield response(("save", "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py"}),
                               ("execute", "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]}))
            else:
                yield response(("corrected", "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}))
    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, NS(types=types), 81920)
    codes = []
    for _ in range(3):
        prepare(e, req := request()); e.actions.allowed = {"write_sandbox_text_file", "execute_command"}
        accepted = await complete(e, model, req)
        for part in accepted[0].content.parts:
            call = part.function_call
            result = await tool(e, call.name, call.args, {"written": 8}, call_id=call.id)
            if "error" in result:
                assert result["error"]["call_id"] == call.id
                codes.append(result["error"]["code"])
    assert codes == ["disallowed_tool", "non_readonly_tool_batch", "non_readonly_tool_batch"]
    assert turns == 3 and not e.block_reason and e.actions.recoveries == 0
    assert e.scripts[("box", "probe.py")].phase == "written"


@pytest.mark.asyncio
async def test_readonly_batch_remains_accepted_without_a_recovery():
    e = setup()
    await activate(e)

    class Fixture(BaseLlm):
        model: str = "openai/action-feedback-fixture"

        async def generate_content_async(self, req, stream=False):
            yield response(("sandbox", "inspect_sandbox", {"sandbox": "box"}),
                           ("document", "inspect_atom_structure", {"target": "doc"}))

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, NS(types=types), 81920)
    prepare(e, req := request())
    result = await complete(e, model, req)
    assert len(result[0].content.parts) == 2 and e.actions.recoveries == 0


def test_initial_and_recovery_prompts_share_the_same_actual_batch_policy():
    assert SERIAL_ACTION_INSTRUCTION in BUILDER_INSTRUCTION
    assert "even when targets or output files differ" in SERIAL_ACTION_INSTRUCTION


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["read_skills", "skills_reader"])
async def test_corrected_read_clears_only_its_own_feedback_and_keeps_call_identity(alias):
    e = setup()
    await activate(e)
    e.configure_tools([ScopedToolDefinition("operation:read_skills", alias, "Reader")])
    args = {"skill_id": "generator", "file_path": "scripts/generate.py", "include_file_contents": False}
    await tool(e, alias, args, {"error": "Missing required toolbox"}, call_id="failed-read")
    assert e.correction_feedback["call_id"] == "failed-read"
    assert e.correction_feedback["read_dependency"] == {"skill_id": "generator", "file_path": "scripts/generate.py"}
    await tool(e, alias, {"toolbox": "skills", "skill_id": "generator", "file_path": "scripts/generate.py"},
               {"file": {"path": "scripts/generate.py", "content": "print(1)"}}, call_id="fixed-read")
    assert e.correction_feedback is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["other_file", "other_skill", "other_toolbox", "other_role", "other_tool"])
async def test_unrelated_read_cannot_clear_an_unresolved_read_error(change):
    e = setup(names=TOOLS | {"read_skills"})
    await activate(e)
    args = {"toolbox": "skills", "skill_id": "generator", "file_path": "scripts/missing.py"}
    await tool(e, "read_skills", dict(args), {"error": "File missing"}, call_id="missing-file")
    feedback = deepcopy(e.correction_feedback)
    changed = dict(args)
    if change == "other_file": changed["file_path"] = "scripts/other.py"
    if change == "other_skill": changed["skill_id"] = "other"
    if change == "other_toolbox": changed["toolbox"] = "other"
    await tool(e, "inspect_sandbox" if change == "other_tool" else "read_skills",
               {"sandbox": "box"} if change == "other_tool" else changed,
               {"file": {"content": "read succeeded"}}, role="planner" if change == "other_role" else "structure_builder")
    assert e.correction_feedback == feedback


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["execution_unknown", "plan_review", "workflow_blocked"])
async def test_a_successful_read_does_not_settle_unknown_execution_or_workflow_blockers(kind):
    e = setup(names=TOOLS | {"read_skills"})
    await activate(e)
    args = {"toolbox": "skills", "skill_id": "generator"}
    feedback = {"role": "structure_builder", "tool": "read_skills", "kind": kind,
                "plan_revision": e.workflow.plan_revision, "read_dependency": dict(args)}
    e.correction_feedback = deepcopy(feedback)
    await tool(e, "read_skills", args, {"skill": {"id": "generator"}})
    assert e.correction_feedback == feedback


@pytest.mark.asyncio
async def test_a_successful_read_cannot_erase_a_modelling_error():
    e = setup(names=TOOLS | {"read_skills"})
    await activate(e)
    feedback = {"role": "structure_builder", "tool": "run_modelling_step", "kind": "step_contract",
                "plan_revision": e.workflow.plan_revision, "message": "Fix the saved candidate"}
    e.correction_feedback = deepcopy(feedback)
    await tool(e, "read_skills", {"toolbox": "skills", "skill_id": "generator"}, {"skill": {"id": "generator"}})
    assert e.correction_feedback == feedback


@pytest.mark.asyncio
async def test_file_preview_feedback_is_scoped_to_its_native_file_reference():
    e = setup(names=TOOLS | {"preview_file"})
    await activate(e)
    reference = {"kind": "sandbox", "source_id": "first-box", "root": "workspace", "path": "shared.extxyz"}
    await tool(e, "preview_file", {"file": reference}, {"error": "File is not readable"}, call_id="failed-preview")
    feedback = deepcopy(e.correction_feedback)
    await tool(e, "preview_file", {"file": {**reference, "source_id": "other-box"}}, {"data": "", "size_bytes": 0})
    assert e.correction_feedback == feedback
    await tool(e, "preview_file", {"file": reference}, {"data": "", "size_bytes": 0})
    assert e.correction_feedback is None
