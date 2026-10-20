"""Bounded public tool details for workers, live streams and durable Run traces.

This module has no host or runtime dependencies so isolated Pack workers can
bound evidence before serializing it onto their transport.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_MAX_CHARS = 16_000
_MAX_ITEMS = 100
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|access_?token|refresh_?token|id_?token|token|password|passwd|"
    r"secret|private_?key|authorization|cookie|credentials?)(?:$|_)", re.I
)


def public_tool_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only public tool fields; never persist provider configuration.

    Providers must still redact known secret values before emitting events.
    Key masking is additional defence, not a promise to detect arbitrary secrets.
    Bound traversal before serialization so oversized results stay inexpensive.
    """
    truncated = bool(payload.get("truncated"))
    remaining = _MAX_CHARS

    def clean(value: Any, depth: int = 0) -> Any:
        nonlocal truncated, remaining
        if remaining <= 0 or depth >= 8:
            truncated = True
            return "[truncated]"
        if isinstance(value, str):
            # Also mask structured JSON returned as text (common for tool results).
            if len(value) <= remaining and value.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(value)
                except (ValueError, RecursionError):
                    pass
                else:
                    return clean(parsed, depth + 1)
            text = value[:remaining]
            remaining -= len(text)
            if len(text) < len(value):
                truncated = True
                text += "\n[truncated]"
            return text
        if isinstance(value, Mapping):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= _MAX_ITEMS or remaining <= 0:
                    truncated = True
                    result["[truncated]"] = True
                    break
                name = str(key)[:256]
                remaining -= len(name)
                result[name] = "[REDACTED]" if _SECRET_KEY.search(name.replace("-", "_")) else clean(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for index, item in enumerate(value):
                if index >= _MAX_ITEMS or remaining <= 0:
                    truncated = True
                    result.append("[truncated]")
                    break
                result.append(clean(item, depth + 1))
            return result
        if value is None or isinstance(value, (bool, int, float)):
            remaining -= 8
            return value
        # Avoid serializing SDK objects/configuration via arbitrary repr().
        return "[unsupported value]"

    result: dict[str, Any] = {"name": str(payload.get("name") or "tool")[:256]}
    arguments = payload.get("arguments")
    if result["name"] == "oaw_invoke_tool" and isinstance(arguments, Mapping):
        capability = arguments.get("capability_id")
        if isinstance(capability, str):
            result["capability_id"] = capability[:512]
            # These are named operation contracts, not opaque resource IDs.
            if capability.startswith(("operation:", "host:")):
                result["tool_name"] = capability.split(":", 1)[1][:256]
    for key in ("tool_name", "capability_id", "author", "response_visibility", "role", "archive_id"):
        if isinstance(payload.get(key), str):
            result[key] = payload[key][:512]
    if isinstance(payload.get("subagent"), bool):
        result["subagent"] = payload["subagent"]
    if type(payload.get("run_attempt")) is int and payload["run_attempt"] >= 0:
        result["run_attempt"] = payload["run_attempt"]
    if isinstance(payload.get("ok"), bool) and "success" not in payload:
        result["success"] = payload["ok"]
    call_id = payload.get("call_id")
    if isinstance(call_id, str):
        result["call_id"] = call_id[:512]
    # Retain explicit failures even when a large output exhausts the budget.
    for key in ("success", "status", "error", "arguments", "response"):
        if key in payload:
            result[key] = clean(payload[key])
    if truncated:
        result["truncated"] = True
    return result
