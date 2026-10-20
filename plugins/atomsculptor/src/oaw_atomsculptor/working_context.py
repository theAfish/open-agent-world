"""Focus a specialist's working request; native Run archives remain complete.

Only entire, settled, unsigned exchanges can leave the working window. This is
host evidence selection, not a fabricated transcript or a model summary call.
"""
from __future__ import annotations

import json


def focus_history(evidence, request, bindings):
    contents = request.contents
    if len(contents) < 4 or any(getattr(p, "thought_signature", None)
                               for c in contents for p in (c.parts or [])):
        return  # Provider-signed history cannot be edited/reordered.
    had_checkpoint = any(c.role == "user" and len(c.parts or []) == 1 and
                         c.parts[0].text in evidence._working_context_texts for c in contents)
    original = list(contents)
    contents = [c for c in contents if not (c.role == "user" and len(c.parts or []) == 1
                and c.parts[0].text in evidence._working_context_texts)]
    # The initial delegation, the current HOST stable frame and the freshly
    # replaced HOST turn frame are retained; only settled history is focused.
    stable = [c for c in contents if c.role == "user" and len(c.parts or []) == 1
              and c.parts[0].text in evidence._host_static_texts]
    flowing = [c for c in contents if c not in stable]
    first, middle, frame = flowing[0], flowing[1:-1], flowing[-1]
    units, current, pending = [], [], []
    for content in middle:
        current.append(content)
        for part in content.parts or []:
            call = getattr(part, "function_call", None)
            response = getattr(part, "function_response", None)
            if call:
                pending.append((call.id, call.name))
            if response:
                match = next((i for i, (identity, name) in enumerate(pending)
                              if (response.id and identity == response.id) or
                              (not response.id and name == response.name)), None)
                if match is None:
                    return  # Leave malformed input to the existing exchange repair.
                pending.pop(match)
        if not pending:
            units.append(current)
            current = []
    if current:
        units.append(current)  # An unresolved suffix is always retained intact.
    def size(items):
        return sum(len(c.model_dump_json(exclude_none=True)) for c in items)
    if size(middle) <= evidence.limits.working_history_chars and not had_checkpoint:
        return
    retained, used = [], 0
    for unit in reversed(units):
        amount = size(unit)
        if retained and used + amount > evidence.limits.working_history_chars:
            break
        retained.insert(0, unit)
        used += amount
    tail = [c for unit in retained for c in unit]
    if len(tail) >= len(middle) and not had_checkpoint:
        return
    # Saved files remain authoritative. Never refill the working window with
    # complete source that the settled history just removed.
    current_source = evidence._current_source_head("structure_builder") if hasattr(evidence, "_current_source_head") else None
    checkpoint = {"source": "HOST working-context checkpoint, not a tool result",
                  "removed_complete_history_contents": len(middle) - len(tail),
                  "source_snapshot": {k: current_source[k] for k in ("sandbox", "path", "version", "source_chars")} if current_source else None,
                  "instruction": "Full exchanges remain in native Run invocation logs. The final HOST frame holds "
                    "the original request, plan/checks and recent real execution evidence. "
                    "Script metadata here is historical; the final HOST frame owns the current source version and bounded excerpts. "
                    "Use the final frame's working_decision for the pending subtask and rejected methods. Use artifact_handoffs for "
                    "frames and producer receipts; do not reuse an invalid candidate merely because its objective is smaller. "
                    "Read any other required file through authorized Sandbox tools; do not reconstruct it from guesses."}
    text = json.dumps(checkpoint, ensure_ascii=False)
    candidate = [first, *stable, bindings.types.Content(role="user", parts=[
        bindings.types.Part.from_text(text=text)]), *tail, frame]
    # A focus pass must buy runway, including its checkpoint and all pinned
    # frames. If the new summary is larger, retain the existing window intact.
    if size(candidate) >= size(original):
        return
    evidence._working_context_texts.add(text)
    request.contents[:] = candidate
    evidence.notice("structure_builder", "Focused working context on recent settled exchanges and saved source",
                    workflow_event="working_context", removed_contents=len(middle) - len(tail),
                    retained_history_chars=used)
