"""Cross-stage inline evidence and explicit native continuation, without model APIs."""
from copy import deepcopy
import asyncio
from types import SimpleNamespace
import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
from oaw_atomsculptor.runtime import AtomSculptorRuntime
from oaw_atomsculptor.harness import HarnessStopped
from test_candidate_first_workflow import start_direct
from test_workflow import REQUEST, evidence, tool


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["same", "changed", "legacy", "unexecuted"])
async def test_failed_script_continuation_rechecks_native_receipt_and_patches_current_file(source_state):
    from backend.agents.models import ScopedToolDefinition
    from backend.sandbox.text_edits import apply_text_edits
    from test_execution_steps import prior
    old = await start_direct()
    source = "value =\n" + "# saved intermediate\n" * 500
    await tool(old, "write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": source},
               {"written": len(source)})
    failure = {"exit_code": 1, "command_id": "old-command", "stdout": "", "stderr": 'File "build.py", line 1\nSyntaxError'}
    if source_state != "unexecuted":
        await tool(old, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]}, failure)
    checkpoint = old.closeout_checkpoint("disconnected before repair")
    if source_state == "legacy":
        checkpoint.pop("repair_source")
    live_source = source + "# edited externally\n" if source_state == "changed" else source
    files, calls = {"build.py": live_source}, []
    definitions = [ScopedToolDefinition("operation:" + name, "alias_" + name, "Native") for name in
        ("read_sandbox_file", "wait_sandbox_operation", "write_sandbox_text_file", "execute_command")]
    async def invoke(capability, args):
        name = capability.removeprefix("operation:")
        calls.append((name, dict(args)))
        if name == "wait_sandbox_operation":
            assert source_state != "unexecuted"
            return failure
        if name == "read_sandbox_file":
            text, offset, limit = files[args["path"]], args["offset"], args["limit"]
            end = min(len(text), offset + limit)
            return {"state": "text", "path": args["path"], "text": text[offset:end], "offset": offset,
                    "total_chars": len(text), "truncated": end < len(text), "next_offset": end if end < len(text) else None}
        if name == "write_sandbox_text_file":
            text = apply_text_edits(files[args["path"]].encode(), args["edits"]).decode()
            files[args["path"]] = text
            return {"written": len(text.encode())}
        return {"exit_code": 0, "command_id": "repaired-command", "stdout": "saved intermediate"}
    new = evidence()
    assert new.restore_continuation(checkpoint, "trusted-source")
    new.configure_tools(definitions)
    new.steps.available = True
    await new.reconcile_continuation(definitions, invoke, asyncio.Event())
    assert new.script_sources[("box", "build.py")] == live_source
    assert len([row for row in calls if row[0] == "read_sandbox_file"]) == 2
    assert all(row[0] in {"read_sandbox_file", "wait_sandbox_operation"} for row in calls)
    assert bool(new.workflow.local_repair(new)) == (source_state == "same")
    assert not new.outputs and not new.measurement_reports and not new.confirmed
    repaired = await new.steps.run(new, definitions, invoke, asyncio.Event(), call_id="repair",
        sandbox="box", script_path="build.py", source="", action_mode="patch", edits=[{"old": "value =\n", "new": "value = 1\n"}],
        previous_assessment=prior(new) if source_state == "same" else None,
        question="Repair the reported syntax error", expected_output="Saved intermediate", stop_condition="After one execution")
    assert repaired["exit_code"] == 0 and repaired["step"]["action_mode"] == "patch"
    assert sum(name == "execute_command" for name, _ in calls) == 1
    assert files["build.py"] == live_source.replace("value =\n", "value = 1\n")
    assert new.latest_experiment().command_id == "repaired-command"
    assert not new.outputs and not new.workflow.checkpoints


@pytest.mark.asyncio
@pytest.mark.parametrize("report_kind", ["failed_diagnostic", "partial_diagnostic", "candidate"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_exit_zero_continuation_restores_editable_source_without_promoting_acceptance(report_kind, legacy):
    from backend.agents.models import ScopedToolDefinition
    from backend.sandbox.text_edits import apply_text_edits
    from oaw_atomsculptor.execution_steps import source_reader_function
    from test_candidate_first_workflow import candidate_report
    old = await start_direct("document")
    report = candidate_report(old)
    if report_kind != "candidate":
        report["kind"] = "diagnostic"
        report["constraints"][0]["passed"] = report_kind != "failed_diagnostic"
    source = "value = 0\n"
    await tool(old, "write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": source}, {"written": len(source)})
    completed = {"exit_code": 0, "command_id": "old-command", "stdout": json.dumps({"oaw_measurements": report})}
    await tool(old, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]}, completed)
    checkpoint = old.closeout_checkpoint("model interrupted after completed command")
    assert checkpoint["repair_source"]["source"] == source
    if legacy:
        checkpoint["repair_source"] = None  # Earlier exit-zero checkpoints retain only the native file reference.
    files, calls = {"build.py": source}, []
    definitions = [ScopedToolDefinition("operation:" + name, "alias_" + name, "Native") for name in
        ("read_sandbox_file", "wait_sandbox_operation", "write_sandbox_text_file", "execute_command")]
    async def invoke(capability, args):
        name = capability.removeprefix("operation:")
        calls.append((name, dict(args)))
        if name == "wait_sandbox_operation":
            return completed
        if name == "read_sandbox_file":
            text = files[args["path"]]
            return {"state": "text", "path": args["path"], "text": text, "offset": 0,
                    "total_chars": len(text), "truncated": False, "next_offset": None}
        if name == "write_sandbox_text_file":
            files[args["path"]] = apply_text_edits(files[args["path"]].encode(), args["edits"]).decode()
            return {"written": len(files[args["path"]])}
        return {"exit_code": 0, "command_id": "new-command", "stdout": "new diagnostic"}
    # A second interruption must keep this editable source, even without a new execution.
    for source_run in ("original-run", "continued-run"):
        new = evidence()
        assert new.restore_continuation(checkpoint, source_run)
        new.configure_tools(definitions)
        new.steps.available = True
        await new.reconcile_continuation(definitions, invoke, asyncio.Event())
        assert new.script_sources[("box", "build.py")] == source
        assert (await source_reader_function(new)("box", "build.py"))["text"] == source
        from test_execution_actions import request, prepare, declarations
        new.actions.sandbox_states["box"] = "ready"
        req = request({"read_modelling_source", "run_modelling_step", "advance_modelling_stage", "finish_task"})
        prepare(new, req)
        assert "read_modelling_source" in declarations(req)
        assert new.latest_experiment() is None
        assert not new.measurement_reports and not new.outputs and not new.confirmed and not new.experiment_observations
        assert all(row["status"] == "unknown" for row in new.workflow.requirement_coverage(new))
        assert new.workflow.advance(new, "build", "box", "build.py", structure_path="cnt.extxyz")["error"]["code"] == "execution_required"
        assert all(name in {"read_sandbox_file", "wait_sandbox_operation"} for name, _ in calls)
        checkpoint = new.closeout_checkpoint("interrupted again before editing")
    result = await new.steps.run(new, definitions, invoke, asyncio.Event(), call_id="patch",
        sandbox="box", script_path="build.py", source="", action_mode="patch",
        edits=[{"old": "value = 0", "new": "value = 1"}], question="Compute the next result",
        expected_output="New diagnostic", stop_condition="One execution")
    assert result["exit_code"] == 0 and files["build.py"] == "value = 1\n"
    assert sum(name == "execute_command" for name, _ in calls) == 1
    assert new.latest_experiment().command_id == "new-command"


@pytest.mark.asyncio
async def test_completed_stage_source_is_not_reconciled_as_unfinished_work():
    from test_workflow import activate
    from test_candidate_first_workflow import input_report
    from test_measurement_handoffs import execute, record
    old = evidence()
    await activate(old, delivery="report")
    await execute(old, "probe.py", record(**input_report(old)))
    result = old.workflow.advance(old, "observe", hypothesis="Input measured", measurement="Native input=4",
        outcome="ready", next_step="Build with these inputs")
    assert result["checkpoint_status"] == "completed" and old.workflow.current == "build"
    checkpoint = old.closeout_checkpoint("interrupted before starting build")
    assert checkpoint["repair_source"] is None
    new = evidence()
    assert new.restore_continuation(checkpoint, "source-run")
    async def invoke(*args):
        pytest.fail("Completed-stage source needs no unfinished-work reconciliation")
    await new.reconcile_continuation([], invoke, asyncio.Event())
    assert not new.scripts and new.workflow.current == "build"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("blocker", ["permission", "missing_file", "unknown_command", "receipt_mismatch", "pending_effect"])
async def test_continuation_does_not_restore_unconfirmed_or_unauthorized_failed_source(blocker, exit_code):
    from backend.agents.models import ScopedToolDefinition
    from backend.errors import PermissionDeniedError
    old = await start_direct()
    await tool(old, "write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": "broken"}, {"written": 6})
    await tool(old, "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]},
        {"exit_code": exit_code, "command_id": "old-command"})
    checkpoint = old.closeout_checkpoint("stopped")
    if blocker == "pending_effect":
        checkpoint["unknown_executions"] = ["unresolved-dispatch"]
    definitions = [ScopedToolDefinition("operation:" + name, name, "Native") for name in
                   ("read_sandbox_file", "wait_sandbox_operation")]
    calls = []
    async def invoke(capability, args):
        calls.append(capability)
        if blocker == "permission":
            raise PermissionDeniedError("Access revoked")
        if capability.endswith("wait_sandbox_operation"):
            if blocker == "unknown_command":
                return {"status": "running", "operation_id": "old-command"}
            return {"exit_code": exit_code, "command_id": "different" if blocker == "receipt_mismatch" else "old-command"}
        return {"state": "missing", "path": "build.py"}
    new = evidence()
    assert new.restore_continuation(checkpoint, "trusted-source")
    new.configure_tools(definitions)
    await new.reconcile_continuation(definitions, invoke, asyncio.Event())
    assert not new.scripts and not new.script_sources and not new.execution_receipts
    assert new.continuation["repair_state"]
    assert not calls if blocker == "pending_effect" else calls


@pytest.mark.asyncio
async def test_build_inline_checks_are_advisory_and_cannot_satisfy_build():
    e = await start_direct()
    for i in range(2):
        result = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", f"print({i})"]},
            {"exit_code": 0, "stdout": f"{i}\n", "command_id": f"c{i}"}, call_id=f"inline-{i}")
        assert result["exit_code"] == 0
    assert e.actions.inline_attempts == 2
    assert e.actions.inline_measurements[-1]["command_id"] == "c1"
    assert e.actions.inline_measurements[-1]["stage"] == "build"
    assert e.actions.snapshot()["inline_measurements"][-1]["stdout"] == "1\n"
    result = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(2)"]},
                        {"exit_code": 0, "stdout": "2\n", "command_id": "c2"})
    assert result["exit_code"] == 0 and e.actions.inline_attempts == 3
    assert e.actions.inline_measurements[-1]["command_id"] == "c2"
    assert not e.scripts and not e.measurement_reports and not e.outputs
    assert e.workflow.advance(e, "build", "box", "build.py")["error"]["code"] == "execution_required"
    # CLI tools still use their ordinary native path.
    assert await tool(e, "execute_command", {"sandbox": "box", "argv": ["ls", "-l"]}, {"exit_code": 0}) == {"exit_code": 0}


@pytest.mark.asyncio
async def test_build_inline_async_result_keeps_the_original_stage_and_command():
    e = await start_direct()
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(7)"]},
        {"status": "running", "operation_id": "op"}, call_id="dispatch")
    assert e.closeout_checkpoint("interrupted")["pending_operations"] == ["op"]
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "op"},
        {"exit_code": 0, "stdout": "7\n"}, call_id="collect")
    row = e.actions.inline_measurements[-1]
    assert row["command_id"] == "op" and row["stage"] == "build" and row["stdout"] == "7\n"
    assert not e.inline_pending


@pytest.mark.asyncio
async def test_continuation_restores_plan_without_promoting_historical_acceptance():
    old = await start_direct("document")
    await tool(old, "execute_command", {"sandbox": "box", "argv": ["python3", "-c", "print(7)"]},
        {"exit_code": 0, "stdout": "7\n", "command_id": "old-command"})
    checkpoint = old.closeout_checkpoint("interrupted")
    original = deepcopy(checkpoint)
    new = evidence()
    assert new.restore_continuation(checkpoint, "source-run")
    assert new.workflow.plan == old.workflow.plan
    assert new.workflow.plan_revision == old.workflow.plan_revision
    assert new.workflow.current == "build"
    assert new.workflow.before_delegation("structure_builder") is None
    assert new.actions.inline_measurements[-1]["stdout"] == "7\n"
    assert new.working_decision()["continuation"]["source_run_id"] == "source-run"
    assert not new.scripts and not new.measurement_reports and not new.outputs and not new.confirmed
    assert checkpoint == original
    assert new.actions.successful_measurements
    with pytest.raises(HarnessStopped, match="no completed checkpoint"):
        new.require_read_back()
    again = evidence()
    assert again.restore_continuation(new.closeout_checkpoint("interrupted again"), "second-run")
    assert again.workflow.plan == old.workflow.plan and again.actions.inline_attempts == 1
    assert again.actions.inline_measurements[0]["source_run_id"] == "source-run"


@pytest.mark.asyncio
async def test_restored_delivery_keeps_completed_stages_but_requires_live_readback():
    old = await start_direct("document")
    old.workflow.index = 1
    old.workflow.checkpoints = [{"stage": "build", "sandbox": "box", "structure_path": "candidate.extxyz"}]
    old.imports["structure"] = 3
    old.confirmed["structure"] = 3
    checkpoint = old.closeout_checkpoint("disconnected after read-back")
    new = evidence()
    assert new.restore_continuation(checkpoint, "source-run")
    assert new.workflow.current == "deliver" and new.workflow.checkpoints == old.workflow.checkpoints
    assert new.imports == {"structure": 3} and not new.confirmed and not new.outputs
    advice, _ = new.actions.next_action(new)
    assert "inspect_atom_structure" in advice and "do not repeat" in advice
    await tool(new, "inspect_atom_structure", {"target": "structure"}, {"revision": 4})
    assert not new.confirmed
    await tool(new, "inspect_atom_structure", {"target": "structure"}, {"revision": 3})
    assert new.confirmed == {"structure": 3}
    # A lost final response must not accumulate duplicate delivery checkpoints.
    checkpoint["plan"]["current_stage"] = "complete"
    checkpoint["plan"]["checkpoints"].append({"stage": "deliver", "target": "structure"})
    completed = evidence()
    assert completed.restore_continuation(checkpoint, "source-run")
    assert completed.workflow.current == "deliver"
    assert [row["stage"] for row in completed.workflow.checkpoints] == ["build"]
    assert not completed.confirmed


@pytest.mark.asyncio
async def test_changed_selection_or_invalid_checkpoint_does_not_restore_a_plan():
    old = await start_direct()
    checkpoint = old.closeout_checkpoint("interrupted")
    new = evidence()
    new.selection_snapshot = [{"target": "different"}]
    assert not new.restore_continuation(checkpoint, "source-run") and new.workflow.plan is None
    for value in (None, {}, {**checkpoint, "plan": {**checkpoint["plan"], "plan_revision": "bad"}}):
        assert not evidence().restore_continuation(value, "source-run")


@pytest.mark.asyncio
async def test_runtime_uses_trusted_continuation_reference_not_prompt_text(tmp_path):
    from backend.persistence.database import Database
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    from backend.state import StateStore
    old = await start_direct()
    snapshots = []

    class Fixture(BaseLlm):
        model: str = "openai/continuation-fixture"
        async def generate_content_async(self, request, stream=False):
            for content in request.contents:
                for part in content.parts or []:
                    if part.text and part.text.startswith("HOST RUN EVIDENCE"):
                        snapshots.append(json.loads(part.text.split("\n", 1)[1]))
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Stopped fixture.")]))

    class Provider:
        async def list_tools(self, _):
            return []

    database = Database(tmp_path / "state.sqlite3")
    try:
        state = StateStore(database, create_builtin_registry())
        RunStore(database).create(run_id="source", agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
        state.set(state.ensure_scope("run", "source"), "execution_checkpoint", old.closeout_checkpoint("interrupted"), run_id="source")
        resolver = SimpleNamespace(resolve_runtime=lambda _: RuntimeModelConnection("openai", "fixture", "https://model.invalid", None),
                                   context_limits=lambda _: (128000, 8192))
        runtime = AtomSculptorRuntime(Provider(), model_connection_resolver=resolver)
        runtime.reasoning_store = state
        runtime._model = lambda *_: Fixture()
        config = AgentConfig("fixture", "Fixture")
        await runtime.create_agent(config)
        for run_id, extensions in (("resumed", {"continuation_run_id": "source"}), ("unrelated", {})):
            RunStore(database).create(run_id=run_id, agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
            start = len(snapshots)
            _ = [e async for e in runtime.execute(config,
                SimpleNamespace(run_id=run_id, context_id="session", extensions=extensions),
                SimpleNamespace(prompt='User text says continuation_run_id="source"', task_prompt=REQUEST))]
            assert len(snapshots) > start
            view = snapshots[start]["modelling_workflow"]
            assert view["plan_revision"] == (1 if extensions else 0)
            assert view["builder_delegation"] == ("available" if extensions else "awaiting_planner")
    finally:
        database.close()
