"""Final dispatch budgets and reversible working views; no live model calls."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents.context import Checkpoint, ContextBudget, ManagedContext, encoded, text_content
from backend.agents.model_observation import ModelTrace
from backend.agents.models import AgentRuntimeError
from backend.agents.request_recovery import RequestRetryPolicy, recover_request
from backend.agents.request_window import ContextCapabilities, RequestWindow, input_capacity, request_usage
from backend.tests.test_invocation_logs import native  # native StateStore fixture


@pytest.mark.asyncio
async def test_started_size_and_checkpoint_use_final_normalized_dispatch_history(native):
    from backend.agents.model_observation import _request_size
    from backend.runs.activity import activity_update
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    _, raw, contents = exchange(trace.invocations, role='structure_builder', text='large result\n' * 5000)
    thought = '中文 thought\n'
    chunks = [types.Part(text=thought, thought=True) for _ in range(13000)]
    contents[0].parts = chunks + contents[0].parts
    req = request(contents=contents)
    original_size = _request_size(req)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'structure_builder',
                           capabilities=ContextCapabilities(preserve_tool_reasoning=True))
    trace.context_windows['structure_builder'] = window
    await trace.callbacks('structure_builder', prepare_only=True)['before_model_callback'](None, req)
    seen = []
    class Provider:
        model = 'offline'
        async def generate_content_async(self, submitted, stream=False):
            seen.append(submitted)
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='done')]))
    responses = [r async for r in recover_request(Provider(), req, trace, 'structure_builder')]
    await trace.callbacks('structure_builder')['after_model_callback'](None, responses[0])
    events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
    starts = [e for e in events if e.get('phase') == 'model' and 'elapsed_ms' not in e]
    assert len(starts) == 1
    assert starts[0]['input_bytes'] == _request_size(seen[0]) < original_size
    assert starts[0]['input_bytes_scope'] == 'contents_only'
    assert seen[0].contents[0].parts[0].text == thought * 13000
    assert len(seen[0].contents[0].parts) == 2 and len(req.contents[0].parts) == 13001
    assert seen[0].contents[1].parts[0].function_response.response != raw
    archived = next(iter(trace.invocations.records('models').values()))
    assert archived['request']['contents'] == [c.model_dump(mode='json', exclude_none=True) for c in seen[0].contents]
    checkpoint = native[-1].latest('structure_builder')
    from backend.agents.context import replayable_contents
    assert checkpoint['contents'] == replayable_contents(archived['request']['contents'])
    lifecycle = {}
    for e in events:
        lifecycle.update(activity_update(lifecycle, 'agent_progress', e, 'fixture-event', 'fixture-time'))
    assert lifecycle['model_requests'][0]['input_bytes'] == starts[0]['input_bytes']
    assert lifecycle['model_requests'][0]['input_bytes_scope'] == 'contents_only'


@pytest.mark.asyncio
async def test_rejected_preflight_never_announces_provider_request_started():
    trace = ModelTrace(asyncio.Queue())
    req = request('oversized ' * 10000)
    trace.context_windows['builder'] = RequestWindow(ContextBudget(1024, 512), trace, 'builder')
    await trace.callbacks('builder', prepare_only=True)['before_model_callback'](None, req)
    class Provider:
        async def generate_content_async(self, *args, **kwargs):
            pytest.fail('preflight must not dispatch')
            yield
    with pytest.raises(AgentRuntimeError, match='safe input window'):
        _ = [r async for r in recover_request(Provider(), req, trace, 'builder')]
    events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
    assert not any(e.get('phase') == 'model' for e in events)
    assert any(e.get('phase') == 'model_preflight_rejected' for e in events)


@pytest.mark.asyncio
async def test_wrong_section_prefix_offers_exact_authorized_retry_without_redirect(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    identity = trace.invocations.start_tool('builder', 'source', 0, 'run_step', {'source': 'print(1)'})
    raw = {'execution': {'stdout': 'done', 'exit_code': 0}}
    trace.invocations.finish_tool(identity, raw)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    window.issue_reference(identity)
    wrong = await window.read_tool_result(identity, '/arguments/source', limit=20)
    assert wrong['ok'] is False and 'text' not in wrong
    retry = wrong['error']['retry_arguments']
    assert retry == dict(invocation_id=identity, section='arguments', pointer='/source', offset=0, limit=20)
    assert (await window.read_tool_result(**retry))['text'] == 'print(1)'
    assert 'retry_arguments' not in (await window.read_tool_result(identity, '/arguments/absent'))['error']
    other = RequestWindow(window.budget, trace, 'planner')
    denied = await other.read_tool_result(identity, '/arguments/source')
    assert denied['error']['code'] == 'permission_denied' and 'retry_arguments' not in denied['error']
    assert trace.invocations.get('tools', identity)['response'] == raw


def request(text="task", *, output=512, instruction="instruction", contents=None):
    return LlmRequest(model="fixture-model", contents=contents or [types.Content(role="user", parts=[types.Part(text=text)])],
        config=types.GenerateContentConfig(system_instruction=instruction, max_output_tokens=output))


class MemoryContext:
    def __init__(self):
        self.saved = Checkpoint()
    def load(self, *_):
        return deepcopy(self.saved)
    def session(self, *_):
        return None
    def save(self, agent_id, context_id, checkpoint, status, run_id, **kwargs):
        self.saved = deepcopy(checkpoint)


@pytest.mark.asyncio
async def test_required_unsigned_reasoning_survives_managed_history_and_native_adk_conversion(monkeypatch):
    from backend.agents.resilient_litellm import ResilientLiteLlm
    from google.adk.models.lite_llm import LiteLLMClient
    from litellm import ModelResponse
    store = MemoryContext()
    model = ResilientLiteLlm("openai/custom-alias", api_base="https://api.deepseek.com")
    thoughts = ["A complete thought without a tool. " * 250, "A complete tool thought. " * 250]
    contents = [
        types.Content(role="model", parts=[types.Part(text=thoughts[0],thought=True),types.Part(text="Prepare next step")]),
        types.Content(role="model", parts=[types.Part(text=thoughts[1],thought=True),types.Part.from_function_call(name="measure",args={})]),
        types.Content(role="user", parts=[types.Part.from_function_response(name="measure",response={"value":3})])]
    manager = ManagedContext(store,"agent","scope","run",model,"Continue",budget=ContextBudget(100000,512))
    req = request(contents=contents)
    await manager.before_model(None, req)
    calls = []
    async def complete(self, **kwargs):
        calls.append(kwargs)
        return ModelResponse(choices=[{"message":{"role":"assistant","content":"Done"},"finish_reason":"stop"}])
    monkeypatch.setattr(LiteLLMClient,"acompletion",complete)
    _ = [event async for event in model.generate_content_async(req)]
    assistants = [m for m in calls[0]["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == thoughts
    restored = ManagedContext(store,"agent","scope","run",model,"Continue",budget=manager.budget)
    assert [p["text"] for c in restored.checkpoint.contents for p in c.get("parts",[]) if p.get("thought")] == thoughts


def exchange(archive, *, role="builder", call_id="one", name="execute_command", text=None):
    result = {"stdout": text or "碳纳米管 " * 12000, "exit_code": 0, "details": {"a/b": ["first", "second"]}}
    identity = archive.start_tool(role, call_id, 0, name, {"argv": ["python3", "build.py"]})
    archive.finish_tool(identity, result)
    return identity, result, [
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args={"argv": ["python3", "build.py"]}))]),
        types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(id=call_id, name=name, response=result))])]


@pytest.mark.asyncio
async def test_host_handoff_shares_only_one_complete_result_and_revokes_on_new_scope(native):
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    identity, result, _ = exchange(trace.invocations)
    other, _, _ = exchange(trace.invocations, call_id="unissued")
    incomplete = trace.invocations.start_tool("builder", "incomplete", 0, "execute_command", {})
    window = RequestWindow(ContextBudget(32768, 512), trace, "planner")
    assert (await window.read_tool_result(identity))["error"]["code"] == "permission_denied"
    assert window.issue_reference(identity) is None
    assert window.issue_reference(incomplete, source_role="builder") is None
    assert window.issue_reference(identity, source_role="builder")["invocation_id"] == identity
    assert (await window.read_tool_result(identity, "/stdout", limit=4))["text"] == result["stdout"][:4]
    assert (await window.read_tool_result(other))["error"]["code"] == "permission_denied"
    window.set_scope("next task")
    assert (await window.read_tool_result(identity))["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_native_projection_is_reversible_scoped_and_never_changes_full_archive(native):
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    archive = trace.invocations
    identity, result, contents = exchange(archive)
    other, _, _ = exchange(archive, role="planner", call_id="other")
    req = request(contents=contents)
    window = RequestWindow(ContextBudget(32768, 512), trace, "builder")
    before = request_usage(req)["estimated_input_tokens"]
    stats = await window.prepare(req)
    assert stats["estimated_input_tokens"] < before and stats["projected_tool_results"] == 1
    assert req.contents[0].parts[0].function_call.id == "one"
    response = req.contents[1].parts[0].function_response
    assert response.id == "one" and response.name == "execute_command"
    assert response.response["context_projection"]["native_invocation_id"] == identity
    assert archive.get("tools", identity)["response"] == result
    section = await window.read_tool_result(identity, "/stdout", 17, 80)
    assert section["text"] == result["stdout"][17:97] and section["next_offset"] == 97
    assert section["truncated"] and "sha256" not in section
    assert (await window.read_tool_result(identity, "/details/a~1b/1"))["text"] == "second"
    assert (await window.read_tool_result(identity, "/argv/1", section="arguments"))["text"] == "build.py"
    for invalid in (other, "guessed", identity.replace(archive.run_id, "different-run")):
        if invalid == identity:
            continue
        assert (await window.read_tool_result(invalid))["error"]["code"] == "permission_denied"
    assert (await window.read_tool_result(identity, limit=8001))["error"]["code"] == "invalid_resource"
    assert (await window.read_tool_result(identity, "/details/a~1b/99"))["error"]["code"] == "tool_result_pointer"
    # A new scoped window can recover genuine references from a persisted view.
    reopened = RequestWindow(window.budget, trace, "builder")
    await reopened.prepare(req)
    assert (await reopened.read_tool_result(identity, "/stdout", limit=3))["text"] == result["stdout"][:3]
    wrong_role = RequestWindow(window.budget, trace, "planner")
    await wrong_role.prepare(req)
    assert (await wrong_role.read_tool_result(identity))["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_missing_native_result_does_not_invent_an_archive_or_receipt():
    trace = ModelTrace(asyncio.Queue())
    result = {"stdout": "record value " * 4000}
    req = request(contents=[types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(id="unknown", name="read_file", response=result))])])
    original = req.model_dump_json()
    window = RequestWindow(ContextBudget(100000, 512), trace, "builder")
    assert (await window.prepare(req))["projected_tool_results"] == 0
    assert req.model_dump_json() == original and not window.references


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["agent", "atom_sculptor", "planner", "structure_builder", "materials_project"])
async def test_final_assembled_input_and_output_reservation_block_before_dispatch(role):
    trace = ModelTrace(asyncio.Queue())
    req = request("small")
    window = RequestWindow(ContextBudget(8192, 512), trace, role)
    assert (await window.prepare(req))["estimated_input_tokens"] < input_capacity(req, window.budget)
    # Late plugin instructions and declarations were absent at the first callback.
    req.config.system_instruction += "host frame " * 3000
    req.config.tools = [types.Tool(function_declarations=[types.FunctionDeclaration(name="probe", description="schema " * 4000)])]
    trace.context_windows[role] = window
    trace.begin(role, req)
    class Provider:
        dispatched = False
        async def generate_content_async(self, *args, **kwargs):
            self.dispatched = True
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="unexpected")]))
    model = Provider()
    with pytest.raises(AgentRuntimeError, match="safe input window"):
        _ = [r async for r in recover_request(model, req, trace, role)]
    assert not model.dispatched
    req = request("碳纳米管" * 600, output=7000)
    with pytest.raises(AgentRuntimeError):
        await window.prepare(req)


@pytest.mark.asyncio
async def test_provider_usage_calibration_is_distinct_and_unknown_alias_is_not_a_fake_hard_cap():
    trace = ModelTrace(asyncio.Queue())
    window = RequestWindow(ContextBudget(2048, 512, known=False), trace, "builder")
    req = request("record value " * 2000)
    stats = await window.prepare(req)
    assert stats["input_capacity"] is None and not stats["known_window"]
    window.observe_usage(LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=6000)))
    before_estimate = stats["estimated_input_tokens"]
    req.contents[0].parts[0].text += "data" * 100
    stats = await window.prepare(req)
    assert stats["estimate_method"] == "local_cl100k_base_reference"
    assert stats["calibrated_input_tokens"] == 6000 + stats["estimated_input_tokens"] - before_estimate


@pytest.mark.asyncio
async def test_signed_prefix_is_not_projected_summarized_or_reordered(native):
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    identity, _, contents = exchange(trace.invocations)
    contents[0].parts.insert(0, types.Part(text="unsigned reasoning", thought=True))
    contents[0].parts.insert(1, types.Part(text="signed reasoning", thought=True, thought_signature=b"signature"))
    req = request(contents=contents)
    original = req.model_dump_json()
    called = []
    window = RequestWindow(ContextBudget(8192, 512), trace, "builder", reducer=lambda r: called.append(r))
    with pytest.raises(AgentRuntimeError, match="Signed history"):
        await window.prepare(req)
    assert req.model_dump_json() == original and not called and not window.references
    manager = ManagedContext(MemoryContext(), "agent", "scope", "run", NS(model="fixture-model"), "task", budget=ContextBudget(8192, 512))
    await manager.before_model(None, req)
    assert req.model_dump_json() == original
    # Native compaction is an explicit adapter extension, never inferred by name.
    async def native_compact(request):
        request.contents = [types.Content(role="user", parts=[types.Part(text="adapter-owned canonical continuation")])]
    window.capabilities = ContextCapabilities(native_compact=native_compact)
    assert (await window.prepare(req))["native_compaction_available"]
    assert req.contents[0].parts[0].text == "adapter-owned canonical continuation"


@pytest.mark.asyncio
async def test_summary_uses_native_request_retry_usage_and_phase_without_tool_replay(native):
    *_, checkpoints = native
    store = MemoryContext()
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    class Model:
        model = "fixture-model"
        attempts = 0
        async def generate_content_async(self, req, stream=False):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("temporary disconnect")
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Keep original objective and forbidden generators. Outcome is unverified; next: run one probe.")]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=700, candidates_token_count=30))
    model = Model()
    manager = ManagedContext(store, "agent", "delegation:one", "run", model, "Do not use forbidden generator", budget=ContextBudget(16384, 1024))
    manager.trace, manager.trace_role = trace, "builder"
    manager.retry_policy = RequestRetryPolicy(max_attempts=2, initial_delay_seconds=0., max_delay_seconds=0.)
    usage = []; manager.on_compaction_response = usage.append
    pinned = types.Content(role="user", parts=[types.Part(text="HOST authoritative: original request; validation NOT complete")])
    req = request(contents=[types.Content.model_validate(text_content(manager.prompt)), types.Content.model_validate(text_content("settled history " * 3000)), pinned], output=1024)
    original = req.model_dump_json()
    await manager.reduce_final_request(req, protected=lambda c: c == pinned)
    assert req.contents[-1] == pinned and len(req.model_dump_json()) < len(original)
    assert request_usage(req)["estimated_input_tokens"] < input_capacity(req, manager.budget)
    rows = list(checkpoints.invocations.records("models").values())
    assert len(rows) >= 2 and rows[0]["state"] == "interrupted"
    assert all(r["phase"] == "context_compaction" and r["role"] == "builder:context_compaction" for r in rows)
    assert rows[1]["request"] == rows[0]["request"]
    assert rows[-1]["input_tokens"] == 700 and rows[-1]["output_tokens"] == 30
    assert len(usage) == len(rows) - 1 and not checkpoints.invocations.records("tools")
    assert store.saved.compaction_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "empty", "truncated", "cancel"])
async def test_failed_or_cancelled_summary_preserves_checkpoint_and_request(failure):
    store = MemoryContext()
    started = asyncio.Event()
    class Model:
        model = "fixture-model"
        async def generate_content_async(self, req, stream=False):
            started.set()
            if failure == "exception":
                raise RuntimeError("summary unavailable")
            if failure == "cancel":
                await asyncio.Event().wait()
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="" if failure == "empty" else "unfinished")]),
                finish_reason="MAX_TOKENS" if failure == "truncated" else "STOP")
    manager = ManagedContext(store, "agent", "scope", "run", Model(), "Original task", budget=ContextBudget(16384, 1024))
    manager.checkpoint.summary = "Original retained facts"
    manager.publish()
    old = encoded(manager.checkpoint.__dict__)
    req = request("past " * 10000)
    wire = req.model_dump_json()
    task = asyncio.create_task(manager.reduce_final_request(req))
    if failure == "cancel":
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError):
            await task
    assert encoded(manager.checkpoint.__dict__) == old
    assert encoded(store.saved.__dict__) == old and req.model_dump_json() == wire


@pytest.mark.asyncio
@pytest.mark.parametrize("request_recovery", [False, True])
async def test_parser_correction_is_checked_again_before_the_next_provider_call(monkeypatch, request_recovery):
    from google.adk.models.lite_llm import LiteLLMClient
    from backend.agents.resilient_litellm import ResilientLiteLlm
    from litellm import ModelResponse
    sent = []
    async def complete(self, **kwargs):
        sent.append(kwargs)
        return ModelResponse(choices=[{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "bad", "type": "function", "function": {"name": "write_file",
                "arguments": '{"content":"' + '碳纳米管' * 3000}}]}, "finish_reason": "tool_calls"}])
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    trace = ModelTrace(asyncio.Queue())
    window = RequestWindow(ContextBudget(4096, 512), trace, "agent")
    model = ResilientLiteLlm("openai/fixture", context_preflight=window.prepare)
    req = request()
    if request_recovery:
        from backend.agents.request_recovery import recoverable_model
        trace.begin("agent", req)
        model = recoverable_model(model, trace, "agent", context_window=window)
    with pytest.raises(AgentRuntimeError, match="safe input window"):
        _ = [r async for r in model.generate_content_async(req)]
    assert len(sent) == 1 and len(req.contents) == 1
    assert "context_preflight" not in sent[0] and "transport_retry_limit" not in sent[0]
    assert "argument_retry_limit" not in sent[0]


@pytest.mark.asyncio
async def test_session_association_can_repair_existing_role_checkpoint(services, monkeypatch):
    from backend.agents.context import ContextStatus
    from backend.tests.test_context_compaction import setup_context
    agent, _, room, session, *_ = await setup_context(services, monkeypatch)
    scope = session.id + "#atomsculptor:coordinator"
    checkpoint = Checkpoint(summary="Scoped coordinator facts")
    status = ContextStatus(estimated_tokens=300, context_limit=8192, pressure=.1)
    services.contexts.save(agent.id, scope, checkpoint, status)
    assert agent.id not in services.contexts.statuses(room.id).get(session.id, {})
    services.contexts.save(agent.id, scope, checkpoint, status, session_context_id=session.id)
    assert services.contexts.statuses(room.id)[session.id][agent.id] == status
    assert services.contexts.load(agent.id, scope).summary == "Scoped coordinator facts"
    assert services.contexts.load(agent.id, session.id).summary == ""


# Use OAW's ordinary service fixture rather than a second persistence mechanism.
from backend.tests.test_context_compaction import services


@pytest.mark.asyncio
async def test_new_delegation_clears_old_usage_and_result_entitlements(native):
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    identity, _, contents = exchange(trace.invocations)
    window = RequestWindow(ContextBudget(100000, 512), trace, "builder")
    window.set_scope("builder:delegation-one")
    await window.prepare(request(contents=contents))
    window.observe_usage(LlmResponse(usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=900)))
    assert identity in window.references
    window.set_scope("builder:delegation-one")
    assert identity in window.references and window.measured_tokens == 900
    window.set_scope("builder:delegation-two")
    assert not window.references and window.measured_tokens is None
    await window.prepare(request("Same original task, fresh assignment"))
    assert (await window.read_tool_result(identity))["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_compaction_adapter_transport_retry_uses_one_native_budget(native, monkeypatch):
    from google.adk.models.lite_llm import LiteLLMClient
    from backend.agents.resilient_litellm import ResilientLiteLlm
    from litellm import ModelResponse
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    sent = []
    async def complete(self, **kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            raise ConnectionError("temporarily disconnected")
        return ModelResponse(choices=[{"message": {"role": "assistant", "content": "Keep the original constraints and run one probe."}, "finish_reason": "stop"}])
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    manager = ManagedContext(MemoryContext(), "agent", "scope", "run", ResilientLiteLlm("openai/fixture"),
        "Original request", budget=ContextBudget(16384, 1024))
    manager.trace, manager.trace_role = trace, "builder"
    manager.retry_policy = RequestRetryPolicy(max_attempts=2, initial_delay_seconds=0., max_delay_seconds=0.)
    assert await manager._summarize(request(output=1024), 2048) == "Keep the original constraints and run one probe."
    rows = list(checkpoints.invocations.records("models").values())
    assert len(sent) == 2 and len(rows) == 2
    assert rows[0]["state"] == "interrupted" and rows[1]["state"] == "returned"


@pytest.mark.asyncio
async def test_local_preflight_failure_archives_request_without_provider_or_network_retry(native):
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    req = request('small', output=7000, instruction='large fixed instructions ' * 500)
    window = RequestWindow(ContextBudget(8192, 7000), trace, 'builder')
    trace.context_windows['builder'] = window
    trace.begin('builder', req)
    class Provider:
        model = 'fixture'
        calls = 0
        async def generate_content_async(self, *args, **kwargs):
            self.calls += 1
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='unexpected')]))
    provider = Provider()
    with pytest.raises(AgentRuntimeError, match='safe input window'):
        _ = [r async for r in recover_request(provider, req, trace, 'builder')]
    rows = list(checkpoints.invocations.records('models').values())
    assert provider.calls == 0 and len(rows) == 1
    row = rows[0]
    assert row['phase'] == 'context_preflight' and row['state'] == 'interrupted'
    assert row['request_complete'] and not row['usage_available']
    assert row['context']['provider_dispatched'] is False
    assert row['context']['error_origin'] == 'local_context_preflight'
    assert row['request']['config']['max_output_tokens'] == 7000
    assert not checkpoints.invocations.records('tools')
    assert checkpoints.latest('builder')['state'] == 'interrupted'


@pytest.mark.asyncio
async def test_checkpoint_uses_all_available_continuation_space_without_a_prose_quota():
    calls = []
    summary = 'Verified probe value 2.4595; saved probe.py exit 0. ' * 32
    class Model:
        model = 'offline'
        async def generate_content_async(self, req, stream=False):
            calls.append(req.model_copy(deep=True))
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text=summary)]), finish_reason='STOP')
    manager = ManagedContext(MemoryContext(), 'agent', 'scope', 'run', Model(), 'Continue original task',
        budget=ContextBudget(128000, 81920, max_output=81920))
    manager.overhead = 36000
    manager.checkpoint.contents = [text_content('settled verified transcript ' * 1000)]
    from backend.agents.context import estimate
    assert manager.budget.input == 128000 - 81920
    assert manager._cut() == 1
    await manager.compact()
    assert len(calls) == 1 and manager.checkpoint.summary == summary.strip()
    assert manager.tokens() <= manager.budget.input
    assert calls[0].config.max_output_tokens == 81920
    assert 'checkpoint that fits' in calls[0].contents[0].parts[0].text


@pytest.mark.asyncio
async def test_oversized_checkpoint_still_rolls_back_when_it_exceeds_complete_continuation_room():
    calls = []
    class Model:
        model = 'offline'
        async def generate_content_async(self, req, stream=False):
            calls.append(req.model_copy(deep=True))
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='excessive summary ' * 7000)]), finish_reason='STOP')
    store = MemoryContext()
    manager = ManagedContext(store, 'agent', 'scope', 'run', Model(), 'Original task',
        budget=ContextBudget(128000, 81920, max_output=81920))
    manager.overhead = 36000
    manager.checkpoint.contents = [text_content('settled history ' * 4000)]
    manager.publish()
    previous = deepcopy(manager.checkpoint)
    with pytest.raises(RuntimeError, match='checkpoint exceeds its budget'):
        await manager.compact()
    assert len(calls) == 3 and manager.checkpoint == store.saved == previous


@pytest.mark.asyncio
async def test_unsigned_stream_granularity_does_not_change_history_text_or_budget():
    from backend.agents.request_window import coalesce_unsigned_thoughts
    thought = 'A measured result supports the next small step. ' * 60
    call = types.Part(function_call=types.FunctionCall(id='probe', name='run_step', args={'source': 'print(1)'}))
    response = types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(
        id='probe', name='run_step', response={'exit_code': 0}))])
    chunked = request(contents=[types.Content(role='model', parts=[types.Part(text=c, thought=True) for c in thought] + [call]), response])
    joined = request(contents=[types.Content(role='model', parts=[types.Part(text=thought, thought=True), call]), response])
    assert request_usage(chunked)['estimated_input_tokens'] > request_usage(joined)['estimated_input_tokens'] * 2
    window = RequestWindow(ContextBudget(8192, 512), ModelTrace(asyncio.Queue()), 'builder')
    stats = await window.prepare(chunked)
    assert stats['coalesced_thought_parts'] == len(thought) - 1
    assert chunked.model_dump_json() == joined.model_dump_json()
    assert coalesce_unsigned_thoughts(chunked) == 0
    signed = request(contents=[types.Content(role='model', parts=[types.Part(text='signed', thought_signature=b'sig'),
        types.Part(text='a', thought=True), types.Part(text='b', thought=True)])])
    before = signed.model_dump_json()
    assert coalesce_unsigned_thoughts(signed) == 0 and signed.model_dump_json() == before


def test_unsigned_normalization_preserves_metadata_and_exchange_boundaries():
    from backend.agents.request_window import coalesce_unsigned_thoughts
    call = types.Part(function_call=types.FunctionCall(id='one', name='probe', args={}))
    visible = types.Part(text='visible answer')
    req = request(contents=[types.Content(role='model', parts=[types.Part(text='a', thought=True),
        types.Part(text='b', thought=True), call, types.Part(text='c', thought=True), visible,
        types.Part(text='d', thought=True), types.Part(text='e', thought=True)])])
    assert coalesce_unsigned_thoughts(req) == 2
    assert req.contents[0].parts == [types.Part(text='ab', thought=True), call,
        types.Part(text='c', thought=True), visible, types.Part(text='de', thought=True)]


def test_token_accounting_uses_bundled_bpe_without_network(monkeypatch):
    import requests
    from backend.agents.context import _local_tokenizer, estimate
    def forbidden(*args, **kwargs):
        raise AssertionError('Token accounting must not fetch tokenizer data')
    monkeypatch.setattr(requests, 'get', forbidden)
    _local_tokenizer.cache_clear()
    tokenizer = _local_tokenizer()
    for text in ('Hello world!', '两根碳纳米管', 'def probe(x):\n    return x + 1\n', '<|endoftext|>'):
        assert estimate(text) == len(tokenizer.encode_ordinary(text))
    budget = ContextBudget(128000, 81920)
    assert budget.input == input_capacity(request(output=81920), budget) == 46080


@pytest.mark.asyncio
async def test_recent_complete_exchange_and_current_task_survive_client_compaction():
    class Model:
        model = 'offline'
        async def generate_content_async(self, req, stream=False):
            assert 'older settled evidence' in req.contents[0].parts[0].text
            assert 'recent-value' not in req.contents[0].parts[0].text
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='Older evidence confirmed; continue the current task.')]))
    manager = ManagedContext(MemoryContext(), 'agent', 'scope', 'run', Model(), 'Current user request', budget=ContextBudget(8192, 512))
    recent = [
        {'role':'model', 'parts':[{'function_call': {'id':'recent', 'name':'probe', 'args':{'x':1}}}]},
        {'role':'user', 'parts':[{'function_response': {'id':'recent', 'name':'probe', 'response':{'value':'recent-value'}}}]},
    ]
    manager.checkpoint.contents = [text_content('older settled evidence ' * 3000)] + deepcopy(recent)
    await manager.compact()
    assert manager.checkpoint.contents == recent
    assert manager.rendered()[-1] == text_content('Current user request')


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True])
async def test_provider_overflow_compacts_and_retries_once_without_tool_replay(native):
    trace = ModelTrace(asyncio.Queue())
    calls, reductions = [], []
    class ContextWindowExceededError(Exception):
        pass
    class Provider:
        model = 'offline'
        async def generate_content_async(self, req, stream=False):
            calls.append(req.model_copy(deep=True))
            if len(calls) == 1:
                raise ContextWindowExceededError('private provider diagnostics')
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='continued')]))
    async def reduce(req):
        reductions.append(True)
        req.contents = [types.Content(role='user', parts=[types.Part(text='saved facts; current task')])]
    def client_forbidden(req):
        raise AssertionError('An adapter-native reducer takes precedence for unsigned history too')
    window = RequestWindow(ContextBudget(8192,512), trace, 'agent',
        reducer=client_forbidden if native else reduce,
        capabilities=ContextCapabilities(native_compact=reduce if native else None))
    req = request('settled history ' * 1000)
    trace.context_windows['agent'] = window
    trace.begin('agent', req)
    result = [r async for r in recover_request(Provider(), req, trace, 'agent',
        policy=RequestRetryPolicy(max_attempts=1))]
    assert len(calls) == 2 and len(reductions) == 1
    assert result[-1].content.parts[0].text == 'continued'
    assert calls[-1].config.max_output_tokens == calls[0].config.max_output_tokens == 512
    assert len(req.contents[0].parts[0].text) > len(calls[-1].contents[0].parts[0].text)
    assert not trace.invocations


@pytest.mark.asyncio
@pytest.mark.parametrize('context_error', [False, True])
async def test_provider_error_does_not_loop_context_recovery(context_error):
    trace = ModelTrace(asyncio.Queue())
    class ContextWindowExceededError(Exception): pass
    class BadRequest(Exception):
        status_code=400
    class Provider:
        model='offline'
        calls=0
        async def generate_content_async(self, req, stream=False):
            self.calls += 1
            raise (ContextWindowExceededError('too long') if context_error else BadRequest('invalid schema'))
            yield
    reductions=[]
    async def reduce(req):
        reductions.append(True)
        req.contents=[types.Content(role='user',parts=[types.Part(text='compact')])]
    window=RequestWindow(ContextBudget(8192,512),trace,'agent',reducer=reduce)
    trace.context_windows['agent']=window
    req=request('history ' * 200)
    trace.begin('agent',req)
    provider=Provider()
    with pytest.raises(ContextWindowExceededError if context_error else BadRequest):
        _=[r async for r in recover_request(provider,req,trace,'agent',policy=RequestRetryPolicy(max_attempts=1))]
    assert provider.calls == (2 if context_error else 1)
    assert len(reductions) == (1 if context_error else 0)


def test_saved_byte_calibration_is_replaced_without_losing_checkpoint_history():
    store = MemoryContext()
    store.saved = Checkpoint(summary='Preserve this fact', contents=[text_content('Prior objective')],
        measured_tokens=12000, measured_estimate=40000)
    manager = ManagedContext(store,'agent','scope','run',NS(model='offline'),'Continue',budget=ContextBudget(8192,512))
    assert manager.checkpoint.summary == 'Preserve this fact'
    assert manager.checkpoint.contents[0] == text_content('Prior objective')
    assert manager.checkpoint.measured_tokens is None
    assert manager.checkpoint.estimate_method == 'local_cl100k_base_reference'
    manager.checkpoint.measured_tokens=2500
    manager.checkpoint.measured_estimate=manager.tokens()
    manager.publish()
    resumed=ManagedContext(store,'agent','scope','run',NS(model='offline'),'Continue',budget=ContextBudget(8192,512))
    assert resumed.checkpoint.measured_tokens == 2500


@pytest.mark.asyncio
async def test_archived_result_is_projected_before_requesting_a_summary(native):
    *_, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    identity, original, contents = exchange(trace.invocations, text='large measured output ' * 10000)
    class Model:
        model='offline'
        async def generate_content_async(self, *args, **kwargs):
            raise AssertionError('A projected archived result already fits; no summary call is needed')
            yield
    budget=ContextBudget(8192,512)
    manager=ManagedContext(MemoryContext(),'agent','scope','run',Model(),'task',budget=budget)
    manager.trace, manager.trace_role=trace,'builder'
    window=RequestWindow(budget,trace,'builder',reducer=manager.reduce_final_request)
    trace.context_windows['builder']=window
    req=request(contents=contents)
    await manager.before_model(None,req)
    assert manager.checkpoint.compaction_count == 0
    assert identity in window.references
    assert trace.invocations.get('tools',identity)['response'] == original
    assert (await window.prepare(req))['estimated_input_tokens'] < input_capacity(req,budget)


@pytest.mark.asyncio
async def test_truncated_summary_repacking_preserves_every_transcript_character():
    class Model:
        model='offline'
        requests=[]
        successful_fragments=[]
        async def generate_content_async(self,req,stream=False):
            self.requests.append(req.model_copy(deep=True))
            from backend.agents.context import estimate
            assert estimate(req.model_dump(mode='json',exclude_none=True)) + req.config.max_output_tokens <= 8192
            if len(self.requests) == 1:
                yield LlmResponse(finish_reason='MAX_TOKENS')
                return
            self.successful_fragments.append(req.contents[0].parts[0].text.split('Next transcript fragment:\n',1)[1])
            yield LlmResponse(content=types.Content(role='model',parts=[types.Part(text='Confirmed earlier evidence; continue.')]))
    model=Model()
    manager=ManagedContext(MemoryContext(),'agent','scope','run',model,'Continue',budget=ContextBudget(8192,1024,max_output=2048))
    manager.checkpoint.contents=[text_content('verified evidence with provenance ' * 4000)]
    original=encoded(['',manager.checkpoint.contents])
    await manager.compact()
    assert ''.join(model.successful_fragments) == original
    assert model.requests[1].config.max_output_tokens == 2048
    assert len(model.requests[1].contents[0].parts[0].text) < len(model.requests[0].contents[0].parts[0].text)
    assert manager.checkpoint.compaction_count == 1


@pytest.mark.asyncio
async def test_request_at_exact_window_boundary_does_not_trigger_compaction():
    from backend.agents.context import estimate
    manager=ManagedContext(MemoryContext(),'agent','scope','run',NS(model='offline'),'Boundary task',budget=ContextBudget(8192,512))
    req=request('Boundary task',output=512)
    fixed=estimate(manager.rendered()) + estimate(req.config.model_dump(mode='json',exclude_none=True))
    manager.budget=ContextBudget(fixed+512,512)
    await manager.before_model(None,req)
    window=RequestWindow(manager.budget,ModelTrace(asyncio.Queue()),'agent',reducer=manager.reduce_final_request)
    stats=await window.prepare(req)
    assert stats['estimated_input_tokens'] == stats['input_capacity'] == fixed
    assert not stats['reduced_window'] and manager.checkpoint.compaction_count == 0


@pytest.mark.asyncio
async def test_projected_string_path_error_is_correctable_and_paging_reads_original(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    archive = trace.invocations
    payload = {'execution': {'stdout': '日志行\n' * 4000, 'exit_code': 0},
               'measurements': {'values': {'distance': 1.42}, 'passed': False}}
    identity = archive.start_tool('builder', 'step', 0, 'run_step', {'source': 'print(1)'})
    archive.finish_tool(identity, payload)
    req = request(contents=[types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(
        id='step', name='run_step', response=payload))])])
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(req)
    preview = req.contents[0].parts[0].function_response.response['preview']
    assert preview['execution']['stdout']['read_pointer'] == '/execution/stdout'
    wrong = await window.read_tool_result(identity, '/execution/stdout/text_preview', offset=7000, limit=6000)
    assert wrong['ok'] is False and wrong['error']['resolved_pointer'] == '/execution/stdout'
    assert wrong['error']['value_type'] == 'str' and wrong['error']['total_chars'] == len(payload['execution']['stdout'])
    assert 'text' not in wrong
    pages, offset = [], 0
    while offset is not None:
        page = await window.read_tool_result(identity, wrong['error']['resolved_pointer'], offset=offset, limit=1000)
        pages.append(page['text']); offset = page['next_offset']
    assert ''.join(pages) == payload['execution']['stdout']
    assert (await window.read_tool_result(identity, '/measurements/values/distance'))['text'] == '1.42'
    beyond = await window.read_tool_result(identity, '/execution/stdout', offset=999999)
    assert beyond['error']['code'] == 'tool_result_offset' and 'text' not in beyond
    assert archive.get('tools', identity)['response'] == payload


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [
    {'pointer': 'stdout'}, {'pointer': '/details/~2'}, {'pointer': None}, {'pointer': 4},
    {'pointer': '/' + 'a' * 2048}, {'offset': -1}, {'offset': True}, {'limit': False},
    {'limit': 0}, {'limit': 8001}, {'section': 'secret'}, {'section': []},
])
async def test_invalid_reader_arguments_are_tool_feedback(native, kwargs):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    identity, _, contents = exchange(trace.invocations)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(request(contents=contents))
    result = await window.read_tool_result(identity, **kwargs)
    assert result['ok'] is False and result['error']['code'] == 'invalid_resource'
    assert 'text' not in result


@pytest.mark.asyncio
async def test_pointer_keys_indices_and_denials_never_disclose_another_scope(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    identity, _, contents = exchange(trace.invocations)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(request(contents=contents))
    missing = await window.read_tool_result(identity, '/details/missing')
    assert missing['error']['available_keys'] == ['a/b'] and missing['error']['resolved_pointer'] == '/details'
    for pointer in ('/details/a~1b/01', '/details/a~1b/-1', '/details/a~1b/٢', '/details/a~1b/99'):
        result = await window.read_tool_result(identity, pointer)
        assert result['error']['code'] == 'tool_result_pointer' and result['error']['total_items'] == 2
    other, _, _ = exchange(trace.invocations, role='planner', call_id='private', text='private answer ' * 2000)
    for invalid in ('guessed', other, []):
        denied = await window.read_tool_result(invalid, '/details/missing')
        assert denied['error']['code'] == 'permission_denied'
        assert 'available_keys' not in denied['error'] and 'resolved_pointer' not in denied['error']
    # Even a stale/corrupted entitlement cannot cross the native role boundary.
    window.references.add(other)
    assert (await window.read_tool_result(other))['error']['code'] == 'permission_denied'
    window.set_scope('new-delegation')
    assert (await window.read_tool_result(identity))['error']['code'] == 'permission_denied'


@pytest.mark.asyncio
async def test_large_nested_preview_stays_structured_and_has_raw_section_paths(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    payload = {'execution': {'stdout': 'measured\n' * 2000},
        'measurements': {'values': {f'diagnostic_{i}': 'many values ' * 1000 for i in range(40)}}}
    identity = trace.invocations.start_tool('builder', 'nested', 0, 'run_step', {})
    trace.invocations.finish_tool(identity, payload)
    req = request(contents=[types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(
        id='nested', name='run_step', response=payload))])])
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(req)
    preview = req.contents[0].parts[0].function_response.response['preview']
    assert len(json.dumps(preview, ensure_ascii=False)) <= 6000
    assert 'text_preview' not in preview  # No opaque, cut serialization at the root.
    assert preview['execution']['stdout']['read_pointer'] == '/execution/stdout'
    assert preview['measurements']['values']['read_pointer'] == '/measurements/values'
    assert (await window.read_tool_result(identity, '/measurements/values/diagnostic_39', limit=40))['text'] == payload['measurements']['values']['diagnostic_39'][:40]


@pytest.mark.asyncio
async def test_reader_cancellation_and_unexpected_store_failure_use_existing_tool_boundary(native, monkeypatch):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    identity, _, contents = exchange(trace.invocations)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(request(contents=contents))
    def fail_store(*args):
        raise RuntimeError('private store detail')
    monkeypatch.setattr(trace.invocations, 'records', fail_store)
    result = await window.read_tool_result(identity)
    assert result['error']['code'] == 'tool_execution_error'
    assert 'private store detail' not in str(result) and result['error']['error_id']
    started, ended = asyncio.Event(), asyncio.Event()
    async def waiting(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()
    monkeypatch.setattr(window, '_read_tool_result', waiting)
    task = asyncio.create_task(window.read_tool_result(identity))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ended.is_set()


@pytest.mark.asyncio
async def test_real_adk_continues_after_bad_read_and_archives_feedback(native):
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import FunctionTool
    from backend.agents.tool_observation import archived_tool_callbacks
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    identity, result, contents = exchange(trace.invocations)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(request(contents=contents))
    turns, mutations = [], []
    async def save_diagnostic() -> dict:
        mutations.append('one diagnostic')
        return {'saved': True}
    class Fixture(BaseLlm):
        model: str = 'offline-result-reader'
        async def generate_content_async(self, request, stream=False):
            # ADK's request also owns callable tools with native DB locks;
            # snapshot only public contents for this offline model fixture.
            turns.append([c.model_dump(mode='json') for c in request.contents])
            responses = [p.function_response for c in request.contents for p in c.parts or [] if p.function_response]
            turn = len(turns)
            if turn == 1:
                name, args = 'read_tool_result', {'invocation_id': identity, 'pointer': '/stdout/text_preview'}
            elif turn == 2:
                assert responses[-1].response['error']['code'] == 'tool_result_pointer'
                name, args = 'read_tool_result', {'invocation_id': identity, 'pointer': '/stdout', 'limit': 30}
            elif turn == 3:
                assert responses[-1].response['text'] == result['stdout'][:30]
                name, args = 'save_diagnostic', {}
            else:
                assert turn == 4
                yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='Continued after correcting the read.')]))
                return
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
                id=f'read-test-{turn}', name=name, args=args))]))
    agent = LlmAgent(name='builder', model=Fixture(), tools=[FunctionTool(window.read_tool_result), FunctionTool(save_diagnostic)],
        **archived_tool_callbacks(trace, 'builder'))
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name='read-test', user_id='user')
    async with Runner(app_name='read-test', agent=agent, session_service=sessions) as runner:
        events = [event async for event in runner.run_async(user_id='user', session_id=session.id,
            new_message=types.Content(role='user', parts=[types.Part(text='Read the issued result, then save one diagnostic.')]))]
    assert mutations == ['one diagnostic'] and len(turns) == 4
    assert events[-1].content.parts[0].text == 'Continued after correcting the read.'
    rows = trace.invocations.records('tools')
    reads = [row for row in rows.values() if row['name'] == 'read_tool_result']
    assert len(reads) == 2 and all(row['state'] == 'returned' and row['response_complete'] for row in reads)
    assert reads[0]['response']['ok'] is False and reads[1]['response']['text'] == result['stdout'][:30]
    assert rows[identity]['response'] == result


@pytest.mark.asyncio
async def test_old_checkpoint_projection_refreshes_only_from_matching_native_archive(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    identity, result, contents = exchange(trace.invocations)
    response = contents[1].parts[0].function_response
    legacy = {'context_projection': {'native_invocation_id': identity, 'run_id': trace.invocations.run_id, 'truncated': True},
              'preview': {'text_preview': '{"stdout": {"text_preview": "cut serialized JSON', 'truncated': True}}
    response.response = deepcopy(legacy)
    req = request(contents=contents)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    assert (await window.prepare(req))['projected_tool_results'] == 1
    projected = req.contents[1].parts[0].function_response.response
    assert projected['context_projection']['version'] == 2
    assert projected['preview']['stdout']['read_pointer'] == '/stdout'
    assert (await window.read_tool_result(identity, '/stdout', limit=50))['text'] == result['stdout'][:50]
    assert (await window.prepare(req))['projected_tool_results'] == 0
    # A fabricated projection for a different role cannot issue access.
    wrong = RequestWindow(window.budget, trace, 'planner')
    req.contents[1].parts[0].function_response.response = deepcopy(legacy)
    assert (await wrong.prepare(req))['projected_tool_results'] == 0
    assert (await wrong.read_tool_result(identity))['error']['code'] == 'permission_denied'


@pytest.mark.asyncio
async def test_reader_resolves_real_keys_even_when_they_share_preview_names(native):
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=native[-1])
    result = {'stdout': {'text_preview': 'a real archived field'},
              '': {'~1': ['first', 'second']}, 'large': 'data ' * 5000}
    identity = trace.invocations.start_tool('builder', 'real-key', 0, 'tool', {})
    trace.invocations.finish_tool(identity, result)
    window = RequestWindow(ContextBudget(100000, 512), trace, 'builder')
    await window.prepare(request(contents=[types.Content(role='user', parts=[types.Part(function_response=types.FunctionResponse(
        id='real-key', name='tool', response=result))])]))
    assert (await window.read_tool_result(identity, '/stdout/text_preview'))['text'] == 'a real archived field'
    assert (await window.read_tool_result(identity, '//~01/1'))['text'] == 'second'
