"""Correct target contracts without regenerating code or replaying native work."""
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import _get_completion_inputs
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from jsonschema import Draft202012Validator

from oaw_atomsculptor.workflow import script_target, validate_plan
from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
from oaw_atomsculptor.runtime import AtomSculptorRuntime
from test_execution_actions import prepare, request
from test_execution_steps import setup, prior
from test_subtask_lifecycle import ACTION, MEASURED
from test_typed_execution import native_sdk_fixture
from test_workflow import PLAN, REQUEST, tool


@pytest.mark.parametrize("path", ["probe.py (saved) + new hole_step.py", "reuse input -> next.py",
                                 "../next.py", "/next.py", "next.py\n", "scripts//next.py"])
def test_composite_or_nonliteral_target_is_rejected_at_plan_boundary(path):
    proposed = deepcopy(PLAN)
    proposed["stages"][0]["experiment"]["artifact"] = path
    parsed, issues = validate_plan(proposed)
    assert parsed is None and any(i["field"].endswith("experiment.artifact") for i in issues)


@pytest.mark.parametrize("path", ["measurements/next step.py", "测量/下一步.py", "cafe\u0301 test.py", "a+b/step-v2.0.py"])
def test_literal_targets_preserve_spaces_unicode_and_folder_names(path):
    assert script_target(path)


@pytest.mark.asyncio
async def test_bad_next_target_is_rejected_atomically_and_retry_is_contract_only():
    e, _, run = await setup(stdout=MEASURED)
    await run()
    pending = deepcopy(e.workflow.pending_experiment)
    args = {"stage": "observe", **{k: v for k, v in prior(e).items() if k != "command_id"},
            "next_experiment": {**ACTION, "artifact": "old.py (saved) + new next.py"}}
    hooks = e.tool_callbacks("structure_builder")
    result = e.workflow.advance(e, **args)
    await hooks["after_tool_callback"](NS(name="advance_modelling_stage"), args, NS(function_call_id="observe"), result)
    assert result["error"]["code"] == "subtask_contract"
    assert "experiment.artifact" in result["error"]["message"]
    assert not e.experiment_observations and e.workflow.pending_experiment == pending
    text, allowed = e.actions.next_action(e)
    assert "advance_modelling_stage" in allowed and "run_modelling_step" not in allowed
    assert "Do not generate source" in text
    args["next_experiment"] = deepcopy(ACTION)
    assert e.workflow.advance(e, **args)["ok"]
    assert e.workflow.pending_experiment["artifact"] == "next.py"


async def rejected_action(*, origin="assessment"):
    e, files, calls, invoke = await native_sdk_fixture()
    e.workflow.schedule_experiment(ACTION, origin=origin)
    args = dict(sandbox="box", script_path="wrong.py", source="print('retained code')",
                question=ACTION["question"], expected_output=ACTION["expected_output"], stop_condition=ACTION["stop_condition"])
    result = await invoke("run_modelling_step", args)
    assert result["error"]["code"] == "planned_experiment_required" and not calls and not files
    recovery = e.steps.target_recovery(e)
    assert result["error"]["recovery"]["subtask_id"] == recovery["subtask_id"]
    return e, files, calls, invoke, args, recovery


def correction(recovery, path="next.py"):
    return {"subtask_id": recovery["subtask_id"], 
            "script_path": path, "reason": "Correct only the unexecuted target filename"}


@pytest.mark.asyncio
async def test_sdk_retains_first_rejection_then_resumes_exact_code_once():
    e, files, calls, invoke, args, recovery = await rejected_action()
    original_plan = deepcopy(e.workflow.plan)
    original_contract = deepcopy(e.workflow.pending_experiment)
    await invoke("run_modelling_step", {**args, "source": "print('a guessed replacement')"})
    assert e.steps.target_recovery(e) == recovery and not calls
    assert args["source"] not in json.dumps(e.steps.snapshot())
    result = await invoke("resume_modelling_step", correction(recovery))
    assert result["ok"] and result["target_correction"]["code_reused"]
    assert files["next.py"] == args["source"]
    assert len(calls) == 2 and calls[1][1]["argv"][-1] == "next.py"
    assert result["step"]["subtask_id"] == original_contract["subtask_id"]
    assert e.workflow.plan == original_plan and not e.workflow.checkpoints
    again = await invoke("resume_modelling_step", correction(recovery))
    assert again["error"]["code"] == "target_recovery_stale" and len(calls) == 2


@pytest.mark.asyncio
async def test_explicit_local_target_correction_preserves_question_identity_and_receipts():
    e, files, _, invoke, args, recovery = await rejected_action()
    before = deepcopy(e.workflow.pending_experiment)
    result = await invoke("resume_modelling_step", correction(recovery, "measurements/下一步 test.py"))
    assert result["ok"] and files["measurements/下一步 test.py"] == args["source"]
    assert result["step"]["subtask_id"] == before["subtask_id"] and not e.workflow.checkpoints
    assert result["target_correction"]["expected_script_path"] == "next.py"


@pytest.mark.asyncio
async def test_planner_review_target_cannot_be_renamed_through_local_recovery():
    e, _, calls, invoke, _, recovery = await rejected_action(origin="planner_review")
    refused = await invoke("resume_modelling_step", correction(recovery, "replacement.py"))
    assert refused["error"]["code"] == "target_recovery_plan" and not calls
    assert e.workflow.pending_experiment["artifact"] == "next.py"
    assert (await invoke("resume_modelling_step", correction(recovery)))["ok"]


@pytest.mark.asyncio
async def test_target_recovery_preserves_native_cas_patch_and_prior_measurement():
    e, files, calls, invoke = await native_sdk_fixture()
    common = dict(sandbox="box", script_path="original.py", question=ACTION["question"],
                  expected_output=ACTION["expected_output"], stop_condition=ACTION["stop_condition"])
    assert (await invoke("run_modelling_step", {**common, "source": "print('old')"}))["ok"]
    assert e.workflow.assess_previous(e, prior(e))["ok"]
    observation = deepcopy(e.experiment_observations)
    e.workflow.schedule_experiment(ACTION, origin="assessment")
    refused = await invoke("patch_modelling_step", {**common, "edits": [{"old": "old", "new": "new"}]})
    assert refused["error"]["code"] == "planned_experiment_required" and len(calls) == 2
    assert (await invoke("resume_modelling_step", correction(e.steps.target_recovery(e), "original.py")))["ok"]
    assert files["original.py"] == "print('new')" and len(calls) == 4
    assert calls[2][1]["edits"] == [{"old": "old", "new": "new"}] and "expected_sha256" not in calls[2][1]
    assert e.experiment_observations == observation and not e.workflow.checkpoints


@pytest.mark.asyncio
async def test_cancellation_prevents_target_recovery_without_consuming_the_action():
    import asyncio
    from open_agent_world.plugin_api import AgentRuntimeError
    e, _, calls, _, _, recovery = await rejected_action()
    stopped = asyncio.Event(); stopped.set()
    with pytest.raises(AgentRuntimeError, match="stopped"):
        await e.steps.resume(e, [], None, stopped, call_id="cancelled", **correction(recovery))
    assert not calls and e.steps.target_recovery(e) == recovery


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["id", "revision", "stage", "contract", "unknown_command"])
async def test_stale_or_uncertain_target_recovery_dispatches_nothing(change):
    e, files, calls, invoke, _, recovery = await rejected_action()
    values = correction(recovery)
    if change == "id": values["subtask_id"] = "old-subtask"
    if change == "revision": e.workflow.plan_revision += 1
    if change == "stage": e.workflow.index += 1
    if change == "contract": e.workflow.pending_experiment["method"] = "Changed scientific method"
    if change == "unknown_command": e.executions["unknown"] = []
    result = await invoke("resume_modelling_step", values)
    assert result["ok"] is False and result["error"]["command_dispatched"] is False
    assert not files and not calls


@pytest.mark.asyncio
async def test_recovery_request_exposes_only_small_corrective_tool_on_actual_wire():
    e, _, _, _, _, recovery = await rejected_action()
    req = request({"resume_modelling_step", "run_modelling_step", "patch_modelling_step", "rewrite_modelling_step",
                   "rerun_modelling_step", "finish_task", "advance_modelling_stage"})
    prepare(e, req)
    _, tools, _, _, choice = await _get_completion_inputs(req, req.model)
    assert choice == "required"
    assert {t["function"]["name"] for t in tools} == {"resume_modelling_step", "finish_task"}
    schema = next(t["function"]["parameters"] for t in tools if t["function"]["name"] == "resume_modelling_step")
    Draft202012Validator.check_schema(schema)
    assert set(schema["properties"]) == {"subtask_id", "script_path", "reason", "corrections"}
    assert Draft202012Validator(schema).is_valid(correction(recovery))
    assert Draft202012Validator(schema).is_valid({**correction(recovery), 'corrections': {'input_manifest_path': None}})
    assert schema["properties"]["subtask_id"]["enum"] == [recovery["subtask_id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_argv', [["--oaw-input-manifest", "wrong.json"], 'wrong type'])
async def test_execution_argument_repair_reuses_source_without_native_replay(bad_argv):
    e, files, calls, invoke = await native_sdk_fixture()
    e.workflow.schedule_experiment(ACTION, origin="assessment")
    source = "print('one retained program')"
    args = dict(sandbox="box", script_path="next.py", source=source, question=ACTION["question"],
        expected_output=ACTION["expected_output"], stop_condition=ACTION["stop_condition"],
        argv=bad_argv)
    denied = await invoke("run_modelling_step", args)
    assert denied["error"]["code"] == "step_arguments" and not calls
    recovery = e.steps.target_recovery(e)
    assert recovery and e.steps._target_rejection["arguments"]["source"] == source
    immutable = await invoke("resume_modelling_step", {**correction(recovery), "corrections": {"source": "changed"}})
    assert immutable["error"]["code"] == "target_recovery_fields" and not calls
    result = await invoke("resume_modelling_step", {**correction(recovery), "corrections": {"argv": []}})
    assert result["ok"] and result["target_correction"]["code_reused"]
    assert files["next.py"] == source and len(calls) == 2
    assert e.steps.argument_issue is None and e.steps.argument_failures == 0
    stale = await invoke("resume_modelling_step", correction(recovery))
    assert stale["error"]["code"] == "target_recovery_stale" and len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing_path', 'unexpected_manifest'])
async def test_missing_target_or_optional_field_can_be_corrected_without_resubmitting_source(case):
    e, files, calls, invoke = await native_sdk_fixture()
    e.workflow.schedule_experiment(ACTION, origin='assessment')
    args = dict(sandbox='box', source="print('retained')", question=ACTION['question'],
        expected_output=ACTION['expected_output'], stop_condition=ACTION['stop_condition'])
    if case == 'unexpected_manifest':
        args.update(script_path='next.py', input_manifest_path='unneeded.json')
    rejected = await invoke('run_modelling_step', args)
    assert not calls and rejected['error']['command_dispatched'] is False
    recovery = e.steps.target_recovery(e)
    assert recovery and e.steps.snapshot()['target_recovery']
    changes = {'input_manifest_path': None} if case == 'unexpected_manifest' else {}
    result = await invoke('resume_modelling_step', {**correction(recovery), 'corrections': changes})
    assert result['ok'] and len(calls) == 2 and files['next.py'] == args['source']


@pytest.mark.asyncio
async def test_real_adk_repairs_bad_contract_and_target_then_runs_without_resending_code():
    root_turns = builder_turns = 0
    native_calls, scripts = [], {}
    source = "print('one unchanged modelling program')"
    class Fixture(BaseLlm):
        model: str = "openai/target-recovery-fixture"
        async def generate_content_async(self, request, stream=False):
            nonlocal root_turns, builder_turns
            hosts = [json.loads(p.text.split("\n", 1)[1]) for c in request.contents for p in c.parts
                     if p.text and p.text.startswith("HOST RUN EVIDENCE")]
            host = hosts[-1]
            role = host["execution_role"]
            if role == "atom_sculptor":
                root_turns += 1
                if root_turns == 1: name, args = "planner", {"request": REQUEST}
                elif root_turns == 2: name, args = "structure_builder", {"request": REQUEST}
                else:
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Further modelling checks remain unmet.")]))
                    return
            elif role == "planner": name, args = "finish_task", {"result": PLAN}
            else:
                builder_turns += 1
                if builder_turns == 1:
                    name, args = "run_modelling_step", dict(sandbox="box", script_path="probe.py", source=source,
                        question="Measure one input", expected_output="A measured count", stop_condition="One execution")
                elif builder_turns in {2, 3}:
                    action = {**ACTION, "artifact": "probe.py (saved) + new next.py"} if builder_turns == 2 else ACTION
                    name, args = "advance_modelling_stage", dict(stage="observe", hypothesis="One measured input",
                        measurement="count=4", outcome="continue", next_step="Measure the next input", next_experiment=action)
                    if builder_turns == 3:
                        advertised = {d.name for t in request.config.tools for d in t.function_declarations}
                        assert "run_modelling_step" not in advertised
                elif builder_turns == 4:
                    name, args = "run_modelling_step", dict(sandbox="box", script_path="wrong.py", source=source,
                        question=ACTION["question"], expected_output=ACTION["expected_output"], stop_condition=ACTION["stop_condition"])
                elif builder_turns == 5:
                    correction_host = host["execution_steps"]["target_recovery"]
                    name, args = "resume_modelling_step", correction(correction_host)
                    _, wire, _, _, _ = await _get_completion_inputs(request, "openai/fixture")
                    for declaration in wire: Draft202012Validator.check_schema(declaration["function"]["parameters"])
                else: name, args = "finish_task", {"result": {"status": "blocked", "reason": "Stop fixture after target recovery"}}
                advertised = {d.name for t in request.config.tools for d in t.function_declarations}
                assert name in advertised, (builder_turns, name, advertised, host.get("correction_feedback"))
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                id=f"{role}-{builder_turns}-{root_turns}", name=name, args=args))]), finish_reason="STOP")
    async def native(_agent, capability, args):
        native_calls.append((capability, deepcopy(args)))
        if capability.endswith("write_sandbox_text_file"):
            scripts[args["path"]] = args["content"]
            import hashlib
            return {"written": len(args["content"].encode())}
        return {"exit_code": 0, "command_id": f"native-{len(native_calls)}", "stdout": MEASURED}
    from backend.agents.models import ScopedToolDefinition
    definitions = [ScopedToolDefinition("operation:" + n, n, "Native fixture")
                   for n in ("write_sandbox_text_file", "execute_command", "read_skill", "run_skill_script",
                             "import_atom_structure_file", "inspect_atom_structure")]
    class Provider:
        async def get_context(self, agent_id): return "Fixture modelling context"
        async def list_tools(self, agent_id): return definitions
        async def invoke_tool(self, agent_id, capability, args): return await native(agent_id, capability, args)
    resolver = NS(resolve_runtime=lambda _: RuntimeModelConnection("openai", "fixture", "https://model.invalid", None),
                  context_limits=lambda _: (128000, 81920))
    runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=resolver)
    runtime._model = lambda *_: Fixture()
    config = AgentConfig("fixture", "Fixture")
    await runtime.create_agent(config)
    events = [ev async for ev in runtime.execute(config, NS(run_id="target-run", context_id="fixture"),
                                                NS(prompt=REQUEST, task_prompt=REQUEST))]
    assert events[-1].run_status == "failed"  # Recovering a step cannot grant global task acceptance.
    assert {path: text for path, text in scripts.items() if not path.startswith("_oaw_modelling_")} == {"probe.py": source, "next.py": source}
    assert len([path for path in scripts if path.startswith("_oaw_modelling_")]) == 1
    assert len(native_calls) == 5 and builder_turns == 6
