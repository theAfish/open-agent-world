"""Recover an unpublished ADK model response, never restart its Agent or tools.

ADK's SSE contract ends with an aggregated partial=False response. Partial
chunks are preview-only; hold completed responses until clean EOF, so a broken
stream cannot publish a tool call and then replay it on retry.
"""
from __future__ import annotations

import asyncio
import copy
import time
from contextlib import aclosing
from typing import Any

from . import model_observation as observation
from .models import AgentRuntimeError


class RequestRecoveryExhausted(AgentRuntimeError):
    request_recovery_exhausted = True


class IncompleteModelResponse(ConnectionError):
    """A stream closed cleanly without its required aggregate response."""


def _snapshot(request: Any) -> Any:
    # tools_dict holds live capability objects; copy data, not those objects.
    return request.model_copy(update={
        "contents": copy.deepcopy(request.contents),
        "config": copy.deepcopy(request.config),
    })


async def recover_request(delegate: Any, request: Any, trace: observation.ModelTrace,
                          role: str, *, stream: bool = False, retries: int = 1,
                          retry_delay: float = 0.5):
    snapshot = _snapshot(request)
    trace.recovery_roles.add(role)
    request_id = trace.pending[role][0]
    for attempt in range(1, retries + 2):
        trace.request_attempts[role] = attempt
        started = time.monotonic()
        trace.pending[role] = (request_id, started, "model")
        trace._last_activity.pop(role, None)
        trace._reasoning.pop(role, None)
        trace._last_reasoning_emit.pop(role, None)
        if attempt > 1 and trace.checkpoint_store is not None:
            trace.checkpoint_store.start(role, request_id, trace.run_attempt, snapshot)
        if attempt > 1:
            trace.queue.put_nowait(("progress", {
                "kind": "status", "phase": "model", "role": role,
                "model_request": request_id, "model_attempt": attempt, "run_attempt": trace.run_attempt,
                "text": f"{role.replace('_', ' ')} · model request {request_id}, attempt {attempt} started",
            }))
        completed = []
        content_chunks = 0
        first_content = None
        try:
            async with aclosing(delegate.generate_content_async(_snapshot(snapshot), stream=stream)) as source:
                while True:
                    last = trace._last_activity.get(role)
                    limit = (observation.STREAM_STALL_SECONDS if last is not None
                             else observation.STREAM_FIRST_BYTE_SECONDS)
                    remaining = limit - (time.monotonic() - (last if last is not None else started))
                    # asyncio.wait distinguishes our watchdog from a TimeoutError
                    # raised by the provider. Always settle the in-flight read.
                    read = asyncio.create_task(anext(source))
                    try:
                        done, _ = await asyncio.wait({read}, timeout=max(0, remaining))
                        if not done:
                            raise observation.ModelStreamStalled({
                                "role": role, "model_request": request_id,
                                "stage": "mid_stream" if last is not None else "first_byte",
                                "idle_seconds": time.monotonic() - (last if last is not None else started),
                            })
                        response = read.result()
                    except StopAsyncIteration:
                        break
                    finally:
                        if not read.done():
                            read.cancel()
                        await asyncio.gather(read, return_exceptions=True)
                    before = trace._last_activity.get(role)
                    content = getattr(response, "content", None)
                    trace.note_activity(role, content)
                    if trace._last_activity.get(role) != before:
                        content_chunks += 1
                        if first_content is None:
                            first_content = time.monotonic()
                    if getattr(response, "partial", False):
                        trace.observe_partial(role, content)
                    else:
                        completed.append(response)
            if not completed:
                raise IncompleteModelResponse("Provider stream ended without a completed response")
        except asyncio.CancelledError:
            preview = trace.interrupted_reasoning(role)
            if preview:
                trace.queue.put_nowait(("progress", preview))
            raise
        except Exception as error:
            preview = trace.interrupted_reasoning(role)
            if preview:
                trace.queue.put_nowait(("progress", preview))
            retryable = observation.model_stream_disconnected(error)
            retrying = retryable and attempt <= retries
            now = time.monotonic()
            last = trace._last_activity.get(role)
            local = isinstance(error, observation.ModelStreamStalled)
            # Exception bodies can echo credentials, input files or entire
            # requests. Emit only structural diagnostics, never arbitrary str(error).
            diagnostic = {
                "kind": "status", "phase": "model_recovery", "role": role,
                "model_request": request_id, "model_attempt": attempt,
                "run_attempt": trace.run_attempt, "retrying": retrying,
                "recovery_scope": "model_request", "error_type": type(error).__name__,
                "error_origin": "local_idle_watchdog" if local else "provider_or_adapter",
                "elapsed_seconds": round(now - started, 3),
                "idle_seconds": round(now - (last if last is not None else started), 3),
                "first_content_seconds": round(first_content - started, 3) if first_content else None,
                "received_content_chunks": content_chunks,
                "stage": "mid_stream" if last is not None else "first_byte",
                "text": (f"{role.replace('_', ' ')} · model request {request_id}, attempt {attempt} "
                         f"interrupted ({type(error).__name__}); "
                         + ("retrying the same request; completed tools will not be replayed"
                            if retrying else "request stopped; completed tools were not replayed")),
            }
            status = getattr(error, "status_code", None)
            if isinstance(status, int):
                diagnostic["http_status"] = status
            trace.queue.put_nowait(("progress", diagnostic))
            if not retryable:
                raise
            if not retrying:
                raise RequestRecoveryExhausted(
                    f"{role} model request {request_id} failed after {attempt} attempts "
                    f"({type(error).__name__}); completed tools were not replayed. "
                    "Inspect the model_recovery event for connection diagnostics."
                ) from error
            await asyncio.sleep(retry_delay)
            continue
        for response in completed:
            yield response
        return


def recoverable_model(model: Any, trace: observation.ModelTrace, role: str) -> Any:
    """Bind a wrapper per role so specialists recover inside their delegation."""
    if isinstance(model, str):
        from google.adk.models import LLMRegistry
        model = LLMRegistry.new_llm(model)
    if not callable(getattr(model, "generate_content_async", None)):
        return model  # Lightweight runtime bindings may use opaque model doubles.
    if hasattr(model, "transport_retry_limit"):
        # One recovery budget and one visible counter, not nested 3 x 2 retries.
        model = model.model_copy(update={"transport_retry_limit": 0})
    from google.adk.models.base_llm import BaseLlm
    from pydantic import Field

    class RecoverableModel(BaseLlm):
        delegate: Any = Field(exclude=True)
        trace: Any = Field(exclude=True)
        role: str

        @property
        def capabilities(self):
            return self.delegate.capabilities

        async def generate_content_async(self, llm_request, stream=False):
            async for response in recover_request(self.delegate, llm_request, self.trace,
                                                  self.role, stream=stream):
                yield response

    return RecoverableModel(model=model.model, delegate=model, trace=trace, role=role)
