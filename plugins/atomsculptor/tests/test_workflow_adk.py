"""Full ADK planner/stage/import handoff using in-memory, non-scientific fixtures."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.models import ScopedToolDefinition, ToolParameter
from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
from oaw_atomsculptor.harness import HarnessStopped
from oaw_atomsculptor.runtime import AtomSculptorRuntime
from test_team_closeout import MemoryStore
from test_workflow import PLAN, REQUEST, LOG_PLAN_PATH


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("object_result", [False, True])
@pytest.mark.parametrize("code_first", [False, True])
@pytest.mark.parametrize("review_plan", [False, True])
@pytest.mark.parametrize("model_output", [2048, 81920])
@pytest.mark.parametrize("host_wait", [False, True])
async def test_real_adk_requires_plan_and_executes_all_stages_once(managed, object_result, code_first, review_plan, model_output, host_wait, native_state=None, composite_steps=False, combined_assessment=False, composite_delivery=False, network_failures=0, omit_delivery_target=False, redundant_after_observe=False, artifact_handoff=False):
    root_turns = planner_turns = builder_turns = 0
    dispatched = []
    files = {}
    if artifact_handoff:
        files["coords.json"] = "fixture coordinate data"
    disconnected_requests = []
    recovered_request = None
    structure_hash = "a" * 64
    document_hash = "b" * 64
    submitted_plan = json.loads(LOG_PLAN_PATH.read_text())
    reviewed_plan = json.loads(json.dumps(submitted_plan))
    reviewed_plan["stages"][0]["checks"] = ["Report measured inputs; contradicted assumptions are observations."]
    reviewed_plan["assumptions"] = ["Arc length is not automatically the measured nearest-neighbour distance."]

    class Fixture(BaseLlm):
        model: str = "openai/workflow-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal root_turns, planner_turns, builder_turns, recovered_request
            names = [d.name for t in request.config.tools or [] for d in t.function_declarations or []]
            snapshots = [json.loads(p.text.split("\n", 1)[1]) for c in request.contents for p in c.parts
                         if p.text and p.text.startswith("HOST RUN EVIDENCE")]
            snapshot = snapshots[-1]
            assert len(snapshots) == 1
            stable = [json.loads(p.text.split("\n", 1)[1]) for c in request.contents for p in c.parts if p.text and p.text.startswith("HOST STABLE CONTEXT")][-1]
            assert stable["original_user_request"] == REQUEST
            if snapshot["execution_role"] == "structure_builder":
                assert request.config.max_output_tokens == model_output
                if composite_steps and builder_turns == 2 and network_failures:
                    serialized = json.dumps({"contents": [c.model_dump(mode="json") for c in request.contents],
                        "config": request.config.model_dump(mode="json")}, sort_keys=True)
                    if len(disconnected_requests) < network_failures:
                        disconnected_requests.append(serialized)
                        yield LlmResponse(partial=True, content=types.Content(role="model",
                            parts=[types.Part(thought=True, text="Interrupted preview, not a tool result")]))
                        raise ConnectionResetError("fixture disconnected")
                    recovered_request = serialized
                # The first call tries to skip code; the host refuses it.
                steps = [
                    ("advance_modelling_stage", {"stage": "probe", "sandbox": "box", "script_path": "probe.py"}),
                    ("write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print('observed boundary counts')"}),
                    ("execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]}),
                    ("advance_modelling_stage", {"stage": "observe", "sandbox": "box", "script_path": "probe.py",
                        "hypothesis": "Measure actual boundary", "measurement": "observed boundary counts",
                        "outcome": "needs_plan_review" if review_plan else "ready", "next_step": "Review measured inputs"}),
                    ("advance_modelling_stage", {"stage": "probe", "sandbox": "box", "script_path": "probe.py"}),
                    ("write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": "print('candidate constructed')"}),
                    ("execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]}),
                    ("advance_modelling_stage", {"stage": "observe", "sandbox": "box", "script_path": "build.py",
                        "hypothesis": "Build candidate", "measurement": "candidate constructed",
                        "outcome": "ready", "next_step": "Validate saved structure independently"}),
                    ("run_skill_script", {"sandbox": "box", "skill": "inspect-skill", "script": "scripts/structure_inspect.py",
                        "argv": ["validate_structure", "--folder", ".", "--file-name", "cnt.extxyz"]}),
                    ("advance_modelling_stage", {"stage": "build", "sandbox": "box", "script_path": "build.py", "structure_path": "cnt.extxyz"}),
                    ("run_skill_script", {"sandbox": "box", "skill": "inspect-skill", "script": "scripts/structure_inspect.py", "argv": ["to_atomsculptor_document"]}),
                    ("import_atom_structure_file", {"sandbox": "box", "target": "structure", "file_name": "cnt.json",  "expected_revision": 1}),
                    ("inspect_atom_structure", {"target": "structure"}),
                    ("advance_modelling_stage", {"stage": "deliver", "target": "structure"}),
                    ("finish_task", {"result": '{"status":"succeeded","document":{"revision":2,"read_back":true}}'}),
                ]
                if omit_delivery_target:
                    steps[-2] = ("advance_modelling_stage", {"stage": "deliver"})
                if composite_delivery:
                    steps = steps[:10] + [("run_delivery_step", {"sandbox": "box", "skill": "inspect-skill",
                        "structure_path": "cnt.extxyz", "target": "structure"}), steps[-1]]
                if review_plan:
                    steps.insert(4, ("finish_task", {"result": json.dumps({"status": "needs_plan_review",
                        "reason": "Measured arc and nearest-neighbour distance contradict the planned equality.",
                        "sandbox": "box", "script_path": "probe.py"})}))
                    steps.insert(5, ("advance_modelling_stage", {"stage": "observe", "sandbox": "box", "script_path": "probe.py",
                        "hypothesis": "Revised assumption preserves measured inputs", "measurement": "observed boundary counts",
                        "outcome": "ready", "next_step": "Advance probe with the existing measurement"}))
                if composite_steps:
                    atomic = []
                    for name, args in steps:
                        if name == "execute_command":
                            continue  # Native save/execute are sequential inside one accepted tool.
                        if name == "write_sandbox_text_file":
                            name, args = "run_modelling_step", {
                                "sandbox": args["sandbox"], "script_path": args["path"], "source": args["content"],
                                "question": "What is the next measured fact?", "expected_output": "Print the observed value",
                                "stop_condition": "Stop after this finite measurement", "action_mode": "new"}
                        atomic.append((name, args))
                    steps = atomic
                    if redundant_after_observe:
                        assert not review_plan and not combined_assessment
                        index = next(i for i, (name, args) in enumerate(steps)
                                     if name == "advance_modelling_stage" and args.get("stage") == "observe")
                        # A repair remains pending, so this is not stage-ready.
                        steps[index][1]["outcome"] = "continue"
                        assessment = {k: v for k, v in steps[index][1].items() if k != "stage"}
                        source = "print('observed boundary counts')"
                        steps[index + 1:index + 1] = [
                            ("patch_modelling_step", {"sandbox": "box", "script_path": "probe.py",
                                "edits": [{"old": source, "new": source + "\n# one bounded repair"}],
                                
                                "question": "Check one repaired input", "expected_output": "Print the observed input",
                                "stop_condition": "One case", "previous_assessment": assessment}),
                            ("advance_modelling_stage", dict(steps[index][1])),
                        ]
                    if combined_assessment:
                        assert not review_plan
                        assessment = next(args for name, args in steps if name == "advance_modelling_stage" and args.get("stage") == "observe")
                        steps = [(name, args) for i, (name, args) in enumerate(steps) if i == 0 or not (name == "advance_modelling_stage" and
                            args.get("script_path") == "probe.py" and args.get("stage") in {"observe", "probe"})]
                        # The initial intentionally invalid stage attempt stays; the real
                        # assessed probe checkpoint is attached to the build action.
                        for name, args in steps:
                            if name == "run_modelling_step" and args["script_path"] == "build.py":
                                args["previous_assessment"] = {k: v for k, v in assessment.items() if k != "stage"}
                                args["previous_assessment"].update(command_id="probe.py", complete_stage=True)
                    if artifact_handoff:
                        for name, args in steps:
                            if name == "run_modelling_step" and args["script_path"] == "build.py":
                                args.update(input_artifacts=[{"path":"coords.json"}],
                                    input_manifest_path="input-context.json",
                                    source="import argparse,json\np=argparse.ArgumentParser()\np.add_argument('--oaw-input-manifest')\ninputs=json.load(open(p.parse_args().oaw_input_manifest))\nprint('candidate constructed')")
                prefix = [
                    ("inspect_sandbox", {"sandbox": "box"}),
                    ("start_sandbox", {"sandbox": "box"}),
                    ("execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(32)"]}),
                ] if code_first else []
                if code_first:
                    # An inline exit is not a measurement checkpoint: Builder
                    # explicitly decides what source to save and execute next.
                    steps = steps[1:]
                steps = prefix + steps
                name, args = steps[builder_turns]
                if name == "patch_modelling_step":
                    assert snapshot["current_experiment"]["assessment_status"] == "recorded"
                step_index = builder_turns - len(prefix)
                if step_index == 0 and code_first:
                    assert snapshot["execution_action"]["inline_measurements"][0]["stdout"] == "32\n"
                    assert snapshot["scripts"] == []
                    assert snapshot["modelling_workflow"]["current_stage"] == "probe"
                if step_index == 1 and not code_first and not combined_assessment:
                    responses = [p.function_response for c in request.contents for p in c.parts if p.function_response]
                    assert any(r.response.get("error", {}).get("code") == "execution_required" for r in responses), [(r.name, r.response) for r in responses]
                if name == "write_sandbox_text_file" and args["path"] == "build.py":
                    checkpoint = snapshot["modelling_workflow"]["checkpoints"][0]
                    assert checkpoint["script_path"] == "probe.py"
                    assert checkpoint["execution_sequence"] > 0
                    assert "observations" not in checkpoint
                    responses = [p.function_response for c in request.contents for p in c.parts if p.function_response]
                    assert any("boundary counts" in json.dumps(r.response) for r in responses)
                    assert snapshot["modelling_workflow"]["current_stage"] == "build"
                builder_turns += 1
            elif snapshot["execution_role"] == "planner":
                assert request.config.max_output_tokens == model_output
                planner_turns += 1
                value = reviewed_plan if planner_turns > 1 else submitted_plan
                name, args = "finish_task", {"result": value if object_result else json.dumps(value)}
            else:
                root_turns += 1
                if root_turns == 1:
                    name, args = "structure_builder", {"request": REQUEST}
                elif root_turns == 2:
                    assert "structure_builder" not in names
                    assert any(p.text and p.text.startswith("HOST MODELLING ROUTE") for c in request.contents for p in c.parts)
                    name, args = "planner", {"request": REQUEST}
                elif root_turns == 3:
                    assert snapshot["modelling_workflow"]["plan"] == submitted_plan["stages"]
                    name, args = "structure_builder", {"request": REQUEST + "\nPlan: " + json.dumps(PLAN)}
                elif review_plan and root_turns == 4:
                    assert snapshot["modelling_workflow"]["review_request"]["observations"]
                    name, args = "planner", {"request": REQUEST + "\nReview the host-observed contradiction."}
                elif review_plan and root_turns == 5:
                    assert snapshot["modelling_workflow"]["plan_revision"] == 2
                    assert snapshot["modelling_workflow"]["review_request"] is None
                    name, args = "structure_builder", {"request": REQUEST + "\nResume with the reviewed plan."}
                else:
                    assert snapshot["modelling_workflow"]["current_stage"] == "complete"
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Fixture stages completed; document read back.")]))
                    return
            parts = [types.Part(function_call=types.FunctionCall(
                id=f"call-{root_turns}-{planner_turns}-{builder_turns}", name=name, args=args))]
            if code_first and name == "start_sandbox":
                parts.append(types.Part(function_call=types.FunctionCall(id="duplicate-start", name=name, args=args)))
                yield LlmResponse(partial=True, content=types.Content(role="model", parts=parts))
            yield LlmResponse(content=types.Content(role="model", parts=parts))

    class Provider:
        async def list_tools(self, _):
            def definition(name, params):
                return ScopedToolDefinition(name, name, "In-memory fixture operation", tuple(ToolParameter(n, t) for n, t in params))
            tools = [
                definition("write_sandbox_text_file", [("sandbox", str), ("path", str), ("content", str)]),
                definition("execute_command", [("sandbox", str), ("argv", list)]),
                definition("run_skill_script", [("sandbox", str), ("skill", str), ("script", str), ("argv", list)]),
                definition("read_skill", []),
                definition("import_atom_structure_file", [("sandbox", str), ("target", str), ("file_name", str), ("expected_revision", int)]),
                definition("inspect_atom_structure", [("target", str)]),
            ]
            if code_first:
                tools.extend([definition("inspect_sandbox", [("sandbox", str)]),
                              definition("start_sandbox", [("sandbox", str)])])
            if host_wait:
                tools.append(definition("wait_sandbox_operation", [("sandbox", str), ("operation_id", str), ("wait_seconds", float)]))
            return tools

        async def invoke_tool(self, _, name, args):
            dispatched.append((name, args))
            if name in {"inspect_sandbox", "start_sandbox"}:
                return {"state": "stopped" if name == "inspect_sandbox" else "ready"}
            if name == "write_sandbox_text_file":
                if args.get("edits"):
                    from backend.sandbox.text_edits import apply_text_edits
                    files[args["path"]] = apply_text_edits(files[args["path"]].encode(), args["edits"]).decode()
                else:
                    files[args["path"]] = args["content"]
                return {"written": len(files[args["path"]])}
            if name == "execute_command":
                if args["argv"][1] == "-c":
                    return {"exit_code": 0, "stdout": "32\n"}
                assert args["argv"][1] in files
                if artifact_handoff and args["argv"][1].startswith(".oaw-input-check-"):
                    return {"exit_code":0,"command_id":"input-check","stdout":json.dumps({"oaw_inputs_verified":json.loads(args["argv"][2])})}
                if artifact_handoff and args["argv"][1] == "probe.py":
                    probe_checks = next(s["checks"] for s in submitted_plan["stages"] if s["id"] == "probe")
                    return {"exit_code":0,"command_id":"probe.py","stdout":"observed boundary counts\n"+json.dumps({"oaw_measurements":{
                        "kind":"baseline","coverage":{"fixture":1},"constraints":[{"name":check,"passed":True,"regions":["fixture"],
                            "check_ids":["probe:"+hashlib.sha256(check.encode()).hexdigest()[:12]]} for check in probe_checks],
                        "artifacts":[{"path":"coords.json",
                            "units":"angstrom","frame":{"origin":[8.,8.,8.],"axes":[[1,0,0],[0,1,0],[0,0,1]]},"components":{"fixture":1}}]}})}
                if artifact_handoff and args["argv"][1] == "build.py":
                    build_checks = next(s["checks"] for s in submitted_plan["stages"] if s["id"] == "build")
                    return {"exit_code":0,"command_id":"build.py","stdout":json.dumps({"oaw_measurements":{
                        "schema_version":1,"kind":"candidate","measurement_revision":"fixture-v1",
                        "coverage":{"fixture":1},"constraints":[{"name":check,"passed":True,"regions":["fixture"],
                            "check_ids":["build:"+hashlib.sha256(check.encode()).hexdigest()[:12]]} for check in build_checks],
                        "artifacts":[{"path":"cnt.extxyz","units":"angstrom",
                            "frame":{"origin":[0,0,0],"axes":[[1,0,0],[0,1,0],[0,0,1]]},"components":{"fixture":1}}]}})}
                if host_wait and args["argv"][1] == "build.py":
                    return {"status": "running", "operation_id": "build-operation"}
                return {"exit_code": 0, "command_id": args["argv"][1], "stdout": files[args["argv"][1]]}
            if name == "wait_sandbox_operation":
                assert args["operation_id"] == "build-operation"
                if sum(name == "wait_sandbox_operation" for name, _ in dispatched) <= 8:
                    return {"status": "running", "operation_id": "build-operation"}
                return {"exit_code": 0, "command_id": "build-operation", "stdout": files["build.py"]}
            if name == "run_skill_script":
                result = ({"passed": True, "file": "cnt.extxyz"}
                    if args["argv"][0] == "validate_structure" else
                    {"input_structure_file": "cnt.extxyz", 
                     "output_document_file": "cnt.json"})
                return {"exit_code": 0, "stdout": json.dumps(result)}
            if composite_delivery and name == "inspect_atom_structure":
                return {"revision": 2 if any(n == "import_atom_structure_file" for n, _ in dispatched) else 1}
            return {"revision": 2}

    resolver = SimpleNamespace(
        resolve_runtime=lambda _: RuntimeModelConnection("openai", "workflow-fixture", "https://model.invalid", None),
        context_limits=lambda _: (128000, model_output))
    runtime = AtomSculptorRuntime(Provider(), context_store=MemoryStore() if managed else None, model_connection_resolver=resolver)
    runtime.reasoning_store = native_state
    runtime._model = lambda *_: Fixture()
    config = AgentConfig("fixture", "Fixture", provider_config={"harness": {"request_recovery": {
        "initial_delay_seconds": 0.0, "max_attempts": 6}}} if network_failures else {})
    await runtime.create_agent(config)
    events = [e async for e in runtime.execute(config, SimpleNamespace(run_id="fixture-run", context_id="fixture"),
        SimpleNamespace(prompt=REQUEST, task_prompt=REQUEST))]
    if network_failures:
        assert len(set(disconnected_requests)) == 1
        assert len(disconnected_requests) == network_failures
        assert runtime.active == {}
        if network_failures == 6:
            assert events[-1].run_status == "failed"
            assert [name for name, _ in dispatched] == ["write_sandbox_text_file", "write_sandbox_text_file", "execute_command"]
            closeout = next(e.payload for e in events if e.payload.get("kind") == "execution_closeout")
            assert closeout["checkpoint_persisted"] is True
            scope = native_state.ensure_scope("run", "fixture-run")
            checkpoint = native_state.get(scope, "execution_checkpoint")
            assert checkpoint["stage"] == "probe"
            assert checkpoint["scripts"][0]["path"] == "probe.py"
            assert checkpoint["scripts"][0]["version"] == 1
            assert "6 attempts" in events[-1].payload["error"]
            from backend.runs.invocation_logs import RunInvocationLogs
            models = list(RunInvocationLogs(native_state, "fixture-run").records("models").values())
            interrupted = [row for row in models if row["state"] == "interrupted"]
            assert len(interrupted) == 6
            assert len({row["model_request"] for row in interrupted}) == 1
            return
        assert recovered_request == disconnected_requests[0]
    assert events[-1].run_status == "succeeded"
    assert (root_turns, planner_turns, builder_turns) == (4 + 2 * int(review_plan), 1 + int(review_plan),
                                                       15 + 2 * int(code_first) + 2 * int(review_plan) - 2 * int(composite_steps) - 2 * int(combined_assessment) - 3 * int(composite_delivery) + 2 * int(redundant_after_observe))
    expected_prefix = ["inspect_sandbox", "start_sandbox", "execute_command"] if code_first else []
    support_writes = [(name, args) for name, args in dispatched if name == "write_sandbox_text_file"
                      and args.get("path", "").startswith("_oaw_modelling_")]
    assert len(support_writes) == int(composite_steps)
    assert [name for name, args in dispatched if name != "wait_sandbox_operation" and
            not (name == "write_sandbox_text_file" and args.get("path", "").startswith("_oaw_modelling_"))] == expected_prefix + ["write_sandbox_text_file", "execute_command",
        *(["write_sandbox_text_file", "execute_command"] if redundant_after_observe else []),
        *(["write_sandbox_text_file", "execute_command", "write_sandbox_text_file"] if artifact_handoff else []),
        "write_sandbox_text_file", "execute_command", "run_skill_script", "run_skill_script",
        *( ["inspect_atom_structure"] if composite_delivery else []), "import_atom_structure_file", "inspect_atom_structure"]
    assert sum(name == "wait_sandbox_operation" for name, _ in dispatched) == (9 if host_wait else 0)
    if artifact_handoff:
        inputs = json.loads(files["input-context.json"])
        assert inputs[0]["frame"]["origin"] == [8.,8.,8.] and inputs[0]["components"] == {"fixture":1}
        assert inputs[0]["command_id"] == "probe.py"
    assert len([e for e in events if e.payload.get("workflow_event") == "modelling_plan"]) == 1 + int(review_plan)
    stages = [e for e in events if e.payload.get("workflow_event") == "modelling_stage"]
    assert len(stages) == 3 and all(e.payload["kind"] == "status" for e in stages)
    coalesced = [e for e in events if e.payload.get("workflow_event") == "sandbox_start_coalesced"]
    assert len(coalesced) == (1 if code_first else 0)
    host_calls = [e for e in events if e.payload.get("workflow_event") == "probe_materialization"]
    assert not host_calls
    assert all(e.payload["phase"] == "host_action" and "model_request" not in e.payload for e in host_calls)
    model_starts = [e for e in events if e.payload.get("phase") == "model"
                    and "input_bytes" in e.payload and "elapsed_ms" not in e.payload]
    assert len(model_starts) == root_turns + planner_turns + builder_turns
    if native_state is not None:
        from backend.runs.invocation_logs import RunInvocationLogs
        archive = RunInvocationLogs(native_state, "fixture-run")
        models = list(archive.records("models").values())
        assert len(models) == len(model_starts) + network_failures
        assert sum(row["state"] == "interrupted" for row in models) == network_failures
        assert all(row["request_complete"] for row in models)
        assert sum(row["state"] == "returned" for row in models) == len(model_starts)
        calls = list(archive.records("tools").values())
        assert {"planner", "structure_builder", "finish_task", "advance_modelling_stage"} <= {row["name"] for row in calls}
        assert all(row["state"] == "returned" and row["arguments_complete"] and row["response_complete"] for row in calls)
        actual_argv = [row["effective_arguments"]["argv"] for row in calls if row["name"] == "execute_command"]
        assert actual_argv == ([["python3", "-c", "print(32)"]] if code_first else []) + (
            [] if composite_steps else [["python3", "probe.py"], ["python3", "build.py"]])
        if composite_steps:
            steps = [row for row in calls if row["name"] == "run_modelling_step"]
            assert len(steps) == 2
            assert all(row["arguments"]["source"] and row["response"]["execution"]["exit_code"] == 0 for row in steps)
            assert all(row["response"]["step"]["state"] == "finished" for row in steps)


@pytest.mark.asyncio
async def test_real_adk_archives_sdk_tools_and_delegate_boundaries_in_native_run_state(tmp_path):
    from backend.persistence.database import Database
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    from backend.state import StateStore
    database = Database(tmp_path / "world.sqlite3")
    try:
        RunStore(database).create(run_id="fixture-run", agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
        await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, True, True, 81920, True,
            StateStore(database, create_builtin_registry()))
    finally:
        database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
async def test_real_adk_consumes_producer_handoff_without_a_separate_model_round(managed):
    await test_real_adk_requires_plan_and_executes_all_stages_once(managed, True, False, False, 81920, False,
        composite_steps=True, combined_assessment=True, composite_delivery=True, artifact_handoff=True)


@pytest.mark.asyncio
async def test_repeated_planner_bypass_stops_before_any_task_or_capability_dispatch():
    calls = 0

    class Fixture(BaseLlm):
        model: str = "openai/workflow-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal calls
            calls += 1
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                id=f"invalid-{calls}", name="structure_builder", args={"request": REQUEST}))]))

    class Provider:
        async def list_tools(self, _):
            return []

    resolver = SimpleNamespace(
        resolve_runtime=lambda _: RuntimeModelConnection("openai", "workflow-fixture", "https://model.invalid", None),
        context_limits=lambda _: (32768, 2048))
    runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=resolver)
    runtime._model = lambda *_: Fixture()
    config = AgentConfig("fixture", "Fixture")
    await runtime.create_agent(config)
    events = []
    async for e in runtime.execute(config, SimpleNamespace(run_id="fixture-run", context_id="fixture"),
        SimpleNamespace(prompt=REQUEST, task_prompt=REQUEST)):
        events.append(e)
    assert events[-1].run_status == "failed" and "repeatedly bypassed" in events[-1].payload["error"]
    assert calls == 2
    assert not any(e.type.value == "tool_started" for e in events)
    assert runtime.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("missing_skill", [False, True])
async def test_real_adk_latest_failure_is_bounded_and_missing_skill_stops_before_builder(managed, missing_skill):
    from open_agent_world.plugin_api import AgentRuntimeError
    root_turns = builder_turns = 0
    dispatched = []

    class Fixture(BaseLlm):
        model: str = "openai/workflow-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal root_turns, builder_turns
            names = [d.name for t in request.config.tools or [] for d in t.function_declarations or []]
            if "advance_modelling_stage" in names:
                builder_turns += 1
                if builder_turns in {2, 4}:
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(thought=True, text="Long topology derivation")]),
                        finish_reason="MAX_TOKENS")
                    return
                name = "inspect_sandbox" if builder_turns == 1 else "start_sandbox"
                args = {"sandbox": "box"}
            elif "finish_task" in names:
                name, args = "finish_task", {"result": json.dumps(PLAN)}
            else:
                root_turns += 1
                name, args = ("planner", {"request": REQUEST}) if root_turns == 1 else ("structure_builder", {"request": REQUEST})
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=
                types.FunctionCall(id=f"call-{root_turns}-{builder_turns}", name=name, args=args))]))

    class Provider:
        async def list_tools(self, _):
            names = ["write_sandbox_text_file", "execute_command", "read_skill", "import_atom_structure_file", "inspect_atom_structure",
                     "inspect_sandbox", "start_sandbox"] + ([] if missing_skill else ["run_skill_script"])
            return [ScopedToolDefinition(n, n, "Fixture", (ToolParameter("sandbox", str),) if n in {"inspect_sandbox", "start_sandbox"} else ()) for n in names]

        async def invoke_tool(self, _, name, args):
            dispatched.append(name)
            return {"state": "ready" if name == "start_sandbox" else "stopped"}

    resolver = SimpleNamespace(resolve_runtime=lambda _: RuntimeModelConnection("openai", "workflow-fixture", "https://model.invalid", None),
                               context_limits=lambda _: (131072, 2048))
    runtime = AtomSculptorRuntime(Provider(), context_store=MemoryStore() if managed else None, model_connection_resolver=resolver)
    runtime._model = lambda *_: Fixture()
    config = AgentConfig("fixture", "Fixture")
    await runtime.create_agent(config)
    events = []
    async def consume():
        async for event in runtime.execute(config, SimpleNamespace(run_id="fixture-run", context_id="fixture"),
            SimpleNamespace(prompt=REQUEST, task_prompt=REQUEST)):
            events.append(event)
    if missing_skill:
        with pytest.raises(AgentRuntimeError, match="preflight blocked"):
            await consume()
    else:
        await consume()
        assert events[-1].run_status == 'failed' and 'single bounded recovery' in events[-1].payload['error']
        assert any(e.payload.get('kind') == 'execution_closeout' for e in events)
    assert dispatched == ([] if missing_skill else ["inspect_sandbox", "start_sandbox"])
    assert builder_turns == (0 if missing_skill else 4)
    assert not any(e.run_status == "succeeded" for e in events)
    assert runtime.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("host_wait,review_plan", [(False, False), (True, False), (True, True)])
async def test_atomic_steps_complete_real_adk_workflow_with_fewer_model_turns(host_wait, review_plan):
    await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, False, review_plan, 81920, host_wait, composite_steps=True)


@pytest.mark.asyncio
async def test_atomic_step_archives_full_source_and_execution_in_native_run_state(tmp_path):
    from backend.persistence.database import Database
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    from backend.state import StateStore
    database = Database(tmp_path / "world.sqlite3")
    try:
        RunStore(database).create(run_id="fixture-run", agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
        await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, True, True, 81920, True,
            StateStore(database, create_builtin_registry()), composite_steps=True)
    finally:
        database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_wait", [False, True])
async def test_assessment_and_probe_checkpoint_combine_with_next_real_adk_action(host_wait):
    await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, False, False, 81920, host_wait,
        composite_steps=True, combined_assessment=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_wait", [False, True])
async def test_actual_adk_delivery_composite_reuses_validation_and_imports_with_readback(host_wait):
    await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, False, False, 81920, host_wait,
        composite_steps=True, combined_assessment=True, composite_delivery=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("network_failures", [3, 6])
async def test_real_adk_transport_retries_preserve_executed_script_and_native_run_archive(tmp_path, network_failures):
    from backend.persistence.database import Database
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    from backend.state import StateStore
    database = Database(tmp_path / "world.sqlite3")
    try:
        RunStore(database).create(run_id="fixture-run", agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
        await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, False, False, 81920, False,
            StateStore(database, create_builtin_registry()), composite_steps=True, network_failures=network_failures)
    finally:
        database.close()


@pytest.mark.asyncio
async def test_actual_adk_can_close_manual_delivery_with_unique_native_target():
    await test_real_adk_requires_plan_and_executes_all_stages_once(False, True, False, False, 81920, False,
        composite_steps=True, omit_delivery_target=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("host_wait", [False, True])
async def test_actual_adk_observe_then_redundant_assessment_patch_delivers_once(managed, host_wait):
    await test_real_adk_requires_plan_and_executes_all_stages_once(managed, True, False, False, 81920, host_wait,
        composite_steps=True, redundant_after_observe=True)
