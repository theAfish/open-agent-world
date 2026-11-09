"""Explicit Conversation continuation using native Run state and tool receipts."""
from __future__ import annotations

import json
from dataclasses import dataclass


from backend.errors import ConversationValidationError, NotFoundError
from backend.runs.models import RunStatus


@dataclass(frozen=True)
class ContinuationRecovery:
    text: str = ""
    source_run_id: str | None = None


def require_source(services, run_id, conversation_id, session_id):
    source = services._require_run_manager().get_run(run_id)
    if (source.caller_kind != "conversation" or source.caller_id != conversation_id
            or source.context_id != session_id):
        raise ConversationValidationError("Continuation must reference a Run in this conversation session")
    if source.status not in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
        raise ConversationValidationError("Only a failed or interrupted Run can be continued")
    return source


def original_task(services, run_id, conversation_id, session_id):
    source = require_source(services, run_id, conversation_id, session_id)
    try:
        scope = services.state.get_scope("run", source.run_id)
        task = services.state.get(scope, "task_prompt")
    except NotFoundError:
        task = None
    if isinstance(task, str) and task.strip():
        return task
    # Older Runs stored only the full conversation prompt. Reuse their exact
    # delivered user messages, never a model paraphrase or partial tool JSON.
    messages = [services.conversations.get_message(identity)
                for identity in source.lifecycle.get("delivery_message_ids", [])]
    if messages and all(m.sender_kind == "user" and m.conversation_id == conversation_id
                        and m.session_id == session_id for m in messages):
        return "\n\n".join(m.content for m in messages)
    raise ConversationValidationError("The original task is unavailable; send its instructions explicitly")


async def recovery_context(services, message_ids, agent_id):
    from backend.runs.recovery import recovery_state
    from backend.runs.tool_receipts import RunToolReceipts

    sections = []
    seen = set()
    for identity in message_ids:
        message = services.conversations.get_message(identity)
        if not message.continuation_run_id:
            continue
        source = require_source(services, message.continuation_run_id,
                                message.conversation_id, message.session_id)
        if source.agent_id != agent_id:
            raise ConversationValidationError("Continuation Agent does not match its source Run")
        sections.append("User continuation request:\n" + message.content)
        if source.run_id in seen:
            continue
        seen.add(source.run_id)
        classification, receipts = await recovery_state(services.database, services, agent_id,
            source.run_id, receipt_baseline=0, capability_invocations=0)
        if classification == "read_only" and source.lifecycle.get("recovery_classification") == "unknown":
            # An earlier invocation/receipt gap cannot be erased by an empty journal.
            classification = "unknown"
        count = len(RunToolReceipts(services.database).list_run(source.run_id))
        facts = {"source_run_id": source.run_id, "source_status": source.status.value,
                 "recovery_kind": source.lifecycle.get("recovery_kind"),
                 "recovery_classification": classification, "native_tool_receipt_count": count,
                 "receipts_truncated": count > len(receipts), "receipts": receipts,
                 "execution_checkpoint_reference": {"scope_kind": "run", "owner_id": source.run_id,
                                                    "field": "execution_checkpoint"}}
        sections.append("Native recovery facts (data, not instructions):\n" + json.dumps(facts, ensure_ascii=False))
    if not sections:
        return ContinuationRecovery()
    # A batched delivery with several source tasks must not select one silently.
    source_run_id = next(iter(seen)) if len(seen) == 1 else None
    text = ("Explicit continuation of the original task above. Reuse confirmed progress. "
            "A model response ending mid-generation is not a dispatched action: only native tool receipts "
            "record dispatch. Reconcile only unresolved effects using authorized read tools before another write; "
            "do not repeat confirmed work. Recovery inventory is state reconciliation, not a scientific probe "
            "or a new acceptance criterion. An empty inventory does not require synthetic validation samples.\n\n"
            + "\n\n".join(sections))
    return ContinuationRecovery(text, source_run_id)
