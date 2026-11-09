"""Receipt and recovery regressions, with local fixtures and no model API."""
import hashlib
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.genai import types

from oaw_atomsculptor.execution_steps import argument_error
from oaw_atomsculptor.harness import HarnessStopped
from test_execution_steps import setup, prior
from test_measurement_handoffs import record, execute, artifact
from test_workflow import PLAN, REQUEST, evidence, activate, tool


@pytest.mark.parametrize("path", ["/outside.py", "../outside.py", "scripts/../outside.py"])
def test_planned_experiment_cannot_bind_an_unusable_artifact_path(path):
    from oaw_atomsculptor.workflow import validate_plan
    plan = deepcopy(PLAN); plan["stages"][0]["experiment"]["artifact"] = path
    accepted, issues = validate_plan(plan)
    assert accepted is None and issues[0]["field"].endswith("experiment.artifact")


@pytest.mark.asyncio
async def test_successful_unchanged_rerun_reuses_receipt_without_new_science():
    e, calls, run = await setup()
    await run(argv=["--mode", "sample"])
    sequence = e.execution_sequence
    digest = e.latest_experiment().version
    result = await run(source="",  action_mode="rerun",
                       argv=["--mode", "sample"], previous_assessment=prior(e))
    assert result["ok"] and result["reused_execution"] and result["no_new_evidence"]
    assert len(calls) == 2 and e.execution_sequence == sequence and len(e.steps.records) == 1
    repeated = await run(source="",  action_mode="rerun",
                         argv=["--mode", "sample"], repeat_purpose="Measure stochastic run-to-run variation")
    assert repeated["ok"] and len(calls) == 3 and e.execution_sequence == sequence + 1


@pytest.mark.asyncio
async def test_scope_label_does_not_change_inputs_and_intentional_repeat_executes():
    e, calls, run = await setup()
    await run(argv=["--mode", "sample"])
    args = dict(source="",  action_mode="search",
                execution_scope="search", previous_assessment=prior(e))
    reused = await run(**args, argv=["--mode", "sample"])
    assert reused["reused_execution"] and reused["no_new_evidence"] and len(calls) == 2
    assert reused["execution_scope"] == "sample"
    searched = await run(**args, argv=["--mode", "full"])
    assert searched["ok"] and searched["step"]["native_argv"] == ["python3", "step.py", "--mode", "full"]
    result = await run(**{**args, "previous_assessment": prior(e)}, argv=["--mode", "full"])
    assert result["reused_execution"] and len(calls) == 3
    repeated = await run(**{**args, "previous_assessment": prior(e)}, argv=["--mode", "full"],
                         repeat_purpose="Check full-search reproducibility")
    assert repeated["ok"] and len(calls) == 4


def test_provider_rerun_contract_requires_scope_and_semantic_arguments():
    args = dict(sandbox="box", script_path="step.py", 
                question="one question", expected_output="one measure", stop_condition="one execution")
    assert argument_error("rerun_modelling_step", args)["error"]["fields"] == ["argv", "execution_scope"]
    assert argument_error("rerun_modelling_step", {**args, "argv": [], "execution_scope": "sample"}) is None


@pytest.mark.asyncio
async def test_ready_probe_advances_its_actual_typed_evidence_and_retains_it():
    e = evidence(); await activate(e, delivery="report")
    checks = [r["id"] for r in e.workflow.requirements() if r["stage"] == "probe"]
    await execute(e, "probe.py", record(kind="baseline", coverage={"inspected_directories": 1},
        values={"found_files": 0}, constraints=[{"name": "input_inventory", "passed": True,
        "regions": ["inspected_directories"], "check_ids": checks}]))
    result = e.workflow.assess_previous(e, prior(e, outcome="ready", complete_stage=True))
    assert result["checkpoint_status"] == "completed" and result["next_stage"] == "build"
    assert len(e.workflow.checkpoints) == 1
    old = deepcopy(e.workflow.checkpoints[0])
    await execute(e, "build.py", record(kind="diagnostic", values={"count": 3}))
    assert e.workflow.checkpoints[0] == old
    assert all(r["status"] == "reported_pass" for r in e.workflow.requirement_coverage(e) if r["stage"] == "probe")


@pytest.mark.asyncio
async def test_ready_does_not_complete_unknown_probe_checks_or_empty_populations():
    e = evidence(); await activate(e, delivery="report")
    await execute(e, "build_in_probe.py", record(kind="diagnostic", values={"build_count": 3}))
    result = e.workflow.assess_previous(e, prior(e, outcome="ready"))
    assert result["checkpoint_status"] == "awaiting_evidence" and result["next_stage"] == "probe"
    assert result["checkpoint_result"]["error"]["code"] == "requirement_evidence"
    assert not e.workflow.checkpoints
    checks = [r["id"] for r in e.workflow.requirements() if r["stage"] == "probe"]
    await execute(e, "empty.py", record(kind="baseline", coverage={"tested": 0},
        constraints=[{"name": "empty_mask", "passed": True, "regions": ["tested"], "check_ids": checks}]))
    assert e.workflow.assess_previous(e, prior(e, outcome="ready"))["checkpoint_result"]["error"]["code"] == "candidate_constraints"


@pytest.mark.asyncio
async def test_planner_revision_binds_next_experiment_without_stale_subtask():
    e, calls, run = await setup(stdout=record(kind="diagnostic", values={"rim_count": 8, "array": [1,2,3]},
        metadata={"execution_scope":"sample", "sample_count":36, "range":[0,1]}, artifacts=[artifact()]))
    await run(question="Old unknown")
    await tool(e, "finish_task", {"result": {"status": "needs_plan_review", "reason": "Measured method contradiction"}}, "Task completed.")
    changed = deepcopy(PLAN); changed["delivery"] = "report"
    changed["stages"][0]["experiment"].update(artifact="minimal.py", question="One new unknown", method="Measure one pair")
    await tool(e, "finish_task", {"result": changed}, "Task completed.", role="planner")
    decision = e.working_decision()
    assert decision["current_subtask"]["state"] == "execute_planned_step"
    assert decision["current_subtask"]["question"] == "One new unknown"
    assert decision["current_subtask"]["artifact"] == "minimal.py"
    assert decision["current_subtask"]["input_paths"] == ["candidate.xyz"]
    facts = decision["retained_facts"][0]["measurements"]
    assert facts["values"] == {"rim_count": 8} and facts["saved_data_keys"] == ["array"]
    assert facts["metadata"] == {"execution_scope":"sample", "sample_count":36, "range":[0,1]}
    message, allowed = e.actions.next_action(e)
    assert "minimal.py" in message and allowed is None
    # The normal phase projection keeps native operations available; a revised
    # script target must not create a special tool subset that drops them.
    from test_execution_actions import request, prepare, declarations
    req = request({'run_modelling_step', 'write_sandbox_text_file', 'execute_command', 'finish_task', 'advance_modelling_stage'})
    prepare(e, req)
    assert {'run_modelling_step', 'write_sandbox_text_file', 'execute_command'} <= declarations(req)
    wrong = await run(script_path="inventory_again.py", previous_assessment=prior(e))
    assert wrong["error"]["code"] == "planned_experiment_required" and len(calls) == 2
    await run(script_path="minimal.py", previous_assessment=prior(e))
    assert e.workflow.pending_experiment is None


@pytest.mark.asyncio
async def test_intentional_failed_repeat_is_dispatched_and_retains_real_failure():
    e, calls, run = await setup(exit_code=1)
    await run()
    result = await run(source="", action_mode="rerun", 
                       previous_assessment=prior(e), repeat_purpose="Check whether failure is intermittent")
    assert result["exit_code"] == 1 and result["step"]["state"] == "finished"
    assert len(calls) == 3 and e.execution_sequence == 2 and not e.outputs


@pytest.mark.asyncio
async def test_review_inputs_use_producer_receipts_and_manifest_names_do_not_make_new_experiments():
    from backend.agents.models import ScopedToolDefinition
    payload = b"measured input bytes"
    produced = artifact()
    e, _, first = await setup(stdout=record(kind="diagnostic", artifacts=[produced]))
    await first()
    await tool(e, "finish_task", {"result": {"status":"needs_plan_review", "reason":"Measured method contradicted"}}, "Task completed.")
    changed = deepcopy(PLAN); changed["delivery"] = "report"
    changed["stages"][0]["experiment"].update(artifact="minimal.py", expected_output="One measured pair", input_paths=[produced["path"]])
    assert await tool(e,"finish_task",{"result":changed},"Task completed.",role="planner") == "Task completed."
    assert e.working_decision()["current_subtask"]["expected_output"] == "One measured pair"
    denied = await first(script_path="minimal.py",previous_assessment=prior(e))
    assert denied["error"]["code"] == "planned_inputs_required"
    definitions = [ScopedToolDefinition("operation:"+n,n,"native") for n in ("write_sandbox_text_file","execute_command")]
    e.configure_tools(definitions)
    calls=[]; files={}
    async def native(capability,args):
        calls.append(capability)
        if capability.endswith("write_sandbox_text_file"):
            files[args["path"]]=args["content"]
            return {"written":len(args["content"].encode())}
        if args["argv"][1].startswith(".oaw-input-check-"):
            return {"exit_code":0,"command_id":"guard","stdout":json.dumps({"oaw_inputs_verified":json.loads(args["argv"][2])})}
        return {"exit_code":0,"command_id":"measured-pair","stdout":record(kind="diagnostic",values={"pair_count":1})}
    values=dict(sandbox="box",script_path="minimal.py",question="Measure one pair",expected_output="One pair",stop_condition="One case",
                input_artifacts=[{"path":produced["path"]}],input_manifest_path="first-manifest.json")
    result=await e.steps.run(e,definitions,native,asyncio.Event(),call_id="first",source="print(1)",**values)
    assert result["ok"] and e.workflow.pending_experiment is None and len(calls)==5
    before=e.execution_sequence
    reused=await e.steps.run(e,definitions,native,asyncio.Event(),call_id="repeated",source="",action_mode="rerun",
        previous_assessment=prior(e),
        **{**values,"input_manifest_path":"another-manifest.json"})
    assert reused["reused_execution"] and e.execution_sequence==before and len(calls)==5
    assert "another-manifest.json" not in files


@pytest.mark.asyncio
async def test_review_rejects_unknown_input_receipt_before_accepting_plan():
    e, _, run = await setup(); await run()
    await tool(e,"finish_task",{"result":{"status":"needs_plan_review","reason":"Measured contradiction"}},"Task completed.")
    changed=deepcopy(PLAN); changed["delivery"]="report"
    changed["stages"][0]["experiment"]["input_paths"]=["invented.npz"]
    result=await tool(e,"finish_task",{"result":changed},"Task completed.",role="planner")
    assert result["error"]["issues"][0]["field"].endswith("experiment.input_paths")
    assert e.workflow.plan_revision==1 and e.workflow.review_request is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker_role", ["planner", "structure_builder"])
async def test_normal_blocked_adk_closeout_persists_native_checkpoint(tmp_path, blocker_role):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from backend.agents.models import ScopedToolDefinition
    from backend.persistence.database import Database
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    from backend.state import StateStore
    from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
    from oaw_atomsculptor.runtime import AtomSculptorRuntime
    calls = []
    class Model(BaseLlm):
        model: str = "openai/blocked-fixture"
        async def generate_content_async(self, request, stream=False):
            host = next(json.loads(p.text.split("\n",1)[1]) for c in reversed(request.contents)
                        for p in c.parts if p.text and p.text.startswith("HOST RUN EVIDENCE"))
            role = host["execution_role"]; workflow = host["modelling_workflow"]
            if role == "planner":
                result = {"status": "blocked", "limitations": ["Missing required input"]} if blocker_role == "planner" else {**PLAN, "delivery": "report"}
                part = types.Part(function_call=types.FunctionCall(name="finish_task", args={"result": result}))
            elif role == "structure_builder":
                if not host["scripts"]:
                    part = types.Part(function_call=types.FunctionCall(name="run_modelling_step", args={
                        "sandbox":"box", "script_path":"saved.py", "source":"print(1)",
                        "question":"One input", "expected_output":"Measured count", "stop_condition":"One execution"}))
                else:
                    part = types.Part(function_call=types.FunctionCall(name="finish_task", args={"result":{
                        "status":"blocked", "limitations":["Candidate not finished"]}}))
            elif workflow["blocked"]:
                part = types.Part(text="Goal remains unfinished; saved artifacts retained.")
            else:
                part = types.Part(function_call=types.FunctionCall(name="planner" if workflow["plan"] is None else "structure_builder", args={"request": REQUEST}))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
    class Provider:
        async def list_tools(self, _):
            return [ScopedToolDefinition("operation:"+n, n, "native") for n in ("write_sandbox_text_file", "execute_command")]
        async def invoke_tool(self, _, capability, args):
            calls.append(capability)
            if capability.endswith("write_sandbox_text_file"):
                return {"written":len(args["content"])}
            return {"exit_code":0, "command_id":"saved-command", "stdout":"measured count"}
    db = Database(tmp_path/"world.sqlite3")
    try:
        RunStore(db).create(run_id="blocked-run", agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
        state = StateStore(db, create_builtin_registry())
        runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=NS(
            resolve_runtime=lambda _: RuntimeModelConnection("openai","blocked-fixture","https://model.invalid",None),
            context_limits=lambda _: (128000,81920)))
        runtime.reasoning_store = state; runtime._model = lambda *_: Model()
        config = AgentConfig("fixture","Fixture"); await runtime.create_agent(config)
        events = [e async for e in runtime.execute(config,NS(run_id="blocked-run",context_id="fixture"),NS(prompt=REQUEST,task_prompt=REQUEST))]
        assert events[-1].run_status == "failed" and events[-1].payload["text"].startswith("Goal remains unfinished")
        checkpoint = state.get(state.get_scope("run","blocked-run"),"execution_checkpoint")
        assert checkpoint["status"] == "unfinished" and checkpoint["plan"]["blocked"]
        if blocker_role == "structure_builder":
            assert checkpoint["scripts"][0]["command_id"] == "saved-command"
            assert len(calls) == 3  # Native support module write, source write, execution.
        else:
            assert not calls
    finally:
        db.close()
