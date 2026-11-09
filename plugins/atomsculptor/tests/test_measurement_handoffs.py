"""Generic measured-progress and file-handoff boundaries; no model calls."""
import hashlib
import json
import subprocess
import sys
import asyncio
from copy import deepcopy

import pytest

from oaw_atomsculptor.measurements import INPUT_GUARD, parse_measurements
from oaw_atomsculptor.harness import RunEvidence
from oaw_atomsculptor.workflow import validate_plan
from test_workflow import PLAN, evidence, tool
from test_execution_steps import prior, setup
from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.execution_steps import typed_step_functions
from types import SimpleNamespace


METRIC = {"name": "residual", "value": 10, "direction": "minimize", "unit": "items"}
FRAME = {"origin": [8., 8., 8.], "axes": [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]}


def record(**values):
    return json.dumps({"oaw_measurements": values})


@pytest.mark.parametrize("prefix", ["", "OAW_MEASUREMENTS "])
def test_both_record_formats_read_the_same_native_metric(prefix):
    report, issues = parse_measurements("regular prose\n" + prefix + record(objective=METRIC))
    assert report["objective"] == METRIC and not issues
    assert RunEvidence._stdout_objective(prefix + record(objective=METRIC)) == METRIC


@pytest.mark.parametrize("bad", [
    '{"oaw_measurements":{"objective":{"name":"x","value":true,"direction":"minimize"}}}',
    '{"oaw_measurements":{"objective":{"name":"x","value":NaN,"direction":"minimize"}}}',
    '{"oaw_measurements":{"objective":{"name":"x","value":1,"value":0,"direction":"minimize"}}}',
    '{"oaw_measurements":{"constraints":[{"name":"region","passed":0}]}}',
    '{"oaw_measurements":{"assumption_invalid":true}}',
    'OAW_MEASUREMENTS {not-json}',
    'OAW_MEASUREMENTS ' + 'x'*16001,
    '{"oaw_measurements":{"kind":"candidate","objective":{"name":"x","value":0,"direction":"minimize"}}}',
])
def test_last_malformed_record_is_diagnosed_and_cannot_reuse_an_old_zero(bad):
    report, issues = parse_measurements(record(objective={**METRIC, "value": 0}) + "\n" + bad)
    assert report is None and issues[0]["code"] == "measurement_contract"


def artifact(**extra):
    return {"path": "candidate.xyz",  "units": "angstrom", "frame": deepcopy(FRAME),
            "components": {"component_a": 20, "component_b": 10}, **extra}


@pytest.mark.parametrize("changed", [
    {"path": "../outside.xyz"}, {"path": ""}, {"components": {"invalid": -1}},
    {"frame": {"origin": [8., 8., 8.], "axes": [[0,0,0]]*3}}, {"units": ""},
])
def test_artifact_contract_rejects_ambiguous_frame_or_identity(changed):
    parsed, issues = parse_measurements(record(artifacts=[artifact(**changed)]))
    assert parsed is None and issues


async def build_context(*, diagnostic_limit=4):
    e = evidence()
    plan = deepcopy(PLAN)
    plan["delivery"] = "report"
    plan["stages"][1]["experiment"].update(objective={k:v for k,v in METRIC.items() if k != "value"}, diagnostic_limit=diagnostic_limit)
    assert await tool(e, "finish_task", {"result": plan}, "Task completed.", role="planner") == "Task completed."
    e.workflow.before_delegation("structure_builder")
    await execute(e, "probe.py", "one primitive input")
    assert assess(e)["ok"]
    assert e.workflow.advance(e, "probe", "box", "probe.py")["ok"]
    return e, plan


async def execute(e, path, stdout):
    source = "print('fixture')"
    await tool(e, "write_sandbox_text_file", {"sandbox":"box", "path":path, "content":source},
               {"written":len(source)})
    return await tool(e, "execute_command", {"sandbox":"box", "argv":["python3", path]},
                      {"exit_code":0, "stdout":stdout, "command_id":path})


def assess(e):
    return e.workflow.assess_previous(e, prior(e))


@pytest.mark.asyncio
async def test_prefix_metrics_report_stagnation_without_forcing_review():
    e, _ = await build_context()
    for i in range(2):
        await execute(e, f"trial{i}.py", "OAW_MEASUREMENTS " + record(objective=METRIC))
        result = assess(e)
        assert result["observation"]["outcome"] == "continue"
    assert "without improvement" in e.workflow.host_signal["reason"]
    assert e.workflow.host_signal["advisory"] is True
    assert e.workflow.before_tool("structure_builder", "run_modelling_step", {}, e) is None


@pytest.mark.asyncio
async def test_better_objective_with_failed_constraint_is_not_progress_or_acceptance():
    e, _ = await build_context()
    await execute(e, "valid.py", record(objective=METRIC, constraints=[{"name":"region_nonempty", "passed":True}]))
    assert assess(e)["ok"]
    await execute(e, "empty.py", record(objective={**METRIC,"value":0}, constraints=[{"name":"region_nonempty", "passed":False}]))
    assert assess(e)["observation"]["candidate_eligible"] is False
    assert next(iter(e.workflow._progress.values()))[0] == 10
    assert e.workflow.advance(e, "build", "box", "empty.py")["error"]["code"] == "candidate_constraints"
    assert e.working_decision()["invalid_candidates"][-1]["failed_constraints"] == ["region_nonempty"]


@pytest.mark.asyncio
async def test_zero_coverage_overrules_a_false_pass_boolean_without_rejecting_real_zero_defects():
    e, _ = await build_context()
    values={"kind":"candidate", "measurement_revision":"stable-region", "objective":{**METRIC,"value":0},
            "constraints":[{"name":"local_check", "passed":True, "regions":["measured_region"]}], "coverage":{"measured_region":0}}
    await execute(e,"empty.py",record(**values))
    assert assess(e)["observation"]["candidate_eligible"] is False and not e.workflow._progress
    assert e.workflow.advance(e,"build","box","empty.py")["error"]["code"]=="candidate_constraints"
    values["coverage"]["measured_region"]=20
    await execute(e,"measured.py",record(**values))
    assert assess(e)["observation"]["candidate_eligible"] is True
    assert next(iter(e.workflow._progress.values()))[0]==0 and e.workflow.host_signal is None


@pytest.mark.asyncio
async def test_unused_zero_counts_remain_truthful_without_overriding_checked_populations():
    from oaw_atomsculptor.measurements import constraint_failures
    e, _ = await build_context()
    values = {"kind": "candidate", "measurement_revision": "zero-optional-group",
              "constraints": [{"name": "actual_check", "passed": True, "regions": ["tested"]}],
              "coverage": {"tested": 20, "optional": 0},
              "artifacts": [artifact(components={"present": 20, "optional": 0})]}
    await execute(e, 'candidate.py', record(**values))
    assert assess(e)['observation']['candidate_eligible']
    parsed = e.measurement_reports[e.latest_experiment().execution_sequence]
    assert parsed['coverage']['optional'] == parsed['artifacts'][0]['components']['optional'] == 0
    assert not constraint_failures(parsed)
    parsed['constraints'][0]['passed'] = False
    assert constraint_failures(parsed)[0]['name'] == 'actual_check'
    parsed['constraints'][0]['passed'] = True
    parsed['constraints'][0]['regions'].append('optional')
    assert constraint_failures(parsed)[0]['name'] == 'coverage:optional'


@pytest.mark.asyncio
async def test_native_skill_output_is_a_scoped_input_and_still_requires_file_inspection(tmp_path):
    from test_candidate_first_workflow import start_direct
    e = await start_direct()
    args = {'sandbox': 'box', 'skill': 'authorized-skill', 'script': 'scripts/build.py', 'argv': ['build']}
    await tool(e, 'run_skill_script', args, {'status': 'running', 'operation_id': 'producer'})
    assert not e.artifact_handoffs
    await tool(e, 'wait_sandbox_operation', {'sandbox': 'box', 'operation_id': 'producer'},
        {'exit_code': 0, 'command_id': 'producer', 'stdout': json.dumps({'output_file': 'input.json',
            'output_files': ['../outside.json', '/host/file', 'missing.json']})})
    receipt = e.artifact_handoffs[('box', 'input.json')]
    assert receipt['command_id'] == 'producer' and receipt['candidate_eligible'] is False
    assert 'units' not in receipt and 'frame' not in receipt and 'execution_sequence' not in receipt
    assert not e.outputs and len(e.artifact_handoffs) == 2
    assert e.workflow.validate_dependencies({'input_paths': ['input.json']}, e, 'box') is None
    assert e.workflow.validate_dependencies({'input_paths': ['input.json']}, e, 'other')['error']['code'] == 'subtask_inputs'
    (tmp_path / 'input.json').write_text('{"positions": [[1, 2, 3]]}')
    checked = subprocess.run([sys.executable, '-c', INPUT_GUARD, json.dumps([receipt])], cwd=tmp_path,
                             capture_output=True, text=True, timeout=10)
    assert checked.returncode == 0 and json.loads(checked.stdout)['oaw_input_metadata'][0]['format'] == 'json'
    missing = e.artifact_handoffs[('box', 'missing.json')]
    checked = subprocess.run([sys.executable, '-c', INPUT_GUARD, json.dumps([missing])], cwd=tmp_path,
                             capture_output=True, text=True, timeout=10)
    assert checked.returncode != 0 and 'input missing' in checked.stdout
    e.outputs[('box', 'input.json')] = {'phase': 'validated'}
    await tool(e, 'run_skill_script', args, {'exit_code': 0, 'command_id': 'producer2',
               'stdout': '{"output_file": "input.json"}'})
    assert e.artifact_handoffs[('box', 'input.json')]['command_id'] == 'producer2'
    assert not e.outputs


@pytest.mark.asyncio
async def test_repeated_constraint_failures_remain_visible_without_forcing_review():
    e, _ = await build_context()
    for i in range(2):
        await execute(e, f"invalid{i}.py", record(objective={**METRIC,"value":0}, constraints=[{"name":"validity", "passed":False}]))
        result = assess(e)
    assert result["observation"]["outcome"] == "continue"
    assert result["observation"]["host_advisory"]["advisory"] is True
    assert not e.workflow._progress


@pytest.mark.asyncio
async def test_diagnostics_do_not_erase_an_invalid_candidate_before_next_trial():
    e, _ = await build_context()
    for path, values in [("bad.py",{"objective":METRIC,"constraints":[{"name":"invariant","passed":False}]}),
                         ("diagnose.py",{"kind":"diagnostic"}),
                         ("bad2.py",{"objective":METRIC,"constraints":[{"name":"invariant","passed":False}]})]:
        await execute(e,path,record(**values))
        result=assess(e)
    assert result["observation"]["outcome"] == "continue"
    assert result["observation"]["host_advisory"]["advisory"] is True
    assert "same unresolved constraint_repair" in e.workflow.host_signal["reason"]


@pytest.mark.asyncio
async def test_diagnostic_advisory_and_explicit_review_reset_after_accepted_revision():
    e, plan = await build_context(diagnostic_limit=2)
    for i in range(2):
        await execute(e, f"diagnostic{i}.py", record(kind="diagnostic", constraints=[{"name":"input_shape", "passed":True}]))
        result = assess(e)
    assert result["observation"]["outcome"] == "continue"
    assert result["observation"]["host_advisory"]["advisory"] is True
    assert await tool(e, "finish_task", {"result":{"status":"needs_plan_review", "reason":"Missing comparable candidate after diagnostics",
        "sandbox":"box", "script_path":"diagnostic1.py"}}, "Task completed.") == "Task completed."
    assert e.workflow.review_request and e.working_decision()["rejected_methods"]
    plan["stages"][1]["experiment"]["method"] = "Revised measured construction"
    assert await tool(e, "finish_task", {"result":plan}, "Task completed.", role="planner") == "Task completed."
    assert e.workflow.plan_revision == 2 and e.workflow.host_signal is None and not e.workflow._diagnostic_experiments


@pytest.mark.asyncio
async def test_structured_contradiction_blocks_completion_but_preserves_model_continue():
    e, _ = await build_context()
    await execute(e, "diagnose.py", record(kind="diagnostic", assumption_invalid=True, contradiction="The proposed method violates a measured invariant"))
    assert assess(e)["observation"]["outcome"] == "continue"
    assert "measured invariant" in e.workflow.host_signal["reason"]
    assert e.workflow.advance(e,"build","box","diagnose.py")["error"]["code"] == "candidate_contradiction"


@pytest.mark.asyncio
async def test_input_contradiction_is_advisory_and_cannot_complete_checkpoint():
    e, _, run = await setup(stdout=record(assumption_invalid=True, contradiction="Executed input disproves the initial assumption"))
    await run()
    assert e.workflow.current == "probe" and assess(e)["observation"]["outcome"] == "continue"
    assert e.workflow.host_signal["advisory"] is True
    assert e.workflow.advance(e, "probe", "box", "step.py")["error"]["code"] == "candidate_contradiction"


@pytest.mark.asyncio
async def test_measurement_revision_changes_start_separate_comparison_series():
    e, _ = await build_context()
    for i, (revision, value) in enumerate([("old-frame",0), ("new-frame",10), ("new-frame",9)]):
        await execute(e, f"trial{i}.py", record(objective={**METRIC,"value":value}, measurement_revision=revision))
        assert assess(e)["ok"]
    assert e.workflow.host_signal is None
    assert {v[0] for v in e.workflow._progress.values()} == {0,9}


@pytest.mark.asyncio
async def test_malformed_output_is_visible_in_native_state_and_blocks_build_claim():
    e, _ = await build_context()
    await execute(e, "broken.py", 'OAW_MEASUREMENTS {broken}')
    assert e.current_experiment()["measurement_issues"][0]["code"] == "measurement_contract"
    assert assess(e)["ok"]
    assert e.workflow.advance(e, "build", "box", "broken.py")["error"]["code"] == "measurement_contract"


@pytest.mark.asyncio
async def test_producer_frame_and_component_identity_are_preserved_in_run_checkpoint():
    e, _, run = await setup(stdout=record(kind="baseline", artifacts=[artifact()]))
    await run()
    handoff = e.closeout_checkpoint("fixture")["artifact_handoffs"][0]
    assert handoff["frame"] == FRAME and handoff["components"] == artifact()["components"]
    assert handoff["command_id"] == e.latest_experiment().command_id
    assert not e.outputs and not e.confirmed


@pytest.mark.asyncio
@pytest.mark.parametrize("inputs", [[{"path":"unreported.xyz"}], [{"path":"missing.xyz"}]])
async def test_input_receipt_mismatch_blocks_before_any_native_mutation(inputs):
    e, calls, run = await setup(stdout=record(artifacts=[artifact()]))
    await run()
    assert assess(e)["ok"]
    result = await run(script_path="consumer.py", input_artifacts=inputs, input_manifest_path="input-context.json")
    assert result["error"]["code"] == "step_input_receipt" and len(calls) == 2


def test_trusted_sandbox_guard_checks_real_bytes_and_does_not_evaluate_them(tmp_path):
    path = tmp_path/"candidate.xyz"
    payload = b"raise RuntimeError('must not execute')"
    path.write_bytes(payload)
    args = json.dumps([{"path":"candidate.xyz"}])
    good = subprocess.run([sys.executable, "-c", INPUT_GUARD, args], cwd=tmp_path, capture_output=True, text=True)
    assert good.returncode == 0 and json.loads(good.stdout)["oaw_inputs_verified"][0]["path"] == path.name
    path.unlink()
    bad = subprocess.run([sys.executable, "-c", INPUT_GUARD, args], cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode != 0 and "candidate.xyz" in bad.stderr


def test_guard_rejects_an_input_resolving_outside_workspace(tmp_path):
    root=tmp_path/"workspace"; root.mkdir(); outside=tmp_path/"outside"; outside.write_bytes(b"x")
    (root/"candidate.xyz").symlink_to(outside)
    result = subprocess.run([sys.executable,"-c",INPUT_GUARD,json.dumps([{"path":"candidate.xyz"}])],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode != 0 and "outside workspace" in result.stderr


@pytest.mark.parametrize("quota", [True, 1, 13, "4"])
def test_retired_planner_diagnostic_quota_is_discarded_on_loading(quota):
    plan=deepcopy(PLAN);plan["stages"][1]["experiment"]["diagnostic_limit"]=quota
    parsed, issues = validate_plan(plan)
    assert not issues and "diagnostic_limit" not in parsed["stages"][1]["experiment"]


@pytest.mark.asyncio
@pytest.mark.parametrize("guard_state", ["verified", "changed", "unknown", "timeout"])
async def test_typed_consumer_sequences_native_guard_and_manifest_before_modelling(guard_state):
    payload = b"C fixture bytes"
    output = artifact()
    e, _, first = await setup(stdout=record(artifacts=[output]))
    await first()
    assert assess(e)["ok"]
    definitions=[ScopedToolDefinition("operation:"+name, name, "native fixture") for name in
                 ["write_sandbox_text_file","execute_command"]]
    e.configure_tools(definitions)
    native_calls=[]; files={"candidate.xyz":payload}
    async def native(capability, args):
        name=capability.removeprefix("operation:");native_calls.append((name,deepcopy(args)))
        if name=="write_sandbox_text_file":
            content=args["content"].encode();files[args["path"]]=content
            return {"written":len(content)}
        if args["argv"][1].startswith(".oaw-input-check-"):
            if guard_state=="timeout":raise TimeoutError("input-check receipt lost")
            if guard_state=="unknown":return {"status":"running","command_id":"guard-op"}
            if guard_state=="changed":return {"exit_code":1,"command_id":"guard-op","stderr":"hash mismatch"}
            return {"exit_code":0,"command_id":"guard-op","stdout":json.dumps({"oaw_inputs_verified":json.loads(args["argv"][2])})}
        return {"exit_code":0,"command_id":"consumer-op","stdout":"next measured fact"}
    function=typed_step_functions(e,definitions,native,asyncio.Event())[0]
    result=await function(sandbox="box",script_path="consumer.py",source="print('next')",
        question="One input-dependent calculation",expected_output="One measured result",stop_condition="One case",
        input_artifacts=[{"path":output["path"]}],input_manifest_path="input-context.json",
        tool_context=SimpleNamespace(function_call_id="consume-step"))
    guarded_args=next(args for name,args in native_calls if name=="execute_command")
    contract=json.loads(guarded_args["argv"][2])[0]
    assert contract["frame"] == FRAME and contract["components"] == output["components"]
    modelling=[args for name,args in native_calls if name=="execute_command" and args["argv"][1]=="consumer.py"]
    if guard_state=="verified":
        assert json.loads(files["input-context.json"])[0] == contract
        assert result["ok"] and modelling and e.execution_sequence==2
        assert result["step"]["input_check"]["command_id"]=="guard-op"
        identity=e.current_experiment()["execution_inputs"]["input_artifacts"][0]
        assert identity["frame"]==FRAME and identity["path"]==output["path"] and "sha256" not in identity
        assert modelling[0]["argv"][-2:]==["--oaw-input-manifest","input-context.json"]
        assert e.current_experiment()["execution_inputs"]["argv"]==[]
    else:
        assert not modelling and "consumer.py" not in files and "input-context.json" not in files and e.execution_sequence==1
        assert result["error"]["code"] == ("step_input_unknown" if guard_state in {"unknown", "timeout"} else "step_input_changed")
        assert bool(e.executions) == (guard_state in {"unknown", "timeout"})


@pytest.mark.asyncio
async def test_verified_input_change_is_a_real_repair_input_even_with_identical_source_and_argv():
    e, _, run = await setup(exit_code=1)
    await run()
    assert assess(e)["ok"]
    old=e.latest_experiment()
    old_hash=old.version
    context=SimpleNamespace(function_call_id="verified-input-retry")
    callback=e.tool_callbacks("structure_builder")["before_tool_callback"]
    arguments={"sandbox":"box","argv":["python3","step.py"]}
    denied=await callback(SimpleNamespace(name="execute_command"),arguments,context)
    assert denied["error"]["code"]=="step_unchanged_failure"
    # Only the composite's confirmed native input guard can populate this map;
    # it is never an LLM/native tool argument.
    identity=[{"path":"candidate.xyz","units":"angstrom","frame":FRAME,"components":{"component":1}}]
    e._verified_execution_inputs[context.function_call_id]={"input_artifacts":deepcopy(identity),"argv":[]}
    assert await callback(SimpleNamespace(name="execute_command"),arguments,context) is None
    assert e.execution_inputs[context.function_call_id]["input_artifacts"]==identity
    assert e.execution_inputs[context.function_call_id]["version"]==old_hash
    assert not e._verified_execution_inputs


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["patch", "rewrite", "foreign", "disk_changed", "provenance_changed"])
async def test_input_manifest_reuse_uses_native_cas_and_preserves_prior_evidence(tmp_path, change):
    """Exercise real exclusive creation and pinned CAS, not a permissive dict write."""
    import base64
    from backend.errors import ResourceValidationError
    from backend.sandbox.files import operate
    from backend.sandbox.models import SandboxValidationError

    output = artifact()
    e, _, first = await setup(stdout=record(artifacts=[output]))
    await first()
    assert assess(e)["ok"]
    definitions = [ScopedToolDefinition("operation:" + name, name, "native fixture")
                   for name in ["write_sandbox_text_file", "execute_command"]]
    e.configure_tools(definitions)
    calls = []

    async def native(capability, args):
        name = capability.removeprefix("operation:")
        calls.append((name, deepcopy(args)))
        if name == "write_sandbox_text_file":
            try:
                if args.get("edits"):
                    return operate(tmp_path, args["path"], "edit", edits=args["edits"])
                raw = args["content"].encode()
                saved = operate(tmp_path, args["path"], "write", data=base64.b64encode(raw).decode(),
                                overwrite=args.get("overwrite", False))
                return {**saved}
            except SandboxValidationError as exc:
                raise ResourceValidationError(str(exc)) from exc
        if args["argv"][1].startswith(".oaw-input-check-"):
            return {"exit_code": 0, "command_id": "guard-op", "stdout": json.dumps(
                {"oaw_inputs_verified": json.loads(args["argv"][2])})}
        return {"exit_code": 1, "command_id": "syntax-op", "stdout": "", "stderr": "SyntaxError"}

    functions = typed_step_functions(e, definitions, native, asyncio.Event())
    common = dict(sandbox="box", script_path="consumer.py", question="One calculation",
                  expected_output="One measured result", stop_condition="One case",
                  input_artifacts=[{"path": output["path"]}],
                  input_manifest_path="inputs.json")
    if change == "foreign":
        (tmp_path / "inputs.json").write_text("foreign input manifest")
    first_result = await functions[0](**common, source="print('bad')",
                                     tool_context=SimpleNamespace(function_call_id="consumer1"))
    if change == "foreign":
        assert first_result["error"]["code"] == "step_input_save"
        assert first_result["error"]["receipt"]["error"]["code"] == "invalid_resource"
        assert (tmp_path / "inputs.json").read_text() == "foreign input manifest"
        assert not any(name == "execute_command" and args["argv"][1] == "consumer.py" for name, args in calls)
        assert not e.steps.active
        return
    assert first_result["step"]["state"] == "finished" and first_result["exit_code"] == 1
    manifest = (tmp_path / "inputs.json").read_bytes()
    digest = e.latest_experiment().version
    if change == "disk_changed":
        (tmp_path / "inputs.json").write_text("externally changed")
    if change == "provenance_changed":
        e.artifact_handoffs[("box", output["path"])]["frame"]["origin"][0] += 1
    code = dict(source="print('fixed')", rewrite_reason="Replace this small malformed source; preserve its input contract") if change == "rewrite" else dict(edits=[{"old": "bad", "new": "fixed"}])
    before = len(calls)
    result = await functions[2 if change == "rewrite" else 1](**common, **code, 
        previous_assessment=prior(e), tool_context=SimpleNamespace(function_call_id="consumer2"))
    assert result["step"]["state"] == "finished"
    assert (tmp_path / "consumer.py").read_text() == "print('fixed')"
    expected = json.loads(manifest)
    if change == "provenance_changed": expected[0]["frame"]["origin"][0] += 1
    assert json.loads((tmp_path / "inputs.json").read_bytes()) == expected
    manifest_write = next(args for name, args in calls[before:] if name == "write_sandbox_text_file" and args["path"] == "inputs.json")
    assert manifest_write["overwrite"] is True and "expected_sha256" not in manifest_write
    assert sum(name == "execute_command" and args["argv"][1] == "consumer.py" for name, args in calls) == 2
    assert e.execution_sequence == 3
    assert not e.steps.active and not e.outputs and not e.workflow.checkpoints
