"""Exercise actual task-mode handoff with durable context, without model APIs."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.context import Checkpoint
from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection
from oaw_atomsculptor.runtime import AtomSculptorRuntime, _capture_delegation_result

TYPED_REQUEST = ('Complete the assigned operation.\nATOMSCULPTOR REQUEST\n'
    '{"operation":"supercell","structure_card":"structure","parameters":{"repetitions":[1,1,1]}}\n'
    'END ATOMSCULPTOR REQUEST')


class MemoryStore:
    def lock(self, *_):
        return asyncio.Lock()

    def load(self, *_):
        return Checkpoint()

    def session(self, *_):
        return None

    def save(self, *_, session_context_id=None):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("delegations", [1, 2])
async def test_team_returns_real_task_results_to_coordinator(managed, delegations):
    root_turns = 0
    builder_turns = 0
    seen = []

    class Fixture(BaseLlm):
        model: str = "openai/team-fixture"

        async def generate_content_async(self, request, stream=False):
            nonlocal root_turns, builder_turns
            evidence = [p.text.split("\n", 1)[1] for c in request.contents for p in c.parts
                        if p.text and p.text.startswith("HOST RUN EVIDENCE")]
            assert evidence
            snapshot = json.loads(evidence[-1])
            stable = [json.loads(p.text.split("\n", 1)[1]) for c in request.contents for p in c.parts if p.text and p.text.startswith("HOST STABLE CONTEXT")][-1]
            assert stable["original_user_request"] == TYPED_REQUEST
            assert "Current speaker" not in stable["original_user_request"]
            declarations = [d.name for t in request.config.tools or [] for d in t.function_declarations or []]
            if "finish_task" in declarations:
                builder_turns += 1
                part = types.Part(function_call=types.FunctionCall(id=f"finish-{builder_turns}",
                    name="finish_task", args={"result": f"Verified result {builder_turns}"}))
            else:
                # Every completed delegation survives SDK -> ManagedContext
                # reconstruction, with the actual result and original call ID.
                responses = [p.function_response for c in request.contents for p in c.parts
                             if p.function_response and p.function_response.name == "structure_builder"]
                for i in range(root_turns):
                    matches = [r for r in responses if r.id == f"delegate-{i+1}"]
                    assert len(matches) == 1
                    assert matches[0].response == {"result": f"Verified result {i+1}"}
                seen.append(len(responses))
                root_turns += 1
                part = (types.Part(function_call=types.FunctionCall(id=f"delegate-{root_turns}",
                    name="structure_builder", args={"request": f"Task {root_turns}"}))
                    if root_turns <= delegations else types.Part(text="Verified task completed."))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    class Provider:
        async def list_tools(self, _):
            return []

    resolver = SimpleNamespace(
        resolve_runtime=lambda _: RuntimeModelConnection("openai", "team-fixture", "https://model.invalid", None),
        context_limits=lambda _: (32768, 2048))
    runtime = AtomSculptorRuntime(Provider(), context_store=MemoryStore() if managed else None,
                                model_connection_resolver=resolver)
    runtime._model = lambda *_: Fixture()
    config = AgentConfig("fixture", "Fixture")
    await runtime.create_agent(config)
    events = [e async for e in runtime.execute(config,
        SimpleNamespace(run_id="fixture-run", context_id="fixture-context"),
        SimpleNamespace(prompt="Current speaker: User. " + TYPED_REQUEST,
                        task_prompt=TYPED_REQUEST))]
    assert events[-1].run_status == "succeeded"
    assert builder_turns == delegations and root_turns == delegations + 1
    assert seen == list(range(delegations + 1))


@pytest.mark.asyncio
async def test_deferred_task_marker_is_not_a_completed_result():
    completed = {}
    await _capture_delegation_result(completed)(SimpleNamespace(name="structure_builder"), {},
        SimpleNamespace(function_call_id="pending"), None)
    assert completed == {}
