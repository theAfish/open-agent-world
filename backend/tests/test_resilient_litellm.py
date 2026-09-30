"""Exercise recovery through the real ADK response conversion boundary."""
import asyncio
import json

import pytest
from google.adk.models.lite_llm import LiteLLMClient
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from litellm import ModelResponse

from backend.agents.resilient_litellm import ResilientLiteLlm


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
    assert len(request.contents) == 1


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
