"""Actual ADK tasks with fixture model responses; no paid API or real writes."""
import asyncio
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.models import ScopedToolDefinition
from open_agent_world.plugin_api import AgentConfig, AgentRuntimeError, RuntimeModelConnection
from oaw_atomsculptor.model_recovery import RECOVERY_INSTRUCTION
from oaw_atomsculptor.runtime import AtomSculptorRuntime
from test_team_closeout import MemoryStore, TYPED_REQUEST


def reply(part, *, truncated=False, tokens=7):
    return LlmResponse(content=types.Content(role="model", parts=[part]),
        finish_reason=types.FinishReason.MAX_TOKENS if truncated else types.FinishReason.STOP,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=tokens))


def setup_runtime(model, *, managed=False):
    dispatched = []

    class Provider:
        async def list_tools(self, _):
            # Fake tool: only record dispatch; no process or filesystem effects.
            return [ScopedToolDefinition("fixture-probe", "execute_command", "Fixture probe")]

        async def invoke_tool(self, agent, capability, arguments):
            dispatched.append((agent, capability, arguments))
            return {"exit_code": 0, "stdout": "Observed lattice counts."}

    resolver = SimpleNamespace(
        resolve_runtime=lambda _: RuntimeModelConnection("openai", "output-fixture", "https://model.invalid", None),
        context_limits=lambda _: (32768, 2048))
    runtime = AtomSculptorRuntime(Provider(), context_store=MemoryStore() if managed else None,
                                model_connection_resolver=resolver)
    runtime._model = lambda *_: model
    return runtime, dispatched


async def run(runtime, config, events):
    await runtime.create_agent(config)
    async for event in runtime.execute(config,
        SimpleNamespace(run_id="fixture-run", context_id="fixture-context"),
        SimpleNamespace(prompt=TYPED_REQUEST, task_prompt=TYPED_REQUEST)):
        events.append(event)


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("mode", ["team", "builder"])
@pytest.mark.parametrize("fails_again", [False, True])
async def test_truncated_reasoning_recovers_inside_task_without_replaying_tools(managed, mode, fails_again):
    builder_turns = root_turns = 0
    request_caps = []

    class Fixture(BaseLlm):
        model: str = "openai/output-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal builder_turns, root_turns
            names = [d.name for t in request.config.tools or [] for d in t.function_declarations or []]
            if mode == "builder" or "finish_task" in names:
                builder_turns += 1
                request_caps.append(request.config.max_output_tokens)
                if builder_turns == 1:
                    part = types.Part(function_call=types.FunctionCall(id="probe-1", name="execute_command", args={}))
                elif builder_turns == 2 or fails_again:
                    yield reply(types.Part(thought=True, text="Repeated topology speculation..."), truncated=True, tokens=1024)
                    return
                else:
                    # Existing probe receipt/signature and call ID are unchanged.
                    responses = [p.function_response for c in request.contents for p in c.parts if p.function_response]
                    assert any(r.id == "probe-1" and r.response["stdout"] == "Observed lattice counts." for r in responses)
                    assert any(p.text == RECOVERY_INSTRUCTION for c in request.contents for p in c.parts)
                    assert not any(p.text == "Repeated topology speculation..." for c in request.contents for p in c.parts)
                    part = (types.Part(function_call=types.FunctionCall(id="finish-1", name="finish_task",
                        args={"result": "Blocked: construction not yet executed."})) if mode == "team"
                        else types.Part(text="Blocked: construction not yet executed."))
            else:
                root_turns += 1
                part = (types.Part(function_call=types.FunctionCall(id="delegate-1", name="structure_builder",
                    args={"request": "Construct the assigned junction."})) if root_turns == 1
                    else types.Part(text="Blocked: construction not yet executed."))
            yield reply(part)

    runtime, dispatched = setup_runtime(Fixture(), managed=managed)
    config = AgentConfig("fixture", "Fixture", provider_config={"execution_mode": mode})
    events = []
    if fails_again:
        await run(runtime, config, events)
        assert events[-1].run_status == "failed"
        assert "single bounded recovery" in events[-1].payload['error']
        assert "Task unfinished" in events[-1].payload['text']
        assert any(e.payload.get('kind') == 'execution_closeout' for e in events)
    else:
        await run(runtime, config, events)
        assert events[-1].run_status == "succeeded"
        assert "Blocked" in events[-1].payload["text"]  # No invented successful geometry.
    assert builder_turns == 3 and len(dispatched) == 1
    assert len(set(request_caps)) == 1  # No increase in output allowance.
    returns = [e.payload for e in events if e.payload.get("elapsed_ms") is not None and e.payload.get("phase") == "model"]
    assert len(returns) == builder_turns + root_turns
    assert sum(r["output_tokens"] for r in returns) == (2048 + 7 + root_turns * 7 if fails_again else 1024 + 14 + root_turns * 7)
    assert len([e for e in events if e.payload.get("recovery_event") == "model_output_recovery"]) == 1
    assert runtime.active == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["truncated_call", "empty_stop", "output_budget"])
async def test_invalid_reply_stops_with_specific_reason_and_no_dispatch(case):
    calls = 0

    class Fixture(BaseLlm):
        model: str = "openai/output-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal calls
            calls += 1
            part = (types.Part(function_call=types.FunctionCall(id="unfinished", name="execute_command", args={}))
                    if case == "truncated_call" else types.Part(thought=True, text="No action."))
            yield reply(part, truncated=case != "empty_stop", tokens=1024)

    runtime, dispatched = setup_runtime(Fixture())
    harness = {"max_output_tokens": 1024} if case == "output_budget" else {}
    config = AgentConfig("fixture", "Fixture", provider_config={"execution_mode": "builder", "harness": harness})
    expected = {"truncated_call": "truncated tool calls were not dispatched",
                "empty_stop": "no final text or tool call", "output_budget": "single bounded recovery"}[case]
    if case == "output_budget":
        events = []
        await run(runtime, config, events)
        assert events[-1].run_status == "failed"
        assert expected in events[-1].payload["error"] and "Task unfinished" in events[-1].payload["text"]
    else:
        with pytest.raises(AgentRuntimeError, match=expected):
            await run(runtime, config, [])
    assert calls == (2 if case == "output_budget" else 1) and dispatched == [] and runtime.active == {}


@pytest.mark.asyncio
async def test_later_model_turn_does_not_reset_output_recovery_allowance():
    calls = 0

    class Fixture(BaseLlm):
        model: str = "openai/output-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal calls
            calls += 1
            if calls == 2:
                yield reply(types.Part(function_call=types.FunctionCall(id="probe", name="execute_command", args={})))
            else:
                yield reply(types.Part(thought=True, text="Still deriving."), truncated=True, tokens=1024)

    runtime, dispatched = setup_runtime(Fixture())
    config = AgentConfig("fixture", "Fixture", provider_config={"execution_mode": "builder"})
    events = []
    await run(runtime, config, events)
    assert events[-1].run_status == 'failed' and 'single bounded recovery' in events[-1].payload['error']
    assert calls == 3 and len(dispatched) == 1
    assert len([e for e in events if e.payload.get("recovery_event") == "model_output_recovery"]) == 1


@pytest.mark.asyncio
async def test_stop_cancels_model_recovery_without_dispatching_tools():
    started = asyncio.Event()
    released = asyncio.Event()
    calls = 0

    class Fixture(BaseLlm):
        model: str = "openai/output-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield reply(types.Part(thought=True, text="Still deriving."), truncated=True, tokens=1024)
            else:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    released.set()

    runtime, dispatched = setup_runtime(Fixture())
    config = AgentConfig("fixture", "Fixture", provider_config={"execution_mode": "builder"})
    task = asyncio.create_task(run(runtime, config, []))
    await asyncio.wait_for(started.wait(), timeout=5)
    await runtime.stop("fixture-run")
    with pytest.raises(AgentRuntimeError, match="stopped"):
        await asyncio.wait_for(task, timeout=5)
    assert released.is_set() and dispatched == [] and runtime.active == {}
