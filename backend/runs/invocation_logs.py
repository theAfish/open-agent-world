"""Full, redacted ADK boundary records in OAW's existing host-only Run state.

These are application requests/results, not raw HTTP transport captures. Unknown
usage and unsupported SDK values remain explicit; recovery checkpoints and public
activity previews keep their independent bounds.
"""
from __future__ import annotations

import base64
import asyncio
import dataclasses
import json
import math
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from backend.errors import NotFoundError
from backend.security.redaction import redact

FIELDS = {"models": "model_request_logs", "tools": "tool_invocation_logs"}
_CREDENTIAL_KEYS = {"api_key", "authorization", "password", "secret", "token", "access_token",
                    "refresh_token", "cookie", "set-cookie", "x-api-key", "credentials"}


def archive_value(value: Any, secrets=()) -> tuple[Any, list[str]]:
    """JSON snapshot without body/array truncation or repr of privileged objects."""
    unavailable = []
    active = set()

    def convert(item, path):
        identity = id(item)
        if identity in active:
            unavailable.append(path)
            return {"unavailable_type": type(item).__name__, "circular_reference": True}
        active.add(identity)
        try:
            return convert_item(item, path)
        finally:
            active.remove(identity)

    def convert_item(item, path):
        from backend.agents.media import ToolImage, VisualToolResult
        if isinstance(item, VisualToolResult):
            unavailable.extend(f"{path}.images[{i}].data" for i in range(len(item.images)))
            return convert({**item.summary(), "images_omitted": True}, path + ".summary")
        if isinstance(item, ToolImage):
            unavailable.append(path + ".data")
            return {"media_type": item.media_type, "size_bytes": len(item.data), "omitted": True}
        if item is None or isinstance(item, (str, bool, int)):
            return redact(item, secrets)
        if isinstance(item, float) and math.isfinite(item):
            return item
        if isinstance(item, bytes):
            return {"encoding": "base64", "data": base64.b64encode(item).decode("ascii")}
        if isinstance(item, Enum):
            return convert(item.value, path)
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                name = str(key)
                safe_name = redact(name, secrets)
                sensitive = name.lower() in _CREDENTIAL_KEYS or name.lower().endswith(("_api_key", "_password", "_secret"))
                result[safe_name] = "[REDACTED]" if sensitive else convert(child, path + "." + safe_name)
            if isinstance(result.get("parts"), list):
                # Aggregates can retain token-sized text Parts. Redact after
                # reassembly as well, so a key split across Parts cannot be
                # reconstructed from the saved JSON even though each Part alone
                # contained only an innocuous prefix/suffix.
                parts = [part for part in result["parts"] if isinstance(part, dict) and isinstance(part.get("text"), str)]
                joined = "".join(part["text"] for part in parts)
                spans = []
                for secret in secrets:
                    if not secret:
                        continue
                    start = 0
                    while (start := joined.find(secret, start)) >= 0:
                        spans.append((start, start + len(secret)))
                        start += len(secret)
                if spans:
                    offset = 0
                    for part in parts:
                        text = part["text"]
                        affected = sorted((max(0, left - offset), min(len(text), right - offset))
                                          for left, right in spans if left < offset + len(text) and right > offset)
                        end = 0
                        safe = []
                        for left, right in affected:
                            if right <= end:
                                continue
                            safe.extend((text[end:max(end, left)], "[REDACTED]"))
                            end = max(end, right)
                        if affected:
                            part["text"] = "".join(safe) + text[end:]
                        offset += len(text)
            return result
        if isinstance(item, (list, tuple)):
            return [convert(child, f"{path}[{i}]") for i, child in enumerate(item)]
        if callable(getattr(item, "model_dump", None)):
            try:
                return convert(item.model_dump(mode="python", exclude_none=True), path)
            except (TypeError, ValueError, RecursionError):
                unavailable.append(path)
                return {"unavailable_type": type(item).__name__, "serialization_failed": True}
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            # asdict recursively copies before our cycle check can run.
            return convert({field.name: getattr(item, field.name) for field in dataclasses.fields(item)}, path)
        unavailable.append(path)
        return {"unavailable_type": type(item).__name__}

    from backend.agents.media import durable_tool_media
    safe, omitted = durable_tool_media(convert(value, "$"))
    return safe, [*unavailable, *omitted]


def model_identity(role, request, run_attempt=0, model_attempt=1):
    return f"model-request:{run_attempt}:{role}:{request}:{model_attempt}"


def tool_identity(role, call_id, run_attempt=0):
    return f"tool-call:{run_attempt}:{role}:{call_id}"


class RunInvocationLogs:
    def __init__(self, state, run_id: str, *, api_key: str | None = None):
        self.state = state
        self.run_id = run_id
        self.secrets = [api_key] if api_key else []

    def records(self, kind: str) -> dict[str, dict]:
        key = FIELDS[kind]
        try:
            return self.state.get(self.state.get_scope("run", self.run_id), key)
        except NotFoundError:
            return {}

    def _save(self, kind, identity, fields):
        scope = self.state.ensure_scope("run", self.run_id)
        # Merge only this identity, using StateStore's normal transaction/revision.
        with self.state.database.transaction(immediate=True):
            self.state.patch(scope, FIELDS[kind], {identity: fields}, run_id=self.run_id)
            if kind == 'tools':
                from .history import RunActivityHistory
                RunActivityHistory(self.state.database, self.run_id).tool(fields)
        return fields

    def _settle(self, kind, identity, fields):
        current = self.records(kind).get(identity)
        if not current or current["state"] != "started":
            return current
        return self._save(kind, identity, {**current, **fields, "updated_at": datetime.now(UTC).isoformat()})

    def start_model(self, role, request_id, run_attempt, model_attempt, request, *, model=None, stream=False, context_stats=None, provenance=None):
        identity = model_identity(role, request_id, run_attempt, model_attempt)
        records = self.records("models")
        if identity in records:
            return identity
        # tools_dict contains live capability objects; config.tools is the wire declaration.
        snapshot = {key: getattr(request, key, None) for key in (
            "model", "contents", "config", "live_connect_config", "cache_config", "cache_metadata",
            "cacheable_contents_token_count", "previous_interaction_id")}
        snapshot["model"] = snapshot["model"] or model
        safe, missing = archive_value(snapshot, self.secrets)
        provenance_id = next((row["id"] for row in records.values() if row.get("provenance_complete")), None)
        safe_provenance, provenance_missing = archive_value(provenance if provenance_id is None else None, self.secrets)
        stamp = datetime.now(UTC).isoformat()
        self._save("models", identity, {"id": identity, "run_id": self.run_id, "role": role,
            "model_request": request_id, "model_attempt": model_attempt, "run_attempt": run_attempt,
            "state": "started", "started_at": stamp, "updated_at": stamp,
            "scope": "ADK request at model delegate boundary", "stream": stream,
            "model": safe["model"], "max_output_tokens": (safe.get("config") or {}).get("max_output_tokens")
                if isinstance(safe.get("config"), dict) else None,
            "request": safe, "request_complete": not missing, "unavailable_request_paths": missing,
            "provenance": safe_provenance, "provenance_ref": provenance_id,
            "provenance_complete": safe_provenance is not None and not provenance_missing,
            "context": context_stats, "phase": "context_compaction" if role.endswith(":context_compaction") else
                "context_preflight" if (context_stats or {}).get("phase") == "context_preflight" else "model",
            "input_tokens": None, "output_tokens": None, "usage_available": False,
            "truncated": False, "redacted": True})
        return identity

    def finish_model(self, identity, responses, *, stream_metrics=None):
        safe, missing = archive_value(responses, self.secrets)
        usage = next((getattr(row, "usage_metadata", None) for row in reversed(responses)
                      if getattr(row, "usage_metadata", None) is not None), None)
        finish = getattr(responses[-1], "finish_reason", None) if responses else None
        return self._settle("models", identity, {"state": "returned", "response": safe,
            "response_complete": not missing, "unavailable_response_paths": missing,
            "returned_at": datetime.now(UTC).isoformat(),
            "finish_reason": str(getattr(finish, "name", finish)).rsplit(".", 1)[-1] if finish is not None else None,
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "cached_input_tokens": getattr(usage, "cached_content_token_count", None),
            "reasoning_tokens": getattr(usage, "thoughts_token_count", None),
            "usage_available": usage is not None,
            **({"stream_metrics": archive_value(stream_metrics, self.secrets)[0]} if stream_metrics is not None else {})})

    def interrupt_model(self, identity, *, error_type=None, stream_metrics=None):
        updates = {"state": "interrupted", "interrupted_at": datetime.now(UTC).isoformat(),
                   "response_complete": False}
        # Generic Run cleanup may settle an already diagnosed interruption.
        # Absence of a new cause must not erase its original error type.
        if error_type is not None:
            updates["error_type"] = error_type
        if stream_metrics is not None:
            updates["stream_metrics"] = archive_value(stream_metrics, self.secrets)[0]
        return self._settle("models", identity, updates)

    def start_tool(self, role, call_id, run_attempt, name, arguments):
        identity = tool_identity(role, call_id, run_attempt)
        if identity in self.records("tools"):
            return identity
        safe, missing = archive_value(arguments, self.secrets)
        stamp = datetime.now(UTC).isoformat()
        self._save("tools", identity, {"id": identity, "run_id": self.run_id, "role": role,
            "call_id": call_id, "run_attempt": run_attempt, "name": name, "state": "started",
            "arguments": safe, "arguments_complete": not missing, "unavailable_argument_paths": missing,
            "started_at": stamp, "updated_at": stamp, "truncated": False, "redacted": True})
        return identity

    def tool_arguments(self, identity, arguments, *, overridden=False):
        current = self.records("tools").get(identity)
        if not current or current["state"] != "started":
            return
        safe, missing = archive_value(arguments, self.secrets)
        self._save("tools", identity, {**current, "effective_arguments": safe,
            "effective_arguments_complete": not missing, "unavailable_effective_argument_paths": missing,
            "overridden": overridden})

    def finish_tool(self, identity, response):
        safe, missing = archive_value(response, self.secrets)
        return self._settle("tools", identity, {"state": "returned", "response": safe,
            "response_complete": not missing, "unavailable_response_paths": missing,
            "returned_at": datetime.now(UTC).isoformat()})

    def error_tool(self, identity, error):
        # Exception strings may contain secrets and arbitrary non-JSON objects.
        return self._settle("tools", identity, {"state": "interrupted" if isinstance(error, asyncio.CancelledError) else "failed",
            "error_type": type(error).__name__,
            "error": redact(str(error), self.secrets), "response_complete": False,
            "finished_at": datetime.now(UTC).isoformat()})

    def interrupt(self):
        for kind in FIELDS:
            for identity, row in self.records(kind).items():
                if row["state"] == "started":
                    self._settle(kind, identity, {"state": "interrupted",
                        "interrupted_at": datetime.now(UTC).isoformat(), "response_complete": False})

    def page(self, kind, offset, limit):
        rows = sorted(self.records(kind).values(), key=lambda row: (row["started_at"], row["id"]))
        items = []
        for row in rows[offset:offset + limit]:
            item = {key: value for key, value in row.items() if key not in
                    {"request", "response", "arguments", "effective_arguments", "error"}}
            if kind == "tools":
                # Lists return metadata, never huge source or stdout bodies.
                item["arguments_preview"] = json.dumps(row.get("effective_arguments", row.get("arguments")), ensure_ascii=False)[:2000]
            items.append(item)
        return {"run_id": self.run_id, "items": items, "total": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    def get(self, kind, identity):
        from .history import stored_record
        row = stored_record(self.state.database, self.run_id, FIELDS[kind], identity)
        if row is None:
            raise NotFoundError("No archived invocation for this Run")
        return row
