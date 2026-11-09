"""General execution-cycle contracts; no generated code runs on the host."""
import asyncio
import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from google.genai import types
from google.adk.models.llm_request import LlmRequest
from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.actions import collect_script_operation
from oaw_atomsculptor.execution_steps import STEP_SCHEMA
from test_workflow import evidence, activate
from test_execution_actions import request, prepare


async def setup(*, save_error=False, exit_code=0, running=False, cancel_after_save=False, stdout="actual measurement", preparation_error=False):
    e = evidence()
    definitions = [ScopedToolDefinition("operation:" + name, "alias_" + name, "Native operation", input_schema={
        "properties": {"timeout_seconds": {"type": "number"}}} if name == "execute_command" else None)
        for name in ("write_sandbox_text_file", "execute_command", "wait_sandbox_operation")]
    e.configure_tools(definitions)
    await activate(e, delivery="report")
    e.steps.available = True
    stopped = asyncio.Event()
    calls = []
    files = {}

    async def native(capability, args):
        name = capability.removeprefix("operation:")
        calls.append((name, dict(args)))
        if name == "write_sandbox_text_file":
            if save_error:
                return {"ok": False, "error": "revision conflict"}
            if cancel_after_save:
                stopped.set()
            from backend.sandbox.text_edits import apply_text_edits
            from backend.errors import ResourceValidationError
            old = files.get(args["path"])
            if old is not None and not args.get("overwrite"):
                raise ResourceValidationError("Sandbox path already exists; explicit overwrite required")
            text = (apply_text_edits(old.encode(), args["edits"]).decode()
                    if args.get("edits") else args["content"])
            files[args["path"]] = text
            return {"written": len(text.encode())}
        if name == "execute_command" and running:
            return {"status": "running", "operation_id": "native-op"}
        if preparation_error:
            return {"ok": False, "operation_id": "native-op", "command_id": "native-op", "command_started": False,
                "error": {"type": "SandboxPreparationError", "code": "environment_preparation_failed",
                          "message": "dependency download timeout", "command_dispatched": False}}
        return {"exit_code": exit_code, "command_id": "native-op", "stdout": stdout() if callable(stdout) else stdout, "stderr": "failure" if exit_code else ""}

    async def invoke(capability, args):
        result = await native(capability, args)
        return await collect_script_operation(e, definitions, native, capability.removeprefix("operation:"), args, result, stopped)

    async def run(**overrides):
        return await e.steps.run(e, definitions, invoke, stopped, call_id="step-call", **{
            "sandbox": "box", "script_path": "step.py", "source": "print('measurement')",
            "question": "What is the next unknown input?", "expected_output": "Print the measured value",
            "stop_condition": "Stop after this measurement", **overrides})

    return e, calls, run


@pytest.mark.asyncio
async def test_queued_preparation_failure_retains_source_without_unknown_execution_or_scientific_review():
    e, calls, run = await setup(running=True, preparation_error=True)
    result = await run()
    assert result["step"]["state"] == "execution_not_started"
    assert result["error"]["command_dispatched"] is False and result["submitted_changes_saved"]
    assert e.script_sources[("box", "step.py")] == "print('measurement')"
    assert not e.pending and not e.executions and e.latest_experiment() is None
    assert e.sandbox_environments["box"]["dependencies"]["state"] == "failed"
    action, allowed = e.actions.next_action(e)
    assert "Infrastructure preparation failed" in action and "run_modelling_step" not in allowed
    assert not e.workflow.review_request


def prior(e, **extra):
    item = e.latest_experiment()
    return {"command_id": item.command_id, "sandbox": item.sandbox, "script_path": item.path,
            "hypothesis": "One measured input", "measurement": "actual measurement", "outcome": "continue",
            "next_step": "Measure the next fact", **extra}


@pytest.mark.asyncio
async def test_combined_assessment_and_cas_repair_uses_one_action_and_real_native_receipts():
    e, calls, run = await setup(exit_code=1)
    await run()
    digest = e.latest_experiment().version
    repaired = await run(source="", edits=[{"old": "measurement", "new": "fixed"}], 
                         previous_assessment=prior(e))
    assert repaired["step"]["state"] == "finished" and repaired["exit_code"] == 1
    assert len(calls) == 4 and "expected_sha256" not in calls[2][1]
    assert e.script_sources[("box", "step.py")] == "print('fixed')"
    assert e.experiment_history[0]["exit_code"] == 1
    assert not e.workflow.checkpoints and not e.outputs


@pytest.mark.asyncio
async def test_stale_receipts_are_rejected_but_model_continue_is_preserved():
    e, calls, run = await setup()
    await run()
    result = await run(script_path="later.py", previous_assessment=prior(e, command_id="stale"))
    assert result["error"]["code"] == "assessment_receipt" and len(calls) == 2
    assessment = e.workflow.assess_previous(e, prior(e, assumption_invalid=True))
    assert assessment["observation"]["outcome"] == "continue"
    blocked = e.workflow.advance(e, "probe", "box", "step.py")
    assert blocked["error"]["code"] == "candidate_contradiction"
    result = await run(script_path="later.py")
    assert result["ok"] and len(calls) == 4
    assert e.experiment_history[0]["assumption_invalid"] is True
    assert not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_combined_assessment_cannot_schedule_a_different_future_script_before_saving():
    from copy import deepcopy
    from test_subtask_lifecycle import ACTION
    e, calls, run = await setup()
    await run()
    item = e.latest_experiment()
    original = e.script_sources[("box", "step.py")]
    pending = deepcopy(e.workflow.pending_experiment)
    result = await run(source="", edits=[{"old": "measurement", "new": "fixed"}], 
                       previous_assessment=prior(e, next_experiment=ACTION))
    assert result["error"]["code"] == "step_assessment_target" and len(calls) == 2
    assert not result["error"]["command_dispatched"] and not result["error"]["submitted_changes_saved"]
    assert e.latest_experiment() is item and e.script_sources[("box", "step.py")] == original
    assert e.workflow.pending_experiment == pending and not e.experiment_observations
    corrected = await run(source="", edits=[{"old": "measurement", "new": "fixed"}], 
                          previous_assessment=prior(e, next_experiment={**ACTION, "artifact": "step.py"}))
    assert corrected["ok"] and len(calls) == 4 and not e.executions


@pytest.mark.asyncio
async def test_mismatched_assessment_recovery_reuses_original_patch_without_retransmitting_code():
    from test_typed_execution import native_sdk_fixture, COMMON
    from test_subtask_lifecycle import ACTION
    from test_target_recovery import correction
    e, files, calls, invoke = await native_sdk_fixture()
    await invoke("run_modelling_step", dict(COMMON, source="print(1)"))
    digest = e.latest_experiment().version
    refused = await invoke("patch_modelling_step", dict(COMMON, 
        edits=[{"old": "1", "new": "2"}], previous_assessment=prior(e, next_experiment=ACTION)))
    recovery = refused["error"]["recovery"]
    assert len(calls) == 2 and files["probe.py"] == "print(1)" and not e.experiment_observations
    result = await invoke("resume_modelling_step", {**correction(recovery, "probe.py"), "corrections": {
        "previous_assessment": prior(e, next_experiment={**ACTION, "artifact": "probe.py"})}})
    assert result["ok"] and result["target_correction"]["code_reused"]
    assert files["probe.py"] == "print(2)" and len(calls) == 4 and not e.executions


@pytest.mark.asyncio
async def test_finite_search_can_start_directly_and_saved_source_is_reused():
    e, calls, run = await setup()
    first = await run(execution_scope="search", argv=["--search"])
    assert first["ok"] and first["step"]["execution_scope"] == "search"
    assert len(calls) == 2 and calls[-1][1]["argv"] == ["python3", "step.py", "--search"]
    version = e.latest_experiment().version
    result = await run(source="", execution_scope="search", argv=["--larger-search"], previous_assessment=prior(e))
    assert result["ok"] and calls[-1][0] == "execute_command" and len(calls) == 3
    assert e.latest_experiment().version == version
    assert (await run(source="print('different')", execution_scope="search", argv=["--search"]))["error"]["code"] == "step_mode"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_prose_over_style_target_does_not_force_source_retransmission():
    e, calls, run = await setup()
    assert (await run(expected_output="x" * 434))["ok"]
    assert len(calls) == 2 and e.steps.records[-1]["expected_output"] == "x" * 434


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code,running", [(0, False), (1, False), (0, True)])
async def test_step_saves_executes_collects_native_receipts_once(exit_code, running):
    e, calls, run = await setup(exit_code=exit_code, running=running)
    result = await run()
    assert [name for name, _ in calls] == ["write_sandbox_text_file", "execute_command"] + (["wait_sandbox_operation"] if running else [])
    assert result["execution"]["stdout"] == "actual measurement"
    assert result["ok"] is (exit_code == 0)
    assert calls[1][1]["argv"] == ["python3", "step.py"]
    assert calls[1][1]["timeout_seconds"] == 120
    script = e.scripts[("box", "step.py")]
    assert script.phase == "executed" and script.exit_code == exit_code
    assert result["step"]['source_version'] == script.version
    assert not e.pending and not e.executions and not e.steps.active
    assert not e.outputs and not e.workflow.checkpoints  # Execution is not acceptance.
    second = await run(script_path="later.py")
    assert second["error"]["code"] == ("local_repair_required" if exit_code else "experiment_observation_required")
    if exit_code == 0:
        guidance = second["error"]["message"]
        assert "previous_assessment" in guidance and "advance_modelling_stage" in guidance
        assert "Omitted receipt fields" in guidance
    assert len(calls) == 2 + int(running)


@pytest.mark.asyncio
async def test_failed_save_never_dispatches_execute_and_manual_stop_keeps_saved_evidence():
    e, calls, run = await setup(save_error=True)
    assert (await run())["error"]["code"] == "step_save_failed"
    assert [name for name, _ in calls] == ["write_sandbox_text_file"]
    assert not e.steps.active and not e.scripts
    e, calls, run = await setup(cancel_after_save=True)
    with pytest.raises(asyncio.CancelledError):
        await run()
    assert [name for name, _ in calls] == ["write_sandbox_text_file"]
    assert e.scripts[("box", "step.py")].phase == "written"
    assert e.steps.records[-1]["state"] == "interrupted" and not e.steps.active


@pytest.mark.asyncio
async def test_assessment_selects_next_step_and_overwrite_keeps_native_compare_and_swap():
    e, calls, run = await setup()
    await run()
    digest = e.scripts[("box", "step.py")].version
    assert e.workflow.advance(e, "observe", "box", "step.py", hypothesis="Measure the input",
        measurement="actual measurement", outcome="continue", next_step="Measure one related input")["ok"]
    result = await run(source="print('related measurement')", question="What is the related input?", timeout_seconds=37, action_mode="rewrite")
    assert result["step"]["state"] == "finished"
    assert "expected_sha256" not in calls[2][1] and calls[2][1]["overwrite"] is True
    assert calls[3][1]["timeout_seconds"] == 37
    assert e.execution_sequence == 2 and e.scripts[("box", "step.py")].version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"question": ""}, {"question": "x" * 12001}, {"expected_output": ""},
    {"stop_condition": ""}, {"script_path": "../outside.py"}, {"source": ""}, {"timeout_seconds": 0}])
async def test_step_contract_rejects_invalid_work_before_native_operations(override):
    e, calls, run = await setup()
    assert (await run(**override))["ok"] is False
    assert not calls and not e.steps.records


@pytest.mark.asyncio
async def test_assessment_schema_matches_phase_and_diagnostics_identify_only_missing_field():
    e, calls, run = await setup()
    await run()
    req = request({"advance_modelling_stage", "run_modelling_step", "finish_task"})
    prepare(e, req)
    declaration = next(d for t in req.config.tools for d in t.function_declarations if d.name == "advance_modelling_stage")
    schema = declaration.parameters_json_schema
    assert schema["properties"]["stage"]["enum"] == ["observe"]
    assert set(schema["required"]) == {"stage", "hypothesis", "measurement", "outcome", "next_step"}
    step = next(d for t in req.config.tools for d in t.function_declarations if d.name == "run_modelling_step")
    assert "previous_assessment" in step.parameters_json_schema["required"]
    result = e.workflow.advance(e, "observe", "box", "step.py", hypothesis="Input", measurement="actual measurement", outcome="continue")
    assert [issue["field"] for issue in result["error"]["issues"]] == ["next_step"]


@pytest.mark.asyncio
async def test_combined_checkpoint_never_bypasses_independent_build_validation():
    e, calls, run = await setup()
    # Restore document delivery after setup's report-only capability fixture.
    e.workflow.delivery = "document"
    await run()
    await run(script_path="build.py", previous_assessment=prior(e, outcome="ready", complete_stage=True))
    assert e.workflow.current == "build" and len(calls) == 4
    result = await run(script_path="unvalidated.py", previous_assessment=prior(e,
        outcome="ready", complete_stage=True, structure_path="candidate.extxyz"))
    assert result["error"]["code"] == "validation_required"
    assert e.workflow.current == "build" and len(calls) == 4 and not e.outputs


@pytest.mark.asyncio
async def test_failed_saved_source_still_needs_explicit_repair_or_rerun():
    e, calls, run = await setup(exit_code=1)
    await run()
    result = await run(source="", execution_scope="search", previous_assessment=prior(e))
    assert result["error"]["code"] == "local_repair_required"
    assert len(calls) == 2
    repaired = await run(source="print('replacement')", action_mode="rewrite", previous_assessment=prior(e))
    assert repaired["step"]["action_mode"] == "rewrite" and len(calls) == 4
    assert e.script_sources[("box", "step.py")] == "print('replacement')"


@pytest.mark.asyncio
async def test_focused_context_preserves_request_and_acceptance_without_future_algorithm():
    e, calls, run = await setup()
    complete_plan = json.loads(json.dumps(e.workflow.plan))
    req = LlmRequest(contents=[], config=types.GenerateContentConfig(max_output_tokens=81920))
    e.before_model("structure_builder", req, NS(types=types), 81920)
    host = json.loads(req.contents[-1].parts[0].text.split("\n", 1)[1])
    view = host["modelling_workflow"]
    stable = [p.text for c in req.contents for p in c.parts if p.text and p.text.startswith("HOST STABLE CONTEXT")]
    stable_plan = json.loads(stable[0].split("\n", 1)[1])["modelling_plan_context"]
    assert json.loads(stable[0].split("\n", 1)[1])["original_user_request"] == e.original_request
    assert "original_user_request" not in host
    assert "plan" not in view
    assert "acceptance_checks" not in view
    requirements = {r["id"]: r for r in stable_plan["requirements"]}
    current = stable_plan["plan"][0]
    assert {k: v for k, v in current.items() if k != "check_ids"} == {k: v for k, v in complete_plan[0].items() if k not in {"checks", "experiment"}}
    actual_action = host["working_decision"]["current_subtask"]
    assert {k: actual_action[k] for k in complete_plan[0]["experiment"]} == complete_plan[0]["experiment"]
    assert [requirements[identity]["check"] for identity in current["check_ids"]] == complete_plan[0]["checks"]
    assert {stage: [requirements[identity]["check"] for identity in ids]
            for stage, ids in stable_plan["acceptance_checks"].items()} == {stage["id"]: stage["checks"] for stage in complete_plan}
    assert stable_plan["plan_revision"] == e.workflow.plan_revision
    assert e.workflow.snapshot()["plan"] == complete_plan
    assert req.config.max_output_tokens == 81920
    assert STEP_SCHEMA["required"] == ["sandbox", "script_path", "action_mode", "question", "expected_output", "stop_condition"]


@pytest.mark.asyncio
async def test_dependent_batch_keeps_call_ids_and_returns_individual_rejections():
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from oaw_atomsculptor.model_recovery import bounded_output_model
    e, calls, run = await setup()
    e.actions.sandbox_states["box"] = "ready"
    req = request({"run_modelling_step", "write_sandbox_text_file", "execute_command", "advance_modelling_stage", "finish_task"})
    prepare(e, req)
    turns = []
    class Fixture(BaseLlm):
        model: str = "openai/step-fixture"
        async def generate_content_async(self, request, stream=False):
            turns.append(request)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=
                types.FunctionCall(name=name, id=name, args={})) for name in ["write_sandbox_text_file", "execute_command"]]))
    accepted = [response async for response in bounded_output_model(Fixture(), e.trace, "structure_builder",
        e, NS(types=types), 81920).generate_content_async(req)]
    assert len(turns) == len(accepted) == 1
    hooks = e.tool_callbacks("structure_builder")
    for part in accepted[0].content.parts:
        call = part.function_call
        rejected = await hooks["before_tool_callback"](NS(name=call.name), call.args, NS(function_call_id=call.id))
        assert rejected["error"]["code"] == "non_readonly_tool_batch"
        assert rejected["error"]["call_id"] == call.id and rejected["error"]["command_dispatched"] is False
    assert not calls and not e.scripts and e.actions.recoveries == 0
    assert not e.actions.dependent_batch(e, [types.Part(function_call=types.FunctionCall(name="inspect_sandbox", args={})),
                                           types.Part(function_call=types.FunctionCall(name="inspect_atom_structure", args={}))])


@pytest.mark.asyncio
async def test_builder_handoff_replaces_expanded_advice_with_authoritative_current_stage():
    e, calls, run = await setup()
    part = types.Part(function_call=types.FunctionCall(name="structure_builder", args={
        "request": "Invented extra output, solve all later algorithms before executing"}))
    assert e.workflow.accept_delegations(e, "atom_sculptor", [part]) is None
    forwarded = part.function_call.args["request"]
    assert forwarded.startswith(e.original_request)
    assert "Invented extra output" not in forwarded and '"current_stage"' in forwarded
    host = json.loads(forwarded.split("HOST EXECUTION HANDOFF (not new user requirements):\n", 1)[1].split("\nExecute only", 1)[0])
    assert host == {"plan_revision": e.workflow.plan_revision, "current_stage": "probe"}


@pytest.mark.asyncio
async def test_signed_delegation_and_native_permissions_remain_authoritative():
    from backend.errors import PermissionDeniedError
    e, calls, run = await setup()
    part = types.Part(thought_signature=b"signed", function_call=types.FunctionCall(
        name="structure_builder", args={"request": "Signed delegation"}))
    e.workflow.accept_delegations(e, "atom_sculptor", [part])
    assert part.thought_signature == b"signed" and part.function_call.args["request"] == "Signed delegation"
    definitions = [ScopedToolDefinition("operation:" + name, name, "Native")
                   for name in ("write_sandbox_text_file", "execute_command")]
    e.configure_tools(definitions)
    denied_calls = []
    async def denied(capability, args):
        denied_calls.append(capability)
        raise PermissionDeniedError("live graph permission revoked")
    result = await e.steps.run(e, definitions, denied, asyncio.Event(), call_id="denied", sandbox="box",
        script_path="step.py", source="print(1)", question="Measure one input",
        expected_output="Print that input", stop_condition="Stop after the measurement")
    assert result["error"]["save"]["error"]["code"] == "permission_denied"
    assert denied_calls == ["operation:write_sandbox_text_file"]
    assert not e.steps.active and not e.scripts


@pytest.mark.asyncio
async def test_unsettled_native_work_prevents_another_step_and_unknown_dispatch_is_not_replayed():
    e, calls, run = await setup()
    e.pending["existing-op"] = [(('box', 'other.py'), 'a' * 64)]
    assert (await run())["error"]["code"] == "step_in_progress"
    assert not calls
    e.pending.clear()
    e.executions["unknown-dispatch"] = [(('box', 'other.py'), 'a' * 64)]
    action, allowed = e.actions.next_action(e)
    assert "no confirmed result" in action and "execute_command" not in allowed
    assert (await run())["error"]["code"] == "step_in_progress"
    assert not calls


@pytest.mark.asyncio
async def test_unconfirmed_execution_is_preserved_without_inventing_exit_or_replay():
    e, calls, run = await setup()
    definitions = [ScopedToolDefinition("operation:" + name, name, "Native")
                   for name in ("write_sandbox_text_file", "execute_command", "inspect_sandbox")]
    e.configure_tools(definitions)
    calls = []
    async def native(capability, args):
        calls.append(capability)
        if capability.endswith("write_sandbox_text_file"):
            return {"written": len(args["content"])}
        return {"error": "command outcome unavailable"}
    args = dict(call_id="step", sandbox="box", script_path="step.py", source="print(1)",
                question="Measure one input", expected_output="Print one value", stop_condition="Stop after the measurement")
    result = await e.steps.run(e, definitions, native, asyncio.Event(), **args)
    assert result["error"]["code"] == "step_execution_unconfirmed"
    assert result["exit_code"] is None and result["step"]["state"] == "execution_unknown"
    assert e.executions and e.scripts[("box", "step.py")].phase == "written"
    assert (await e.steps.run(e, definitions, native, asyncio.Event(), **args))["error"]["code"] == "step_in_progress"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_definite_execution_refusal_preserves_saved_source_without_an_unknown_command(monkeypatch):
    e, calls, run = await setup()
    original = e.workflow.before_tool
    def reject_execute(role, name, args, evidence):
        if name == "execute_command":
            return {"ok": False, "error": {"code": "fixture_execution_refused",
                "message": "Permission changed after saving", "command_dispatched": False}}
        return original(role, name, args, evidence)
    monkeypatch.setattr(e.workflow, "before_tool", reject_execute)
    result = await run()
    assert result["error"]["code"] == "fixture_execution_refused" and not result["error"]["command_dispatched"]
    assert result["step"]["state"] == "execution_not_started" and result["submitted_changes_saved"]
    assert e.script_sources[("box", "step.py")] == "print('measurement')" and len(calls) == 1
    assert not e.executions and e.execution_sequence == 0 and not e.workflow.checkpoints


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,failure", [("write_sandbox_text_file", "domain"),
    ("execute_command", "timeout"), ("execute_command", "unexpected"), ("execute_command", "child_cancel")])
async def test_composite_native_failures_return_feedback_without_replaying(operation, failure):
    from backend.errors import ResourceValidationError
    e, _, _ = await setup()
    definitions = [ScopedToolDefinition("operation:" + name, name, "native fixture")
                   for name in ("write_sandbox_text_file", "execute_command")]
    e.configure_tools(definitions)
    calls = []
    async def invoke(capability, args):
        name = capability.removeprefix("operation:")
        calls.append(name)
        if name == operation:
            if failure == "domain":
                raise ResourceValidationError("Destination already exists")
            if failure == "timeout":
                raise TimeoutError("receipt lost")
            if failure == "unexpected":
                raise RuntimeError("receipt lost")
            raise asyncio.CancelledError()
        return {"written": len(args["content"])}
    args = dict(call_id="native-fault", sandbox="box", script_path="new.py", source="print('fact')",
                question="One fact", expected_output="Fact", stop_condition="One case")
    result = await e.steps.run(e, definitions, invoke, asyncio.Event(), **args)
    assert not result["ok"] and not e.steps.active
    if operation == "write_sandbox_text_file":
        assert result["error"]["code"] == "step_save_failed" and calls == [operation]
        assert result["error"]["save"]["error"]["code"] == "invalid_resource"
        assert not e.executions
    else:
        assert result["error"]["code"] == "step_execution_unconfirmed"
        assert e.executions
        before = list(calls)
        again = await e.steps.run(e, definitions, invoke, asyncio.Event(), **args)
        assert again["error"]["code"] == "step_in_progress" and calls == before
    assert not e.outputs and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_composite_owner_cancellation_joins_native_operation():
    e, _, _ = await setup()
    definitions = [ScopedToolDefinition("operation:" + name, name, "native fixture")
                   for name in ("write_sandbox_text_file", "execute_command")]
    e.configure_tools(definitions)
    entered = asyncio.Event()
    joined = asyncio.Event()
    async def invoke(capability, args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            joined.set()
    task = asyncio.create_task(e.steps.run(e, definitions, invoke, asyncio.Event(), call_id="cancelled",
        sandbox="box", script_path="new.py", source="print('fact')", question="Fact",
        expected_output="Fact", stop_condition="One case"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert joined.is_set() and not e.steps.active
    assert e.steps.records[-1]["state"] == "interrupted"
