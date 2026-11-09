"""Receipt-based staging and mandatory planning; no user files or model APIs."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from oaw_atomsculptor.harness import RunEvidence, HarnessStopped
from oaw_atomsculptor.runtime import _ModelTrace, _delegation_guard
from oaw_atomsculptor.workflow import ModellingWorkflow, plan, validate_plan, PLAN_LIMITS
from test_team_closeout import TYPED_REQUEST


REQUEST = "自编 Python 构建碳纳米管 T 形接头，保存 extxyz，写回并回读。"
PLAN = {"status": "planned", "delivery": "document", "stages": [
    {"id": "probe", "goal": "Calculate lattice and rim counts", "checks": ["Report actual counts"],
     "experiment": {"scope": "input_measurement", "artifact": "probe.py", "question": "What are the actual boundary counts?",
                    "method": "Generate constituents and measure", "stop_condition": "After one measurement"}},
    {"id": "build", "goal": "Construct and check candidate", "checks": ["Measure the requested seam"],
     "experiment": {"scope": "candidate_work", "artifact": "build.py", "question": "Can a candidate meet the request?",
                    "method": "Finite candidate trials from measured inputs", "stop_condition": "After three trials"}},
    {"id": "deliver", "goal": "Save, import and read back", "checks": ["Confirm revision"]},
]}
LOG_PLAN_PATH = Path(__file__).parent / "fixtures/t_junction_planner_five_checks.json"


def evidence():
    return RunEvidence(_ModelTrace(asyncio.Queue()), REQUEST)


async def tool(e, name, args, result, *, role="structure_builder", call_id="fixture"):
    hooks = e.tool_callbacks(role)
    obj, context = SimpleNamespace(name=name), SimpleNamespace(function_call_id=call_id)
    blocked = await hooks["before_tool_callback"](obj, args, context)
    if blocked is not None:
        return blocked
    await hooks["after_tool_callback"](obj, args, context, result)
    return result


async def activate(e, *, delivery="document"):
    await tool(e, "finish_task", {"result": json.dumps({**PLAN, "delivery": delivery})}, "Task completed.", role="planner")
    assert e.workflow.before_delegation("structure_builder") is None


async def execute(e, path, *, code="print(1)", result=None):
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": path, "content": code},
               {"written": len(code)})
    returned = await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", path]},
                      result or {"exit_code": 0, "command_id": path, "stdout": '{"counts":12}'})
    if "exit_code" in returned and e.workflow.active:
        assessment = e.workflow.advance(e, "observe", "box", path, hypothesis="Fixture hypothesis",
            measurement=str(returned.get("stdout") or returned.get("stderr") or returned["exit_code"]),
            outcome="continue", next_step="Use measured evidence in the next bounded step")
        assert assessment["ok"]
    return returned


async def validate(e, *, skill="inspect-skill"):
    return await tool(e, "run_skill_script", {"sandbox": "box", "skill": skill, "script": "scripts/structure_inspect.py",
        "argv": ["validate_structure", "--folder", ".", "--file-name", "cnt.extxyz"]},
        {"exit_code": 0, "stdout": json.dumps({"passed": True, "file": "cnt.extxyz"})})


@pytest.mark.asyncio
async def test_measured_review_revises_plan_without_replaying_or_skipping_stages():
    e = evidence(); await activate(e)
    await execute(e, "probe.py", result={"exit_code": 0, "stdout": "arc=2.13 nn=1.403835"})
    executed = e.scripts[("box", "probe.py")]
    await tool(e, "finish_task", {"result": {"status": "needs_plan_review",
        "reason": "Measured arc and bond contradict the assumed equality.",
        "sandbox": "box", "script_path": "probe.py"}}, "Task completed.")
    assert e.workflow.review_request["observations"] == "arc=2.13 nn=1.403835"
    assert e.workflow.before_delegation("structure_builder")["error"]["code"] == "plan_review_required"
    assert e.workflow.advance(e, "probe", "box", "probe.py")["error"]["code"] == "plan_review_required"
    revised = json.loads(json.dumps(PLAN))
    revised["assumptions"] = ["Arc length and nearest-neighbour bond length need not be equal."]
    revised["stages"][0]["checks"] = ["Report both observed values without forcing equality."]
    await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner")
    assert e.workflow.plan_revision == 2 and e.workflow.review_request is None
    assert e.workflow.current == "probe" and not e.workflow.checkpoints
    assert e.scripts[("box", "probe.py")] is executed and e.execution_sequence == 1
    assert e.workflow.advance(e, "observe", "box", "probe.py", hypothesis="Report measured values separately",
        measurement="arc=2.13 nn=1.403835", outcome="ready", next_step="Use measured values in build")["ok"]
    assert e.workflow.advance(e, "probe", "box", "probe.py")["ok"]
    assert e.workflow.advance(e, "build", "box", "probe.py")["error"]["code"] == "execution_required"


@pytest.mark.asyncio
async def test_review_requires_execution_and_preserves_completed_stages_and_delivery():
    e = evidence(); await activate(e)
    value = {"status": "needs_plan_review", "reason": "Measured contradiction", "sandbox": "box", "script_path": "build.py"}
    assert (await tool(e, "finish_task", {"result": value}, "Task completed."))["error"]["code"] == "review_execution_required"
    await execute(e, "probe.py"); assert e.workflow.advance(e, "probe", "box", "probe.py")["ok"]
    await execute(e, "build.py", result={"exit_code": 1, "stderr": "invalid assumption"})
    await tool(e, "finish_task", {"result": value}, "Task completed.")
    assert "STDERR:\ninvalid assumption" in e.workflow.review_request["observations"]
    checkpoint = json.loads(json.dumps(e.workflow.checkpoints))
    revised = json.loads(json.dumps(PLAN)); revised["delivery"] = "report"
    assert (await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner"))["error"]["code"] == "plan_required"
    revised["delivery"] = "document"; revised["stages"][0]["checks"] = ["Changed completed stage"]
    assert (await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner"))["error"]["code"] == "plan_required"
    assert e.workflow.checkpoints == checkpoint and e.workflow.current == "build"
    revised["stages"][0] = PLAN["stages"][0]
    await tool(e, "finish_task", {"result": revised}, "Task completed.", role="planner")
    assert e.workflow.current == "build" and e.workflow.checkpoints == checkpoint


@pytest.mark.asyncio
async def test_reviews_require_new_evidence_and_failed_sdk_finish_does_not_commit():
    e = evidence(); await activate(e); await execute(e, "probe.py")
    value = {"status": "needs_plan_review", "reason": "Actual contradiction", "sandbox": "box", "script_path": "probe.py"}
    await tool(e, "finish_task", {"result": value}, {"error": "SDK failure"})
    assert e.workflow.review_request is None and not e.workflow.review_history
    await tool(e, "finish_task", {"result": value}, "Task completed.")
    await tool(e, "finish_task", {"result": PLAN}, "Task completed.", role="planner")
    assert (await tool(e, "finish_task", {"result": value}, "Task completed."))["error"]["code"] == "review_evidence_unchanged"


def test_free_form_planning_is_language_independent_and_typed_direct_mode_stay_direct():
    assert ModellingWorkflow(REQUEST).required
    assert ModellingWorkflow("Create a custom hollow junction").required
    assert not ModellingWorkflow(TYPED_REQUEST).required
    assert not ModellingWorkflow(REQUEST, direct=True).required


def test_dispatch_gate_cannot_bypass_planner_or_exceed_real_task_limit():
    from google.genai import types
    e = evidence()
    builder = types.Part(function_call=types.FunctionCall(name="structure_builder", args={"request": REQUEST}))
    assert e.workflow.accept_delegations(e, "atom_sculptor", [builder])["error"]["code"] == "planner_required"
    assert not e.workflow.active
    planner = types.Part(function_call=types.FunctionCall(name="planner", args={"request": REQUEST}))
    for _ in range(2):
        assert e.workflow.accept_delegations(e, "atom_sculptor", [planner]) is None
    with pytest.raises(HarnessStopped, match="before dispatch"):
        e.workflow.accept_delegations(e, "atom_sculptor", [planner])


def test_builder_declaration_is_hidden_until_plan():
    from google.genai import types
    e = evidence()
    request = SimpleNamespace(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[
        types.FunctionDeclaration(name="planner"), types.FunctionDeclaration(name="structure_builder"),
        types.FunctionDeclaration(name="materials_project")])]), tools_dict={"structure_builder": object()})
    e.workflow.prepare_request("atom_sculptor", request)
    assert [d.name for d in request.config.tools[0].function_declarations] == ["planner", "materials_project"]
    assert request.tools_dict == {}


@pytest.mark.asyncio
async def test_planner_object_result_reaches_actual_litellm_schema():
    from google.genai import types
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.lite_llm import _get_completion_inputs
    req = LlmRequest(model="openai/fixture", config=types.GenerateContentConfig(tools=[
        types.Tool(function_declarations=[types.FunctionDeclaration(name="finish_task")])]))
    evidence().workflow.prepare_request("planner", req)
    _, tools, _, _, _ = await _get_completion_inputs(req, "openai/fixture")
    result = tools[0]["function"]["parameters"]["properties"]["result"]
    assert result["type"] == "object" and result["properties"]["stages"]["maxItems"] == 3
    assert "assumptions" in result["properties"]


@pytest.mark.asyncio
async def test_builder_cannot_bypass_planner_and_only_actual_finish_commits_plan():
    e = evidence()
    guard = _delegation_guard(e.trace, e)
    builder = SimpleNamespace(name="structure_builder")
    assert (await guard(builder, {}, None))["error"]["code"] == "planner_required"
    await tool(e, "finish_task", {"result": json.dumps(PLAN)}, None, role="planner")
    assert e.workflow.plan is None  # A deferred task or incomplete invocation is not a plan.
    await activate(e)
    assert await guard(builder, {}, None) is None
    assert e.workflow.plan == PLAN["stages"]
    assert e.workflow.current == "probe"


@pytest.mark.asyncio
async def test_repeated_bypass_and_invalid_plans_cannot_advance():
    e = evidence()
    guard = _delegation_guard(e.trace, e)
    for _ in range(2):
        assert await guard(SimpleNamespace(name="structure_builder"), {}, None)
    with pytest.raises(HarnessStopped, match="bypassed"):
        await guard(SimpleNamespace(name="structure_builder"), {}, None)
    invalid = {**PLAN, "stages": list(reversed(PLAN["stages"]))}
    assert plan(invalid) is None
    assert plan({k: v for k, v in PLAN.items() if k != "delivery"}) is None
    assert (await tool(e, "finish_task", {"result": json.dumps(invalid)}, "Task completed.", role="planner"))["error"]["code"] == "plan_required"
    assert e.workflow.plan is None
    assert not e.workflow.advance(e, "probe")["ok"]


@pytest.mark.asyncio
async def test_stage_gates_require_fresh_execution_validation_and_readback():
    e = evidence()
    await activate(e)
    assert (await validate(e))["error"]["code"] == "stage_incomplete"
    assert (await tool(e, "import_atom_structure_file", {}, {}))["error"]["code"] == "stage_incomplete"
    assert not e.workflow.advance(e, "build")["ok"]
    await execute(e, "probe.py", result={"exit_code": 1})
    assert not e.workflow.advance(e, "probe", "box", "probe.py")["ok"]
    await execute(e, "probe.py", code="print(2)")
    checkpoint = e.workflow.advance(e, "probe", "box", "probe.py")
    assert checkpoint["checkpoint"]["observations"] == '{"counts":12}'
    assert checkpoint["next_stage"] == "build"
    assert not e.workflow.advance(e, "build", "box", "probe.py", "cnt.extxyz")["ok"]
    await validate(e)  # Validation before build's code is not sufficient.
    await execute(e, "build.py")
    assert not e.workflow.advance(e, "build", "box", "build.py", "cnt.extxyz")["ok"]
    await validate(e)
    assert e.workflow.advance(e, "build", "box", "build.py", "cnt.extxyz")["next_stage"] == "deliver"
    assert not e.workflow.advance(e, "deliver", target="structure")["ok"]
    conversion = {"input_structure_file": "cnt.extxyz", 
                  "output_document_file": "cnt.json"}
    await tool(e, "run_skill_script", {"sandbox": "box", "script": "scripts/structure_inspect.py", "argv": ["to_atomsculptor_document"]},
        {"exit_code": 0, "stdout": json.dumps(conversion)})
    await tool(e, "import_atom_structure_file", {"sandbox": "box", "file_name": "cnt.json", "target": "structure", "expected_revision": 1}, {"revision": 2})
    assert not e.workflow.advance(e, "deliver", target="structure")["ok"]
    await tool(e, "inspect_atom_structure", {"target": "structure"}, {"revision": 2})
    assert e.workflow.advance(e, "deliver", target="structure")["next_stage"] == "complete"
    e.require_read_back()


@pytest.mark.asyncio
async def test_async_execution_wait_and_edit_do_not_fake_stage_completion():
    e = evidence()
    await activate(e)
    await execute(e, "probe.py", result={"status": "running", "operation_id": "op"})
    assert not e.workflow.advance(e, "probe", "box", "probe.py")["ok"]
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "op"}, {"exit_code": 0, "command_id": "op"})
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(2)"}, {"written": 8})
    assert not e.workflow.advance(e, "probe", "box", "probe.py")["ok"]


@pytest.mark.asyncio
async def test_failed_validation_cannot_advance_build_and_blocked_report_can_finish():
    e = evidence()
    await activate(e)
    await execute(e, "probe.py")
    e.workflow.advance(e, "probe", "box", "probe.py")
    await execute(e, "build.py")
    assert not e.workflow.advance(e, "build", "box", "build.py", "cnt.extxyz")["ok"]
    assert (await tool(e, "finish_task", {"result": '{"status":"succeeded"}'}, "Task completed."))["error"]["code"] == "stage_incomplete"
    with pytest.raises(HarnessStopped, match="no completed checkpoint"):
        e.require_read_back()
    await tool(e, "finish_task", {"result": '{"status":"blocked","limitations":"Seam construction failed"}'}, "Task completed.")
    assert e.workflow.blocked == "Seam construction failed"
    with pytest.raises(HarnessStopped, match="modelling goal unfinished"):
        e.require_read_back()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["files", "report"])
async def test_file_and_analysis_requests_do_not_require_unrequested_document_writes(delivery):
    e = evidence()
    await activate(e, delivery=delivery)
    await execute(e, "probe.py")
    e.workflow.advance(e, "probe", "box", "probe.py")
    await execute(e, "build.py")
    if delivery == "files":
        await validate(e)
    assert e.workflow.advance(e, "build", "box", "build.py", "cnt.extxyz")["ok"]
    assert e.workflow.advance(e, "deliver")["ok"]
    assert e.imports == {}
    e.require_read_back()


@pytest.mark.asyncio
@pytest.mark.parametrize("as_object", [False, True])
async def test_actual_five_check_planner_submission_is_accepted_without_losing_checks(as_object):
    value = json.loads(LOG_PLAN_PATH.read_text())
    assert len(value["stages"][1]["checks"]) == 5
    assert validate_plan(value)[1] == []
    e = evidence()
    args = {"result": value if as_object else json.dumps(value, ensure_ascii=False)}
    assert await tool(e, "finish_task", args, "Task completed.", role="planner") == "Task completed."
    assert isinstance(args["result"], str)  # Real SDK expects a string.
    assert json.loads(args["result"]) == value
    assert e.workflow.plan == value["stages"] and e.workflow.plan_rejections == 0


@pytest.mark.parametrize("field,invalid,expected", [
    ("checks", ["check"] * 13, "result.stages[1].checks"),
    ("checks", [None], "result.stages[1].checks[0]"),
    ("goal", None, "result.stages[1].goal"),
    ("experiment", {}, "result.stages[1].experiment.artifact"),
    ("experiment", {"artifact": "file.txt", "question": "Q", "method": "M", "stop_condition": "S"},
     "result.stages[1].experiment.artifact"),
])
def test_plan_rejection_identifies_invalid_field_and_declares_actual_limits(field, invalid, expected):
    value = json.loads(json.dumps(PLAN))
    value["stages"][1][field] = invalid
    e = evidence()
    error = e.workflow.before_tool("planner", "finish_task", {"result": json.dumps(value)})["error"]
    assert error["code"] == "plan_required" and error["issues"][0]["field"] == expected
    assert expected in error["message"] and error["limits"] == PLAN_LIMITS
    assert "Unicode is supported" in error["message"]
    assert e.workflow.plan is None


@pytest.mark.parametrize("value,field", [
    ('{"status":', "result"), (json.dumps(json.dumps(PLAN)), "result"),
    ({"status": "planned", "delivery": [], "stages": PLAN["stages"]}, "result.delivery"),
])
def test_invalid_json_encoding_and_delivery_type_have_specific_diagnostics(value, field):
    parsed, issues = validate_plan(value)
    assert parsed is None and issues[0]["field"] == field


def test_check_count_limit_preserves_all_checks_and_is_visible_to_planner():
    value = json.loads(json.dumps(PLAN))
    value["stages"][1]["checks"] = [f"check {index}" for index in range(PLAN_LIMITS["checks_per_stage"])]
    parsed, issues = validate_plan(value)
    assert not issues and parsed["stages"][1]["checks"] == value["stages"][1]["checks"]
    assert evidence().workflow.snapshot()["plan_limits"] == PLAN_LIMITS


def test_different_plan_errors_preserve_corrective_progress():
    e = evidence()
    for key in ("checks", "goal", "experiment"):
        value = json.loads(json.dumps(PLAN))
        value["stages"][1][key] = None
        assert e.workflow.before_tool("planner", "finish_task", {"result": value})["error"]["code"] == "plan_required"
    assert e.workflow.plan_rejections == 3 and e.workflow.repeated_plan_rejections == 1
    e.before_model("planner", SimpleNamespace(config=SimpleNamespace(tools=[])), None, 1024)


def test_same_unresolved_plan_error_still_stops_without_running_science():
    e = evidence(); invalid = json.loads(json.dumps(PLAN)); invalid['stages'][1]['experiment'] = None
    for _ in range(3): e.workflow.before_tool('planner','finish_task',{'result':invalid})
    with pytest.raises(HarnessStopped, match='same unresolved plan issue'):
        e.before_model('planner',SimpleNamespace(),None,1024)
