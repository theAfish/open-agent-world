"""Exercise the shared boundary through the actual Codex subprocess bridge."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from backend.agents import AgentConfig, AgentEventType
from backend.errors import PermissionDeniedError
from backend.runs import InvocationCaller, InvocationContext, RuntimeInput
from oaw_codex.runtime import CodexRuntime


class FailingCapabilities:
    def __init__(self, failure):
        self.failure = failure
        self.calls = 0

    async def list_tools(self, agent_id):
        return ()

    async def invoke_tool(self, agent_id, capability_id, arguments):
        self.calls += 1
        if isinstance(self.failure, BaseException):
            raise self.failure
        if self.failure is not None:
            return self.failure
        return {"content": "updated"}


def invocation(run_id):
    return InvocationContext(run_id, "agent1", None, run_id,
                             InvocationCaller("test"), "session1", None, "openai.codex")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    TypeError("private plugin failure"), TimeoutError("private timeout"),
    asyncio.CancelledError(), PermissionDeniedError("capability revoked"),
    {"ok": False, "error": {"code": "rejected", "message": "request rejected"}},
    object(),
])
async def test_codex_tool_failure_is_feedback_and_following_run_recovers(tmp_path, failure):
    capabilities = FailingCapabilities(failure)
    fixture = Path(__file__).resolve().parents[2] / "plugins/codex/tests/fake_server.py"
    runtime = CodexRuntime(capabilities, state_directory=tmp_path / "state",
                           server_command=[sys.executable, str(fixture), str(tmp_path / "protocol.jsonl")])
    config = AgentConfig("agent1", "Codex", model="default", runtime_provider_id="openai.codex",
                         provider_config={"workspace_path": str(tmp_path)})
    await runtime.create_agent(config)

    async def collect(run_id, prompt):
        return [event async for event in runtime.execute(config, invocation(run_id), RuntimeInput(prompt))]

    # The fixture checks that the failed invocation receives success=False and
    # then emits a normal final model response, without restarting the runtime.
    events = await asyncio.wait_for(collect("run1", "revoke"), timeout=10)
    started = [e for e in events if e.type == AgentEventType.TOOL_STARTED]
    completed = [e for e in events if e.type == AgentEventType.TOOL_COMPLETED]
    assert [e.payload["call_id"] for e in started] == ["call-1", "call-2"]
    assert [e.payload["call_id"] for e in completed] == ["call-1", "call-2"]
    assert completed[-1].payload["success"] is False
    assert json.loads(completed[-1].payload["response"])["ok"] is False
    assert "private" not in completed[-1].payload["response"]
    assert events[-1].run_status == "succeeded"
    assert capabilities.calls == 1
    assert not runtime.active

    capabilities.failure = None
    recovered = await asyncio.wait_for(collect("run2", "tool"), timeout=10)
    assert recovered[-1].run_status == "succeeded"
    assert capabilities.calls == 2
    assert not runtime.active
