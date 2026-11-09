"""Model choices, small contracts and per-call feedback through native interfaces."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from google.adk import Agent, Runner
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from jsonschema import Draft202012Validator

from backend.plugins.registry import PluginRegistration
from open_agent_world.skill_packages import SkillPackagePlugin
from oaw_atomsculptor.measurements import Measurement, measurement_contract
from oaw_atomsculptor.model_recovery import bounded_output_model
from oaw_atomsculptor.runtime import _model_callbacks
from oaw_atomsculptor.skill_package import atomsculptor_skills
from test_execution_actions import setup, request, prepare
from test_execution_steps import prior
from test_typed_execution import native_sdk_fixture
from test_workflow import activate, tool


@pytest.mark.asyncio
async def test_generated_contract_is_readable_through_native_skill_file_interface():
    package = atomsculptor_skills()
    plugin = SkillPackagePlugin(package)
    registration = PluginRegistration(plugin.descriptor)
    plugin.register(registration)
    class Context:
        async def node_document_action(self, *_):
            return {"value": package.model_dump(mode="json")}
    result = await registration.capability_handlers["atomsculptor.skills.toolbox.read"](
        Context(), NS(), {"skill_id": "structure_inspect", "file_path": "references/measurement-contract.json"})
    full = json.loads(result["file"]["content"])
    assert full == measurement_contract()
    assert full["schema"] == Measurement.model_json_schema()
    for example in (full["example"], full["candidate_example"]):
        Measurement.model_validate(example["oaw_measurements"])
    assert full["candidate_example"]["oaw_measurements"]["constraints"][0]["passed"] is False


@pytest.mark.asyncio
async def test_known_step_fields_are_optional_in_schema_and_filled_at_sdk_boundary():
    e, files, calls, invoke = await native_sdk_fixture()
    pending = dict(e.workflow.pending_experiment)
    req = request({"run_modelling_step", "finish_task", "advance_modelling_stage"})
    prepare(e, req)
    schema = next(d.parameters_json_schema for t in req.config.tools for d in t.function_declarations
                  if d.name == "run_modelling_step")
    arguments = {"sandbox": "box", "script_path": "probe.py", "source": "print(1)"}
    Draft202012Validator(schema).validate(arguments)
    result = await invoke("run_modelling_step", arguments)
    assert result["ok"] and files["probe.py"] == "print(1)" and len(calls) == 2
    assert {k: result["step"][k] for k in ("question", "expected_output", "stop_condition")} == {
        k: pending[k] for k in ("question", "expected_output", "stop_condition")}
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    req = request({"run_modelling_step", "patch_modelling_step", "rerun_modelling_step", "rewrite_modelling_step"})
    prepare(e, req)
    for t in req.config.tools:
        for declaration in t.function_declarations:
            assert "previous_assessment" not in declaration.parameters_json_schema["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["artifact", "stage", "revision"])
async def test_unknown_or_stale_subtasks_do_not_supply_step_defaults(mismatch):
    e, files, calls, invoke = await native_sdk_fixture()
    if mismatch == "artifact":
        e.workflow.pending_experiment["artifact"] = "other.py"
    elif mismatch == "stage":
        e.workflow.pending_experiment["stage"] = "build"
    else:
        e.workflow.pending_experiment["plan_revision"] -= 1
    result = await invoke("run_modelling_step", {"sandbox": "box", "script_path": "probe.py", "source": "print(1)"})
    assert not result["ok"] and not calls and not files
    assert not result["error"]["command_dispatched"]


@pytest.mark.asyncio
async def test_report_graph_without_a_skill_reader_keeps_the_full_contract_available():
    e = setup(names={"write_sandbox_text_file", "execute_command", "finish_task", "advance_modelling_stage"})
    await activate(e, delivery="report")
    req = request(); prepare(e, req)
    frame = next(json.loads(p.text.split("\n", 1)[1]) for c in req.contents for p in c.parts
                 if p.text and p.text.startswith("HOST STABLE CONTEXT"))
    assert frame["measurement_contract"]["schema"] == Measurement.model_json_schema()


@pytest.mark.asyncio
async def test_an_unreadable_contract_reference_falls_back_to_the_same_inline_schema():
    e = setup(); await activate(e, delivery="report")
    await tool(e, "read_skill", {"skill": "old-copy", "file_path": "references/measurement-contract.json"},
        {"ok": False, "error": "Reference missing from this equipped Skill copy"})
    req = request(); prepare(e, req)
    frame = next(json.loads(p.text.split("\n", 1)[1]) for c in req.contents for p in c.parts
                 if p.text and p.text.startswith("HOST STABLE CONTEXT"))
    assert frame["measurement_contract"]["schema"] == Measurement.model_json_schema()
    assert not e.block_reason and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_inline_thresholds_are_advisory_and_do_not_replace_saved_script_receipts():
    e = setup(); await activate(e, delivery="report")
    for index in range(3):
        result = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c",
            f"print({index})\n#" + "input check " * 160]}, {"exit_code": 0, "stdout": str(index)}, call_id=f"inline-{index}")
        assert result["exit_code"] == 0
    assert e.actions.inline_attempts == 3 and not e.workflow.checkpoints and e.latest_experiment() is None
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}, {"written": 8})
    refused = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(1)"]}, {}, call_id="inline-after-save")
    assert refused["error"]["code"] == "saved_probe_required"
    assert e.scripts[("box", "probe.py")].phase == "written" and not e.workflow.checkpoints


def test_call_feedback_is_role_scoped_and_survives_a_later_prepared_response():
    e = setup()
    parts = [types.Part(function_call=types.FunctionCall(id="shared-id", name="absent_tool", args={}))]
    req = request({"inspect_sandbox"})
    e.actions.prepare_response(e, parts, req)
    assert e.actions.rejected_call("planner", "absent_tool", "shared-id") is None
    e.actions.prepare_response(e, [types.Part(function_call=types.FunctionCall(
        id="other-id", name="inspect_sandbox", args={}))], req)
    denied = e.actions.rejected_call("structure_builder", "absent_tool", "shared-id")
    assert denied["error"]["code"] == "disallowed_tool"
    assert not e.actions.response_rejections


@pytest.mark.asyncio
async def test_actual_adk_returns_unknown_and_batch_denials_without_replaying_valid_reads():
    e = setup(); await activate(e, delivery="report")
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    reads, writes, turns = [], [], []
    async def inspect_sandbox(sandbox: str):
        """Read native Sandbox state."""
        reads.append(sandbox)
        return {"state": "ready"}
    async def write_sandbox_text_file(sandbox: str, path: str, content: str):
        """Save native Sandbox source."""
        writes.append((sandbox, path, content))
        return {"written": len(content)}
    async def finish_task(result: str):
        """Return the current task result."""
        return "Task completed."
    class Fixture(BaseLlm):
        model: str = "openai/policy-fixture"
        async def generate_content_async(self, req, stream=False):
            turns.append(req)
            if len(turns) == 1:
                calls = [("unknown", "absent_tool", {}), ("read", "inspect_sandbox", {"sandbox": "box"}),
                         ("write", "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"})]
                parts = [types.Part(function_call=types.FunctionCall(id=i, name=n, args=a),
                                    thought_signature=b"preserve-this-part") for i, n, a in calls]
            elif len(turns) == 2:
                results = {p.function_response.id: p.function_response.response for c in req.contents
                           for p in c.parts or [] if p.function_response}
                assert results["unknown"]["error"]["code"] == "disallowed_tool"
                assert results["write"]["error"]["code"] == "non_readonly_tool_batch"
                assert results["read"] == {"state": "ready"}
                assert reads == ["box"] and not writes
                original = [p for c in req.contents for p in c.parts or [] if p.function_call]
                assert {p.function_call.id for p in original} == {"unknown", "read", "write"}
                assert all(p.thought_signature == b"preserve-this-part" for p in original)
                parts = [types.Part(function_call=types.FunctionCall(id="finish", name="finish_task",
                    args={"result": json.dumps({"status": "blocked", "limitations": ["Fixture finished checking feedback"]})}))]
            else:
                parts = [types.Part(text="Fixture feedback collected.")]
            yield LlmResponse(content=types.Content(role="model", parts=parts), finish_reason="STOP")
    callbacks = _model_callbacks(e.trace, "structure_builder", None, NS(types=types), evidence=e)
    callbacks.update(e.tool_callbacks("structure_builder"))
    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, NS(types=types), 81920)
    agent = Agent(name="structure_builder", model=model, tools=[FunctionTool(f) for f in (
        inspect_sandbox, write_sandbox_text_file, finish_task)], **callbacks)
    sessions = InMemorySessionService()
    await sessions.create_session(app_name="policy", user_id="user", session_id="session")
    runner = Runner(app_name="policy", agent=agent, session_service=sessions)
    events = [event async for event in runner.run_async(user_id="user", session_id="session",
        new_message=types.Content(role="user", parts=[types.Part(text="Inspect feedback.")]))]
    assert events and len(turns) == 3 and reads == ["box"] and not writes
    assert e.actions.recoveries == 0 and not e.scripts and not e.executions
