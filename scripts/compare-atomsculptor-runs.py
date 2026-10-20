"""Summarize exported Agent/Conversation logs without printing their contents."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
import hashlib
from pathlib import Path
import re


def objects(path: Path):
    raw = path.read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else [value]
    except ValueError:
        decoder = json.JSONDecoder()
        found = []
        for match in re.finditer(r"^\{", raw, re.MULTILINE):
            try:
                value, _ = decoder.raw_decode(raw[match.start():])
                found.append(value)
            except ValueError:
                continue
        return found


def summarize(agent_path: Path, conversation_path: Path):
    entries = objects(agent_path)
    runs = [item["run"] for item in entries if isinstance(item, dict) and isinstance(item.get("run"), dict)]
    if not runs:
        raise ValueError("Agent log must contain an exported run object")
    run = runs[-1]
    lifecycle = run.get("lifecycle") or {}
    trace = lifecycle.get("tool_trace", run.get("tool_trace", []))
    calls = [item for item in trace if item.get("type") == "tool_started"]
    results = [item for item in trace if item.get("type") == "tool_completed"]
    trace_truncated = bool(lifecycle.get("tool_trace_truncated") or lifecycle.get("tool_count", 0) > len(calls))
    metrics = lifecycle.get("tool_metrics")
    by_name = ({name: counts.get("started", 0) for name, counts in metrics.get("by_name", {}).items()}
               if metrics else dict(Counter(item.get("name") for item in calls)))
    tool_calls = lifecycle.get("tool_calls")
    ordered_calls = ([{"name": row["name"]} for row in tool_calls if row.get("started_at")]
                     if tool_calls is not None else calls)
    streak = longest = 0
    for call in ordered_calls:
        if call.get("name") == "execute_command":
            streak = 0
        elif call.get("name") == "write_sandbox_text_file":
            streak += 1
            longest = max(longest, streak)
    conversation = conversation_path.read_text(encoding="utf-8")
    starts = re.findall(r"^.*? · model request (\d+) started \((\d+) input bytes\)", conversation, re.MULTILINE)
    elapsed = re.findall(r"^.*? · model request \d+ returned in ([\d.]+)s", conversation, re.MULTILINE)
    duration = None
    if run.get("started_at") and run.get("finished_at"):
        duration = (datetime.fromisoformat(run["finished_at"].replace("Z", "+00:00")) -
                    datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))).total_seconds()
    usage_by_request = {}
    for index, item in enumerate(entries):
        if isinstance(item, dict) and isinstance(item.get("output_tokens"), int):
            identity = (item.get("role"), item.get("model_request", index), item.get("model_attempt"))
            usage_by_request[identity] = item
    usage = list(usage_by_request.values())
    model_requests = lifecycle.get("model_requests")
    if model_requests is not None:
        started = [row for row in model_requests if row.get("started_at")]
        returned = [row for row in model_requests if row.get("returned_at")]
        usage = returned
        started_count, returned_count = len(started), len(returned)
        model_seconds = sum(row.get("elapsed_ms", 0) / 1000 for row in returned)
    else:
        started_count, returned_count = len(starts), len(elapsed)
        model_seconds = sum(float(value) for value in elapsed)
    native_models = read_json(agent_path.parent / "model requests.json")
    phase_usage = None
    if isinstance(native_models, list) and native_models:
        # Native archives include compactions and interrupted attempts which
        # public lifecycle snapshots can omit. Never fill unknown usage with 0.
        usage = native_models
        phase_usage = {}
        for row in native_models:
            phase = row.get("phase") or "task"
            cost = phase_usage.setdefault(phase, {"attempts": 0, "returned": 0, "interrupted": 0,
                "retry_attempts": 0, "known_input_tokens": None, "known_output_tokens": None,
                "known_reasoning_tokens": None, "known_cached_input_tokens": None,
                "attempts_with_unknown_usage": 0, "returned_model_seconds": 0., "interrupted_model_seconds": 0.})
            cost["attempts"] += 1
            returned = bool(row.get("returned_at"))
            cost["returned"] += returned
            cost["interrupted"] += bool(row.get("interrupted_at") or row.get("state") == "interrupted")
            cost["retry_attempts"] += (row.get("model_attempt") or 1) > 1
            elapsed_seconds = seconds_between(row.get("started_at"), row.get("returned_at") or row.get("interrupted_at"))
            if elapsed_seconds is not None:
                cost["returned_model_seconds" if returned else "interrupted_model_seconds"] += elapsed_seconds
            for field in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens"):
                if type(row.get(field)) is int:
                    cost["known_" + field] = (cost["known_" + field] or 0) + row[field]
            cost["attempts_with_unknown_usage"] += any(type(row.get(field)) is not int for field in ("input_tokens", "output_tokens"))
        started_count = len(native_models)
        returned_count = sum(bool(row.get("returned_at")) for row in native_models)
        model_seconds = sum(cost["returned_model_seconds"] for cost in phase_usage.values())
    known_output = [row["output_tokens"] for row in usage if isinstance(row.get("output_tokens"), int)]
    known_input = [row["input_tokens"] for row in usage if isinstance(row.get("input_tokens"), int)]
    complete_usage = ((phase_usage is not None or model_requests is not None and not lifecycle.get("model_requests_truncated"))
                      and started_count == returned_count and len(known_output) == returned_count
                      and len(known_input) == returned_count)
    return {
        "run_id": run.get("run_id"), "status": run.get("status"), "duration_seconds": duration,
        "model_requests_started": started_count, "model_requests_returned": returned_count,
        "returned_model_seconds": round(model_seconds, 3),
        "model_usage_by_phase": phase_usage,
        "model_usage_source": "native_invocation_archives_including_compaction" if phase_usage is not None else "public_lifecycle_or_conversation",
        "tool_calls_started": metrics["started"] if metrics else lifecycle.get("tool_count", len(calls)),
        "tool_calls_completed": metrics["completed"] if metrics else None if trace_truncated else len(results),
        "tool_calls_by_name": by_name,
        "tool_calls_by_name_scope": "run" if metrics or not trace_truncated else "retained_trace",
        "tool_trace_truncated": trace_truncated,
        "tool_call_metadata_truncated": bool(lifecycle.get("tool_calls_truncated")),
        "longest_write_streak_between_execute_calls": longest,
        "write_streak_scope": "accepted_sdk_calls_only" if by_name.get("run_modelling_step") else "run" if (tool_calls is not None and not lifecycle.get("tool_calls_truncated")) or not trace_truncated else "retained_trace",
        "import_calls": by_name.get("import_atom_structure_file", 0),
        "known_output_tokens": sum(known_output) if known_output else None,
        "known_input_tokens": sum(known_input) if known_input else None,
        "responses_with_known_output_tokens": len(known_output),
        "model_usage_scope": "run" if complete_usage else "partial_or_unavailable",
        "structure_outcomes": lifecycle.get("structure_outcomes", {}),
        "delivery_state": lifecycle.get("delivery_state"),
        "execution_detail": execution_detail(agent_path.parent, run, model_requests or []),
        "experiment": read_json(agent_path.parent / "experiment.json"),
        "notes": ["Use model_usage_scope and tool_calls_by_name_scope before comparing full-run costs/counts.",
                  "Native model totals include compactions and retries. Unknown interrupted usage is a lower bound. Reasoning and output tokens are separate provider fields; do not add them without provider semantics.",
                  "An execute_command call is not proof that Python ran or validation passed.",
                  "Import call count is not proof of a successful import or read-back; inspect receipts.",
                  "Use one run per pair of log files; the last exported run is selected."],
    }



def host_frame(row):
    """The final host frame owns the current stage/subtask; prior frames are history."""
    host = {}
    for content in (row.get("request") or {}).get("contents", []):
        if content.get("role") not in {None, "user"}:
            continue
        for part in content.get("parts", []):
            text = part.get("text") or ""
            if text.startswith("HOST RUN EVIDENCE"):
                try:
                    candidate = json.loads(text.split("\n", 1)[1])
                    if isinstance(candidate, dict):
                        host = candidate
                except (ValueError, IndexError):
                    continue
    return host


def seconds_between(start, end):
    if not start or not end:
        return None
    try:
        seconds = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                   datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
        return seconds if seconds >= 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def process_metrics(folder, run, models, tools):
    """Costs and first measured result from existing native archives, not inferred reasoning."""
    costs, by_attempt, measurements, completed = {}, {}, {}, {}
    modelling_exits, repeated_executions, intentional_repeats = {}, 0, 0
    for row in tools:
        result = row.get("response")
        if not isinstance(result, dict) or row.get("response_complete") is False:
            continue
        execution = result.get("execution", result)
        if not isinstance(execution, dict) or type(execution.get("exit_code")) is not int:
            continue
        identity = execution.get("command_id")
        step = result.get("step") or {}
        args = row.get("effective_arguments") or row.get("arguments") or {}
        if identity and (isinstance(step, dict) and step.get("state") == "finished" or
                         row.get("name") == "execute_command" and isinstance(args, dict) and
                         any(isinstance(a, str) and a.endswith(".py") for a in args.get("argv", []))):
            modelling_exits.setdefault(identity, execution["exit_code"])
        repeated_executions += bool(result.get("reused_execution"))
        intentional_repeats += bool(isinstance(args, dict) and args.get("repeat_purpose"))
        if identity and execution["exit_code"] == 0 and row.get("returned_at"):
            when = row["returned_at"]
            if identity not in completed or when < completed[identity]:
                completed[identity] = when
        report = result.get("measurements")
        step = result.get("step") or {}
        if not isinstance(step, dict):
            step = {}
        if (identity and execution["exit_code"] == 0 and step.get("state") == "finished"
                and not result.get("measurement_issues") and not result.get("reused_execution")):
            measurements[identity] = (report, step.get("subtask_id") or "unavailable")
    for row in models:
        host = host_frame(row)
        stage = host.get("modelling_workflow", {}).get("current_stage") or "unavailable"
        subtask = host.get("working_decision", {}).get("current_subtask", {}).get("subtask_id") or "unavailable"
        role = row.get("role") or "unavailable"
        key = (role, stage, subtask, row.get("phase") or "task")
        cost = costs.setdefault(key, {"role": role, "stage": stage, "subtask_id": subtask, "phase": key[3],
            "model_attempts_started": 0, "returned_model_requests": 0, "interrupted_model_attempts": 0,
            "known_input_tokens": 0, "known_output_tokens": 0, "attempts_with_unknown_usage": 0,
            "returned_model_seconds": 0., "interrupted_model_seconds": 0.,
            "received_reasoning_chars": 0, "interrupted_received_reasoning_chars": 0,
            "reasoning_records_truncated": 0})
        by_attempt[(role, row.get("run_attempt", 0), row.get("model_request"), row.get("model_attempt"))] = cost
        cost["model_attempts_started"] += bool(row.get("started_at"))
        cost["returned_model_requests"] += bool(row.get("returned_at"))
        interrupted = bool(row.get("interrupted_at") or row.get("error_type"))
        cost["interrupted_model_attempts"] += interrupted
        seconds = seconds_between(row.get("started_at"), row.get("returned_at") or row.get("interrupted_at"))
        if seconds is not None:
            cost["interrupted_model_seconds" if interrupted and not row.get("returned_at") else "returned_model_seconds"] += seconds
        for field in ("input_tokens", "output_tokens"):
            if type(row.get(field)) is int:
                cost["known_" + field] += row[field]
        for field in ("cached_input_tokens", "reasoning_tokens"):
            cost.setdefault("known_" + field, None)
            cost.setdefault("attempts_with_known_" + field, 0)
            if type(row.get(field)) is int:
                cost["known_" + field] = (cost["known_" + field] or 0) + row[field]
                cost["attempts_with_known_" + field] += 1
        cost["attempts_with_unknown_usage"] += any(type(row.get(f)) is not int for f in ("input_tokens", "output_tokens"))
        # Legacy standalone tools have no composite measurement field. A saved
        # HOST report is parsed native stdout, and its command must also have a
        # completed native invocation receipt; its prose assessment is ignored.
        current = host.get("current_experiment") or {}
        if (current.get("exit_code") == 0 and current.get("command_id") in completed
                and not current.get("measurement_issues")):
            measurements.setdefault(current["command_id"], (current.get("measurements"), subtask))
    thinking_path = folder / "model thinking.json"
    latest, unattributed_reasoning_chars = {}, 0
    if thinking_path.exists():
        for row in json.loads(thinking_path.read_text(encoding="utf-8")):
            if row.get("run_id") not in {None, run.get("run_id")}:
                continue
            identity = (row.get("role"), row.get("run_attempt", 0), row.get("model_request"), row.get("model_attempt"))
            if identity not in latest or row.get("revision", 0) >= latest[identity].get("revision", 0):
                latest[identity] = row
        for identity, row in latest.items():
            cost = by_attempt.get(identity)
            if cost is None:
                unattributed_reasoning_chars += len(row.get("text") or "")
                continue
            count = len(row.get("text") or "")
            cost["received_reasoning_chars"] += count
            if row.get("interrupted"):
                cost["interrupted_received_reasoning_chars"] += count
            cost["reasoning_records_truncated"] += bool(row.get("truncated") or row.get("suffix_withheld"))
    observed = []
    for identity, (report, subtask) in measurements.items():
        if not isinstance(report, dict) or not any(report.get(f) for f in ("values", "measurements", "objective", "constraints", "coverage", "artifacts")):
            continue
        elapsed = seconds_between(run.get("started_at"), completed.get(identity))
        if elapsed is not None:
            observed.append({"command_id": identity, "subtask_id": subtask, "kind": report.get("kind"),
                             "elapsed_seconds": round(elapsed, 3)})
    for cost in costs.values():
        for field in ("returned_model_seconds", "interrupted_model_seconds"):
            cost[field] = round(cost[field], 3)
    events_path = folder / "events.json"
    events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
    local = {((e.get("payload") or {}).get("plan_revision"), (e.get("payload") or {}).get("execution_sequence"))
             for e in events if (e.get("payload") or {}).get("workflow_event") == "modelling_subtask"}
    failed_episodes = recovered_episodes = 0
    failed = False
    for exit_code in modelling_exits.values():
        if exit_code != 0:
            if not failed:
                failed_episodes += 1
            failed = True
        elif failed:
            recovered_episodes += 1
            failed = False
    routing = {((e.get("payload") or {}).get("execution_sequence"),
                ((e.get("payload") or {}).get("recovery") or {}).get("fingerprint"))
               for e in events if (e.get("payload") or {}).get("workflow_event") == "execution_review_signal"}
    return {"model_cost_by_subtask": list(costs.values()),
        "recovery": {"modelling_failure_episodes": failed_episodes, "episodes_followed_by_successful_execution": recovered_episodes,
                     "native_execution_recovery_rate": recovered_episodes / failed_episodes if failed_episodes else None,
                     "unresolved_failure_episode": failed, "planner_review_signals": len(routing),
                     "reused_execution_receipts": repeated_executions, "explicit_repeat_actions": intentional_repeats,
                     "scope": "Observed completed modelling commands, deduplicated by command ID; execution recovery does not imply task acceptance or scientific correctness"},
        "unattributed_received_reasoning_chars": unattributed_reasoning_chars if thinking_path.exists() else None,
        "subtask_cost_scope": "saved invocation attempts; unknown stage/subtask and usage remain explicit",
        "reasoning_scope": "received provider-exposed text characters, latest revision per attempt; not reasoning tokens or hidden thoughts" if thinking_path.exists() else "unavailable",
        "first_valid_measurement": min(observed, key=lambda m: m["elapsed_seconds"]) if observed else None,
        "measurement_timing_scope": "successful native command receipt plus host-parsed nonempty report; includes network wait; not stage/task acceptance",
        "local_subtasks_completed": len(local), "local_subtask_scope": "native modelling_subtask events" if events_path.exists() else "unavailable"}


def execution_detail(folder: Path, run: dict, metadata: list):
    """Read the existing immutable export; no database, provider or replay."""
    def saved(name):
        path = folder / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    manifest = saved("manifest.json")
    if not manifest or manifest.get("run_id") != run.get("run_id"):
        return {"scope": "unavailable", "reason": "No matching native export manifest"}
    tools = saved("tool calls.json")
    models = saved("model requests.json")
    history = saved("sandbox logs.json")
    if tools is None or models is None or history is None:
        return {"scope": "unavailable", "reason": "Native invocation/command archives missing"}
    commands = {}
    command_sources = set()

    def command(row, source, fallback=None):
        if not isinstance(row, dict):
            return
        identity = row.get("command_id") or fallback
        if not identity:
            return
        command_sources.add(source)
        previous = commands.get(identity, {})
        # Running or redacted receipts must not overwrite a completed result.
        if type(previous.get("exit_code")) is int and type(row.get("exit_code")) is not int:
            commands[identity] = {**row, **previous}
        else:
            commands[identity] = {**previous, **{k: v for k, v in row.items() if v is not None}}

    for i, row in enumerate(history):
        result = row.get("tool_result") or {}
        command({**row, **result}, "sandbox_history", row.get("id") or f"history:{i}")
    command_names = {"execute_command", "run_skill_script", "wait_sandbox_operation", "cancel_command"}
    for row in tools:
        result = row.get("response")
        if not isinstance(result, dict):
            continue
        if row.get("name") in command_names:
            command(result, "tool_results")
        command(result.get("execution"), "tool_results")
        error = result.get("error") or {}
        if isinstance(error, dict):
            for key in ("validation", "conversion"):
                command(error.get(key), "delivery_results")
    for row in saved("tool receipts.json") or []:
        try:
            hints = json.loads(row.get("result_hints_json") or "{}")
        except (ValueError, TypeError):
            continue
        if row.get("capability_kind") in command_names | {"sandbox.execute", "sandbox.wait", "sandbox.cancel", "sandbox.run_skill_script"}:
            command(hints, "capability_receipts")
    commands = list(commands.values())
    rejections, native_failures = Counter(), 0
    stages, last_sources = set(), {}
    new_artifacts = changed = unchanged = versions = 0
    for row in tools:
        result = row.get("response") or {}
        if not isinstance(result, dict):
            continue
        error = result.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            rejections[error["code"]] += 1
        step = result.get("step") or {}
        save = result.get("save", result if row.get("name") == "write_sandbox_text_file" else {})
        if not isinstance(save, dict):
            continue
        args = row.get("effective_arguments") or row.get("arguments") or {}
        path = step.get("script_path") or args.get("path")
        source = (("version", step["source_version"]) if "source_version" in step else
                  ("text", args["content"]) if isinstance(args.get("content"), str) else
                  ("legacy_archive", save["sha256"]) if isinstance(save.get("sha256"), str) else None)
        if (source is not None and not save.get("reused_saved_file")
                and (type(save.get("written")) is int or "sha256" in save)
                and isinstance(path, str) and path.endswith(".py")):
            key = (step.get("sandbox") or args.get("sandbox"), path)
            previous = last_sources.get(key)
            versions += 1
            if previous is None:
                new_artifacts += 1
            elif previous == source:
                unchanged += 1
            else:
                changed += 1
            last_sources[key] = source
    returned_by_stage, seconds_by_stage = Counter(), Counter()
    for row in models:
        host = host_frame(row)
        stage = host.get("modelling_workflow", {}).get("current_stage") or "unavailable"
        if host.get("execution_role") == "structure_builder" and stage != "unavailable":
            stages.add(stage)
        if row.get("returned_at"):
            key = f"{row.get('role') or 'unavailable'}:{stage}"
            returned_by_stage[key] += 1
            if row.get("started_at"):
                seconds_by_stage[key] += (datetime.fromisoformat(row["returned_at"].replace("Z", "+00:00")) -
                                         datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))).total_seconds()
    interrupted = [row for row in metadata if row.get("interrupted_at") or row.get("error_type")]
    known_interrupted_seconds = sum(row.get("elapsed_seconds", row.get("elapsed_ms", 0) / 1000) for row in interrupted)
    completed = [row for row in commands if type(row.get("exit_code")) is int]
    native_failures = sum(row["exit_code"] != 0 for row in completed)
    exits, command_seconds = Counter(), Counter()
    for row in completed:
        exits[str(row["exit_code"])] += 1
        command_seconds[str(row["exit_code"])] += row.get("duration_seconds") or 0
    coverage = manifest.get("invocations", {})
    return {"scope": "native_export", **process_metrics(folder, run, models, tools), "stages_observed": sorted(stages),
        "rejections_by_code": dict(rejections), "rejection_scope": "accepted_tool_responses",
        "native_commands": len(commands), "native_finished_commands": len(completed),
        "native_failed_commands": native_failures,
        "native_command_seconds": round(sum(row.get("duration_seconds") or 0 for row in commands), 3),
        "native_command_sources": sorted(command_sources), "retained_sandbox_history_entries": len(history),
        "native_command_scope": "observed_receipts_deduplicated_by_command_id",
        "native_exit_codes": dict(exits),
        "native_seconds_by_exit_code": {k: round(v, 3) for k, v in command_seconds.items()},
        "returned_model_requests_by_role_stage": dict(returned_by_stage),
        "returned_model_seconds_by_role_stage": {k: round(v, 3) for k, v in seconds_by_stage.items()},
        "stage_timing_scope": "saved_invocation_timestamps; unknown stages explicitly unavailable",
        "saved_script_versions": versions, "new_script_artifacts": new_artifacts,
        "changed_script_saves": changed, "unchanged_script_saves": unchanged,
        "planner_dispatches": sum(row.get("name") == "planner" for row in tools),
        "interrupted_model_attempts": len(interrupted), "known_interrupted_model_seconds": round(known_interrupted_seconds, 3),
        "retry_wait_seconds": round(sum(row.get("retry_delay_seconds") or 0 for row in interrupted), 3),
        "unknown_input_token_attempts": coverage.get("model_requests_with_unknown_input_tokens"),
        "all_observed_model_requests_saved": coverage.get("all_observed_model_requests_saved"),
        "all_observed_tool_calls_saved": coverage.get("all_observed_tool_calls_saved"),
        "comparison_note": "Compare matching prompts/model settings and completed stages; shorter early failures are not faster successful builds. Interrupted usage remains unknown."}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def paired_compare(current_folders, baseline_folders):
    """Compare matched repetitions, preserving task flips and unknown verdicts."""
    def index(folders):
        values, excluded = {}, []
        for folder in folders:
            metadata = read_json(folder / "experiment.json") or {}
            manifest = read_json(folder / "manifest.json") or {}
            expected = (manifest.get("sha256") or {}).get("experiment.json")
            if (expected and hashlib.sha256((folder / "experiment.json").read_bytes()).hexdigest() != expected
                    or manifest.get("run_id") not in {None, metadata.get("run_id")}):
                excluded.append({"folder": str(folder), "reason": "Experiment archive hash/Run identity mismatch"})
                continue
            key = (metadata.get("task_id"), metadata.get("repeat"))
            if not key[0] or type(key[1]) is not int or key[1] < 1:
                excluded.append({"folder": str(folder), "reason": "Missing explicit task/repetition identity"})
                continue
            values.setdefault(key, []).append((folder, metadata))
        return values, excluded
    current, ce = index(current_folders)
    baseline, be = index(baseline_folders)
    pairs, excluded = [], [*ce, *be]
    for key in sorted(current.keys() | baseline.keys()):
        a, b = current.get(key, []), baseline.get(key, [])
        if len(a) != 1 or len(b) != 1:
            excluded.append({"task_id": key[0], "repeat": key[1], "reason": "Missing or duplicate matched repetition"})
            continue
        (cf, cm), (bf, bm) = a[0], b[0]
        issues = []
        for metadata in (cm, bm):
            if not metadata.get("provenance_available"):
                issues.append("Run-time source/configuration provenance unavailable")
            provenance = metadata.get("provenance") or {}
            code, model, inputs = (provenance.get(field) or {} for field in ("code", "model", "inputs"))
            if (code.get("complete") is False or not code.get("working_source_sha256")
                    or any(not model.get(field) for field in ("adapter", "model_id", "gateway"))
                    or any(not inputs.get(field) for field in ("prompt_sha256", "selection_sha256"))
                    or not metadata.get("prompt_sha256")):
                issues.append("Run-time source/configuration provenance incomplete")
        if cm.get("prompt_sha256") != bm.get("prompt_sha256"):
            issues.append("Original prompts differ")
        for field in ("model", "inputs"):
            if (cm.get("provenance") or {}).get(field) != (bm.get("provenance") or {}).get(field):
                issues.append(field + " differ")
        config_field = "initial_model_configurations" if "initial_model_configurations" in cm and "initial_model_configurations" in bm else "effective_model_configurations"
        if cm.get(config_field) != bm.get(config_field):
            issues.append("Initial effective model/output/context settings differ")
        if issues:
            excluded.append({"task_id": key[0], "repeat": key[1], "reasons": sorted(set(issues))})
            continue
        c = summarize(cf / "agent logs.txt", cf / "conversation logs.txt")
        b = summarize(bf / "agent logs.txt", bf / "conversation logs.txt")
        if c["run_id"] != cm.get("run_id") or b["run_id"] != bm.get("run_id"):
            excluded.append({"task_id": key[0], "repeat": key[1], "reason": "Experiment and Run identities differ"})
            continue
        ca, ba = cm.get("acceptance", "unknown"), bm.get("acceptance", "unknown")
        flip = f"{ba}_to_{ca}" if ca != "unknown" and ba != "unknown" else "acceptance_unknown"
        def difference(field):
            left, right = c.get(field), b.get(field)
            if field in {"known_input_tokens", "known_output_tokens"} and any(
                    r.get("model_usage_scope") != "run" for r in (c, b)):
                return None
            return left - right if type(left) in {int, float} and type(right) in {int, float} else None
        pairs.append({"task_id": key[0], "repeat": key[1], "flip": flip,
            "current": c, "baseline": b,
            "delta_current_minus_baseline": {f: difference(f) for f in ("duration_seconds", "known_input_tokens", "known_output_tokens")},
            "source_changed": (cm.get("provenance") or {}).get("code", {}).get("working_source_sha256") !=
                              (bm.get("provenance") or {}).get("code", {}).get("working_source_sha256"),
            "settings_changed": (cm.get("provenance") or {}).get("settings") != (bm.get("provenance") or {}).get("settings")})
    flips = Counter(p["flip"] for p in pairs)
    successful = [p for p in pairs if p["flip"] == "passed_to_passed"]
    return {"matched_pairs": len(pairs), "acceptance_flips": dict(flips), "pairs": pairs, "excluded": excluded,
            "success_cost_pairs": len(successful),
            "successful_pair_mean_duration_delta_seconds": (sum(p["delta_current_minus_baseline"]["duration_seconds"]
                for p in successful) / len(successful)) if successful and all(
                p["delta_current_minus_baseline"]["duration_seconds"] is not None for p in successful) else None,
            "note": "Task acceptance is an explicit external verdict. Compare costs among paired successes; interrupted usage is a lower bound, not zero. No provider price is assumed."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-log", type=Path)
    parser.add_argument("--conversation-log", type=Path)
    parser.add_argument("--baseline-agent-log", type=Path)
    parser.add_argument("--baseline-conversation-log", type=Path)
    parser.add_argument("--current-root", type=Path, help="Recursively compare exported task/repetition pairs")
    parser.add_argument("--baseline-root", type=Path)
    options = parser.parse_args()
    if options.current_root or options.baseline_root:
        if not options.current_root or not options.baseline_root or options.agent_log or options.conversation_log:
            parser.error("Provide both comparison roots, separately from individual log paths")
        print(json.dumps(paired_compare([p.parent for p in options.current_root.rglob("experiment.json")],
            [p.parent for p in options.baseline_root.rglob("experiment.json")]), ensure_ascii=False, indent=2))
        return
    if not options.agent_log or not options.conversation_log:
        parser.error("Provide agent and conversation logs, or both comparison roots")
    if bool(options.baseline_agent_log) != bool(options.baseline_conversation_log):
        parser.error("Provide both baseline log paths")
    result = {"current": summarize(options.agent_log, options.conversation_log)}
    if options.baseline_agent_log:
        result["baseline"] = summarize(options.baseline_agent_log, options.baseline_conversation_log)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
