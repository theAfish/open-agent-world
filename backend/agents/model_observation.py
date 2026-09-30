"""Shared model-request observations for OAW ADK runtimes.

Only provider-exposed thought parts are surfaced; an incomplete stream never
becomes a completed model response or a replayable tool call.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from typing import Any

from .models import AgentRuntimeError


# Two independent bounds keep a provider stream from hanging a Run forever,
# following the same semantics as Codex CLI's stream_idle_timeout (which wraps
# the wait for every event, including the first):
# - a request that already delivered content and then went silent, and
# - a request whose provider never delivered a single byte. Cold-start queueing
#   is legitimate, so the first-byte bound is deliberately looser.
STREAM_STALL_SECONDS = 90.0
STREAM_FIRST_BYTE_SECONDS = 300.0


class ModelStreamStalled(ConnectionError):
    """A provider stream went silent: either mid-stream or before its first byte."""

    def __init__(self, stalled: dict[str, Any]) -> None:
        self.stalled = stalled
        if stalled.get("stage") == "first_byte":
            message = (f"{stalled['role']} model request {stalled['model_request']} "
                       f"received no content at all for {stalled['idle_seconds']:.0f}s "
                       "after the request was submitted")
        else:
            message = (f"{stalled['role']} model request {stalled['model_request']} stopped "
                       f"delivering content {stalled['idle_seconds']:.0f}s after its last chunk")
        super().__init__(message)


def _request_size(request: Any) -> int:
    """Estimate bytes without retaining or emitting prompts, images or secrets."""
    contents = getattr(request, "contents", ()) or ()
    try:
        return sum(len(json.dumps(
            content.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False, default=str,
        ).encode("utf-8")) for content in contents)
    except (AttributeError, TypeError, ValueError):
        return 0


class ModelTrace:
    def __init__(self, queue: asyncio.Queue[tuple[str, Any]], api_key: str | None = None,
                 *, run_attempt: int = 0, checkpoint_store: Any = None,
                 primary_role: str = "agent", display_name: str = "Agent") -> None:
        self.queue = queue
        self.primary_role = primary_role
        self.display_name = display_name
        self.api_key = api_key
        self.run_attempt = run_attempt
        self.checkpoint_store = checkpoint_store
        self.sequence = 0
        self.pending: dict[str, tuple[int, float, str]] = {}
        self._reasoning: dict[str, str] = {}
        self._last_reasoning_emit: dict[str, float] = {}
        self._last_activity: dict[str, float] = {}
        self._output_limits: dict[str, int] = {}
        self.last_response: dict[str, dict[str, Any]] = {}
        self.request_attempts: dict[str, int] = {}
        self.recovery_roles: set[str] = set()

    def _remember_output_limit(self, role: str, request: Any) -> None:
        limit = getattr(getattr(request, "config", None), "max_output_tokens", None)
        if isinstance(limit, int) and limit > 0:
            self._output_limits[role] = limit

    def _reasoning_event(self, role: str, request_id: int, reasoning: str, *,
                         final: bool, interrupted: bool = False) -> dict[str, Any] | None:
        from backend.security.redaction import redact

        # Hold back a suffix until another chunk arrives. Otherwise a secret
        # split across two provider chunks could be shown before redaction sees
        # the complete value.
        safe = redact(reasoning, [self.api_key] if self.api_key else [])
        withheld = max(0, len(self.api_key or "") - 1)
        # A disconnected stream has no later chunk to prove that the held
        # suffix is harmless; keep withholding it even in the closing preview.
        visible = safe if (final and not interrupted) or not withheld else safe[:-withheld]
        if not visible:
            return None
        return {
            "kind": "model_reasoning", "role": role, "model_request": request_id,
            "model_attempt": self.request_attempts.get(role, 1),
            "run_attempt": self.run_attempt,
            "provider_message_id": (f"model-reasoning:{role}:{request_id}"
                                    if self.run_attempt == 0 else
                                    f"model-reasoning:retry{self.run_attempt}:{role}:{request_id}")
                                   + (f":attempt{self.request_attempts[role]}"
                                      if self.request_attempts.get(role, 1) > 1 else ""),
            "text": visible[:64_000], "truncated": len(reasoning) > 64_000 or len(visible) > 64_000,
            "streaming": not final, "interrupted": interrupted,
        }

    def _emit_reasoning(self, role: str, request_id: int, reasoning: str, *, final: bool) -> None:
        payload = self._reasoning_event(role, request_id, reasoning, final=final)
        if payload is None:
            return
        self.queue.put_nowait(("progress", payload))
        self._last_reasoning_emit[role] = time.monotonic()

    def interrupted_reasoning(self, role: str) -> dict[str, Any] | None:
        """Close the live preview without presenting it as a complete thought."""
        pending = self.pending.get(role)
        reasoning = self._reasoning.get(role)
        if pending is None:
            return None
        if self.checkpoint_store is not None:
            self.checkpoint_store.settle(role, pending[0], "interrupted")
        if not reasoning:
            return None
        return self._reasoning_event(role, pending[0], reasoning, final=True, interrupted=True)

    def observe_partial(self, role: str, content: Any) -> None:
        pending = self.pending.get(role)
        if pending is None or pending[2] != "model":
            return
        parts = getattr(content, "parts", None) or ()
        chunk = "".join(part.text for part in parts
                        if getattr(part, "thought", False)
                        and isinstance(getattr(part, "text", None), str) and part.text)
        if not chunk:
            return
        previous = self._reasoning.get(role, "")
        limit = 64_000 + len(self.api_key or "")
        self._reasoning[role] = (previous + chunk)[:limit]
        now = time.monotonic()
        if role not in self._last_reasoning_emit or now - self._last_reasoning_emit[role] >= 0.25:
            self._emit_reasoning(role, pending[0], self._reasoning[role], final=False)

    def note_activity(self, role: str, content: Any) -> None:
        """Record that the role's pending request delivered real streamed content.

        Empty partial/keepalive events must not postpone the stall watchdog.
        Reasoning, visible text, tool arguments and media all count as content.
        """
        parts = getattr(content, "parts", None) or ()
        has_content = any(
            bool(getattr(part, "text", None)) or any(
                getattr(part, field, None) is not None for field in (
                    "function_call", "function_response", "inline_data", "file_data",
                    "executable_code", "code_execution_result",
                )
            ) for part in parts
        )
        if has_content and role in self.pending:
            self._last_activity[role] = time.monotonic()

    def stalled_stream(self) -> dict[str, Any] | None:
        """A silent stream, if one is currently pending.

        ``mid_stream``: the request already delivered content and then went
        quiet for ``STREAM_STALL_SECONDS``. ``first_byte``: the request never
        delivered anything and outlived ``STREAM_FIRST_BYTE_SECONDS`` — a
        gateway that accepted the request and never answered.
        """
        now = time.monotonic()
        for role, (request_id, started, phase) in self.pending.items():
            if phase != "model" or role in self.recovery_roles:
                continue
            last = self._last_activity.get(role)
            if last is not None:
                if now - last >= STREAM_STALL_SECONDS:
                    return {"role": role, "model_request": request_id,
                            "idle_seconds": now - last, "stage": "mid_stream"}
            elif now - started >= STREAM_FIRST_BYTE_SECONDS:
                return {"role": role, "model_request": request_id,
                        "idle_seconds": now - started, "stage": "first_byte"}
        return None

    def begin(self, role: str, request: Any, *, phase: str = "model") -> None:
        self.sequence += 1
        request_id = self.sequence
        self.pending[role] = (request_id, time.monotonic(), phase)
        self.request_attempts[role] = 1
        self._reasoning.pop(role, None)
        self._last_reasoning_emit.pop(role, None)
        self._last_activity.pop(role, None)
        self._output_limits.pop(role, None)
        self.last_response.pop(role, None)
        self._remember_output_limit(role, request)
        if phase == "model" and self.checkpoint_store is not None:
            self.checkpoint_store.start(role, request_id, self.run_attempt, request)
        size = _request_size(request)
        text = (f"{role.replace('_', ' ')} · preparing context for request {request_id}"
                if phase == "context" else
                f"{role.replace('_', ' ')} · model request {request_id} started ({size} input bytes)")
        self.queue.put_nowait(("progress", {
            "kind": "status", "role": role, "model_request": request_id,
            "phase": phase, "input_bytes": size, "input_bytes_scope": "contents_only",
            "model_attempt": 1, "run_attempt": self.run_attempt, "text": text,
        }))

    def model_ready(self, role: str, request: Any) -> None:
        pending = self.pending.get(role)
        if pending is None:
            return
        request_id, _, _ = pending
        self.pending[role] = (request_id, time.monotonic(), "model")
        self._remember_output_limit(role, request)
        if self.checkpoint_store is not None:
            self.checkpoint_store.start(role, request_id, self.run_attempt, request)
        size = _request_size(request)
        self.queue.put_nowait(("progress", {
            "kind": "status", "role": role, "model_request": request_id,
            "phase": "model", "input_bytes": size, "input_bytes_scope": "contents_only",
            "model_attempt": 1, "run_attempt": self.run_attempt,
            "text": f"{role.replace('_', ' ')} · model request {request_id} started ({size} input bytes)",
        }))

    def callbacks(self, role: str) -> dict[str, Any]:
        async def before_model(callback_context: Any, llm_request: Any) -> None:
            self.begin(role, llm_request)

        async def after_model(callback_context: Any, llm_response: Any) -> None:
            if getattr(llm_response, "partial", False):
                return
            pending = self.pending.pop(role, None)
            if pending is None:
                return
            request_id, started, _ = pending
            if self.checkpoint_store is not None:
                self.checkpoint_store.settle(role, request_id, "finished")
            elapsed_ms = round((time.monotonic() - started) * 1000)
            usage = getattr(llm_response, "usage_metadata", None)
            input_tokens = getattr(usage, "prompt_token_count", None)
            output_tokens = getattr(usage, "candidates_token_count", None)
            raw_finish_reason = getattr(llm_response, "finish_reason", None)
            finish_reason = (str(getattr(raw_finish_reason, "name", None)
                                 or getattr(raw_finish_reason, "value", raw_finish_reason))
                             .rsplit(".", 1)[-1].upper() if raw_finish_reason is not None else None)
            output_limit = self._output_limits.pop(role, None)
            limit_reached = finish_reason in {"MAX_TOKENS", "LENGTH"}
            at_output_cap = isinstance(output_tokens, int) and output_limit is not None and output_tokens >= output_limit
            parts = getattr(getattr(llm_response, "content", None), "parts", None) or ()
            thoughts = [part.text for part in parts
                        if getattr(part, "thought", False) and isinstance(getattr(part, "text", None), str) and part.text]
            # ADK may deliver one thought part per streamed token. Inserting
            # paragraph breaks here corrupts the final snapshot that replaces
            # the correctly concatenated live reasoning in Run history.
            reasoning = "".join(thoughts)
            if not reasoning and isinstance(getattr(llm_response, "reasoning", None), str):
                reasoning = llm_response.reasoning
            if not reasoning:
                reasoning = self._reasoning.get(role, "")
            if reasoning:
                self._emit_reasoning(role, request_id, reasoning, final=True)
            self._reasoning.pop(role, None)
            self._last_reasoning_emit.pop(role, None)
            self.last_response[role] = {
                "finish_reason": finish_reason, "output_tokens": output_tokens,
                "max_output_tokens": output_limit, "limit_reached": limit_reached,
                "at_output_cap": at_output_cap, "reasoning_available": bool(reasoning),
                "actionable_output": any(
                    getattr(part, "function_call", None) is not None
                    or (not getattr(part, "thought", False) and bool(getattr(part, "text", None)))
                    for part in parts
                ),
            }
            self.queue.put_nowait(("progress", {
                "kind": "status", "role": role, "model_request": request_id,
                "model_attempt": self.request_attempts.get(role, 1), "run_attempt": self.run_attempt,
                "phase": "model", "elapsed_ms": elapsed_ms, "input_tokens": input_tokens,
                "output_tokens": output_tokens, "max_output_tokens": output_limit,
                "finish_reason": finish_reason, "reasoning_available": bool(reasoning),
                "text": (f"{role.replace('_', ' ')} · model request {request_id} returned in {elapsed_ms / 1000:.1f}s"
                         + (" (output limit reached)" if limit_reached else
                            " (likely at output limit)" if at_output_cap else "")
                         + (" (model thinking not exposed)" if not reasoning else "")),
            }))

        return {"before_model_callback": before_model, "after_model_callback": after_model}

    def waiting(self) -> dict[str, Any] | None:
        if not self.pending:
            return None
        role, (request_id, started, phase) = min(self.pending.items(), key=lambda item: item[1][1])
        reasoning = self._reasoning.get(role)
        if reasoning and time.monotonic() - self._last_reasoning_emit.get(role, 0) >= 0.25:
            self._emit_reasoning(role, request_id, reasoning, final=False)
        elapsed = round(time.monotonic() - started)
        idle = round(time.monotonic() - self._last_activity.get(role, started))
        attempt = self.request_attempts.get(role, 1)
        return {"kind": "status", "role": role, "model_request": request_id,
                "phase": phase, "elapsed_seconds": elapsed, "idle_seconds": idle,
                "model_attempt": attempt, "run_attempt": self.run_attempt,
                "received_content": role in self._last_activity,
                "text": (f"{role.replace('_', ' ')} · context preparation still running ({elapsed}s)"
                         if phase == "context" else
                         f"{role.replace('_', ' ')} · model request {request_id}, attempt {attempt} "
                         f"still waiting ({elapsed}s; {idle}s without content)")}

    def missing_final_error(self) -> str:
        message = f"{self.display_name} ended without a final response"
        response = self.last_response.get(self.primary_role)
        if not response:
            return message
        if response["limit_reached"] or response["at_output_cap"]:
            limit = response["max_output_tokens"]
            count = response["output_tokens"]
            usage = f"{count}/{limit} tokens; " if isinstance(count, int) and isinstance(limit, int) else ""
            qualifier = "reached" if response["limit_reached"] else "likely reached"
            return (f"{message}: model output {qualifier} its token limit "
                    f"({usage}finish_reason={response['finish_reason'] or 'unknown'})")
        if response["reasoning_available"] and not response["actionable_output"]:
            return f"{message}: model returned reasoning but no final text or tool call"
        return message


def model_stream_disconnected(error: BaseException) -> bool:
    """Classify a broken provider stream without masking tool/runtime errors."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "request_recovery_exhausted", False):
            return False
        status = getattr(current, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 429}:
            return False
        if isinstance(current, (ConnectionError, TimeoutError)) or type(current).__name__ in {
            "APIConnectionError", "APITimeoutError", "MidStreamFallbackError",
            "TransferEncodingError", "Timeout", "ReadTimeout", "ConnectTimeout", "ReadError",
            "ConnectError", "RemoteProtocolError",
        } or (isinstance(status, int) and not isinstance(status, bool)
              and (status in {408, 429} or 500 <= status < 600)):
            return True
        current = current.__cause__ or current.__context__
    return False


async def multiplex_adk_events(stream: AsyncIterator[Any], queue: asyncio.Queue[tuple[str, Any]],
                               model_trace: ModelTrace | None = None,
                               stop_event: asyncio.Event | None = None,
                               *, stop_message: str = "Agent Run was stopped") -> AsyncIterator[tuple[str, Any]]:
    """Yield ADK events and model callbacks without a model-request deadline."""
    async def pump() -> None:
        try:
            async with aclosing(stream):
                async for item in stream:
                    queue.put_nowait(("adk", item))
        except BaseException as error:
            queue.put_nowait(("error", error))
        finally:
            queue.put_nowait(("done", None))

    task = asyncio.create_task(pump(), name="oaw-adk-events")
    stop_task = (asyncio.create_task(stop_event.wait(), name="oaw-agent-stop")
                 if stop_event is not None else None)
    get_task: asyncio.Task[tuple[str, Any]] | None = None
    try:
        while True:
            get_task = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {get_task, stop_task} if stop_task is not None else {get_task},
                timeout=15 if model_trace else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task is not None and stop_task in done:
                get_task.cancel()
                with suppress(asyncio.CancelledError):
                    await get_task
                raise AgentRuntimeError(stop_message)
            if get_task not in done:
                get_task.cancel()
                with suppress(asyncio.CancelledError):
                    await get_task
                # A stream that already delivered content and then went silent
                # is a disconnect in fact, even though the transport never
                # raised: synthesize one and let the normal recovery classify it.
                stalled = model_trace.stalled_stream() if model_trace else None
                if stalled is not None:
                    raise ModelStreamStalled(stalled)
                waiting = model_trace.waiting() if model_trace else None
                if waiting is not None:
                    yield "progress", waiting
                continue
            kind, value = get_task.result()
            if kind == "done":
                break
            if kind == "error":
                raise value
            yield kind, value
    finally:
        if get_task is not None and not get_task.done():
            get_task.cancel()
            with suppress(asyncio.CancelledError):
                await get_task
        if stop_task is not None:
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
