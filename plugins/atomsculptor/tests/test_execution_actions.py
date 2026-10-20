"""Adversarial regressions from the T-junction run; no model API or sandbox code."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.models import ScopedToolDefinition
from open_agent_world.plugin_api import AgentRuntimeError
from oaw_atomsculptor.harness import HarnessLimits, RunEvidence
from oaw_atomsculptor.model_recovery import bounded_output_model
from oaw_atomsculptor.runtime import _ModelTrace
from test_workflow import PLAN, REQUEST, activate, execute, tool


TOOLS = {"write_sandbox_text_file", "execute_command", "run_skill_script", "read_skill",
         "import_atom_structure_file", "inspect_atom_structure", "inspect_sandbox", "start_sandbox",
         "finish_task", "advance_modelling_stage", "wait_sandbox_operation"}


def setup(*, names=TOOLS, limits=None):
    trace = _ModelTrace(asyncio.Queue())
    evidence = RunEvidence(trace, REQUEST, limits)
    evidence.configure_tools([ScopedToolDefinition("operation:" + name, name, "Fixture") for name in names])
    return evidence


def test_existing_agent_card_deadlines_upgrade_without_mutating_other_limits():
    from oaw_atomsculptor import AtomSculptorAgentConfig
    original = {"model_action_seconds": 60, "action_recovery_seconds": 30,
                "max_model_requests": 45, "max_run_seconds": 1800}
    parsed = AtomSculptorAgentConfig.model_validate({"harness": original}).harness
    runtime_limits = HarnessLimits.model_validate(original)
    assert parsed == runtime_limits
    assert parsed.model_action_seconds == parsed.action_recovery_seconds == 600
    assert parsed.max_model_requests == 45 and parsed.max_run_seconds == 1800
    assert original["model_action_seconds"] == 60 and original["action_recovery_seconds"] == 30
    custom = HarnessLimits(model_action_seconds=120, action_recovery_seconds=90)
    assert custom.model_action_seconds == 120 and custom.action_recovery_seconds == 90


@pytest.mark.asyncio
async def test_serialized_legacy_default_cannot_reenable_a_six_request_cutoff():
    from oaw_atomsculptor import AtomSculptorAgentConfig
    original = HarnessLimits().model_dump()
    original['model_requests_without_progress'] = 6
    e = setup(limits=AtomSculptorAgentConfig.model_validate({'harness': original}).harness)
    assert original['model_requests_without_progress'] == 6
    assert e.limits.model_requests_without_progress is None
    assert HarnessLimits.model_validate(e.limits.model_dump()).model_requests_without_progress is None
    await activate(e)
    await execute(e, 'probe.py')
    for _ in range(12):
        prepare(e, request())
        await tool(e, 'inspect_sandbox', {'sandbox': 'box'}, {'state': 'ready'})
    assert e.actions.requests_without_progress == 12
    assert not e.block_reason and e.time_budget()['effective_brakes']['no_progress_requests'] is None


def request(names=TOOLS):
    return LlmRequest(model="openai/action-fixture", contents=[], config=types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(name=n) for n in sorted(names)])]))


def declarations(req):
    return {d.name for t in req.config.tools for d in t.function_declarations or []}


def prepare(e, req):
    e.before_model("structure_builder", req, SimpleNamespace(types=types), 16384)


def response(name=None, *, truncated=False):
    part = (types.Part(function_call=types.FunctionCall(id="action", name=name, args={})) if name
            else types.Part(thought=True, text="Still deciding topology"))
    return LlmResponse(content=types.Content(role="model", parts=[part]),
        finish_reason="MAX_TOKENS" if truncated else "STOP",
        usage_metadata=types.GenerateContentResponseUsageMetadata(candidates_token_count=11))


@pytest.mark.asyncio
async def test_preflight_blocks_before_builder_and_keeps_authorization_intact():
    e = setup(names=TOOLS - {"run_skill_script", "read_skill"})
    await tool(e, "finish_task", {"result": json.dumps(PLAN)}, "Task completed.", role="planner")
    with pytest.raises(AgentRuntimeError, match="preflight blocked.*read_skill, run_skill_script"):
        e.workflow.accept_delegations(e, "atom_sculptor", [types.Part(function_call=
            types.FunctionCall(name="structure_builder", args={"request": REQUEST}))])
    assert not e.workflow.active and not e.scripts
    assert e.workflow.snapshot()["missing_tools"] == ["read_skill", "run_skill_script"]


@pytest.mark.asyncio
async def test_report_only_preflight_does_not_require_unrequested_structure_tools():
    e = setup(names={"write_sandbox_text_file", "execute_command"})
    await activate(e, delivery="report")
    assert e.workflow.active


@pytest.mark.asyncio
async def test_ready_environment_forces_save_then_execute_and_wait_without_rewrite():
    e = setup()
    await activate(e)
    req = request(); prepare(e, req)
    assert declarations(req) == {"inspect_sandbox", "inspect_atom_structure", "finish_task", "advance_modelling_stage"}
    await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "stopped"})
    req = request(); prepare(e, req)
    assert declarations(req) == {"start_sandbox", "finish_task", "advance_modelling_stage"}
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    assert {"write_sandbox_text_file", "execute_command", "inspect_sandbox", "read_skill"} <= declarations(req)
    assert req.config.tool_config.function_calling_config.mode == "ANY"
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(32)"}, {"written": 9})
    req = request(); prepare(e, req)
    assert {"execute_command", "read_skill", "inspect_sandbox"} <= declarations(req)
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]}, {"status": "running", "operation_id": "op"})
    req = request(); prepare(e, req)
    assert declarations(req) == {"wait_sandbox_operation", "finish_task", "advance_modelling_stage"}
    assert e.actions.requests_without_progress == 4  # Running work freezes the count.
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "op"}, {"exit_code": 0})
    req = request(); prepare(e, req)
    assert declarations(req) == {"advance_modelling_stage", "finish_task", "read_skill", "inspect_sandbox", "inspect_atom_structure"}
    assert e.workflow.advance(e, "observe", "box", "probe.py", hypothesis="Count the boundary",
        measurement="Script returned exit_code=0", outcome="continue", next_step="Compare measured counts")["ok"]
    req = request(); prepare(e, req)
    assert {"write_sandbox_text_file", "execute_command", "read_skill"} <= declarations(req)
    assert e.actions.requests_without_progress == 1  # A first assessment is workflow progress, not task acceptance.


@pytest.mark.asyncio
async def test_missing_start_grant_reports_card_action_before_construction():
    e = setup(names=TOOLS - {"start_sandbox"})
    await activate(e)
    await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "stopped"})
    with pytest.raises(AgentRuntimeError, match="Start/Stop or start it from its card"):
        prepare(e, request())


@pytest.mark.asyncio
async def test_saved_script_repair_gets_observed_hash_but_unknown_file_never_overwrites():
    e = setup(); await activate(e)
    await execute(e, "probe.py", code="print(96)")
    hooks = e.tool_callbacks("structure_builder")
    repair = {"sandbox": "box", "path": "probe.py", "content": "print(16)"}
    await hooks["before_tool_callback"](SimpleNamespace(name="write_sandbox_text_file"), repair,
                                         SimpleNamespace(function_call_id="repair"))
    assert repair["overwrite"] is True
    assert "expected_sha256" not in repair and repair["overwrite"] is True
    unknown = {"sandbox": "box", "path": "unrelated.py", "content": "print(16)"}
    await hooks["before_tool_callback"](SimpleNamespace(name="write_sandbox_text_file"), unknown,
                                         SimpleNamespace(function_call_id="new"))
    assert "overwrite" not in unknown and "expected_sha256" not in unknown
    explicit = {"sandbox": "box", "path": "probe.py", "content": "print(16)", "overwrite": False}
    await hooks["before_tool_callback"](SimpleNamespace(name="write_sandbox_text_file"), explicit,
                                         SimpleNamespace(function_call_id="explicit"))
    assert explicit["overwrite"] is False and "expected_sha256" not in explicit


@pytest.mark.asyncio
async def test_first_failed_execution_refreshes_decision_budget_but_reads_and_empty_scripts_do_not():
    e = setup(limits=HarnessLimits(model_requests_without_progress=3))
    await activate(e)
    assert (await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "empty.py", "content": " \n"},
                       {"written": 2}))["error"]["code"] == "empty_script"
    for _ in range(3):
        prepare(e, request())
        await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "ready"})
    await execute(e, "probe.py", result={"exit_code": 1, "stderr": "Actual failed probe"})
    prepare(e, request())
    assert e.actions.requests_without_progress == 1
    for _ in range(2):
        prepare(e, request())
        await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "ready"})
    with pytest.raises(AgentRuntimeError, match="no new execution/assessment/checkpoint"):
        prepare(e, request())


@pytest.mark.asyncio
async def test_semantic_aliases_support_preflight_filtering_and_receipts():
    e = setup()
    definitions = [ScopedToolDefinition("operation:" + name, "alias_" + name, "Fixture") for name in TOOLS]
    e.configure_tools(definitions)
    await activate(e)
    req = request({d.name for d in definitions}); prepare(e, req)
    assert "alias_inspect_sandbox" in declarations(req)
    await tool(e, "alias_inspect_sandbox", {"sandbox": "box"}, {"state": "ready"})
    await tool(e, "alias_write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}, {"written": 8})
    req = request({d.name for d in definitions}); prepare(e, req)
    assert "alias_execute_command" in declarations(req)
    assert e.scripts[("box", "probe.py")].phase == "written"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "output_limit", "wrong_tool"])
async def test_long_thought_or_disallowed_action_recovers_once_to_real_next_step(kind):
    e = setup(limits=HarnessLimits(model_action_seconds=1, action_recovery_seconds=1))
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    if kind == "wrong_tool":
        e.actions.allowed = {"write_sandbox_text_file"}  # A narrowed, dependency-bound action.
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    calls = 0
    closed = False

    class Fixture(BaseLlm):
        model: str = "openai/action-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal calls, closed
            calls += 1
            if calls == 1:
                if kind == "timeout":
                    try:
                        # An explicitly configured hard ceiling still bounds
                        # an endless active stream, independently of idle.
                        while True:
                            yield LlmResponse(partial=True, content=types.Content(role="model", parts=[types.Part(thought=True, text="thinking ")]))
                            await asyncio.sleep(.05)
                    finally:
                        closed = True
                else:
                    yield response("inspect_sandbox" if kind == "wrong_tool" else None, truncated=kind == "output_limit")
            else:
                assert any(p.text and "HOST EXECUTION ACTION RECOVERY" in p.text for c in req.contents for p in c.parts)
                assert "write_sandbox_text_file" in declarations(req)
                if kind == "output_limit":
                    assert req.config.max_output_tokens == 16384
                yield response("write_sandbox_text_file")

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    accepted = [r async for r in model.generate_content_async(req, stream=True) if not r.partial]
    assert calls == 2 and len(accepted) == 1
    assert accepted[0].content.parts[0].function_call.name == "write_sandbox_text_file"
    assert e.actions.recoveries == (0 if kind == "output_limit" else 1) and not e.scripts
    if kind == "output_limit":
        assert e.output_recoveries["structure_builder"] == 1
    if kind == "timeout":
        assert closed and e.output_tokens == 0  # Interrupted usage unknown, never fabricated.
        await e.trace.callbacks("structure_builder")["after_model_callback"](None, accepted[0])
        from backend.runs.activity import activity_update
        lifecycle = {}
        for index, (_, payload) in enumerate(list(e.trace.queue._queue)):
            lifecycle.update(activity_update(lifecycle, "agent_progress", payload, str(index), "2026-10-05T12:00:00Z"))
        rows = lifecycle["model_requests"]
        assert len(rows) == 2 and all(r["model_attempt"] == 1 for r in rows)
        assert rows[0]["finish_reason"] == "HOST_ACTION_TIMEOUT" and rows[0]["output_tokens"] is None
        assert rows[0]["timeout_kind"] == "request_wall_clock" and rows[0]["deadline_seconds"] == 1
        assert rows[1]["output_tokens"] == 11


@pytest.mark.asyncio
async def test_latest_max_tokens_start_max_tokens_stops_without_replay():
    e = setup()
    await activate(e)
    await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "stopped"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    calls = 0
    limits = []

    class Fixture(BaseLlm):
        model: str = "openai/action-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal calls
            calls += 1
            limits.append(req.config.max_output_tokens)
            yield response("start_sandbox" if calls == 2 else None, truncated=calls != 2)

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    accepted = [r async for r in model.generate_content_async(req)]
    assert len(accepted) == 1
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    with pytest.raises(AgentRuntimeError, match="single bounded recovery"):
        _ = [r async for r in model.generate_content_async(req)]
    assert calls == 3 and not e.scripts and not e.imports
    assert limits == [16384, 16384, 16384]


@pytest.mark.asyncio
async def test_output_recovery_then_advance_continues_as_small_executed_build_steps():
    """Replay the latest failure boundary, without a provider or host execution."""
    e = setup()
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    await execute(e, "probe.py")
    calls = 0
    limits = []

    class Fixture(BaseLlm):
        model: str = "openai/incremental-build-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal calls
            calls += 1
            limits.append(req.config.max_output_tokens)
            if calls == 1:
                yield response(truncated=True)
                return
            if calls == 2:
                name, args = "advance_modelling_stage", {"stage": "probe", "sandbox": "box", "script_path": "probe.py"}
            elif calls == 3:
                assert e.workflow.current == "build"
                assert "write_sandbox_text_file" in declarations(req)
                name, args = "write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": "print('partial counts')"}
            else:
                assert "Execute the saved file build.py" in e.actions.last_action
                name, args = "execute_command", {"sandbox": "box", "argv": ["python3", "build.py"]}
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=
                types.FunctionCall(id=str(calls), name=name, args=args))]), finish_reason="STOP")

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    for result in [None, {"written": 23}, {"exit_code": 0, "stdout": "partial counts"}]:
        req = request(); prepare(e, req)
        await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
        replies = [r async for r in model.generate_content_async(req)]
        assert len(replies) == 1
        call = replies[0].content.parts[0].function_call
        if call.name == "advance_modelling_stage":
            assert e.workflow.advance(e, **call.args)["next_stage"] == "build"
        else:
            await tool(e, call.name, call.args, result, call_id=call.id)
    assert limits == [16384, 16384, 16384, 16384]
    assert e.execution_sequence == 2 and e.output_recoveries == {"structure_builder": 1}
    assert e.workflow.current == "build" and not e.outputs and not e.imports
    assert e.scripts[("box", "probe.py")].version == 1
    req = request(); prepare(e, req)
    assert "Assess the finished experiment build.py" in e.actions.last_action
    assert e.workflow.advance(e, "observe", "box", "build.py", hypothesis="Build one component",
        measurement="Partial build executed", outcome="continue", next_step="Measure next missing part")["ok"]
    req = request(); prepare(e, req)
    assert "ONE remaining" in e.actions.last_action
    assert e.actions.output_token_ceiling is None
    assert not any(path == "probe.py" and stage == "build" for _, path, _, stage in e.actions.successful_scripts)


@pytest.mark.asyncio
async def test_build_feedback_and_pending_collection_preserve_authorized_tools():
    e = setup(); await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    await execute(e, "probe.py"); e.workflow.advance(e, "probe", "box", "probe.py")
    await execute(e, "build.py", result={"status": "running", "operation_id": "build-op"})
    req = request(); prepare(e, req)
    assert declarations(req) == {"wait_sandbox_operation", "finish_task", "advance_modelling_stage"}
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "build-op"},
               {"exit_code": 1, "stderr": "bad boundary count"})
    req = request(); prepare(e, req)
    assert "Assess the finished experiment build.py" in e.actions.last_action
    assert e.workflow.advance(e, "observe", "box", "build.py", hypothesis="Compute boundary",
        measurement="stderr: bad boundary count", outcome="continue", next_step="Repair the failing code")["ok"]
    req = request(); prepare(e, req)
    assert "actual stderr from build.py" in e.actions.last_action
    assert {"read_skill", "write_sandbox_text_file", "execute_command"} <= declarations(req)


@pytest.mark.asyncio
async def test_recovered_output_ceiling_preserves_lower_limits_and_other_roles():
    e = setup(limits=HarnessLimits(recovered_action_output_tokens=3072))
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    e.output_recoveries["structure_builder"] = 1
    req = request(); prepare(e, req)
    assert req.config.max_output_tokens == 3072
    req = request(); req.config.max_output_tokens = 1536; prepare(e, req)
    assert req.config.max_output_tokens == 1536
    for role in ["planner", "materials_project"]:
        req = request(); e.before_model(role, req, SimpleNamespace(types=types), 16384)
        assert req.config.max_output_tokens == 16384
    direct = RunEvidence(_ModelTrace(asyncio.Queue()), REQUEST, direct_builder=True)
    direct.output_recoveries["structure_builder"] = 1
    req = request(); prepare(direct, req)
    assert req.config.max_output_tokens == 16384


@pytest.mark.parametrize("with_recovery", [False, True])
def test_legacy_output_policy_inherits_oaw_selection_without_database_edits(with_recovery):
    original = {"max_output_tokens": 65536, "specialist_output_tokens": 16384,
                "model_action_seconds": 60, "action_recovery_seconds": 30}
    if with_recovery:
        original["recovered_action_output_tokens"] = 4096
    limits = HarnessLimits.model_validate(original)
    assert limits.specialist_output_tokens is None and limits.recovered_action_output_tokens is None
    assert "max_output_tokens" not in type(limits).model_fields
    assert limits.model_action_seconds == limits.action_recovery_seconds == 600
    assert original["specialist_output_tokens"] == 16384 and original["max_output_tokens"] == 65536
    custom = HarnessLimits.model_validate({**original, "max_output_tokens": 100000})
    assert custom.specialist_output_tokens == 16384 and "max_output_tokens" not in type(custom).model_fields
    custom = HarnessLimits.model_validate({**original, "recovered_action_output_tokens": 3072})
    assert custom.recovered_action_output_tokens == 3072 and custom.specialist_output_tokens == 16384


@pytest.mark.asyncio
async def test_selected_81920_survives_output_recovery_and_next_request():
    e = setup(limits=HarnessLimits.model_validate({"max_output_tokens": 65536,
        "specialist_output_tokens": 16384, "recovered_action_output_tokens": 4096}))
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    caps = []

    class Fixture(BaseLlm):
        model: str = "openai/selected-limit-fixture"

        async def generate_content_async(self, req, stream=False):
            caps.append(req.config.max_output_tokens)
            if len(caps) == 1:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(thought=True, text="incomplete")]),
                    finish_reason="MAX_TOKENS", usage_metadata=types.GenerateContentResponseUsageMetadata(candidates_token_count=81920))
            else:
                yield response("write_sandbox_text_file")

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 81920)
    for _ in range(2):
        req = request(); e.before_model("structure_builder", req, SimpleNamespace(types=types), 81920)
        await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
        assert len([r async for r in model.generate_content_async(req)]) == 1
        budget = json.loads(req.contents[-1].parts[0].text.split("\n", 1)[1])["output_budget"]
        assert budget["selected_model_limit"] == budget["effective_request_limit"] == 81920
        assert budget["specialist_limit"] is None and budget["recovered_action_limit"] is None
    assert caps == [81920, 81920, 81920]
    assert e.output_tokens == 81920 and e.output_recoveries == {"structure_builder": 1}
    assert not e.scripts  # No fixture tool is actually dispatched or replayed.
    e.output_tokens = 1048576
    req = request(); e.before_model("structure_builder", req, SimpleNamespace(types=types), 81920)
    assert req.config.max_output_tokens == 81920
    assert e.closing_reason is None
    e.output_tokens += 81920
    req = request(); e.before_model("atom_sculptor", req, SimpleNamespace(types=types), 81920)
    assert req.config.max_output_tokens == 4096  # Explicit coordinator role setting only.


def test_explicit_specialist_cap_is_respected_below_oaw_selection():
    e = setup(limits=HarnessLimits(specialist_output_tokens=8192))
    req = request(); e.before_model("structure_builder", req, SimpleNamespace(types=types), 81920)
    assert req.config.max_output_tokens == 8192


@pytest.mark.asyncio
async def test_running_work_does_not_reset_or_consume_stagnation_budget():
    e = setup(limits=HarnessLimits(model_requests_without_progress=2))
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    prepare(e, request())
    await execute(e, "build.py", result={"status": "running", "operation_id": "op"})
    for _ in range(9):
        prepare(e, request())
        assert e.actions.requests_without_progress == 1
        await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "op"},
                   {"status": "running", "operation_id": "op"})
    assert not e.actions.successful_scripts
    await tool(e, "wait_sandbox_operation", {"sandbox": "box", "operation_id": "op"},
               {"exit_code": -15, "timed_out": True, "stderr": "command deadline"})
    prepare(e, request())
    assert e.actions.requests_without_progress == 1  # First failed result is new evidence.
    prepare(e, request())
    assert e.actions.requests_without_progress == 2
    with pytest.raises(AgentRuntimeError, match="no new execution/assessment/checkpoint"):
        prepare(e, request())


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [None, 37])
async def test_saved_script_deadline_uses_advertised_field_and_preserves_explicit_timeout(explicit):
    e = setup()
    e.configure_tools([ScopedToolDefinition("operation:" + name, name, "Fixture", input_schema=
        {"properties": {"timeout_seconds": {"type": "number"}}} if name == "execute_command" else None)
        for name in TOOLS])
    await activate(e)
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}, {"written": 8})
    args = {"sandbox": "box", "argv": ["python3", "probe.py"]}
    if explicit is not None:
        args["timeout_seconds"] = explicit
    await tool(e, "execute_command", args, {"exit_code": 0})
    assert args["timeout_seconds"] == (120 if explicit is None else explicit)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["finished", "timeout", "permission", "changed_operation", "cancel"])
async def test_host_collects_only_same_operation_without_model_requests_or_replaying_code(outcome):
    from oaw_atomsculptor.actions import collect_script_operation
    from backend.errors import ResourceValidationError
    e = setup(); await activate(e)
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": "print(1)"}, {"written": 8})
    definitions = [ScopedToolDefinition("operation:wait_sandbox_operation", "wait_sandbox_operation", "Fixture")]
    calls = []
    stopped = asyncio.Event()
    initial = {"status": "running", "operation_id": "op", "command_id": "op"}
    async def invoke(cap, args):
        calls.append((cap, args))
        assert args["operation_id"] == "op" and 0 < args["wait_seconds"] <= 30
        if outcome == "permission":
            raise ResourceValidationError("Access revoked")
        if outcome == "changed_operation":
            return {"status": "running", "operation_id": "other"}
        if outcome == "cancel":
            raise asyncio.CancelledError()
        if len(calls) <= 8:
            return initial
        return {"exit_code": 0 if outcome == "finished" else -15, "command_id": "op",
                "timed_out": outcome == "timeout", "stdout": "observed result"}
    args = {"sandbox": "box", "argv": ["python3", "build.py"]}
    if outcome == "permission":
        with pytest.raises(ResourceValidationError, match="revoked"):
            await collect_script_operation(e, definitions, invoke, "execute_command", args, initial, stopped)
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await collect_script_operation(e, definitions, invoke, "execute_command", args, initial, stopped)
    else:
        result = await collect_script_operation(e, definitions, invoke, "execute_command", args, initial, stopped)
        assert result == initial if outcome == "changed_operation" else result["exit_code"] == (0 if outcome == "finished" else -15)
    assert e.model_requests == 0 and "op" in e.pending
    assert e.scripts[("box", "build.py")].phase == "written"  # No invented execution receipt.
    assert all(cap == "operation:wait_sandbox_operation" for cap, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("argv,wait_available", [(["cat", "build.py"], True), (["python3", "unknown.py"], True),
                                               (["python3", "build.py"], False)])
async def test_host_collection_does_not_grant_missing_wait_or_collect_untracked_commands(argv, wait_available):
    from oaw_atomsculptor.actions import collect_script_operation
    e = setup(); await activate(e)
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "build.py", "content": "print(1)"}, {"written": 8})
    definitions = [ScopedToolDefinition("operation:wait_sandbox_operation", "wait_sandbox_operation", "Fixture")] if wait_available else []
    async def invoke(*_):
        pytest.fail("No capability should be invoked")
    initial = {"status": "running", "operation_id": "op"}
    assert await collect_script_operation(e, definitions, invoke, "execute_command",
        {"sandbox": "box", "argv": argv}, initial, asyncio.Event()) == initial
    assert not e.pending


def test_planner_requires_executable_experiments():
    from oaw_atomsculptor.workflow import plan
    invalid = json.loads(json.dumps(PLAN))
    del invalid["stages"][0]["experiment"]
    assert plan(invalid) is None
    invalid = json.loads(json.dumps(PLAN))
    invalid["stages"][1]["experiment"]["stop_condition"] = ""
    assert plan(invalid) is None


@pytest.mark.asyncio
async def test_editing_or_reexecuting_same_source_does_not_refresh_success_progress():
    e = setup(limits=HarnessLimits(model_requests_without_progress=3))
    await activate(e)
    await execute(e, "probe.py")
    prepare(e, request())
    await tool(e, "write_sandbox_text_file", {"sandbox": "box", "path": "probe.py", "content": "print(1)"}, {"written": 8})
    prepare(e, request())
    await tool(e, "execute_command", {"sandbox": "box", "argv": ["python3", "probe.py"]}, {"exit_code": 0})
    prepare(e, request())
    with pytest.raises(AgentRuntimeError, match="no new execution/assessment/checkpoint"):
        prepare(e, request())


@pytest.mark.asyncio
async def test_narrow_tool_choice_reaches_litellm_wire_arguments():
    from google.adk.models.lite_llm import _get_completion_inputs
    e = setup()
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    _, tools, _, _, choice = await _get_completion_inputs(req, "openai/action-fixture")
    assert choice == "required"
    assert {t["function"]["name"] for t in tools} == declarations(req)
    assert {"inspect_sandbox", "read_skill", "execute_command"} <= {t["function"]["name"] for t in tools}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_action_deadline_is_bounded_and_user_cancellation_never_recovers(cancel):
    e = setup(limits=HarnessLimits(model_action_seconds=1, action_recovery_seconds=1))
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    started = asyncio.Event()
    opened = closed = 0

    class Fixture(BaseLlm):
        model: str = "openai/action-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal opened, closed
            opened += 1
            started.set()
            try:
                await asyncio.Event().wait()
                yield response("write_sandbox_text_file")
            finally:
                closed += 1

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    async def consume():
        return [r async for r in model.generate_content_async(req)]
    task = asyncio.create_task(consume())
    await started.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert e.actions.recoveries == 0 and opened == closed == 1
    else:
        with pytest.raises(AgentRuntimeError, match="bounded action recovery"):
            await asyncio.wait_for(task, timeout=4)
        assert e.actions.recoveries == 1 and opened == closed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_seconds", [1, 3])
async def test_successful_recovery_does_not_shorten_next_action(monkeypatch, recovery_seconds):
    """Replay inspect -> correction -> start -> compute from the latest Run."""
    import oaw_atomsculptor.model_recovery as recovery
    e = setup(limits=HarnessLimits(model_action_seconds=2, action_recovery_seconds=recovery_seconds))
    await activate(e)
    await tool(e, "inspect_sandbox", {"sandbox": "box"}, {"state": "stopped"})
    observed_limits = []
    original_timeout = asyncio.timeout

    def record_timeout(seconds):
        observed_limits.append(seconds)
        return original_timeout(seconds)

    monkeypatch.setattr(recovery.asyncio, "timeout", record_timeout)
    calls = 0

    class Fixture(BaseLlm):
        model: str = "openai/action-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal calls
            calls += 1
            yield response(None if calls == 1 else "start_sandbox" if calls == 2 else "execute_command")

    model = bounded_output_model(Fixture(), e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    accepted = [r async for r in model.generate_content_async(req)]
    await e.trace.callbacks("structure_builder")["after_model_callback"](None, accepted[0])
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    accepted = [r async for r in model.generate_content_async(req)]
    assert accepted[0].content.parts[0].function_call.name == "execute_command"
    assert observed_limits == [2, max(2, recovery_seconds), 2]
    assert e.actions.recoveries == 1 and calls == 3 and not e.scripts


@pytest.mark.asyncio
async def test_active_reasoning_outlives_old_ceiling_with_native_idle_watchdog(monkeypatch):
    """Scaled time: content continues past 60s, completes at about 120s."""
    from backend.agents import model_observation
    from backend.agents.request_recovery import recoverable_model
    import oaw_atomsculptor.model_recovery as recovery
    e = setup()
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    original_timeout = asyncio.timeout
    monkeypatch.setattr(recovery.asyncio, "timeout", lambda seconds: original_timeout(seconds / 1000))
    monkeypatch.setattr(model_observation, "STREAM_STALL_SECONDS", .09)
    monkeypatch.setattr(model_observation, "STREAM_FIRST_BYTE_SECONDS", .3)
    closed = 0

    class Fixture(BaseLlm):
        model: str = "openai/action-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal closed
            try:
                for _ in range(12):
                    yield LlmResponse(partial=True, content=types.Content(role="model", parts=[
                        types.Part(thought=True, text="Developing the executable probe. ")]))
                    await asyncio.sleep(.01)
                yield response("write_sandbox_text_file")
            finally:
                closed += 1

    model = bounded_output_model(recoverable_model(Fixture(), e.trace, "structure_builder"),
        e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    accepted = [r async for r in model.generate_content_async(req, stream=True) if not r.partial]
    assert len(accepted) == 1 and accepted[0].content.parts[0].function_call.name == "write_sandbox_text_file"
    assert e.actions.recoveries == 0 and closed == 1 and not e.scripts
    assert not any(p.get("finish_reason") == "HOST_ACTION_TIMEOUT" for _, p in e.trace.queue._queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_keepalive", [False, True])
async def test_native_idle_recovery_owns_silence_instead_of_action_retry(monkeypatch, empty_keepalive):
    from backend.agents import model_observation
    from backend.agents.request_recovery import recoverable_model, RequestRecoveryExhausted, RequestRetryPolicy
    e = setup()
    await activate(e)
    await tool(e, "start_sandbox", {"sandbox": "box"}, {"state": "ready"})
    req = request(); prepare(e, req)
    await e.trace.callbacks("structure_builder")["before_model_callback"](None, req)
    monkeypatch.setattr(model_observation, "STREAM_STALL_SECONDS", .05)
    monkeypatch.setattr(model_observation, "STREAM_FIRST_BYTE_SECONDS", .05)
    opened = closed = 0

    class Fixture(BaseLlm):
        model: str = "openai/action-fixture"

        async def generate_content_async(self, req, stream=False):
            nonlocal opened, closed
            opened += 1
            try:
                if not empty_keepalive:
                    yield LlmResponse(partial=True, content=types.Content(role="model", parts=[
                        types.Part(thought=True, text="Starting probe.")]))
                while True:
                    await asyncio.sleep(.01)
                    yield LlmResponse(partial=True, content=types.Content(role="model", parts=[]))
            finally:
                closed += 1

    policy = RequestRetryPolicy(initial_delay_seconds=0.0)
    model = bounded_output_model(recoverable_model(Fixture(), e.trace, "structure_builder", policy),
        e.trace, "structure_builder", e, SimpleNamespace(types=types), 16384)
    with pytest.raises(RequestRecoveryExhausted):
        _ = [r async for r in model.generate_content_async(req, stream=True)]
    diagnostics = [p for _, p in e.trace.queue._queue if p.get("phase") == "model_recovery"]
    assert opened == closed == policy.max_attempts and e.actions.recoveries == 0
    assert len(diagnostics) == policy.max_attempts
    assert all(p["stage"] == ("first_byte" if empty_keepalive else "mid_stream") for p in diagnostics)
    assert not e.scripts and not e.imports
    assert e.trace.recovery_deadlines == {}
