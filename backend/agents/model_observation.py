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


def content_kinds(content: Any) -> frozenset[str]:
    """Kinds visible at the adapter boundary, without retaining their payloads."""
    kinds = set()
    for part in getattr(content, "parts", None) or ():
        if getattr(part, "text", None):
            kinds.add("reasoning" if getattr(part, "thought", False) else "text")
        for field, kind in (("function_call", "tool_call"), ("function_response", "tool_result"),
                            ("inline_data", "media"), ("file_data", "media"),
                            ("executable_code", "tool_call"), ("code_execution_result", "tool_result")):
            if getattr(part, field, None) is not None:
                kinds.add(kind)
    return frozenset(kinds)


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
        # Optional active per-attempt computation ceilings. Request recovery
        # pauses these while backing off and resets them for a new attempt.
        self.recovery_deadlines: dict[str, tuple[asyncio.Timeout, float]] = {}
        self.context_windows = {}
        self.context_stats = {}
        self.request_stream_metrics: dict[str, dict[str, Any]] = {}
        self.provenance = None
        self.invocations = getattr(checkpoint_store, "invocations", None)
        self._archived_tools: dict[tuple[str, str], tuple[str, str]] = {}

    def archive_tool_start(self, role: str, call_id: str, name: str, arguments: Any):
        if self.invocations is None or not call_id:
            return None
        key = (role, call_id)
        if key in self._archived_tools:
            return self._archived_tools[key][0]
        identity = self.invocations.start_tool(role, call_id, self.run_attempt, name, arguments)
        self._archived_tools[key] = (identity, name)
        self.invocations.tool_arguments(identity, arguments)
        return identity

    def archive_tool_response(self, role: str, call_id: str | None, name: str, response: Any):
        if self.invocations is None:
            return
        known = self._archived_tools.get((role, call_id)) if call_id else None
        if known is None:
            candidates = [entry for (_, candidate_id), entry in self._archived_tools.items()
                          if (candidate_id == call_id if call_id else entry[1] == name)]
            # A response emitted by a child/user author can have a different
            # role. Pair only a unique call identity, never guess among twins.
            known = candidates[0] if len(candidates) == 1 else None
        if known:
            self.invocations.finish_tool(known[0], response)
            return known[0]

    def observe_tool_exchange(self, role: str, content: Any):
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None:
                self.archive_tool_start(role, getattr(call, "id", None), call.name, getattr(call, "args", {}))
            response = getattr(part, "function_response", None)
            if response is not None:
                self.archive_tool_response(role, getattr(response, "id", None), response.name,
                                           getattr(response, "response", {}))

    def archive_model_start(self, role: str, request: Any, *, model: str | None = None, stream=False):
        if self.invocations is None or role not in self.pending:
            return None
        return self.invocations.start_model(role, self.pending[role][0], self.run_attempt,
            self.request_attempts.get(role, 1), request, model=model, stream=stream,
            context_stats=self.context_stats.get(role), provenance=self.provenance)

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
        payload = {
            "kind": "model_reasoning", "role": role, "model_request": request_id,
            "model_attempt": self.request_attempts.get(role, 1),
            "run_attempt": self.run_attempt,
            "provider_message_id": (f"model-reasoning:{role}:{request_id}"
                                    if self.run_attempt == 0 else
                                    f"model-reasoning:retry{self.run_attempt}:{role}:{request_id}")
                                   + (f":attempt{self.request_attempts[role]}"
                                      if self.request_attempts.get(role, 1) > 1 else ""),
            "text": visible, "truncated": False, "suffix_withheld": withheld if interrupted else 0,
            "streaming": not final, "interrupted": interrupted,
        }
        # Persist the full redacted observation before constructing its public
        # preview. This also runs on cancellation, when nobody may drain events.
        if self.checkpoint_store is not None and hasattr(self.checkpoint_store, "save_reasoning"):
            self.checkpoint_store.save_reasoning(payload)
        return {**payload, "text": visible[:64_000], "text_length": len(visible),
                "truncated": len(visible) > 64_000,
                "reasoning_persisted": getattr(self.checkpoint_store, "reasoning", None) is not None}

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
        if self.invocations is not None:
            from backend.runs.invocation_logs import model_identity
            self.invocations.interrupt_model(model_identity(role, pending[0], self.run_attempt,
                                                          self.request_attempts.get(role, 1)))
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
        self._reasoning[role] = previous + chunk
        now = time.monotonic()
        if role not in self._last_reasoning_emit or now - self._last_reasoning_emit[role] >= 0.25:
            self._emit_reasoning(role, pending[0], self._reasoning[role], final=False)

    def note_activity(self, role: str, content: Any) -> frozenset[str]:
        """Record that the role's pending request delivered real streamed content.

        Empty partial/keepalive events must not postpone the stall watchdog.
        Reasoning, visible text, tool arguments and media all count as content.
        """
        kinds = content_kinds(content)
        if kinds and role in self.pending:
            self._last_activity[role] = time.monotonic()
        return kinds

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

    def callbacks(self, role: str, *, prepare_only: bool = False) -> dict[str, Any]:
        async def before_model(callback_context: Any, llm_request: Any) -> None:
            # ADK callbacks run before the shared dispatch preflight. Stream
            # chunk normalization, archive projections and compaction can all
            # change this history before it is submitted to the provider.
            self.begin(role, llm_request, phase="context" if prepare_only else "model")

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
                "phase": "context_compaction" if role.endswith(":context_compaction") else "model",
                "context": self.context_stats.get(role), "elapsed_ms": elapsed_ms, "input_tokens": input_tokens,
                "output_tokens": output_tokens, "max_output_tokens": output_limit,
                "finish_reason": finish_reason, "reasoning_available": bool(reasoning),
                "stream_metrics": self.request_stream_metrics.pop(role, None),
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
    import errno
    import socket
    import ssl
    seen: set[int] = set()
    current: BaseException | None = error
    retryable = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return False
        if getattr(current, "request_recovery_exhausted", False):
            return False
        status = getattr(current, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 429}:
            return False
        if (isinstance(current, socket.gaierror) and current.errno == socket.EAI_AGAIN) or isinstance(current, (ConnectionError, TimeoutError)) or type(current).__name__ in {
            "APIConnectionError", "APITimeoutError", "MidStreamFallbackError",
            "TransferEncodingError", "Timeout", "ReadTimeout", "ConnectTimeout", "ReadError",
            "ConnectError", "RemoteProtocolError",
            "WriteError", "WriteTimeout", "PoolTimeout", "NetworkError", "ProtocolError",
            "ServerDisconnectedError",
        } or (isinstance(status, int) and not isinstance(status, bool)
              and (status in {408, 429} or 500 <= status < 600)) or (
              isinstance(current, OSError) and current.errno in {errno.ENETDOWN, errno.ENETUNREACH,
                  errno.EHOSTUNREACH, errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.EPIPE}):
            retryable = True
        current = current.__cause__ or current.__context__
    return retryable


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
