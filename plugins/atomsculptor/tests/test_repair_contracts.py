"""Execution intent, native recovery and delivery boundaries; no paid model calls."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest
from google.adk.tools import FunctionTool
from google.genai import types
from oaw_atomsculptor.delivery import delivery_step_function, run_delivery_step
from oaw_atomsculptor.runtime import _trim_specialist_history, _ModelTrace
from test_execution_steps import setup, prior
from test_workflow import tool, PLAN
from test_optimization_paths import delivery_setup


@pytest.mark.asyncio
async def test_missing_patch_and_unchanged_failure_never_start_native_work():
    e, calls, run = await setup(exit_code=1)
    await run()
    args = dict(source="",  previous_assessment=prior(e))
    for _ in range(2):
        assert (await run(**args))["error"]["code"] == "step_patch_required"
    assert (await run(**args, action_mode="rerun"))["error"]["code"] == "step_unchanged_failure"
    assert len(calls) == 2 and not e.experiment_history
    patched = await run(**args, action_mode="patch", edits=[{"old": "measurement", "new": "fixed"}])
    assert patched["step"]["bytes_changed"] and len(calls) == 4


@pytest.mark.asyncio
async def test_patch_that_cancels_itself_is_not_new_execution():
    e, calls, run = await setup()
    await run()
    result = await run(source="",  action_mode="patch",
        previous_assessment=prior(e), edits=[{"old": "measurement", "new": "changed"}, {"old": "changed", "new": "measurement"}])
    assert result["error"]["code"] == "step_no_change" and len(calls) == 2
    assert e.latest_experiment().phase == "executed"


@pytest.mark.asyncio
async def test_explicit_changed_arguments_allow_a_legitimate_failed_experiment_rerun():
    e, calls, run = await setup(exit_code=1)
    await run(argv=["--seed=1"])
    result = await run(source="",  action_mode="rerun",
                       previous_assessment=prior(e), argv=["--seed=2"])
    assert result["step"]["state"] == "finished" and not result["step"]["bytes_changed"]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_repeated_execution_cause_stays_in_local_repair_without_false_planner_review():
    e, calls, run = await setup(exit_code=1)
    await run()
    for replacement in ["second"]:
        await run(source="",  action_mode="patch",
                  previous_assessment=prior(e), edits=[{"old": e.script_sources[("box", "step.py")], "new": f"print('{replacement}')"}])
    assert e.block_reason is None and e.workflow.host_signal is None
    assert e.workflow.local_repair(e)['script_path'] == 'step.py'
    assert e.execution_sequence == 2 and len(e.experiment_history) == 1
    result = await tool(e, "finish_task", {"result": {"status": "needs_plan_review", "reason": "Same native code failure after repair",
                "sandbox": "box", "script_path": "step.py"}}, "Task completed.")
    assert result == 'Task completed.' and e.workflow.review_request['exit_code'] == 1
    assert e.workflow.plan_revision == 1 and e.workflow.host_signal is None


@pytest.mark.asyncio
async def test_unmeasured_zero_does_not_create_a_stagnation_objective():
    e, calls, run = await setup()
    for i in range(4):
        await run(script_path=f"metric{i}.py")
        result = e.workflow.assess_previous(e, prior(e, objective={"name": "score", "value": 0, "direction": "minimize"}))
        assert result["observation"]["objective_status"] == "unavailable_or_mismatch"
        assert "objective" not in result["observation"] and result["observation"]["outcome"] == "continue"


def test_thought_limits_apply_to_merged_parts_and_turns_without_touching_signatures():
    from test_workflow import evidence
    e = evidence()
    for sizes in [[20000], [100] * 8]:
        contents = [types.Content(role="model", parts=[types.Part(text="x" * size, thought=True)]) for size in sizes]
        signed = types.Part(text="signed", thought=True, thought_signature=b"signature")
        contents[0].parts.append(signed)
        req = NS(contents=contents)
        prefix = [c.model_dump_json(exclude_none=True) for c in contents]
        _trim_specialist_history(NS(types=types), _ModelTrace(asyncio.Queue()), "structure_builder", req, e)
        assert [c.model_dump_json(exclude_none=True) for c in req.contents] == prefix
        contents[0].parts.remove(signed)
        _trim_specialist_history(NS(types=types), _ModelTrace(asyncio.Queue()), "structure_builder", req, e)
        assert sum(len(p.text) for c in req.contents for p in c.parts if p.thought and not p.thought_signature) <= 4096
        assert sum(any(p.thought and not p.thought_signature for p in c.parts) for c in req.contents) <= 2
        assert not any(p.thought_signature for c in req.contents for p in c.parts)


@pytest.mark.asyncio
async def test_delivery_sdk_name_and_reused_validation_receipt_are_real():
    e, defs, invoke, stopped, calls = await delivery_setup()
    sdk = FunctionTool(delivery_step_function(e, defs, invoke, stopped))
    assert sdk.name == sdk._get_declaration().name == "run_delivery_step"
    result = await sdk.run_async(args={"sandbox": "box", "skill": "skill-node", "structure_path": "cnt.extxyz", "target": "structure"},
                                tool_context=NS(function_call_id="delivery-sdk"))
    assert result["ok"] and result["steps"][0]["receipt_reused"]
    assert sum(name == "run_skill_script" for name, _ in calls) == 1
    assert e.workflow.current == "complete" and e.confirmed["structure"] == 2


@pytest.mark.asyncio
async def test_delivery_running_without_waiter_retains_pending_and_never_replays():
    e, defs, _, stopped, calls = await delivery_setup()
    defs = [d for d in defs if d.name != "wait_sandbox_operation"]
    dispatched = []
    async def invoke(capability, args):
        dispatched.append(capability)
        return {"status": "running", "operation_id": "validate-pending"}
    for _ in range(2):
        result = await run_delivery_step(e, defs, invoke, stopped, call_id="delivery-pending", sandbox="box",
                                        skill="skill-node", structure_path="cnt.extxyz", target="structure", validate={})
        assert not result["ok"]
    assert len(dispatched) == 1 and "validate-pending" in e.skill_operations


@pytest.mark.asyncio
async def test_delivery_unknown_import_is_not_replayed():
    e, defs, native, stopped, calls = await delivery_setup()
    async def invoke(capability, args):
        return {} if capability.endswith("import_atom_structure_file") else await native(capability, args)
    args = dict(call_id="unknown-import", sandbox="box", skill="skill-node", structure_path="cnt.extxyz", target="structure")
    result = await run_delivery_step(e, defs, invoke, stopped, **args)
    assert result["error"]["code"] == "delivery_import_unknown"
    count = len(calls)
    assert (await run_delivery_step(e, defs, invoke, stopped, **args))["error"]["code"] == "delivery_import_unknown"
    assert len(calls) == count and not e.confirmed


@pytest.mark.asyncio
async def test_actual_adk_repairs_arguments_then_unchanged_failures_return_to_planner_and_complete():
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from backend.agents.models import ScopedToolDefinition
    from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
    from oaw_atomsculptor.runtime import AtomSculptorRuntime
    from test_workflow import REQUEST
    planner_calls = commands = 0
    source_omitted = False
    hashes = []
    files = {}

    class Fixture(BaseLlm):
        model: str = "openai/recovery-fixture"
        async def generate_content_async(self, request, stream=False):
            nonlocal planner_calls, source_omitted
            host = next(json.loads(p.text.split("\n", 1)[1]) for c in reversed(request.contents) for p in c.parts
                        if p.text and p.text.startswith("HOST RUN EVIDENCE"))
            workflow, role = host["modelling_workflow"], host["execution_role"]
            if role == "planner":
                planner_calls += 1
                name, args = "finish_task", {"result": {**PLAN, "delivery": "report", "assumptions": ["Verify inferred methods in executed code"]}}
                if planner_calls == 1:
                    args["assumptions"] = ["Wrong outer field; must be inside result"]
            elif role == "atom_sculptor":
                if workflow["current_stage"] == "complete":
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Measured workflow completed.")]))
                    return
                name = "planner" if workflow["plan"] is None or workflow["review_request"] else "structure_builder"
                args = {"request": REQUEST}
            elif workflow["host_execution_signal"]:
                name, args = "finish_task", {"result": {"status": "needs_plan_review", "reason": "Same native failure after repair",
                    "sandbox": "box", "script_path": "probe.py"}}
            elif workflow["current_stage"] == "deliver":
                name, args = "advance_modelling_stage", {"stage": "deliver"}
            elif workflow["current_stage"] == "complete":
                name, args = "finish_task", {"result": {"status": "succeeded"}}
            else:
                path = "probe.py" if workflow["current_stage"] == "probe" else "build.py"
                latest = host["scripts"][-1] if host["scripts"] else None
                if latest and latest["path"] == path and latest["exit_code"] == 0:
                    assessed = host["experiment_observations"] and host["experiment_observations"][-1]["execution_sequence"] == latest["execution_sequence"]
                    name, args = "advance_modelling_stage", ({"stage": workflow["current_stage"], "sandbox": "box", "script_path": path} if assessed else
                        {"stage": "observe", "hypothesis": "Measure one value", "measurement": "native measured value", "outcome": "ready", "next_step": "Advance checkpoint"})
                else:
                    name, args = "run_modelling_step", {"sandbox": "box", "script_path": path,
                        "question": "Measure one value", "expected_output": "Print the measured value", "stop_condition": "One execution"}
                    if path not in files:
                        args.update(action_mode="new", source="print(1)")
                        if not source_omitted:
                            # Real failure pattern: code in thoughts is not an
                            # executable payload. The next request corrects it.
                            source_omitted = True
                            args.pop("source")
                    else:
                        name = "patch_modelling_step"
                        args.update(edits=[{"old": files[path], "new": f"print({commands + 1})"}],
                             previous_assessment={"hypothesis": "Repair actual error", "measurement": "exit 1: code failure",
                            "outcome": "continue", "next_step": "One bounded repair"})
            parts = [types.Part(function_call=types.FunctionCall(name=name, args=args))]
            if name == "run_modelling_step" and "source" not in args:
                parts.insert(0, types.Part(text="Code I intend to run:\n```python\nprint(1)\n```", thought=True))
            yield LlmResponse(content=types.Content(role="model", parts=parts))

    class Provider:
        async def list_tools(self, agent_id):
            return [ScopedToolDefinition("operation:" + n, n, "Native fixture") for n in ("write_sandbox_text_file", "execute_command")]
        async def invoke_tool(self, agent_id, capability, args):
            nonlocal commands
            import hashlib
            from backend.sandbox.text_edits import apply_text_edits
            if capability.endswith("write_sandbox_text_file"):
                path = args["path"]
                files[path] = apply_text_edits(files[path].encode(), args["edits"]).decode() if args.get("edits") else args["content"]
                digest = hashlib.sha256(files[path].encode()).hexdigest()
                hashes.append(digest)
                return {"written": len(files[path])}
            commands += 1
            return {"exit_code": 1 if commands <= 3 else 0, "command_id": f"cmd-{commands}", "stdout": "native measured value", "stderr": "code failure" if commands <= 3 else ""}

    runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=NS(
        resolve_runtime=lambda _: RuntimeModelConnection("openai", "recovery-fixture", "https://model.invalid", None),
        context_limits=lambda _: (128000, 81920)))
    runtime._model = lambda *_: Fixture()
    config = AgentConfig("fixture", "Fixture")
    await runtime.create_agent(config)
    events = [e async for e in runtime.execute(config, NS(run_id="recovery-run", context_id="fixture"), NS(prompt=REQUEST, task_prompt=REQUEST))]
    assert events[-1].run_status == "succeeded", [e.payload for e in events if e.type.value == "tool_completed"][-8:]
    assert planner_calls == 2 and commands == 5 and len(set(hashes)) >= 4
    assert any(e.payload.get("workflow_event") == "modelling_argument_error" for e in events)
    assert not any(e.payload.get("workflow_event") == "execution_review_signal" for e in events)
    assert not runtime.active
