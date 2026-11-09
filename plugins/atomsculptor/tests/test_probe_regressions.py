"""Code-first measurements and request hygiene from the 137-second stalled run."""
import copy
import json
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from oaw_atomsculptor.actions import inline_python
from oaw_atomsculptor.harness import HarnessLimits
from oaw_atomsculptor.model_recovery import bounded_output_model
from oaw_atomsculptor.workflow import validate_plan
from oaw_atomsculptor.runtime import _model_callbacks
from test_execution_actions import setup, request, prepare, declarations
from test_workflow import PLAN, activate, tool


@pytest.mark.parametrize("argv", [
    ["python3", "-c", "print(8)"], ["/managed/bin/python3.12", "-B", "-u", "-c", "print(8)"],
])
def test_inline_measurement_recognizes_only_direct_python(argv):
    assert inline_python(argv) == "print(8)"


@pytest.mark.parametrize("argv", [[], "python3 -c print(8)", ["sh", "-c", "python3 -c 'print(8)'"],
    ["python3", "probe.py"], ["python3", "-m", "module"], ["python-other", "-c", "print(8)"],
    ["python3", "-c", "print(8)", "extra"], ["python3", "-c", None]])
def test_unrelated_commands_are_not_inline_measurement_receipts(argv):
    assert inline_python(argv) is None


def test_plan_declares_measurement_and_candidate_work_separately_with_field_diagnostics():
    value = copy.deepcopy(PLAN)
    value["stages"][0]["experiment"]["scope"] = "candidate_work"
    parsed, issues = validate_plan(value)
    assert parsed is None and issues[0]["field"] == "result.stages[0].experiment.scope"
    assert "move fitting, matching and candidate search into build" in issues[0]["message"]
    value["stages"][0]["experiment"]["scope"] = "input_measurement"
    assert validate_plan(value) == ({"delivery": value["delivery"], "stages": value["stages"]}, [])


@pytest.mark.asyncio
async def test_actual_inline_result_precedes_saved_probe_but_cannot_advance_or_validate_it():
    e = setup()
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    assert {"execute_command", "write_sandbox_text_file"} <= declarations(req)
    result = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(32)"]},
                        {"exit_code": 0, "stdout": "32\n"}, call_id="inline")
    assert result["exit_code"] == 0 and not e.scripts and not e.outputs
    assert e.actions.inline_measurements[-1]["stdout"] == "32\n"
    assert e.workflow.advance(e, "probe", "box", "probe.py")["error"]["code"] == "execution_required"
    req = request(); prepare(e, req)
    assert "execute_command" in declarations(req) and e.actions.requests_without_progress == 1
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(32)"}, {"written": 9})
    blocked = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(32)"]}, {"exit_code": 0})
    assert blocked["error"]["code"] == "saved_probe_required"
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]},
               {"exit_code": 0, "stdout": "32\n"})
    assert e.workflow.advance(e, "observe", "box", "probe.py", hypothesis="Measure primitive count",
        measurement="32", outcome="ready", next_step="Advance the measured probe")["ok"]
    assert e.workflow.advance(e, "probe", "box", "probe.py")["next_stage"] == "build"


@pytest.mark.asyncio
async def test_failed_inline_attempts_preserve_progress_and_thresholds_are_advisory():
    e = setup(limits=HarnessLimits(inline_probe_chars=128))
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    prepare(e, request())
    for argv, code in [(["sh", "-c", "echo fake"], "input_measurement_required"),
                       (["python3", "-c", " "], "inline_probe_size")]:
        result = await tool(e, "execute_command", {"sandbox": "box", "argv": argv}, {"exit_code": 0})
        assert result["error"]["code"] == code
    assert e.actions.inline_attempts == 0
    for i in range(3):
        result = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", f"raise ValueError({i})\n#" + "x" * 130]},
                   {"exit_code": 1, "stderr": f"ValueError: {i}"}, call_id=f"fail-{i}")
        assert result['exit_code'] == 1
        prepare(e, request())
    req = request(); prepare(e, req)
    assert "execute_command" in declarations(req)
    assert e.actions.requests_without_progress == 5 and not e.actions.successful_measurements
    result = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(1)"]}, {"exit_code": 0})
    assert result['exit_code'] == 0 and e.actions.inline_attempts == 4
    assert not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_running_inline_probe_must_be_collected_without_restarting_or_editing():
    e = setup()
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(8)"]},
               {"status": "running", "operation_id": "inline-op"}, call_id="inline")
    req = request(); prepare(e, req)
    assert declarations(req) == {"wait_sandbox_operation", "finish_task", "advance_modelling_stage"}
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "inline-op"},
               {"status": "running", "operation_id": "inline-op"})
    assert "inline-op" in e.inline_pending and not e.actions.successful_measurements
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "inline-op"},
               {"exit_code": 0, "stdout": "8\n"})
    assert not e.inline_pending and len(e.actions.successful_measurements) == 1
    assert not e.workflow.advance(e, "probe", "box", "probe.py")["ok"]


@pytest.mark.asyncio
async def test_repeated_inline_source_does_not_create_unbounded_new_progress():
    e = setup()
    await activate(e)
    for i in range(2):
        await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(8)"]},
                   {"exit_code": 0, "stdout": "8"}, call_id=f"inline-{i}")
        prepare(e, request())
    assert len(e.actions.successful_measurements) == 1 and e.actions.requests_without_progress == 2


@pytest.mark.asyncio
async def test_recovery_replaces_only_host_snapshot_and_preserves_source_receipts_and_user_text():
    e = setup()
    await activate(e)
    user = types.Content(role="user", parts=[types.Part(text="HOST RUN EVIDENCE pasted by user: keep me")])
    source = types.Content(role="model", parts=[types.Part(thought_signature=b"signed", function_call=
        types.FunctionCall(id="write", name="write_sandbox_text_file", args={"content": "print(8)", "path": "probe.py"}))])
    receipt = types.Part(function_response=types.FunctionResponse(id="write", name="write_sandbox_text_file",
                                                                 response={"written": 8}))
    req = request(); req.contents = [user, source]
    prepare(e, req)
    first_snapshot = req.contents[-1].parts[0].text
    req.contents[-1].parts.append(receipt)  # Mixed content must retain its real tool receipt.
    e.actions.recoveries = 1
    req.contents.append(types.Content(role="user", parts=[types.Part(text="HOST EXECUTION ACTION RECOVERY: act now")]))
    prior_prefix = [c.model_dump_json(exclude_none=True) for c in req.contents]
    prepare(e, req)
    assert [c.model_dump_json(exclude_none=True) for c in req.contents[:len(prior_prefix)]] == prior_prefix
    texts = [p.text for c in req.contents for p in c.parts if p.text]
    host = [t for t in texts if t.startswith("HOST RUN EVIDENCE (not a tool invocation)")]
    assert len(host) == 2 and first_snapshot in texts
    assert json.loads(host[-1].split("\n", 1)[1])["execution_action"]["action_recoveries"] == 1
    assert user in req.contents and source in req.contents
    assert any(p == receipt for c in req.contents for p in c.parts)
    assert "HOST EXECUTION ACTION RECOVERY: act now" in texts
    prepare(e, req)
    assert sum(p.text and p.text.startswith("HOST RUN EVIDENCE (not a tool invocation)") or False
               for c in req.contents for p in c.parts) == 3


@pytest.mark.asyncio
async def test_duplicate_starts_coalesced_before_dispatch_without_replaying_or_dropping_other_tools():
    e = setup()
    await activate(e)
    req = request(); prepare(e, req)
    # Match the actual stopped -> duplicate start response, including distinct IDs.
    await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "stopped"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    first = types.Part(function_call=types.FunctionCall(id="start-1", name="start_sandbox", args={"sandbox": "box"}))
    duplicate = types.Part(function_call=types.FunctionCall(id="start-2", name="start_sandbox", args={"sandbox": "box"}))
    thought = types.Part(thought=True, thought_signature=b"signed", text="Start the available sandbox")

    class Fixture(BaseLlm):
        model: str = "openai/duplicate-fixture"

        async def generate_content_async(self, req, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[thought, first, duplicate]), finish_reason="STOP")

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    replies = [r async for r in model.generate_content_async(req)]
    parts = replies[0].content.parts
    assert parts == [thought, first] and e.actions.recoveries == 0
    calls = [p.function_call for p in parts if p.function_call]
    assert [c.id for c in calls] == ["start-1"]
    # No de-dup state leaks across requests: an actual failed start can be retried.
    later = [LlmResponse(content=types.Content(role="model", parts=[duplicate]))]
    e.actions.deduplicate_starts(e, "structure_builder", later)
    assert later[0].content.parts == [duplicate]


@pytest.mark.asyncio
async def test_coalescing_preserves_distinct_arguments_other_mutations_and_signed_calls():
    e = setup()
    await activate(e)
    def part(name, args, signature=None):
        return types.Part(thought_signature=signature, function_call=types.FunctionCall(name=name, args=args))
    parts = [part("start_sandbox", {"sandbox": "a"}), part("start_sandbox", {"sandbox": "b"}),
             part("start_sandbox", {"sandbox": "a", "include_shared_python": True}),
             part("execute_command", {"sandbox": "a", "argv": ["python3", "probe.py"]}),
             part("execute_command", {"sandbox": "a", "argv": ["python3", "probe.py"]}),
             part("start_sandbox", {"sandbox": "a"}, b"signed")]
    replies = [LlmResponse(content=types.Content(role="model", parts=parts))]
    e.actions.deduplicate_starts(e, "structure_builder", replies)
    assert replies[0].content.parts == parts


@pytest.mark.asyncio
async def test_successful_exit_does_not_automatically_promote_or_reexecute_inline_probe():
    e = setup()
    await activate(e)
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print('invalid count 96')"]},
               {"exit_code": 0, "stdout": "invalid count 96\n"}, call_id="inline")
    req = request()
    hooks = _model_callbacks(e.trace, "structure_builder", None, SimpleNamespace(types=types), evidence=e, output_limit=16384)
    assert await hooks["before_model_callback"](None, req) is None
    assert e.model_requests == 1 and not e.scripts and not e.workflow.checkpoints
    assert e.actions.inline_measurements[0]["stdout"] == "invalid count 96\n"
    assert e.workflow.advance(e, "probe", "box", "probe.py")["error"]["code"] == "execution_required"


@pytest.mark.asyncio
async def test_inline_error_and_missing_tool_never_trigger_host_writes():
    e = setup()
    await activate(e)
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "bad()"]},
               {"exit_code": 1, "stderr": "NameError"}, call_id="inline")
    req = request({"execute_command", "finish_task"})
    hooks = _model_callbacks(e.trace, "structure_builder", None, SimpleNamespace(types=types), evidence=e, output_limit=16384)
    assert await hooks["before_model_callback"](None, req) is None
    assert "write_sandbox_text_file" not in declarations(req)
    assert not e.scripts and not e.workflow.checkpoints
