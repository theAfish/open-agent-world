"""Project native activity without turning internal reports into user replies."""


def is_conversation_author(event, *, child=False):
    # The Pack's planner owns user communication in both normal and Flash mode.
    # Child executors have a separate invocation even if an author name repeats.
    return not child and getattr(event, "author", None) == "thinking_agent"


def public_events(event, *, child=False):
    """Publish tools and exposed thinking as activity; only planner text is a reply.

    The worker still persists the complete native event in its ADK session so
    internal reports remain available to the planner and checkpoint recovery.
    """
    for index, part in enumerate(getattr(getattr(event, "content", None), "parts", None) or []):
        base = {"author": getattr(event, "author", "MatCreator"), "subagent": child}
        if getattr(part, "thought", False):
            text = getattr(part, "text", None)
            if text and text.strip():
                # Native runners return complete model responses, not token deltas.
                yield {"type": "event", "event": "agent_progress", "payload": {**base,
                    "kind": "model_reasoning", "role": base["author"], "text": text,
                    "provider_message_id": f"{event.id}:{index}", "streaming": False,
                    "interrupted": bool(getattr(event, "error_code", None)
                        or getattr(event, "finish_reason", None) == "MAX_TOKENS")}}
            continue
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        if call:
            yield {"type": "event", "event": "tool_started", "payload": {**base,
                "name": call.name, "call_id": call.id, "arguments": call.args}}
        if response:
            from open_agent_world.tool_activity import public_tool_payload

            result = response.response or {}
            failed = result.get("ok") is False or bool(result.get("error")) or result.get("status") in (
                "error", "failed", "cancelled", "needs_replanning")
            payload = public_tool_payload({**base,
                "name": response.name, "call_id": response.id, "success": not failed,
                # Resource results are public tool evidence, sanitized/bounded
                # by the host before WS publication and durable checkpointing.
                # Internal planner/executor reports remain in the native session.
                "response": (result if response.name in {"oaw_invoke_tool", "oaw_list_tools"}
                    else {"success": not failed, **{key: result[key] for key in ("ok", "status", "job_ids") if key in result}}),
                "response_visibility": "full" if response.name in {"oaw_invoke_tool", "oaw_list_tools"} else "summary"})
            yield {"type": "event", "event": "tool_completed", "payload": {**payload, "ok": not failed}}
        if getattr(part, "text", None) and is_conversation_author(event, child=child):
            yield {"type": "event", "event": "agent_message", "payload": {**base,
                "text": part.text, "final": bool(event.is_final_response()),
                "provider_message_id": f"{event.id}:{index}"}}
