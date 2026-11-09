"""Causal recovery and measured prerequisites through workflow callbacks."""
from copy import deepcopy
import pytest
from backend.agents.context import ContextBudget
from backend.agents.request_window import RequestWindow
from backend.runs.invocation_logs import RunInvocationLogs
from test_workflow import evidence, activate, execute, tool, PLAN
from test_evidence_contract import build
from test_measurement_handoffs import execute as measure, record, assess


@pytest.mark.asyncio
async def test_distinct_failures_do_not_exhaust_a_shared_counter():
    e = evidence(); await activate(e)
    for index, cause in enumerate(("ValueError: wrong shape", "KeyError: index", "TypeError: bad call", "IndexError: bounds")):
        await execute(e, f"repair-{index}.py", code=f"print({index})", result={"exit_code":1, "stderr":cause})
        assert e.workflow.host_signal is None
        assert e.workflow.recovery["category"] == "execution_repair"


@pytest.mark.asyncio
async def test_changed_source_alone_does_not_hide_the_same_unresolved_cause():
    e = evidence(); await activate(e)
    await execute(e, "a.py", code="print(1)", result={"exit_code":1, "stderr":"ValueError: wrong shape"})
    await execute(e, "b.py", code="print(2)", result={"exit_code":1, "stderr":"ValueError: wrong shape"})
    assert e.workflow.recovery["source_changed"] and e.workflow.recovery["unchanged_evidence"]
    assert e.workflow.host_signal is None
    assert e.workflow.recovery['category'] == 'execution_repair'


@pytest.mark.asyncio
async def test_new_native_environment_and_changed_measurements_allow_repair():
    e = evidence(); await activate(e)
    await execute(e, "a.py", result={"exit_code":1, "stderr":"ImportError: missing package"})
    e.environment_revisions["box"] = 1
    await execute(e, "b.py", result={"exit_code":1, "stderr":"ImportError: missing package"})
    assert not e.workflow.host_signal and not e.workflow.recovery["unchanged_evidence"]
    e = await build()
    for count in range(8):
        await measure(e, f"measure-{count}.py", record(kind="diagnostic", values={"observed_count": count}))
        assert assess(e)["observation"]["outcome"] == "continue"
    assert e.workflow.host_signal is None


@pytest.mark.asyncio
async def test_more_than_two_reviews_are_allowed_with_new_execution_facts():
    e = evidence(); await activate(e)
    for count in range(4):
        await execute(e, "probe.py", code=f"print({count})", result={"exit_code":1, "stderr":f"ValueError: distinct {count}"})
        result = await tool(e, "finish_task", {"result": {"status":"needs_plan_review", "reason":f"Native contradiction {count}"}}, "Task completed.")
        assert result == "Task completed."
        await tool(e, "finish_task", {"result": deepcopy(PLAN)}, "Task completed.", role="planner")
        assert e.workflow.advance(e, "observe", "box", "probe.py", hypothesis="Retain the reviewed receipt",
            measurement=f"ValueError: distinct {count}", outcome="continue", next_step="Execute revised probe")["ok"]
    assert len(e.workflow.review_history) == 4 and not e.workflow.blocked


@pytest.mark.asyncio
async def test_prerequisites_require_done_subtasks_and_assessed_host_evidence():
    e = await build()
    await measure(e, "first.py", record(kind="diagnostic", values={"count":2}))
    assert assess(e)["ok"]
    action = {**deepcopy(e.workflow.plan[1]["experiment"]), "expected_output":"Measured count",
              "depends_on":[], "evidence_refs":[f"execution:{e.execution_sequence}"]}
    assert e.workflow.validate_next_experiment(action, e, "box")[1] is None
    assert e.workflow.validate_next_experiment({**action, "evidence_refs":["execution:999"]}, e, "box")[1]["error"]["code"] == "subtask_evidence"
    assert e.workflow.validate_next_experiment({**action, "depends_on":["not-yet-completed"]}, e, "box")[1]["error"]["code"] == "subtask_dependencies"
    e.workflow.subtask_history.append({"subtask_id":"done", "execution_sequence":e.execution_sequence})
    assert e.workflow.validate_next_experiment({**action, "depends_on":["done"]}, e, "box")[1] is None


def test_old_dependency_is_retained_before_newer_unrelated_measurements():
    e = evidence()
    e.workflow.pending_experiment = {"input_paths":["old.json"], "evidence_refs":[]}
    e.artifact_handoffs[("box", "old.json")] = {"path":"old.json", "execution_sequence":1}
    e.measurement_reports = {i:{"values":{"count":i}} for i in range(1, 30)}
    assert e.relevant_measurements()[0][0] == 1


@pytest.mark.asyncio
async def test_returning_to_an_excluded_measured_state_is_not_new_progress():
    e = await build()
    for index, count in enumerate((2, 3, 2)):
        await measure(e, f"measure-{index}.py", record(kind="diagnostic", values={"count":count}))
        outcome = assess(e)["observation"]["outcome"]
        assert outcome == "continue"
    assert e.workflow.host_signal["advisory"] is True
    assert e.workflow.before_tool("structure_builder", "run_modelling_step", {}, e) is None


@pytest.mark.asyncio
async def test_renaming_revision_or_check_prose_cannot_hide_the_same_failed_requirement():
    e = await build()
    check_id = next(r["id"] for r in e.workflow.requirements() if r["stage"] == "build")
    for index in range(2):
        await measure(e, f"method-{index}.py", record(kind="diagnostic", measurement_revision=f"method-{index}",
            constraints=[{"name": f"algorithm-{index}", "reason": f"wording-{index}", "passed": False,
                          "check_ids": [check_id], "regions": ["tested"]}],
            coverage={"tested": 4}, values={"residual": 2, "method_label": f"strategy-{index}",
                "samples": {"detail_source": f"report-{index}.json", "record_pointer": "/values/samples", "size": 500}}))
        assert assess(e)["ok"]
    assert e.workflow.recovery["unchanged_evidence"] and e.workflow.host_signal
    assert e.workflow.host_signal["advisory"] is True
    assert e.experiment_history[-1]["outcome"] == "continue"
    assert e.workflow.advance(e, "build", "box", "method-1.py")["error"]["code"] == "candidate_constraints"


@pytest.mark.asyncio
async def test_protocol_loop_needs_identical_rejection_after_feedback_not_a_request_count():
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    from types import SimpleNamespace as NS
    e = evidence(); await activate(e)
    hooks = e.tool_callbacks("structure_builder")
    args = {"stage":"probe", "sandbox":"box", "script_path":"missing.py"}
    obj, ctx = NS(name="advance_modelling_stage"), NS(function_call_id="rejected")
    async def rejected(arguments):
        assert await hooks["before_tool_callback"](obj, arguments, ctx) is None
        response = e.workflow.advance(e, **arguments)
        assert response["error"]
        await hooks["after_tool_callback"](obj, arguments, ctx, response)
    await rejected(dict(args))
    assert e.block_reason is None
    request = LlmRequest(contents=[], config=types.GenerateContentConfig())
    e.before_model("structure_builder", request, NS(types=types), 81920)
    await rejected({**args,"script_path":"changed.py"})
    assert e.block_reason is None
    await rejected(dict(args))
    assert "unchanged rejected" in e.block_reason


@pytest.mark.asyncio
async def test_host_measurement_reference_reads_native_stdout_after_scope_change(tmp_path):
    from backend.persistence.database import Database
    from backend.state import StateStore
    from backend.plugins import create_builtin_registry
    from backend.runs.store import RunStore
    db = Database(tmp_path / "state.sqlite3")
    try:
        run = RunStore(db).create(agent_id="fixture", runtime_provider_id="atomsculptor.adk-team", caller_kind="user")
        e = evidence(); await activate(e)
        logs = RunInvocationLogs(StateStore(db, create_builtin_registry()), run.run_id)
        e.trace.invocations = logs
        window = RequestWindow(ContextBudget(100000, 512), e.trace, "planner")
        e.trace.context_windows["planner"] = window
        identity = logs.start_tool("structure_builder", "fixture", 0, "execute_command", {})
        stdout = record(kind="diagnostic", values={"count":3})
        await execute(e, "probe.py", result={"exit_code":0,"command_id":"probe.py","stdout":stdout})
        logs.finish_tool(identity, {"execution":{"command_id":"probe.py", "stdout":stdout, "exit_code":0}})
        e._context_role = "planner"
        reference = e.measurement_summary(e.execution_sequence)["native_measurement_reference"]
        assert reference["pointer"] == "/execution/stdout"
        assert (await window.read_tool_result(reference["invocation_id"], reference["pointer"]))["text"] == stdout
        window.set_scope("new delegation")
        assert (await window.read_tool_result(identity))["error"]["code"] == "permission_denied"
        refreshed = e.measurement_reference(e.execution_sequence)
        assert (await window.read_tool_result(refreshed["invocation_id"], refreshed["pointer"]))["text"] == stdout
    finally:
        db.close()
