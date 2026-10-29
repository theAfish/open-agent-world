"""Exercise recovery through the real ADK response conversion boundary."""
import asyncio
import json

import pytest
from google.adk.models.lite_llm import LiteLLMClient
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from litellm import ModelResponse, ModelResponseStream

from backend.agents.resilient_litellm import ResilientLiteLlm
from backend.agents.model_observation import ModelTrace, model_stream_disconnected
from backend.agents.request_recovery import RequestRecoveryExhausted, RequestRetryPolicy, recoverable_model


class UnsupportedChoice(Exception):
    status_code = 400


def forced_request():
    return LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="Write once")])],
        config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[
            types.FunctionDeclaration(name="write_file"), types.FunctionDeclaration(name="delete_file")])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY", allowed_function_names=["write_file"]))))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("message", ["Thinking mode does not support this tool_choice",
    "`tool_choice` is not supported in thinking mode", "tool_choice must be auto when thinking is enabled"])
async def test_negotiates_rejected_choice_once_preserving_tools_and_original_request(monkeypatch, stream, message):
    requests = []
    async def chunks():
        yield ModelResponseStream(choices=[{"index":0,"delta":{"role":"assistant", "tool_calls":[
            {"index":0,"id":"call_1","type":"function","function":{"name":"write_file", "arguments":'{"content":"ready"}'}}]},
            "finish_reason":None}])
        yield ModelResponseStream(choices=[{"index":0,"delta":{},"finish_reason":"tool_calls"}])
    async def complete(self, **kwargs):
        requests.append(kwargs)
        if kwargs.get("tool_choice") == "required":
            raise UnsupportedChoice(message)
        return chunks() if stream else response('{"content":"ready"}')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    model = ResilientLiteLlm("openai/test", transport_retry_limit=0)
    request = forced_request()
    events = [event async for event in model.generate_content_async(request, stream=stream)]
    final = next(event for event in reversed(events) if not event.partial)
    assert final.content.parts[0].function_call.args == {"content":"ready"}
    assert len(requests) == 2 and requests[0]["tool_choice"] == "required"
    assert requests[1].get("tool_choice", "auto") == "auto"
    assert [t["function"]["name"] for t in requests[1]["tools"]] == ["write_file"]
    assert final.custom_metadata["oaw_adapter"]["requested_tool_choice"] == "required"
    assert str(request.config.tool_config.function_calling_config.mode).endswith("ANY")
    assert len(request.config.tools[0].function_declarations) == 2
    # A later call on this model reuses the observed capability, without another 400.
    _ = [event async for event in model.generate_content_async(request, stream=stream)]
    assert len(requests) == 3 and requests[-1].get("tool_choice", "auto") == "auto"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,message", [(400,"Unsupported tool schema"),
    (401,"Unsupported tool_choice"), (400,"Invalid max_tokens"), (400,"Invalid tool_choice: unknown function"),
    (400,"Unsupported max_tokens; request contained tool_choice='required'")])
async def test_choice_fallback_does_not_hide_other_permanent_errors(monkeypatch, status, message):
    class Failure(Exception):
        status_code = status
    calls = []
    async def complete(self, **kwargs):
        calls.append(kwargs)
        raise Failure(message)
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    with pytest.raises(Failure):
        _ = [r async for r in ResilientLiteLlm("openai/test").generate_content_async(forced_request())]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_choice_negotiation_does_not_repeat_an_already_automatic_rejection(monkeypatch):
    calls = []
    async def complete(self, **kwargs):
        calls.append(kwargs)
        raise UnsupportedChoice("Thinking mode does not support this tool_choice")
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    with pytest.raises(UnsupportedChoice):
        _ = [r async for r in ResilientLiteLlm("openai/test").generate_content_async(forced_request())]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_choice_negotiation_never_replays_a_published_stream(monkeypatch):
    from google.adk.models.llm_response import LlmResponse
    calls = []
    async def generate(self, request, stream=False):
        calls.append(request)
        yield LlmResponse(partial=True, content=types.Content(role="model", parts=[types.Part(text="started")]))
        raise UnsupportedChoice("Thinking mode does not support this tool_choice")
    monkeypatch.setattr(LiteLlm, "generate_content_async", generate)
    with pytest.raises(UnsupportedChoice):
        _ = [r async for r in ResilientLiteLlm("openai/test").generate_content_async(forced_request(), stream=True)]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_supported_required_choice_is_preserved(monkeypatch):
    calls = []
    async def complete(self, **kwargs):
        calls.append(kwargs)
        return response('{"content":"ready"}')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    events = [r async for r in ResilientLiteLlm("openai/test").generate_content_async(forced_request())]
    assert len(calls) == 1 and calls[0]["tool_choice"] == "required"
    assert not events[0].custom_metadata


@pytest.mark.parametrize("model,endpoint,required", [
    ("openai/deepseek-flash","https://api.deepseek.com/v1",True),
    ("deepseek/custom-alias","",True),
    ("openai/deepseek-flash","https://gateway.example/v1",False),
    ("openai/deepseek-flash","https://api.deepseek.com.example/v1",False),
    ("openai/another-model","https://api.openai.com/v1",False)])
def test_reasoning_protocol_comes_from_route_not_a_model_name(model, endpoint, required):
    from backend.agents.request_window import ContextCapabilities
    adapter = ResilientLiteLlm(model, api_base=endpoint)
    assert ContextCapabilities.for_model(adapter).preserve_tool_reasoning is required


def response(arguments):
    return ModelResponse(choices=[{"message": {"role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "write_file", "arguments": arguments}}]}, "finish_reason": "tool_calls"}])


@pytest.mark.asyncio
async def test_corrects_arguments_without_publishing_rejected_call(monkeypatch):
    requests = []
    async def complete(self, **kwargs):
        requests.append(kwargs)
        return response('{"content":"bad "quote""}') if len(requests) == 1 else response('{"content":"fixed"}')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part.from_text(text="write file")])])
    events = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(request)]
    assert len(events) == 1
    assert events[0].content.parts[0].function_call.args == {"content": "fixed"}
    assert len(requests) == 2
    assert "No tool in that response was executed" in str(requests[1]["messages"])
    assert "column" in str(requests[1]["messages"])
    assert not any(m["role"] == "assistant" for m in requests[1]["messages"])
    assert len(request.contents) == 1


@pytest.mark.asyncio
async def test_streamed_thoughts_do_not_disable_tool_argument_correction(monkeypatch):
    requests = []

    async def chunks(arguments):
        yield ModelResponseStream(choices=[{"index": 0, "delta": {
            "role": "assistant", "reasoning_content": "Choose a small executable change."}, "finish_reason": None}])
        yield ModelResponseStream(choices=[{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function", "function": {
                "name": "write_file", "arguments": arguments}}]}, "finish_reason": None}])
        yield ModelResponseStream(choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}])

    async def complete(self, **kwargs):
        requests.append(kwargs)
        return chunks('{"content":"bad "quote""}' if len(requests) == 1 else '{"content":"fixed"}')

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    request = forced_request()
    events = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(request, stream=True)]
    calls = [part.function_call for event in events if event.content for part in event.content.parts if part.function_call]
    assert len(requests) == 2 and len(calls) == 1 and calls[0].args == {"content": "fixed"}
    assert any(part.thought for event in events if event.content for part in event.content.parts)
    assert events[-1].custom_metadata["oaw_adapter"]["tool_argument_corrections"] == 1
    assert "No tool in that response was executed" in str(requests[1]["messages"])
    assert len(request.contents) == 1


@pytest.mark.asyncio
async def test_request_boundary_corrects_streamed_text_and_bad_batch_without_replaying_prior_tools(monkeypatch):
    requests = []

    async def chunks(attempt):
        yield ModelResponseStream(choices=[{"index": 0, "delta": {
            "role": "assistant", "content": "I will write the next files."}, "finish_reason": None}])
        arguments = ([json.dumps({"content": "earlier write"})] if attempt == 1 else
                     [json.dumps({"content": "valid sibling"}),
                      '{"content":"private bad "quote""}' if attempt == 2 else json.dumps({"content": "fixed"})])
        yield ModelResponseStream(choices=[{"index": 0, "delta": {"tool_calls": [
            {"index": index, "id": f"call_{attempt}_{index}", "type": "function", "function": {
                "name": "write_file", "arguments": args}} for index, args in enumerate(arguments)
        ]}, "finish_reason": None}])
        yield ModelResponseStream(choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}])

    async def complete(self, **kwargs):
        requests.append(kwargs)
        return chunks(len(requests))

    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    trace = ModelTrace(asyncio.Queue())
    request = forced_request()
    model = recoverable_model(ResilientLiteLlm("openai/test"), trace, "agent")
    trace.begin("agent", request)
    first = [event async for event in model.generate_content_async(request, stream=True)]
    executed = [part.function_call.args for event in first for part in event.content.parts if part.function_call]
    call = next(part.function_call for event in first for part in event.content.parts if part.function_call)
    request.contents.extend([event.content for event in first] + [types.Content(role="user", parts=[
        types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response={"saved": True}))])])
    before = request.model_dump_json()
    trace.begin("agent", request)
    second = [event async for event in model.generate_content_async(request, stream=True)]
    executed.extend(part.function_call.args for event in second for part in event.content.parts if part.function_call)
    assert executed == [{"content": "earlier write"}, {"content": "valid sibling"}, {"content": "fixed"}]
    assert len(requests) == 3 and request.model_dump_json() == before
    assert all(not event.partial for event in first + second)
    assert requests[2]["messages"][:len(requests[1]["messages"])] == requests[1]["messages"]
    assert "No tool in that response was executed" in str(requests[2]["messages"])
    assert "earlier successful responses" in str(requests[2]["messages"])
    assert all("argument_retry_limit" not in sent for sent in requests)
    events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
    recovery = [event for event in events if event.get("phase") == "model_recovery"]
    assert len(recovery) == 1 and recovery[0]["error_origin"] == "tool_arguments" and recovery[0]["retrying"]
    assert "private bad" not in str(events)


@pytest.mark.asyncio
async def test_request_argument_corrections_share_retry_budget_and_do_not_restart_agent(monkeypatch):
    requests = []
    async def complete(self, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise ConnectionResetError("private connection detail")
        return response('{"private_payload":')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    trace = ModelTrace(asyncio.Queue())
    request = forced_request()
    trace.begin("agent", request)
    model = recoverable_model(ResilientLiteLlm("openai/test"), trace, "agent",
        RequestRetryPolicy(max_attempts=3, initial_delay_seconds=0.0))
    published = []
    with pytest.raises(RequestRecoveryExhausted, match="Tool arguments remained invalid after 3") as error:
        async for event in model.generate_content_async(request):
            published.append(event)
    assert len(requests) == 3 and not published and len(request.contents) == 1
    assert not model_stream_disconnected(error.value)
    events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
    assert "private" not in str(events) + str(error.value)


@pytest.mark.asyncio
async def test_request_boundary_does_not_correct_transport_json(monkeypatch):
    requests = []
    async def complete(self, **kwargs):
        requests.append(kwargs)
        raise json.JSONDecodeError("transport", "bad", 0)
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    trace = ModelTrace(asyncio.Queue())
    request = forced_request()
    trace.begin("agent", request)
    model = recoverable_model(ResilientLiteLlm("openai/test"), trace, "agent")
    with pytest.raises(json.JSONDecodeError):
        _ = [event async for event in model.generate_content_async(request)]
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("published_kind", ["tool", "text"])
async def test_argument_correction_never_replays_a_published_action(monkeypatch, published_kind):
    from google.adk.models.lite_llm import _parse_tool_call_arguments
    from google.adk.models.llm_response import LlmResponse
    calls = []

    async def generate(self, request, stream=False):
        calls.append(request)
        part = (types.Part(function_call=types.FunctionCall(name="write_file", args={"content": "done"}))
                if published_kind == "tool" else types.Part(text="A published answer"))
        yield LlmResponse(partial=True, content=types.Content(role="model", parts=[part]))
        _parse_tool_call_arguments('{"content":"bad "quote""}')

    monkeypatch.setattr(LiteLlm, "generate_content_async", generate)
    with pytest.raises(json.JSONDecodeError):
        _ = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(forced_request(), stream=True)]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_bounded_recovery_does_not_expose_payload(monkeypatch):
    calls = 0
    async def complete(self, **kwargs):
        nonlocal calls
        calls += 1
        return response('{"private_payload":')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    with pytest.raises(RuntimeError, match="after 2 correction attempts") as error:
        _ = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(LlmRequest())]
    assert calls == 3
    assert "private_payload" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.CancelledError(), json.JSONDecodeError("transport", "bad", 0), RuntimeError("provider down")])
async def test_does_not_retry_transport_or_cancellation(monkeypatch, failure):
    calls = 0
    async def complete(self, **kwargs):
        nonlocal calls
        calls += 1
        raise failure
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    with pytest.raises(type(failure)):
        _ = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(LlmRequest())]
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_connection_once_before_any_chunk_is_published(monkeypatch):
    class APIConnectionError(Exception):
        pass

    calls = 0
    async def complete(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise APIConnectionError("connection reset")
        return response('{"content":"ready"}')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part.from_text(text="write")])])
    events = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(request)]
    assert calls == 2
    assert events[0].content.parts[0].function_call.args == {"content": "ready"}


@pytest.mark.asyncio
async def test_retries_two_transient_failures_before_any_chunk(monkeypatch):
    class ServiceUnavailable(Exception):
        status_code = 503

    calls = 0
    async def complete(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ServiceUnavailable("temporary")
        return response('{"content":"ready"}')
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    events = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(LlmRequest())]
    assert calls == 3
    assert events[0].content.parts[0].function_call.args == {"content": "ready"}


@pytest.mark.asyncio
async def test_does_not_retry_permanent_provider_status(monkeypatch):
    class Unauthorized(Exception):
        status_code = 401

    calls = 0
    async def complete(self, **kwargs):
        nonlocal calls
        calls += 1
        raise Unauthorized("bad credentials")
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    with pytest.raises(Unauthorized):
        _ = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(LlmRequest())]
    assert calls == 1


@pytest.mark.asyncio
async def test_never_retries_after_publishing_partial_content(monkeypatch):
    calls = 0
    async def streamed(self, request, stream=False):
        nonlocal calls
        calls += 1
        yield object()
        raise ConnectionResetError("stream dropped")
    monkeypatch.setattr(LiteLlm, "generate_content_async", streamed)
    events = []
    with pytest.raises(ConnectionResetError):
        async for event in ResilientLiteLlm("openai/test").generate_content_async(LlmRequest(), stream=True):
            events.append(event)
    assert len(events) == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_rejects_entire_parallel_batch_before_dispatch(monkeypatch):
    calls = 0
    async def complete(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            return response('{"content":"corrected batch"}')
        batch = response('{"content":"valid sibling"}')
        broken = response('{"content":')
        batch.choices[0].message.tool_calls.extend(broken.choices[0].message.tool_calls)
        return batch
    monkeypatch.setattr(LiteLLMClient, "acompletion", complete)
    events = [event async for event in ResilientLiteLlm("openai/test").generate_content_async(LlmRequest())]
    assert len(events) == 1
    assert len(events[0].content.parts) == 1
    assert events[0].content.parts[0].function_call.args == {"content": "corrected batch"}
    assert calls == 2
