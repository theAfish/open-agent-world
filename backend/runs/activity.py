"""Compact durable activity metadata, independent of the bounded UI trace."""
from __future__ import annotations

from typing import Any


def activity_update(lifecycle: dict[str, Any], kind: str, payload: dict[str, Any],
                    event_id: str, timestamp: str) -> dict[str, Any]:
    if kind == "agent_progress":
        updates = {}
        if payload.get("stage") in {"imported", "confirmed"} and isinstance(payload.get("target"), str):
            outcomes = dict(lifecycle.get("structure_outcomes") or {})
            outcomes[payload["target"]] = {"phase": payload["stage"], "revision": payload.get("revision")}
            updates["structure_outcomes"] = outcomes
        if payload.get("kind") == "model_reasoning" or payload.get("phase") not in {"context", "model", "model_recovery", "model_preflight_rejected"}:
            return updates
        if not isinstance(payload.get("model_request"), int):
            return updates
        requests = [dict(row) for row in lifecycle.get("model_requests", [])]
        identity = {key: payload.get(key, 0) for key in ("role", "model_request", "run_attempt", "model_attempt")}
        row = next((row for row in requests if all(row.get(k) == v for k, v in identity.items())), None)
        if row is None:
            row = dict(identity)
            requests.append(row)
        if payload["phase"] == "model_preflight_rejected":
            row.update({"interrupted_at": timestamp, "error_origin": "local_context_preflight",
                        "provider_dispatched": False, "reasoning_available": False, "retrying": False,
                        "error_type": payload.get("error_type"),
                        "context": payload.get("context")})
        elif payload["phase"] == "model_recovery":
            # Preserve OAW's native idle/transport diagnostics in the same Run
            # metadata. An interrupted attempt is not a completed response.
            row.update({key: payload[key] for key in ("error_type", "error_origin", "recovery_scope",
                "elapsed_seconds", "idle_seconds", "first_content_seconds", "received_content_chunks",
                "http_status", "retrying", "max_attempts", "next_attempt", "retry_delay_seconds",
                "observed_response_chunks", "empty_response_chunks", "first_response_seconds",
                "last_response_idle_seconds", "watchdog_scope", "error_chain_types", "stream_metrics", "recovery_mode") if key in payload})
            if "stage" in payload:
                row["stream_stage"] = payload["stage"]
            row["interrupted_at"] = timestamp
        elif payload["phase"] == "context":
            row.setdefault("prepared_at", timestamp)
        elif "elapsed_ms" in payload:
            row.update({key: payload[key] for key in ("elapsed_ms", "input_tokens", "output_tokens",
                "max_output_tokens", "finish_reason", "timeout_kind", "deadline_seconds",
                "received_content", "idle_seconds", "reasoning_available", "stream_metrics") if key in payload})
            row["returned_at"] = timestamp
        else:
            row.setdefault("started_at", timestamp)
            if "input_bytes" in payload:
                row["input_bytes"] = payload["input_bytes"]
                row["input_bytes_scope"] = payload.get("input_bytes_scope", "contents_only")
        updates["model_requests"] = requests[-512:]
        updates["model_requests_truncated"] = bool(lifecycle.get("model_requests_truncated") or len(requests) > 512)
        return updates
    if kind not in {"tool_started", "tool_completed"}:
        return {}
    metrics = dict(lifecycle.get("tool_metrics") or {"started": 0, "completed": 0, "by_name": {}})
    phase = "started" if kind == "tool_started" else "completed"
    metrics[phase] = metrics.get(phase, 0) + 1
    metrics["by_name"] = {key: dict(value) for key, value in metrics.get("by_name", {}).items()}
    name = str(payload.get("name") or "tool")
    counts = metrics["by_name"].setdefault(name, {"started": 0, "completed": 0})
    counts[phase] += 1
    # Keep identities and small receipts for the complete ordinary Run. Large
    # argv/source/stdout/images stay in native logs, not lifecycle JSON.
    calls = [dict(row) for row in lifecycle.get("tool_calls", [])]
    identity = payload.get("call_id") or event_id
    row = next((r for r in calls if r["call_id"] == identity and r["name"] == name), None)
    if row is None:
        row = {"call_id": identity, "name": name}
        calls.append(row)
    row[phase + "_at"] = timestamp
    response = payload.get("response")
    if isinstance(response, dict):
        row.update({key: response[key] for key in ("exit_code", "command_id", "operation_id", "revision",
            "cancelled", "timed_out") if key in response})
        row["failed"] = bool(response.get("error") or response.get("ok") is False or response.get("success") is False
                             or (isinstance(response.get("exit_code"), int) and response["exit_code"] != 0))
    return {"tool_metrics": metrics, "tool_calls": calls[-1024:],
        "tool_calls_truncated": bool(lifecycle.get("tool_calls_truncated") or len(calls) > 1024)}
