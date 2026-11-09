"""Bound one tool-free output-limit retry inside the existing ADK task.

Hold complete responses until clean EOF. Never commit truncated tool calls,
restart delegations, or replay already executed capabilities.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from typing import Any

from open_agent_world.plugin_api import AgentRuntimeError


RECOVERY_INSTRUCTION = """HOST OUTPUT-LIMIT RECOVERY: the previous model response exhausted its
output allowance before producing a usable action. That response was not committed;
no tool from it was dispatched. Existing tool receipts and the original request
remain authoritative. This is the only output-limit retry for your role in this Run.
Do not repeat the long derivation or enumerate alternative algorithms. Use the next
authorized tool to obtain a missing fact or execute a small, testable part of the
construction. For uncertain input counts, parameters or invariants, compute them in the Sandbox
rather than guessing in prose. Keep user constraints unchanged. If you cannot take
an authorized next step, report the actual blocker concisely via your normal delivery
path. Do not claim unexecuted construction or validation succeeded."""


def _reason(response: Any) -> str:
    value = getattr(response, "finish_reason", None) or getattr(response, "error_code", None)
    return str(getattr(value, "name", None) or getattr(value, "value", value)).rsplit(".", 1)[-1].upper()


def bounded_output_model(model: Any, trace: Any, role: str, evidence: Any,
                         bindings: Any, output_limit: int | None) -> Any:
    if not callable(getattr(model, "generate_content_async", None)):
        return model
    from google.adk.models.base_llm import BaseLlm
    from pydantic import Field
    from open_agent_world.plugin_api.model_runtime import snapshot_request as _snapshot

    class BoundedOutputModel(BaseLlm):
        delegate: Any = Field(exclude=True)

        @property
        def capabilities(self):
            return self.delegate.capabilities

        async def generate_content_async(self, llm_request, stream=False):
            request = _snapshot(llm_request)
            recovering_action = False
            while True:
                responses = []
                action_mode = evidence.actions.enabled(evidence, role)
                # Recovery allowance applies only to this request, never later
                # actions. A retry retains at least the ordinary reasoning time.
                # The cumulative action recovery count is telemetry, not a fuse.
                seconds = evidence.limits.model_action_seconds if action_mode else None
                if seconds is not None and recovering_action:
                    seconds = max(seconds, evidence.limits.action_recovery_seconds or seconds)
                deadline = asyncio.timeout(seconds)
                timed_out = False
                try:
                    async with deadline:
                        if seconds is not None:
                            trace.recovery_deadlines[role] = (deadline, seconds)
                        try:
                            async with aclosing(self.delegate.generate_content_async(request, stream=stream)) as source:
                                async for response in source:
                                    if getattr(response, "partial", False):
                                        yield response  # Preview only; ADK never dispatches partial calls.
                                    else:
                                        responses.append(response)
                        finally:
                            trace.recovery_deadlines.pop(role, None)
                except TimeoutError:
                    if not deadline.expired():
                        raise  # A provider TimeoutError belongs to transport recovery.
                    timed_out = True
                    preview = trace.interrupted_reasoning(role)
                    if preview:
                        trace.queue.put_nowait(("progress", preview))
                    pending = trace.pending.pop(role, None)
                    now = time.monotonic()
                    last = trace._last_activity.get(role)
                    evidence.notice(role, f"Model request hard ceiling reached ({seconds}s); "
                        "response incomplete, no tools dispatched",
                        phase="model", model_request=pending[0] if pending else None,
                        model_attempt=trace.request_attempts.get(role, 1), run_attempt=trace.run_attempt,
                        elapsed_ms=round((now - pending[1]) * 1000) if pending else seconds * 1000,
                        input_tokens=None, output_tokens=None,
                        finish_reason="HOST_ACTION_TIMEOUT", interrupted=True,
                        timeout_kind="request_wall_clock", deadline_seconds=seconds,
                        received_content=last is not None,
                        idle_seconds=round(now - (last if last is not None else pending[1]), 3) if pending else None)
                evidence.actions.deduplicate_starts(evidence, role, responses)
                truncated = any(_reason(r) in {"MAX_TOKENS", "LENGTH"} for r in responses)
                parts = [p for r in responses for p in getattr(getattr(r, "content", None), "parts", None) or ()]
                calls = any(getattr(p, "function_call", None) is not None for p in parts)
                usable = calls or any(not getattr(p, "thought", False) and bool(getattr(p, "text", None)) for p in parts)
                if action_mode:
                    provider_error = any(getattr(r, "error_code", None) for r in responses)
                    # Output exhaustion uses its own recovery below. It is not
                    # a permission/action error or a stream timeout.
                    action_failure = timed_out or (not truncated and not provider_error and not calls)
                    if action_failure:
                        for response in responses:
                            evidence.after_model(response)
                            if not timed_out:
                                await trace.callbacks(role)["after_model_callback"](None, response)
                        reason = "request_deadline" if timed_out else "no_tool_action"
                        messages = {
                            "request_deadline": "The model response exceeded this request's hard ceiling; no calls were dispatched.",
                            "no_tool_action": "This stage needs an authorized tool action or an actual blocker reported through finish_task.",
                        }
                        rejected = evidence.workflow.error(reason, messages[reason])
                        proposed = [p.function_call for p in parts if getattr(p, "function_call", None)]
                        rejected["error"]["calls"] = [{"call_id": c.id, "tool": c.name} for c in proposed]
                        if timed_out:
                            if recovering_action:
                                evidence.stop("Builder made no permitted executable action (request_deadline) "
                                              "after the bounded action recovery; current stage=" + evidence.workflow.current)
                        else:
                            # Reuse the native unchanged-rejection detector. Call IDs
                            # change on retries; tool semantics/arguments and host facts
                            # identify the action that has actually received feedback.
                            evidence.record_rejection(role, "model_response", {"calls": [
                                {"tool": evidence.semantic_tool(c.name), "arguments": c.args} for c in proposed]}, rejected)
                            if evidence.block_reason:
                                evidence.stop(evidence.block_reason)
                        evidence.actions.recoveries += 1
                        recovering_action = True
                        evidence.notice(role, "Correcting the rejected response; no tools dispatched or replayed",
                            recovery_event="model_action_recovery", recovery_scope="model_response", retrying=True,
                            reason=reason, error=rejected["error"])
                        request.contents.append(bindings.types.Content(role="user", parts=[
                            bindings.types.Part.from_text(text=evidence.actions.recovery_instruction(rejected["error"]))]))
                        evidence.before_model(role, request, bindings, output_limit)
                        await trace.callbacks(role)["before_model_callback"](None, request)
                        continue
                if not truncated and usable:
                    parts = [p for r in responses for p in getattr(getattr(r, "content", None), "parts", None) or ()]
                    if action_mode:
                        evidence.actions.prepare_response(evidence, parts, request, role)
                    try:
                        route_error = evidence.workflow.accept_delegations(evidence, role, parts)
                    except AgentRuntimeError:
                        # A capability/budget rejection discards this call before
                        # SDK dispatch, but its actual model usage still happened.
                        for response in responses:
                            evidence.after_model(response)
                            await trace.callbacks(role)["after_model_callback"](None, response)
                        raise
                    if route_error is not None:
                        for response in responses:
                            evidence.after_model(response)
                            await trace.callbacks(role)["after_model_callback"](None, response)
                        if evidence.workflow.routing_recoveries:
                            evidence.stop("coordinator repeatedly bypassed the required Planner")
                        evidence.workflow.routing_recoveries += 1
                        evidence.notice(role, "Planner required before custom modelling · correcting delegation once; no tools dispatched",
                            workflow_event="modelling_route_recovery", error=route_error["error"])
                        request.contents.append(bindings.types.Content(role="user", parts=[
                            bindings.types.Part.from_text(text="HOST MODELLING ROUTE: " + route_error["error"]["message"])]))
                        evidence.before_model(role, request, bindings, output_limit)
                        await trace.callbacks(role)["before_model_callback"](None, request)
                        continue
                if not truncated and (usable or any(getattr(r, "error_code", None) for r in responses)):
                    for response in responses:
                        yield response
                    return
                # ADK will never see this discarded response, so settle its
                # actual usage/trace here. The next accepted one uses callbacks.
                for response in responses:
                    evidence.after_model(response)
                    await trace.callbacks(role)["after_model_callback"](None, response)
                if truncated and not calls and evidence.output_recoveries.get(role, 0) == 0:
                    evidence.output_recoveries[role] = 1
                    evidence.notice(role, f"{role.replace('_', ' ')} · output limit reached without a tool call; "
                        "retrying once with an action-focused instruction; completed tools will not be replayed",
                        recovery_event="model_output_recovery", recovery_scope="model_response", retrying=True)
                    request.contents.append(bindings.types.Content(role="user", parts=[
                        bindings.types.Part.from_text(text=RECOVERY_INSTRUCTION +
                            ("\n" + evidence.actions.recovery_instruction() if action_mode else ""))]))
                    evidence.before_model(role, request, bindings, output_limit)
                    await trace.callbacks(role)["before_model_callback"](None, request)
                    continue
                reason = ("model output reached its token limit; truncated tool calls were not dispatched"
                          if truncated and calls else
                          "model output reached its token limit after the single bounded recovery"
                          if truncated else "model returned no final text or tool call")
                if truncated and evidence.output_recoveries.get(role):
                    # Reuse native unfinished-checkpoint closeout. The discarded
                    # response contains no confirmed action or task completion.
                    evidence.stop(f"{role.replace('_', ' ')}: {reason}")
                raise AgentRuntimeError(f"AtomSculptor {role.replace('_', ' ')} stopped: {reason}. "
                    "Completed tools were not replayed; inspect existing Sandbox files and document state before continuing.")

    return BoundedOutputModel(model=model.model, delegate=model)
