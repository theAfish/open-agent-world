"""Recover an unpublished ADK model response, never restart its Agent or tools.

ADK's SSE contract ends with an aggregated partial=False response. Partial
chunks are preview-only; hold completed responses until clean EOF, so a broken
stream cannot publish a tool call and then replay it on retry.
"""
from __future__ import annotations

import asyncio
import copy
import random
import time
from contextlib import aclosing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import model_observation as observation
from .adk_tool_errors import append_argument_feedback, invalid_tool_arguments
from .models import AgentRuntimeError


class RequestRecoveryExhausted(AgentRuntimeError):
    request_recovery_exhausted = True


class IncompleteModelResponse(ConnectionError):
    """A stream closed cleanly without its required aggregate response."""


class RequestRetryPolicy(BaseModel):
    """One shared budget for an unpublished model request, not Agent/tool replay."""
    model_config = ConfigDict(extra="forbid", strict=True)
    max_attempts: int = Field(default=5, ge=1, le=20)
    initial_delay_seconds: float = Field(default=2.0, ge=0, le=60, allow_inf_nan=False)
    max_delay_seconds: float = Field(default=30.0, ge=0, le=60, allow_inf_nan=False)
    jitter_ratio: float = Field(default=0.2, ge=0, le=0.5, allow_inf_nan=False)

    @model_validator(mode="after")
    def delay_range(self):
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds must be at least initial_delay_seconds")
        return self


def _retry_delay(error, failed_attempt, policy):
    delay = min(policy.max_delay_seconds, policy.initial_delay_seconds * 2 ** (failed_attempt - 1))
    delay = min(policy.max_delay_seconds, delay * random.uniform(1, 1 + policy.jitter_ratio))
    # Honor bounded Retry-After without publishing exception bodies or headers.
    headers = getattr(getattr(error, "response", None), "headers", None) or getattr(error, "headers", None)
    if headers is not None:
        value = headers.get("retry-after", headers.get("Retry-After"))
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                seconds = (stamp - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError, AttributeError):
                seconds = 0
        import math
        if math.isfinite(seconds):
            delay = max(delay, min(policy.max_delay_seconds, max(0, seconds)))
    return delay


async def _wait_to_retry(trace, role, request_id, next_attempt, max_attempts, delay):
    remaining = delay
    while remaining > 0:
        trace.queue.put_nowait(("progress", {
            "kind": "status", "phase": "model_retry_wait", "role": role,
            "model_request": request_id, "run_attempt": trace.run_attempt,
            "next_attempt": next_attempt, "max_attempts": max_attempts,
            "retry_delay_seconds": round(remaining, 3),
            "text": f"{role.replace('_', ' ')} · waiting to reconnect; attempt {next_attempt}/{max_attempts} in {remaining:.1f}s",
        }))
        interval = min(15, remaining)
        await asyncio.sleep(interval)  # Native cancellation interrupts the wait.
        remaining -= interval


def _snapshot(request: Any) -> Any:
    # tools_dict holds live capability objects; copy data, not those objects.
    return request.model_copy(update={
        "contents": copy.deepcopy(request.contents),
        "config": copy.deepcopy(request.config),
    })


def _error_chain_types(error):
    """Bounded structural diagnostics only; exception messages can contain keys."""
    names, seen = [], set()
    while error is not None and id(error) not in seen and len(names) < 6:
        seen.add(id(error))
        names.append(type(error).__name__)
        error = error.__cause__ or error.__context__
    return names


def _context_window_exceeded(error):
    # Only explicit provider/SDK context errors. A generic HTTP 400, invalid
    # schema or authentication rejection is not a compaction signal.
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if type(error).__name__ == "ContextWindowExceededError":
            return True
        code = getattr(error, "code", None)
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            detail = body.get("error", body)
            if isinstance(detail, dict):
                code = detail.get("code", code)
        if isinstance(code, str) and code in {"context_length_exceeded", "max_context_length_exceeded", "prompt_too_long"}:
            return True
        error = error.__cause__ or error.__context__
    return False


async def recover_request(delegate: Any, request: Any, trace: observation.ModelTrace,
                          role: str, *, stream: bool = False, retries: int | None = None,
                          retry_delay: float | None = None, policy: RequestRetryPolicy | None = None):
    policy = policy or RequestRetryPolicy()
    if retries is not None or retry_delay is not None:
        values = policy.model_dump()
        if retries is not None:
            values["max_attempts"] = retries + 1
        if retry_delay is not None:
            values.update(initial_delay_seconds=retry_delay, max_delay_seconds=max(retry_delay, policy.max_delay_seconds))
        policy = RequestRetryPolicy.model_validate(values)
    snapshot = _snapshot(request)
    if hasattr(delegate, "argument_retry_limit"):
        # This boundary owns the shared budget and buffers every response until
        # clean EOF. The adapter alone cannot safely retry after visible text.
        delegate = delegate.model_copy(update={"argument_retry_limit": 0})
    window = trace.context_windows.get(role)
    if window is not None:
        try:
            await window.prepare(snapshot)
        except Exception as error:
            # A rejected local preflight never reaches the provider/retry loop,
            # but its complete assembled request still belongs in OAW's native
            # archive. Distinguish it from an interrupted network attempt.
            from .request_window import input_capacity, request_usage
            trace.context_stats[role] = {**request_usage(snapshot),
                "input_capacity": input_capacity(snapshot, window.budget),
                "known_window": window.budget.known, "role": role, "scope": window.scope,
                "phase": "context_preflight", "provider_dispatched": False,
                "error_origin": "local_context_preflight", "error_type": type(error).__name__}
            archive_id = trace.archive_model_start(role, snapshot, model=getattr(delegate, "model", None), stream=stream)
            trace.interrupted_reasoning(role)
            if archive_id is not None:
                trace.invocations.interrupt_model(archive_id, error_type=type(error).__name__)
            if role in trace.pending:
                trace.queue.put_nowait(("progress", {
                    "kind": "status", "phase": "model_preflight_rejected", "role": role,
                    "model_request": trace.pending[role][0], "model_attempt": 1,
                    "run_attempt": trace.run_attempt, "error_origin": "local_context_preflight",
                    "error_type": type(error).__name__,
                    "provider_dispatched": False, "context": trace.context_stats[role],
                    "text": f"{role.replace('_', ' ')} · context check stopped request before provider dispatch",
                }))
            raise
    # Announce and checkpoint the assembled history after preflight, using the
    # same snapshot as the native model invocation archive. An earlier callback
    # snapshot can contain thousands of stream envelopes that never go on wire.
    if trace.pending[role][2] == "context":
        trace.model_ready(role, snapshot)
    trace.recovery_roles.add(role)
    request_id = trace.pending[role][0]
    context_recovered = False
    # One provider-overflow recovery is separate from network retries. Neither
    # re-enters the Agent loop or publishes a tool call from a failed stream.
    for attempt in range(1, policy.max_attempts + 2):
        attempt_limit = policy.max_attempts + int(context_recovered)
        active_deadline = trace.recovery_deadlines.get(role)
        if active_deadline:
            deadline, seconds = active_deadline
            deadline.reschedule(asyncio.get_running_loop().time() + seconds)
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
        response_chunks = 0
        first_response = last_response = None
        archive_id = trace.archive_model_start(role, snapshot, model=getattr(delegate, "model", None), stream=stream)
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
                    response_chunks += 1
                    last_response = time.monotonic()
                    if first_response is None:
                        first_response = last_response
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
        except (asyncio.CancelledError, GeneratorExit):
            preview = trace.interrupted_reasoning(role)
            if preview:
                trace.queue.put_nowait(("progress", preview))
            raise
        except Exception as error:
            if archive_id is not None:
                trace.invocations.interrupt_model(archive_id, error_type=type(error).__name__)
            preview = trace.interrupted_reasoning(role)
            if preview:
                trace.queue.put_nowait(("progress", preview))
            if window is not None and not context_recovered and _context_window_exceeded(error):
                context_recovered = True
                if active_deadline:
                    deadline.reschedule(None)  # Compaction is its own model call.
                await window.prepare(snapshot, provider_overflow=True)
                trace.queue.put_nowait(("progress", {
                    "kind": "status", "phase": "context_recovery", "role": role,
                    "model_request": request_id, "model_attempt": attempt,
                    "recovery_scope": "model_request", "error_type": type(error).__name__,
                    "context": window.last_stats,
                    "text": f"{role.replace('_', ' ')} · provider context overflow; compacted history and retrying the model request",
                }))
                continue
            argument_error = invalid_tool_arguments(error)
            retryable = argument_error or observation.model_stream_disconnected(error)
            retrying = retryable and attempt < attempt_limit
            delay = (0.0 if argument_error else _retry_delay(error, attempt - int(context_recovered), policy)) if retrying else None
            now = time.monotonic()
            last = trace._last_activity.get(role)
            local = isinstance(error, observation.ModelStreamStalled)
            # Exception bodies can echo credentials, input files or entire
            # requests. Emit only structural diagnostics, never arbitrary str(error).
            diagnostic = {
                "kind": "status", "phase": "model_recovery", "role": role,
                "model_request": request_id, "model_attempt": attempt,
                "run_attempt": trace.run_attempt, "retrying": retrying,
                "max_attempts": attempt_limit, "next_attempt": attempt + 1 if retrying else None,
                "retry_delay_seconds": round(delay, 3) if delay is not None else None,
                "recovery_scope": "model_request", "error_type": type(error).__name__,
                "error_origin": "tool_arguments" if argument_error else "local_idle_watchdog" if local else "provider_or_adapter",
                "elapsed_seconds": round(now - started, 3),
                "idle_seconds": round(now - (last if last is not None else started), 3),
                "first_content_seconds": round(first_content - started, 3) if first_content else None,
                "received_content_chunks": content_chunks,
                "observed_response_chunks": response_chunks,
                "empty_response_chunks": response_chunks - content_chunks,
                "first_response_seconds": round(first_response - started, 3) if first_response is not None else None,
                "last_response_idle_seconds": round(now - last_response, 3) if last_response is not None else None,
                "watchdog_scope": "adapter_content",
                "error_chain_types": _error_chain_types(error),
                "stage": "mid_stream" if last is not None else "first_byte",
                "text": (f"{role.replace('_', ' ')} · model request {request_id}, attempt {attempt} "
                         f"interrupted ({type(error).__name__}); "
                         + (f"reconnecting in {delay:.1f}s (next attempt {attempt + 1}/{attempt_limit}); completed tools will not be replayed"
                            if retrying else "request stopped; completed tools were not replayed")),
            }
            if argument_error:
                diagnostic["text"] = (
                    f"{role.replace('_', ' ')} model request {request_id}: invalid tool arguments; "
                    + (f"correcting response (next attempt {attempt + 1}/{attempt_limit}). "
                       if retrying else "correction budget exhausted. ")
                    + "No tools from the rejected response were executed; earlier completed tools will not be replayed."
                )
            status = getattr(error, "status_code", None)
            if isinstance(status, int):
                diagnostic["http_status"] = status
            trace.queue.put_nowait(("progress", diagnostic))
            if not retryable:
                raise
            if not retrying:
                if argument_error:
                    raise RequestRecoveryExhausted(
                        f"Tool arguments remained invalid after {attempt} model request attempts. "
                        "No tools from the rejected responses were executed; earlier completed work is preserved. "
                        "Try splitting the remaining operation into smaller tool calls."
                    ) from error
                raise RequestRecoveryExhausted(
                    f"{role} model request {request_id} failed after {attempt} attempts "
                    f"({type(error).__name__}); completed tools were not replayed. "
                    "Inspect the model_recovery event for connection diagnostics."
                ) from error
            if active_deadline:
                deadline.reschedule(None)  # Backoff is not model computation time.
            if argument_error:
                append_argument_feedback(snapshot, error)
                if window is not None:
                    # Corrective feedback also consumes context; compact or stop
                    # before dispatching an oversized corrected request.
                    await window.prepare(snapshot)
            await _wait_to_retry(trace, role, request_id, attempt + 1, attempt_limit, delay)
            continue
        if archive_id is not None:
            trace.invocations.finish_model(archive_id, completed)
        for response in completed:
            if window is not None:
                window.observe_usage(response)
            yield response
        return


def recoverable_model(model: Any, trace: observation.ModelTrace, role: str,
                      policy: RequestRetryPolicy | None = None, *, context_window=None) -> Any:
    """Bind a wrapper per role so specialists recover inside their delegation."""
    if isinstance(model, str):
        from google.adk.models import LLMRegistry
        model = LLMRegistry.new_llm(model)
    if not callable(getattr(model, "generate_content_async", None)):
        return model  # Lightweight runtime bindings may use opaque model doubles.
    if context_window is not None:
        trace.context_windows[role] = context_window
        if hasattr(model, "context_preflight"):
            model = model.model_copy(update={"context_preflight": context_window.prepare})
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
                                                  self.role, stream=stream, policy=policy):
                yield response

    return RecoverableModel(model=model.model, delegate=model, trace=trace, role=role)
