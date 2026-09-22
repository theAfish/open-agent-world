from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from pydantic import PrivateAttr

from backend.agents import AgentConfig, GoogleAdkAgentRuntime, MockAgentRuntime
from backend.agents.context import ContextBudget, ContextStatus, ManagedContext, SUMMARY_INSTRUCTION, encoded, estimate, text_content
from backend.config import Settings
from backend.conversations import ConversationSessionCreate, ConversationPost
from backend.runs import InvocationCaller, InvocationContext, RuntimeInput
from backend.services import create_services
from backend.security.model_connections import CatalogEdit
from backend.world.models import CardCreate, EdgeCreate
from backend.tests.test_agent_runtime import MutableCapabilityProvider


class ScriptedModel(BaseLlm):
    model: str = "openai/context-test"
    _requests: list = PrivateAttr(default_factory=list)
    _summaries: list = PrivateAttr(default_factory=list)
    _output_limits: list = PrivateAttr(default_factory=list)
    _tool_rounds: int = PrivateAttr(default=0)
    _failure: bool = PrivateAttr(default=False)
    _block: asyncio.Event | None = PrivateAttr(default=None)

    async def generate_content_async(self, request, stream=False):
        wire = encoded([content.model_dump(mode="json", exclude_none=True) for content in request.contents])
        if request.config.system_instruction == SUMMARY_INSTRUCTION:
            self._summaries.append(wire)
            assert estimate(wire) < 8192
            if self._block:
                await self._block.wait()
            if self._failure:
                raise RuntimeError("summary service unavailable")
            text = "Goal: finish the analysis. Confirmed code: cobalt-731. Decision: preserve output at sandbox://lab/result.csv. Next: answer using retained facts."
            if "Outcome is unknown" in wire:
                text += " Outcome is unknown for the interrupted tool; inspect state before retrying."
        else:
            self._requests.append(wire)
            self._output_limits.append(request.config.max_output_tokens)
            if self._tool_rounds > 0:
                self._tool_rounds -= 1
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_function_call(
                    name="replace_notes", args={"content": "cobalt-731 " + "tool data " * 1800})]))
                return
            text = "The confirmed code is cobalt-731." if "cobalt-731" in wire else "No code available."
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))


@pytest.fixture
def services(data_root):
    result = create_services(Settings.for_data_root(data_root))
    yield result
    result.close()


async def setup_context(services, monkeypatch):
    agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
    peer = await services.create_card(CardCreate(type="agent", name="Boreal"))
    room = await services.create_card(CardCreate(type="conversation", name="Research"))
    session = services.conversations.create_session(room.id, ConversationSessionCreate(title="Research", participant_ids=[agent.id, peer.id]))
    model = ScriptedModel()
    monkeypatch.setattr(ContextBudget, "for_model", classmethod(lambda cls, model: cls(8192, 1024)))
    runtime = GoogleAdkAgentRuntime(MutableCapabilityProvider(), context_store=services.contexts)
    monkeypatch.setattr(runtime, "_adk_model", lambda model_id: model)
    config = AgentConfig(agent_id=agent.id, name="Atlas")
    await runtime.create_agent(config)
    services.install_runtime_provider("google.adk", runtime, default=True)
    runtime.model_connections.save(CatalogEdit(connections=[dict(id="scripted", name="Scripted", adapter="openai",
        base_url="http://model.test", auth_mode="none", models=[dict(id="scripted", name="Scripted", model_id="context-test",
        context_window=8192, max_output_tokens=1024)])], default_model="oaw:model:scripted"))
    return agent, peer, room, session, model, runtime, config


def invocation(agent_id, session_id, run_id="run-1"):
    return InvocationContext(run_id=run_id, agent_id=agent_id, parent_run_id=None,
        root_run_id=run_id, caller=InvocationCaller("test"), context_id=session_id,
        task_id=None, runtime_provider_id="google.adk")


@pytest.mark.asyncio
async def test_private_delegation_context_does_not_replay_parent_conversation(services, monkeypatch):
    agent, _, room, session, model, runtime, config = await setup_context(services, monkeypatch)
    add_history(services, room, session, count=2)
    private = invocation(agent.id, "summon:isolated-task")
    result = [event async for event in runtime.execute(config, private, RuntimeInput("Inspect only the assigned file"))]
    assert result
    assert "cobalt-731" not in model._requests[-1]
    assert "Inspect only the assigned file" in model._requests[-1]


@pytest.mark.asyncio
async def test_saved_limits_drive_pressure_output_and_next_run_compaction(services, monkeypatch):
    agent, _, room, session, model, runtime, config = await setup_context(services, monkeypatch)
    add_history(services, room, session, count=30)
    def no_metadata(name):
        raise AssertionError("Catalog models must use saved limits without metadata detection")
    monkeypatch.setattr(ContextBudget, "for_model", no_metadata)
    draft = runtime.model_connections.read().model_dump()
    draft["connections"][0]["models"][0].update(context_window=128000, max_output_tokens=16384)
    runtime.model_connections.save(CatalogEdit.model_validate(draft))
    result = [event async for event in runtime.execute(config, invocation(agent.id, session.id), RuntimeInput("Continue"))]
    assert result[-1].run_status == "succeeded"
    assert model._output_limits[-1] == 16384  # Not silently capped to the former 8K policy.
    assert not model._summaries
    first = services.contexts.statuses(room.id)[session.id][agent.id]
    assert first.context_limit == 128000
    draft = runtime.model_connections.read().model_dump()
    draft["connections"][0]["models"][0].update(context_window=8192, max_output_tokens=2048)
    runtime.model_connections.save(CatalogEdit.model_validate(draft))
    result = [event async for event in runtime.execute(config, invocation(agent.id, session.id, "next"), RuntimeInput("Continue"))]
    assert result[-1].run_status == "succeeded"
    assert model._output_limits[-1] == 2048
    assert services.contexts.statuses(room.id)[session.id][agent.id].context_limit == 8192
    assert services.contexts.load(agent.id, session.id).compaction_count > 0
    assert len(services.conversations.list_messages(room.id, session.id, limit=100)) == 30


def add_history(services, room, session, count=65):
    for index in range(count):
        services.conversations.add_message(room.id, session.id, sender_kind="user", sender_id=None,
            sender_name="You", content=("Confirmed code: cobalt-731; sandbox://lab/result.csv. " if index == 0 else "") + f"Entry {index}. " + "historical detail " * 28)


@pytest.mark.asyncio
async def test_real_adk_long_conversation_compacts_and_continues_without_losing_history(services, monkeypatch):
    agent, peer, room, session, model, runtime, config = await setup_context(services, monkeypatch)
    add_history(services, room, session)
    before = services.conversations.list_messages(room.id, session.id, limit=100)
    async with services.events.subscribe() as queue:
        result = [event async for event in runtime.execute(config, invocation(agent.id, session.id), RuntimeInput("What is the confirmed code?"))]
        statuses = []
        while not queue.empty():
            event = queue.get_nowait()
            if event.type == "context_status":
                statuses.append(event.payload["context_status"])
    assert result[-1].payload["text"] == "The confirmed code is cobalt-731."
    assert model._summaries and "Entry 0" in model._summaries[0]
    assert any(status["state"] == "compacting" for status in statuses)
    assert statuses[-1]["pressure"] < .65
    assert statuses[-1]["compaction_count"] == 1
    assert services.conversations.list_messages(room.id, session.id, limit=100) == before
    checkpoint = services.contexts.load(agent.id, session.id)
    assert "sandbox://lab/result.csv" in checkpoint.summary
    assert checkpoint.cursor == 65
    assert services.contexts.load(peer.id, session.id).compaction_count == 0
    assert services.contexts.load(agent.id, "other-session").compaction_count == 0
    # The next run ingests only new canonical messages, never replaying 40 messages.
    result = [event async for event in runtime.execute(config, invocation(agent.id, session.id, "run-2"), RuntimeInput("Repeat the code."))]
    assert result[-1].payload["text"] == "The confirmed code is cobalt-731."
    assert model._requests[-1].count("Entry 64") == 1


@pytest.mark.asyncio
async def test_real_adk_long_tool_run_compacts_repeatedly_and_preserves_pairs(services, monkeypatch):
    agent, _, _, session, model, runtime, config = await setup_context(services, monkeypatch)
    model._tool_rounds = 3
    result = [event async for event in runtime.execute(config, invocation(agent.id, session.id), RuntimeInput("Use the notes and finish the analysis."))]
    assert result[-1].payload["text"] == "The confirmed code is cobalt-731."
    assert len(runtime._provider.invocations) == 3
    assert services.contexts.load(agent.id, session.id).compaction_count >= 2
    for request in model._requests:
        assert request.count('"function_call"') == request.count('"function_response"')


@pytest.mark.asyncio
async def test_restart_restores_checkpoint_tail_count_and_session_projection(data_root, services, monkeypatch):
    agent, peer, room, session, model, runtime, config = await setup_context(services, monkeypatch)
    add_history(services, room, session)
    _ = [event async for event in runtime.execute(config, invocation(agent.id, session.id), RuntimeInput("What is the code?"))]
    before = services.contexts.load(agent.id, session.id)
    # Separate service container/SQLite connection models a backend restart.
    restored = create_services(Settings.for_data_root(data_root), default_runtime_provider_id="google.adk")
    try:
        assert restored.contexts.load(agent.id, session.id) == before
        assert restored.conversation_summary(room.id).context_statuses[session.id][agent.id].compaction_count == 1
        new_runtime = GoogleAdkAgentRuntime(MutableCapabilityProvider(), context_store=restored.contexts)
        monkeypatch.setattr(new_runtime, "_adk_model", lambda model_id: model)
        await new_runtime.create_agent(config)
        result = [event async for event in new_runtime.execute(config, invocation(agent.id, session.id, "after-restart"), RuntimeInput("Repeat the code."))]
        assert result[-1].payload["text"] == "The confirmed code is cobalt-731."
        assert len(restored.conversations.list_messages(room.id, session.id, limit=100)) == 65
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_failure_and_cancellation_keep_checkpoint_and_clear_compacting(services, monkeypatch):
    agent, _, room, session, model, _, _ = await setup_context(services, monkeypatch)
    add_history(services, room, session)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Finish")
    original = list(managed.checkpoint.contents)
    model._failure = True
    with pytest.raises(RuntimeError, match="unavailable"):
        await managed.before_model(None, LlmRequest())
    assert managed.checkpoint.contents == original
    assert managed.checkpoint.compaction_count == 0
    assert services.contexts.statuses(room.id)[session.id][agent.id].state == "high"
    model._failure = False
    model._block = asyncio.Event()
    task = asyncio.create_task(managed.before_model(None, LlmRequest()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert services.contexts.load(agent.id, session.id).contents == original
    assert services.contexts.statuses(room.id)[session.id][agent.id].state == "high"


@pytest.mark.asyncio
async def test_usage_metadata_and_bucketed_events(services, monkeypatch):
    agent, _, _, session, model, _, _ = await setup_context(services, monkeypatch)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Finish")
    async with services.events.subscribe() as queue:
        await managed.after_model(None, SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=3000), content=None))
        assert managed.status.estimated_tokens == 3000
        for _ in range(10):
            managed.publish()
        assert queue.qsize() == 1
        managed.observe(SimpleNamespace(partial=True, content=None))
        assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_plugin_ownership_and_independent_statuses(services, monkeypatch):
    agent, peer, room, session, model, _, _ = await setup_context(services, monkeypatch)
    second = services.conversations.create_session(room.id, ConversationSessionCreate(title="Other", participant_ids=[agent.id]))
    for actor, scope, pressure in [(agent.id, session.id, .8), (peer.id, session.id, .2), (agent.id, second.id, .4)]:
        services.contexts.save(actor, scope, services.contexts.load(actor, scope), ContextStatus(pressure=pressure))
    summary = services.conversation_summary(room.id)
    assert summary.context_statuses[session.id][agent.id].pressure == .8
    assert summary.context_statuses[session.id][peer.id].pressure == .2
    assert summary.context_statuses[second.id][agent.id].pressure == .4
    plugin = MockAgentRuntime(MutableCapabilityProvider())
    services.install_runtime_provider("plugin.test", plugin, default=True)
    assert not services.run_manager.uses_oaw_context(agent)
    assert summary.context_statuses and not any(services.conversation_summary(room.id).context_statuses.values())
    # Even replacing the google.adk ID with a different implementation cannot opt it in.
    services.install_runtime_provider("google.adk", plugin, default=True)
    assert not services.run_manager.uses_oaw_context(agent)
    assert not hasattr(plugin, "context_store")


@pytest.mark.asyncio
async def test_session_deletion_cascades_private_context_only(services, monkeypatch):
    agent, _, room, session, model, _, _ = await setup_context(services, monkeypatch)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Finish")
    managed.publish()
    services.conversations.delete_session(room.id, session.id)
    assert not services.contexts.load(agent.id, session.id).initialized


@pytest.mark.asyncio
async def test_interrupted_tool_call_is_repaired_without_reexecution(services, monkeypatch):
    agent, _, _, session, model, _, _ = await setup_context(services, monkeypatch)
    checkpoint = services.contexts.load(agent.id, session.id)
    checkpoint.contents = [{"role": "model", "parts": [{"function_call": {"name": "write_file", "id": "call-1", "args": {}}}]}]
    services.contexts.save(agent.id, session.id, checkpoint, ContextStatus())
    managed = ManagedContext(services.contexts, agent.id, session.id, "retry", model, "Continue")
    repaired = managed.checkpoint.contents[-1]["parts"][0]["function_response"]
    assert repaired["id"] == "call-1"
    assert "Outcome is unknown" in repaired["response"]["error"]


def test_unknown_model_has_conservative_internal_budget():
    assert ContextBudget.for_model("unlisted/private-model").limit == 32768
    assert not ContextBudget.for_model("unlisted/private-model").known
    assert ContextBudget.for_model("openai/gpt-4o").limit > 8192
    assert ContextBudget.for_model("anthropic/claude-sonnet-4-20250514").limit > 8192


def test_compatible_model_names_resolve_windows_without_overriding_provider_limits(monkeypatch):
    from litellm import model_cost
    for name in ("openai/DeepSeek-V4.1-Flash", "openai/deepseek-ai/DeepSeek-V4.1-Flash",
                 "deepseek/deepseek-flash", "DeepSeek-V4.1-Flash"):
        budget = ContextBudget.for_model(name)
        assert budget.known and budget.limit == 1_000_000
        assert budget.output == 8192
    monkeypatch.setitem(model_cost, "openai/DeepSeek-V4.1-Flash", {"max_input_tokens": 64000, "max_output_tokens": 4096})
    assert ContextBudget.for_model("openai/DeepSeek-V4.1-Flash").limit == 64000
    assert not ContextBudget.for_model("openai/DeepSeek-V4.1-Flash-private-small").known


@pytest.mark.asyncio
async def test_fresh_conversation_with_large_tool_catalog_uses_real_model_window(services, monkeypatch):
    from dataclasses import replace
    resolve_budget = ContextBudget.for_model
    agent, _, room, old_session, model, runtime, config = await setup_context(services, monkeypatch)
    add_history(services, room, old_session)
    old = services.contexts.load(agent.id, old_session.id)
    old.summary = "Private fact only in the old conversation: cobalt-731"
    services.contexts.save(agent.id, old_session.id, old, ContextStatus())
    fresh_room = await services.create_card(CardCreate(type="conversation", name="Fresh"))
    fresh = services.conversations.create_session(fresh_room.id, ConversationSessionCreate(title="Fresh", participant_ids=[agent.id]))
    services.conversations.add_message(fresh_room.id, fresh.id, sender_kind="user", sender_id=None, sender_name="You", content="Hello")
    monkeypatch.setattr(ContextBudget, "for_model", classmethod(lambda cls, name: resolve_budget(name)))
    model.model = "openai/DeepSeek-V4.1-Flash"
    config = replace(config, model=model.model)
    # Real ADK runner assembles a schema-heavy request (~16K estimated input).
    runtime._provider.definition = replace(runtime._provider.definition,
        description="Available resource instructions. " * 1000)
    result = [event async for event in runtime.execute(config, invocation(agent.id, fresh.id), RuntimeInput("Hello"))]
    assert result[-1].run_status == "succeeded"
    assert not model._summaries
    assert "cobalt-731" not in model._requests[-1]
    checkpoint = services.contexts.load(agent.id, fresh.id)
    assert checkpoint.cursor == 1 and not checkpoint.summary
    assert services.contexts.load(agent.id, old_session.id) == old
    status = services.contexts.statuses(fresh_room.id)[fresh.id][agent.id]
    assert 8192 < status.estimated_tokens < status.context_limit == 1_000_000


@pytest.mark.asyncio
async def test_unknown_model_does_not_treat_fallback_as_a_hard_limit(services, monkeypatch):
    resolve_budget = ContextBudget.for_model
    agent, _, _, session, model, _, _ = await setup_context(services, monkeypatch)
    monkeypatch.setattr(ContextBudget, "for_model", classmethod(lambda cls, name: resolve_budget(name)))
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Hello")
    request = LlmRequest(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[
        types.FunctionDeclaration(name="inspect", description="Tool instructions. " * 4000)])]))
    await managed.before_model(None, request)
    assert managed.tokens() > managed.budget.input
    assert not model._summaries
    assert managed.status.context_limit == 0
    assert "Hello" in encoded([part.model_dump() for part in request.contents])


@pytest.mark.asyncio
async def test_measured_usage_takes_precedence_over_fixed_overhead_estimate(services, monkeypatch):
    agent, _, _, session, model, _, _ = await setup_context(services, monkeypatch)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Hello")
    request = LlmRequest(config=types.GenerateContentConfig(system_instruction="Instructions. " * 1000))
    managed.checkpoint.measured_tokens = 2000
    managed.checkpoint.measured_estimate = estimate(managed.rendered()) + estimate(request.config.model_dump(mode="json", exclude_none=True))
    await managed.before_model(None, request)
    assert managed.status.estimated_tokens == 2000
    assert not model._summaries


class OutputLimitedSummaryModel(ScriptedModel):
    _limits: list[int] = PrivateAttr(default_factory=list)
    _required: int = PrivateAttr(default=2048)
    _rejected: bool = PrivateAttr(default=False)

    async def generate_content_async(self, request, stream=False):
        assert request.config.system_instruction == SUMMARY_INSTRUCTION
        assert not request.config.tools
        self._limits.append(request.config.max_output_tokens)
        if self._rejected:
            yield LlmResponse(error_code="UNAVAILABLE")
        elif request.config.max_output_tokens < self._required:
            # ADK puts MAX_TOKENS in both fields when reasoning exhausts output.
            yield LlmResponse(error_code=types.FinishReason.MAX_TOKENS,
                              finish_reason=types.FinishReason.MAX_TOKENS)
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(
                text="Confirmed code: cobalt-731. Artifact: sandbox://lab/result.csv. Next: finish the analysis.")]))


class BudgetedSummaryModel(ScriptedModel):
    """Records complete requests and supplies deliberate boundary responses."""
    replies: list[str] = []
    window: int = 128000

    async def generate_content_async(self, request, stream=False):
        assert request.config.system_instruction == SUMMARY_INSTRUCTION
        assert not request.config.tools
        assert estimate(request.model_dump(mode="json", exclude_none=True)) + request.config.max_output_tokens <= self.window
        self._summaries.append(request.model_copy(deep=True))
        reply = self.replies.pop(0) if self.replies else "Goal: continue. Confirmed code: cobalt-731. Artifact: sandbox://lab/result.csv."
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=reply)]))


@pytest.mark.asyncio
async def test_window_relative_summary_accepts_complete_checkpoint_above_old_fixed_cap(services, monkeypatch):
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    summary = "Confirmed research result and artifact reference. " * 240
    assert estimate(summary) > 4096
    model = BudgetedSummaryModel(replies=[summary])
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(128000, 8192, max_output=8192))
    managed.checkpoint.contents = [text_content("Historical research evidence. " * 4000)]
    await managed.compact()
    assert managed.checkpoint.summary == summary.strip()
    assert managed.tokens() <= managed.compaction_budget().target
    assert len(model._summaries) == 1


@pytest.mark.parametrize("first", ["", "Overly verbose findings. " * 1000])
@pytest.mark.asyncio
async def test_invalid_summary_retry_keeps_source_and_respects_request_budget(services, monkeypatch, first):
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    model = BudgetedSummaryModel(replies=[first], window=8192)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(8192, 1024, max_output=1024))
    managed.checkpoint.contents = [text_content("Original evidence cobalt-731. " * 1000)]
    await managed.compact()
    assert len(model._summaries) >= 2
    assert "Original evidence cobalt-731" in model._summaries[1].contents[0].parts[0].text
    assert "cobalt-731" in managed.checkpoint.summary


@pytest.mark.parametrize("reply,reason", [("", "empty checkpoint"), ("verbose " * 3000, "exceeds its budget")])
@pytest.mark.asyncio
async def test_invalid_summary_exhaustion_is_diagnostic_and_atomic(services, monkeypatch, reply, reason):
    from copy import deepcopy
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    model = BudgetedSummaryModel(replies=[reply] * 3, window=8192)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(8192, 1024, max_output=1024))
    managed.checkpoint.contents = [text_content("Original evidence. " * 1200)]
    before = deepcopy(managed.checkpoint)
    with pytest.raises(RuntimeError, match=reason):
        await managed.compact()
    assert len(model._summaries) == 3
    assert services.contexts.load(agent.id, session.id) == before


@pytest.mark.asyncio
async def test_smaller_window_rechunks_old_summary_and_escaped_unicode_history(services, monkeypatch):
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    model = BudgetedSummaryModel(window=8192)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(8192, 1024, max_output=1024))
    managed.checkpoint.summary = "Prior confirmed research. " * 1200
    managed.checkpoint.contents = [text_content('中文\\"\n\t' * 6000)]
    source = encoded([managed.checkpoint.summary, managed.checkpoint.contents])
    await managed.compact()
    assert len(model._summaries) > 3
    # Every original character is fed once, even with JSON escaping and a prior
    # checkpoint larger than the new model's entire input window.
    assert "".join(r.contents[0].parts[0].text.split("Next transcript fragment:\n", 1)[1]
                   for r in model._summaries) == source
    assert managed.tokens() <= managed.compaction_budget().target


@pytest.mark.asyncio
async def test_existing_summary_alone_can_be_compacted_after_window_change(services, monkeypatch):
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    model = BudgetedSummaryModel(window=8192)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(8192, 1024, max_output=1024))
    managed.checkpoint.summary = "Old model checkpoint. " * 900
    assert not managed.checkpoint.contents
    await managed.before_model(None, LlmRequest())
    assert managed.checkpoint.compaction_count == 1
    assert managed.tokens() < managed.compaction_budget().target


@pytest.mark.asyncio
async def test_compaction_triggers_before_input_limit_with_fixed_context_reserved(services, monkeypatch):
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    model = BudgetedSummaryModel(window=8192)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(8192, 1024, max_output=1024))
    managed.checkpoint.contents = [text_content("evidence " * 1180)]
    assert managed.compaction_budget().trigger < managed.tokens() < managed.budget.input
    await managed.before_model(None, LlmRequest())
    assert managed.checkpoint.compaction_count == 1
    assert managed.tokens() <= managed.compaction_budget().target


@pytest.mark.asyncio
async def test_unknown_model_rolls_history_even_when_static_context_exceeds_fallback(services, monkeypatch):
    agent, _, _, session, _, _, _ = await setup_context(services, monkeypatch)
    model = BudgetedSummaryModel(window=8192)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue",
                             budget=ContextBudget(8192, 1024, known=False, max_output=1024))
    request = LlmRequest(config=types.GenerateContentConfig(system_instruction="Static instructions. " * 1000))
    managed.checkpoint.contents = [text_content("Historical evidence. " * 1200)]
    await managed.before_model(None, request)
    assert managed.checkpoint.compaction_count == 1
    assert managed.tokens() > managed.budget.input
    assert managed.status.context_limit == 0
    assert managed.tokens() <= managed.compaction_budget().target


@pytest.mark.asyncio
async def test_truncated_summary_retries_with_reasoning_headroom_then_commits(services, monkeypatch):
    agent, _, room, session, _, _, _ = await setup_context(services, monkeypatch)
    add_history(services, room, session, count=12)
    model = OutputLimitedSummaryModel()
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue")
    await managed.compact()
    assert model._limits[:2] == [1024, 2048]
    assert managed.checkpoint.compaction_count == 1
    assert "cobalt-731" in managed.checkpoint.summary
    assert len(services.conversations.list_messages(room.id, session.id, limit=100)) == 12


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.asyncio
async def test_summary_retry_exhaustion_preserves_checkpoint_and_provider_cap(services, monkeypatch, rejected):
    from copy import deepcopy
    agent, _, room, session, _, _, _ = await setup_context(services, monkeypatch)
    add_history(services, room, session, count=12)
    monkeypatch.setattr(ContextBudget, "for_model", classmethod(lambda cls, name: cls(8192, 1024, max_output=2048)))
    model = OutputLimitedSummaryModel()
    model._required = 100000
    model._rejected = rejected
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Continue")
    before = deepcopy(managed.checkpoint)
    with pytest.raises(RuntimeError, match="retained context is unchanged"):
        await managed.compact()
    assert model._limits == ([1024] if rejected else [1024, 2048])
    assert services.contexts.load(agent.id, session.id) == before
    assert services.contexts.statuses(room.id)[session.id][agent.id].state != "compacting"


@pytest.mark.asyncio
async def test_conversation_run_integration_ingests_new_turns_past_tool_events(services, monkeypatch):
    agent, _, room, session, model, runtime, _ = await setup_context(services, monkeypatch)
    await runtime.delete_agent(agent.id)  # RunManager owns registration in this test.
    await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship="participate"))
    add_history(services, room, session)
    # Execute through the public conversation service and RunManager, including
    # streaming persistence. Tool messages stay nonfinal in canonical history.
    model._tool_rounds = 1
    await services.post_conversation_message(room.id, session.id,
        ConversationPost(content="What is the confirmed code?", mention_agent_ids=[agent.id]))
    run = services.run_manager.store.list(agent_id=agent.id)[-1]
    finished = await services.run_manager.wait_terminal(run.run_id)
    assert finished.status == "succeeded", finished.error
    await asyncio.sleep(.01)
    assert services.contexts.load(agent.id, session.id).compaction_count >= 1
    tool_count = len(runtime._provider.invocations)
    await services.post_conversation_message(room.id, session.id,
        ConversationPost(content="New instruction: use delta-942 next.", mention_agent_ids=[agent.id]))
    run = services.run_manager.store.list(agent_id=agent.id)[-1]
    finished = await services.run_manager.wait_terminal(run.run_id)
    assert finished.status == "succeeded", finished.error
    assert "delta-942" in model._requests[-1]
    assert len(runtime._provider.invocations) == tool_count
    projection = services.conversation_summary(room.id).model_dump(mode="json")
    assert projection["context_statuses"][session.id][agent.id]["compaction_count"] >= 1
    assert "cobalt-731" not in encoded(projection)
    await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_peer_stream_updates_behind_cursor_and_nonfinal_tools_do_not_block(services, monkeypatch):
    agent, peer, room, session, model, _, _ = await setup_context(services, monkeypatch)
    stream = services.conversations.add_message(room.id, session.id, sender_kind="agent", sender_id=peer.id,
        sender_name="Boreal", content="Partial finding", is_final=False)
    services.conversations.add_message(room.id, session.id, sender_kind="agent", sender_id=peer.id,
        sender_name="Boreal", content="Finished inspect", kind="tool_completed", is_final=False)
    services.conversations.add_message(room.id, session.id, sender_kind="user", sender_id=None,
        sender_name="You", content="New user message after the unfinished peer")
    first = ManagedContext(services.contexts, agent.id, session.id, "run-1", model, "Continue")
    first.publish()
    assert "New user message" in encoded(first.rendered())
    assert first.checkpoint.cursor == 3
    with services.database.transaction() as db:
        db.execute("UPDATE conversation_messages SET content=?, is_final=1 WHERE id=?", ("Complete finding: delta-942", stream.id))
    second = ManagedContext(services.contexts, agent.id, session.id, "run-2", model, "Continue")
    assert "Complete finding: delta-942" in encoded(second.rendered())
    assert not second.checkpoint.pending_messages
    assert encoded(second.rendered()).count("New user message") == 1


@pytest.mark.asyncio
async def test_scope_lock_serializes_same_pair_without_blocking_other_sessions(services, monkeypatch):
    agent, _, room, session, model, runtime, config = await setup_context(services, monkeypatch)
    add_history(services, room, session)
    model._block = asyncio.Event()
    async def execute(scope, run):
        return [event async for event in runtime.execute(config, invocation(agent.id, scope, run), RuntimeInput("Continue"))]
    first = asyncio.create_task(execute(session.id, "run-1"))
    for _ in range(100):
        if model._summaries:
            break
        await asyncio.sleep(.001)
    second = asyncio.create_task(execute(session.id, "run-2"))
    other = await asyncio.wait_for(execute("separate-context", "run-3"), timeout=2)
    assert other[-1].run_status == "succeeded"
    assert not first.done() and not second.done()
    model._block.set()
    await asyncio.gather(first, second)
    assert services.contexts.load(agent.id, session.id).compaction_count == 1


@pytest.mark.asyncio
async def test_oversized_current_input_fails_explicitly_without_destroying_history(services, monkeypatch):
    agent, _, _, session, model, _, _ = await setup_context(services, monkeypatch)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Huge current task " * 8000)
    with pytest.raises(RuntimeError, match="safe input window"):
        await managed.before_model(None, LlmRequest())
    assert services.contexts.load(agent.id, session.id).compaction_count == 0


@pytest.mark.asyncio
async def test_closing_adk_stream_releases_scope_and_does_not_replay_interrupted_tool(services, monkeypatch):
    agent, _, _, session, model, runtime, config = await setup_context(services, monkeypatch)
    model._tool_rounds = 1
    stream = runtime.execute(config, invocation(agent.id, session.id), RuntimeInput("Inspect notes"))
    started = await anext(stream)
    assert started.type == "tool_started"
    await stream.aclose()
    assert not services.contexts.lock(agent.id, session.id).locked()
    assert not runtime._provider.invocations
    result = [event async for event in runtime.execute(config, invocation(agent.id, session.id, "after-stop"), RuntimeInput("Continue after Stop"))]
    assert result[-1].run_status == "succeeded"
    assert "Outcome is unknown" in model._requests[-1]
    assert not runtime._provider.invocations


@pytest.mark.asyncio
async def test_adk_request_can_lead_public_events_without_missing_or_duplicating_tools(services, monkeypatch):
    agent, _, _, session, model, _, _ = await setup_context(services, monkeypatch)
    managed = ManagedContext(services.contexts, agent.id, session.id, "run", model, "Inspect")
    call = {"role": "model", "parts": [{"function_call": {"name": "read", "args": {}}}]}
    response = {"role": "user", "parts": [{"function_response": {"name": "read", "response": {"value": "actual result"}}}]}
    request = LlmRequest(contents=[types.Content.model_validate(value) for value in [text_content("Inspect"), call, response]])
    await managed.before_model(None, request)
    assert "actual result" in encoded(managed.rendered())
    await managed.after_model(None, SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=200)))
    for value in (call, response):
        for part in value["parts"]:
            next(iter(part.values()))["id"] = "adk-call-1"
        managed.observe(SimpleNamespace(content=types.Content.model_validate(value)))
    assert encoded(managed.rendered()).count('"function_call"') == 1
    assert encoded(managed.rendered()).count('"function_response"') == 1
    assert managed.status.estimated_tokens == 200
    # Gemini may omit an auto ID from the request even though its public call
    # event retained it. This is still one complete call/result pair.
    mixed = ManagedContext(services.contexts, agent.id, "mixed-wire-context", "mixed", model, "Inspect")
    mixed.checkpoint.contents = [call, {"role": "user", "parts": [{
        "function_response": {"name": "read", "response": {"ok": True}}}]}]
    mixed._repair_interrupted_tools()
    assert len(mixed.checkpoint.contents) == 2
