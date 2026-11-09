"""Final ADK request accounting and bounded views over native Run archives.

This component keeps no conversational history. ADK delegation scopes and the
existing ContextStore remain their owners. A projection never changes a receipt
or grants access to another Run.
"""
from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from .context import ContextBudget, ESTIMATE_METHOD, estimate
from .models import AgentRuntimeError
from .tool_execution import execute_tool
from backend.errors import PermissionDeniedError, ResourceValidationError


def signed_history(request):
    return any(getattr(part, "thought_signature", None)
        for content in request.contents for part in (getattr(content, "parts", None) or []))


def request_usage(request):
    """Local BPE reference counts, separate from provider-reported usage."""
    categories = {key: 0 for key in ("instructions_and_tools", "tool_inputs", "tool_results", "reasoning", "messages")}
    config = getattr(request, "config", None)
    if config is not None:
        categories["instructions_and_tools"] = estimate(config.model_dump(mode="json", exclude_none=True))
    for content in request.contents:
        for part in getattr(content, "parts", None) or []:
            category = ("tool_inputs" if getattr(part, "function_call", None) else "tool_results" if getattr(part, "function_response", None)
                else "reasoning" if getattr(part, "thought", False) else "messages")
            categories[category] += estimate(part.model_dump(mode="json", exclude_none=True))
    # Include role/content envelopes as well as all Parts and actual tool schemas.
    total = estimate([c.model_dump(mode="json", exclude_none=True) for c in request.contents]) + categories["instructions_and_tools"]
    return {"estimated_input_tokens": total, "components_estimated_tokens": categories,
            "estimate_method": ESTIMATE_METHOD, "tokenizer_matches_provider": False,
            "signed_history": signed_history(request)}


def input_capacity(request, budget):
    output = getattr(getattr(request, "config", None), "max_output_tokens", None) or budget.output
    return max(0, budget.limit - output) if budget.known else None


def coalesce_unsigned_thoughts(request):
    """Normalize adjacent text-only stream chunks without changing their text.

    A provider can return hundreds of Parts for one logical thought. Their
    envelope cost must not determine whether otherwise identical history fits.
    Signed prefixes and parts carrying any additional metadata stay untouched.
    """
    if signed_history(request):
        return 0
    merged = 0
    for content in request.contents:
        parts, group = [], []
        def flush():
            nonlocal merged
            if group:
                parts.append(group[0] if len(group) == 1 else
                    group[0].model_copy(update={"text": "".join(p.text for p in group)}))
                merged += len(group) - 1
                group.clear()
        for part in content.parts or []:
            fields = part.model_dump(mode="json", exclude_none=True)
            if part.thought and isinstance(part.text, str) and set(fields) <= {"text", "thought"}:
                group.append(part)
            else:
                flush()
                parts.append(part)
        flush()
        if len(parts) != len(content.parts or []):
            content.parts = parts
    return merged


def _pointer_child(pointer, key):
    return pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _preview(value, depth=0, *, pointer="", text_limit=1600, item_limit=4, field_limit=16):
    """Keep a structured view; every omitted subtree names its raw read path."""
    def child(item, key):
        return _preview(item, depth + 1, pointer=_pointer_child(pointer, key),
                        text_limit=text_limit, item_limit=item_limit, field_limit=field_limit)

    if isinstance(value, str):
        return value if len(value) <= text_limit else {
            "text_preview": value[:text_limit], "total_chars": len(value),
            "read_pointer": pointer, "truncated": True}
    if isinstance(value, list):
        return ([child(item, i) for i, item in enumerate(value)] if len(value) <= item_limit * 2 and depth < 5 else
                {"items_preview": [child(item, i) for i, item in enumerate(value[:item_limit])] if depth < 5 else [],
                 "read_pointer": pointer, "total_items": len(value), "truncated": True})
    if isinstance(value, dict):
        if depth >= 5:
            return {"keys_preview": list(value)[:field_limit], "read_pointer": pointer,
                    "total_keys": len(value), "truncated": True}
        if len(value) > field_limit * 2:
            return {"fields_preview": {k: child(v, k) for k,v in list(value.items())[:field_limit]},
                    "read_pointer": pointer, "total_keys": len(value), "truncated": True}
        return {key: child(item, key) for key,item in value.items()}
    return value


@dataclass
class ContextCapabilities:
    """Native reduction must be explicitly supplied by an adapter, never guessed."""
    native_compact: Any = None
    preserve_tool_reasoning: bool = False

    @classmethod
    def for_model(cls, model):
        return cls(preserve_tool_reasoning=getattr(model, "requires_tool_reasoning", False) is True)


@dataclass(frozen=True)
class BoundedReader:
    """Host-registered paging contract, independent of a particular tool name.

    Only a bounded text page with small positioning metadata is exempt from
    result projection. Tool output cannot opt itself out of accounting.
    """
    identity_fields: tuple[str, ...]
    max_chars: int = 8000

    def accepts(self, value):
        if not isinstance(value, dict) or value.get("ok") is False or value.get("error"):
            return False
        text, offset, total = value.get("text"), value.get("offset"), value.get("total_chars")
        return (isinstance(text, str) and len(text) <= self.max_chars
            and type(offset) is int and offset >= 0 and type(total) is int
            and total >= offset + len(text)
            and all(type(value.get(k)) in (str, int) for k in self.identity_fields)
            and len(json.dumps({k: v for k, v in value.items() if k != "text"}, ensure_ascii=False)) <= 4000)


class RequestWindow:
    def __init__(self, budget: ContextBudget, trace, role: str, *, reducer=None, capabilities=None):
        self.budget, self.trace, self.role = budget, trace, role
        self.reducer = reducer
        self.capabilities = capabilities or ContextCapabilities()
        self.references: set[str] = set()
        self.reference_roles: dict[str, str] = {}
        self.scope = role
        self.last_estimate = 0
        self.measured_tokens = None
        self.measured_estimate = 0
        self.last_stats = {}
        self.bounded_readers: dict[str, BoundedReader] = {}
        self.visible_reads: list[dict] = []
        self.register_bounded_reader("read_tool_result", identity_fields=("invocation_id", "section", "pointer"))
        self.register_bounded_reader("read_sandbox_file", identity_fields=("sandbox_id", "path"))

    def register_bounded_reader(self, name: str, *, identity_fields: tuple[str, ...]):
        """Adapters register trusted readers; normal input accounting still applies."""
        self.bounded_readers[name] = BoundedReader(identity_fields)

    def _bounded_read(self, response):
        reader = self.bounded_readers.get(response.name)
        return reader if reader is not None and reader.accepts(response.response) else None

    def read_pages(self, request):
        """Metadata for pages actually present, not a second result archive."""
        pages = []
        for content in request.contents:
            for part in content.parts or []:
                response = getattr(part, "function_response", None)
                reader = self._bounded_read(response) if response is not None else None
                if reader is not None and response.id:
                    value = response.response
                    pages.append({"tool": response.name, "call_id": response.id,
                        "source": {k: value[k] for k in reader.identity_fields},
                        "offset": value["offset"], "end": value["offset"] + len(value["text"]),
                        "total_chars": value["total_chars"]})
        return pages

    def retrieval_context(self, request):
        """Protect the latest complete read exchange during native compaction.

        Keep its original calls and responses together. Older pages remain
        available through native archives and are not pinned indefinitely.
        """
        if signed_history(request):
            return []
        for end in range(len(request.contents) - 1, -1, -1):
            responses = [p.function_response for p in request.contents[end].parts or [] if p.function_response]
            if not responses:
                continue
            if any(not r.id or self._bounded_read(r) is None for r in responses):
                return []
            pending = {r.id for r in responses}
            for start in range(end - 1, -1, -1):
                earlier = [p.function_response for p in request.contents[start].parts or [] if p.function_response]
                if earlier:
                    if any(not r.id or self._bounded_read(r) is None or r.id in pending for r in earlier):
                        return []
                    pending.update(r.id for r in earlier)
                calls = [p.function_call for p in request.contents[start].parts or [] if p.function_call]
                if calls:
                    if {c.id for c in calls} == pending:
                        return request.contents[start:end + 1]
                    return []  # Never pin an orphan or only half of a batch.
            return []
        return []

    def set_scope(self, scope: str):
        if scope != self.scope:
            self.scope = scope
            self.references.clear()
            self.reference_roles.clear()
            self.measured_tokens = None
            self.measured_estimate = 0
            self.last_stats = {}
            self.visible_reads = []

    def issue_reference(self, invocation_id: str, *, source_role: str | None = None):
        """Host-only handoff of one complete, redacted result in this Run.

        Scope changes revoke access. Only a host handoff can share another
        role's evidence; this method is never exposed as a model tool.
        """
        return self.issue_references([(invocation_id, source_role or self.role)]).get(invocation_id)

    def issue_references(self, references):
        """Batch explicit host handoffs against one native Run snapshot."""
        if self.trace.invocations is None:
            return {}
        rows = self.trace.invocations.records("tools")
        issued = {}
        for invocation_id, source_role in references:
            row = rows.get(invocation_id)
            if (not row or row.get("run_id") != self.trace.invocations.run_id or
                    row.get("role") != source_role or row.get("state") != "returned" or
                    not row.get("response_complete")):
                continue
            self.references.add(invocation_id)
            self.reference_roles[invocation_id] = source_role
            issued[invocation_id] = {"invocation_id": invocation_id, "run_id": row["run_id"], "section": "response"}
        return issued

    def _record(self, response, rows=None):
        if self.trace.invocations is None or not response.id:
            return None
        matches = [row for row in (rows if rows is not None else self.trace.invocations.records("tools")).values()
            if row["call_id"] == response.id and row["role"] == self.role and row["state"] == "returned"
            and row["name"] == response.name and row.get("response_complete")]
        return matches[0] if len(matches) == 1 else None

    def project_results(self, request):
        if signed_history(request):
            return 0
        changed = 0
        rows = None
        for content in request.contents:
            for part in getattr(content, "parts", None) or []:
                response = getattr(part, "function_response", None)
                if response is None or self._bounded_read(response) is not None:
                    continue
                # A persisted working checkpoint may already contain a projection.
                # Re-authorize it only against this Run's returned native record.
                refresh_projection = False
                projection = response.response.get("context_projection", {}) if isinstance(response.response, dict) else {}
                if isinstance(projection, dict) and projection.get("run_id") == getattr(self.trace.invocations, "run_id", None):
                    if rows is None and self.trace.invocations is not None:
                        rows = self.trace.invocations.records("tools")
                    row = self._record(response, rows)
                    if row is not None and projection.get("native_invocation_id") == row["id"]:
                        self.references.add(row["id"])
                        if projection.get("version") == 2:
                            continue
                        # Refresh an older opaque view from its complete native
                        # archive, including after checkpoint restoration.
                        refresh_projection = True
                if not refresh_projection and len(json.dumps(response.response, ensure_ascii=False)) <= 8000:
                    continue
                if rows is None and self.trace.invocations is not None:
                    rows = self.trace.invocations.records("tools")
                row = self._record(response, rows)
                if row is None:
                    continue  # No archive means there is no honest retrieval reference.
                self.references.add(row["id"])
                preview = _preview(row.get("response", {}))
                if len(json.dumps(preview, ensure_ascii=False)) > 6000:
                    preview = _preview(row.get("response", {}), text_limit=400, item_limit=2, field_limit=8)
                if len(json.dumps(preview, ensure_ascii=False)) > 6000:
                    # Never cut serialized JSON: that erases later fields and
                    # teaches the model paths belonging only to the preview.
                    raw = row.get("response", {})
                    preview = {"read_pointer": "", "truncated": True,
                               "keys_preview": [k for k in raw if len(k) <= 80][:16]} if isinstance(raw, dict) else {
                                   "read_pointer": "", "truncated": True, "type": type(raw).__name__}
                part.function_response = response.model_copy(update={"response": {
                    "context_projection": {"version": 2, "native_invocation_id": row["id"], "run_id": self.trace.invocations.run_id,
                        "arguments_reference": {"invocation_id": row["id"], "section": "arguments", "pointer": ""},
                        "truncated": True, "instruction": "Partial preview of a real archived result, not a new receipt. "
                        "Read paths are rooted at the ORIGINAL response (or arguments). Use each omitted subtree's "
                        "read_pointer with read_tool_result. preview, text_preview, items_preview and fields_preview "
                        "are view-only wrappers, not path segments added to the archive. Read only the needed field; "
                        "for a string use character offset/limit directly at its read_pointer."},
                    "preview": preview}})
                changed += 1
        return changed

    async def read_tool_result(self, invocation_id: str, pointer: str = "", offset: int = 0, limit: int = 4000, section: str = "response") -> dict:
        """Read a bounded slice of a projected result or arguments in this Run.

        pointer addresses the ORIGINAL archive, e.g. /stdout or /execution/stdout.
        Use read_pointer from previews; view-only wrappers are not archive paths.
        Offsets and lengths are characters. Read one needed field rather than the
        entire result. Invalid arguments return feedback for correction.
        To read submitted source, use section='arguments', pointer='/source';
        /arguments/source is not a response path. limit is 1..8000 characters.
        Use an issued invocation_id from a host handoff or native_invocation_id
        from a context projection. No tool is replayed.
        """
        # Use the same failure/cancellation boundary as authorized capability
        # tools. A bad read is tool feedback, not a Run-level runtime failure.
        outcome = await execute_tool(lambda: self._read_tool_result(invocation_id, pointer, offset, limit, section))
        return outcome.response

    async def _read_tool_result(self, invocation_id, pointer, offset, limit, section):
        if not isinstance(invocation_id, str) or invocation_id not in self.references or self.trace.invocations is None:
            raise PermissionDeniedError("This result was not projected into the current role's context. Use an issued native_invocation_id.")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
            raise ResourceValidationError("Use a nonnegative integer offset and integer limit from 1 to 8000")
        if not isinstance(section, str) or section not in {"response", "arguments"}:
            raise ResourceValidationError("Section must be response or arguments")
        if not isinstance(pointer, str) or len(pointer) > 2048 or (pointer and not pointer.startswith("/")):
            raise ResourceValidationError("Use an RFC 6901 JSON pointer, or an empty string for the section root")
        row = self.trace.invocations.records("tools").get(invocation_id)
        if (row is None or row["role"] != self.reference_roles.get(invocation_id, self.role) or
                row["state"] != "returned" or not row.get("response_complete") or
                row["run_id"] != self.trace.invocations.run_id):
            raise PermissionDeniedError("Archived result is unavailable for this role")
        value = row.get("effective_arguments", row.get("arguments")) if section == "arguments" else row.get("response")
        resolved = ""
        if pointer:
            for segment in pointer[1:].split("/"):
                if re.search(r"~(?![01])", segment):
                    raise ResourceValidationError("JSON pointer escapes must be ~0 for '~' and ~1 for '/'")
                key = segment.replace("~1", "/").replace("~0", "~")
                parent = value
                try:
                    if isinstance(value, list):
                        if not re.fullmatch(r"0|[1-9][0-9]*", key):
                            raise ValueError("Use a canonical array index")
                        value = value[int(key)]
                    elif isinstance(value, dict):
                        value = value[key]
                    else:
                        raise TypeError("Scalar values have no child paths")
                except (KeyError, IndexError, TypeError, ValueError):
                    # Only describe the nearest parent after Run/role/access
                    # checks. No guessed reference can enumerate another Run.
                    details = {"resolved_pointer": resolved, "value_type": type(parent).__name__}
                    # Suggest the correct section only when the omitted section
                    # prefix actually names a field in this authorized record.
                    # Do not silently reinterpret the caller's pointer.
                    if not resolved and key in {"arguments", "response"}:
                        alternate = row.get("effective_arguments", row.get("arguments")) if key == "arguments" else row.get("response")
                        remainder = pointer[len("/" + key):]
                        try:
                            for segment in remainder[1:].split("/") if remainder else []:
                                child = segment.replace("~1", "/").replace("~0", "~")
                                alternate = alternate[int(child)] if isinstance(alternate, list) else alternate[child]
                        except (KeyError, IndexError, TypeError, ValueError):
                            pass
                        else:
                            details["retry_arguments"] = {"invocation_id": invocation_id, "section": key,
                                "pointer": remainder, "offset": offset, "limit": limit}
                    if isinstance(parent, dict):
                        details["available_keys"] = [k for k in parent if len(k) <= 80][:16]
                        details["total_keys"] = len(parent)
                    elif isinstance(parent, list):
                        details["total_items"] = len(parent)
                    elif isinstance(parent, str):
                        details["total_chars"] = len(parent)
                    return {"ok": False, "error": {"code": "tool_result_pointer", "type": "ResourceValidationError",
                        "message": "JSON pointer does not resolve in the original archive. Read the resolved parent "
                        "or choose one of its actual keys. A string is read directly with offset/limit; it has no preview child.",
                        **details}, "invocation_id": invocation_id, "pointer": pointer, "section": section}
                resolved = _pointer_child(resolved, key)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if offset > len(text):
            return {"ok": False, "error": {"code": "tool_result_offset", "type": "ResourceValidationError",
                "message": "Offset is beyond this field. Use an offset up to total_chars.", "total_chars": len(text)},
                "invocation_id": invocation_id, "pointer": pointer, "section": section}
        return {"invocation_id": invocation_id, "pointer": pointer, "section": section,
            "text": text[offset:offset + limit], "offset": offset, "total_chars": len(text),
            "next_offset": offset + limit if offset + limit < len(text) else None,
            "truncated": offset > 0 or offset + limit < len(text)}

    async def prepare(self, request, *, provider_overflow=False):
        coalesced = coalesce_unsigned_thoughts(request)
        projected = self.project_results(request)
        stats = request_usage(request)
        # Measure what will actually be sent after cheap archived-result
        # projection. Do not tokenize a discarded multi-megabyte result first.
        before = stats["estimated_input_tokens"]
        capacity = input_capacity(request, self.budget)
        # Usage deltas calibrate future requests without claiming an exact tokenizer.
        estimated = stats["estimated_input_tokens"]
        calibrated = max(0, self.measured_tokens + estimated - self.measured_estimate) if self.measured_tokens is not None else estimated
        reduced_window = False
        compaction_seconds = 0.
        if provider_overflow or (capacity is not None and max(estimated, calibrated) > capacity):
            reducer = self.capabilities.native_compact or (None if stats["signed_history"] else self.reducer)
            if provider_overflow and reducer is None:
                raise AgentRuntimeError("Provider rejected the context window; configure its actual limits or supply adapter-native compaction. Existing history was retained.")
            if reducer is not None:
                started = time.monotonic()
                result = reducer(request)
                if inspect.isawaitable(result):
                    await result
                compaction_seconds = round(time.monotonic() - started, 3)
                reduced_window = True
                reduced = request_usage(request)
                if reduced["estimated_input_tokens"] >= estimated:
                    raise AgentRuntimeError("Context reduction did not shrink the final request; existing history was retained")
                stats = reduced
                self.measured_tokens = None
                calibrated = stats["estimated_input_tokens"]
            if capacity is not None and max(stats["estimated_input_tokens"], calibrated) > capacity:
                detail = " Signed history requires an adapter-supported compaction or a fresh delegation from saved facts." if stats["signed_history"] else " Read large artifacts in slices or shorten fixed tool/instruction context."
                raise AgentRuntimeError("Final model request exceeds its configured safe input window; no model or tool was dispatched." + detail)
        stats.update(input_capacity=capacity, known_window=self.budget.known,
            coalesced_thought_parts=coalesced,
            calibrated_input_tokens=calibrated, projected_tool_results=projected,
            before_compaction_input_tokens=before, role=self.role, scope=self.scope,
            referenced_tool_results=len(self.references), reduced_window=reduced_window,
            compaction_seconds=compaction_seconds,
            reduction_ratio=round(1 - stats["estimated_input_tokens"] / max(1, before), 4),
            provider_reasoning_preserved=self.capabilities.preserve_tool_reasoning,
            native_compaction_available=callable(self.capabilities.native_compact),
            provider_native_compaction_available=callable(self.capabilities.native_compact),
            oaw_compaction_available=callable(self.reducer))
        self.last_stats = stats
        self.last_estimate = stats["estimated_input_tokens"]
        self.visible_reads = self.read_pages(request)
        self.trace.context_stats[self.role] = stats
        return stats

    def observe_usage(self, response):
        if getattr(response, "partial", False):
            return
        tokens = getattr(getattr(response, "usage_metadata", None), "prompt_token_count", None)
        if tokens is not None and tokens > 0:
            self.measured_tokens, self.measured_estimate = tokens, self.last_estimate
