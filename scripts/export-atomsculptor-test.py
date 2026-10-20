"""Archive one local AtomSculptor Run through a read-only SQLite snapshot."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from zoneinfo import ZoneInfo


def rows(connection, sql, args=()):
    return [dict(row) for row in connection.execute(sql, args)]


def objects(path):
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    except ValueError:
        decoder = json.JSONDecoder()
        found = []
        for match in re.finditer(r"^\{", text, re.MULTILINE):
            try:
                value, _ = decoder.raw_decode(text[match.start():])
                found.append(value)
            except ValueError:
                pass
        return found


def render(events):
    lines = []
    for event in events:
        payload = event["payload"]
        lines.append(f"{event['timestamp']} · {event['type']} · {payload.get('role', '')}")
        if isinstance(payload.get("text"), str):
            lines.append(payload["text"])
        else:
            lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
        lines.append("")
    return "\n".join(lines)


def execution_path(models, tools):
    """Readable inventory from native invocations, without inventing execution.

    Calls are ordered by start time: a delegation may enclose its children's
    calls. Returned, rejected and uncertain outcomes remain distinct.
    """
    def cell(value):
        return str(value if value is not None else '未知').replace('|', '\\|').replace('\n', ' ')[:600]

    def payload(value):
        if not isinstance(value, dict):
            return {}
        return value.get('result') if isinstance(value.get('result'), dict) else value

    timeline = []
    for row in models:
        kind = ('请求中断' if row.get('state') == 'interrupted' else
                ('请求重试 · ' if row.get('model_attempt', 1) > 1 else '模型响应 · ') + str(row.get('state')))
        timeline.append((row.get('started_at', ''), '模型请求', row.get('role'), row.get('id'), kind,
            f"request={row.get('model_request')}, attempt={row.get('model_attempt', 1)}; "
            f"input={row.get('input_tokens')}, output={row.get('output_tokens')}, reasoning={row.get('reasoning_tokens')}; "
            f"max_output={row.get('max_output_tokens')}, finish={row.get('finish_reason')}"))
    for index, row in enumerate(tools, 1):
        args = row.get('effective_arguments') or row.get('arguments') or {}
        args = args if isinstance(args, dict) else {}
        response = row.get('response')
        result = payload(response)
        error = result.get('error') or {}
        step = result.get('step') or {}
        step = step if isinstance(step, dict) else {}
        command = payload(result.get('execution')) or result
        details = []
        for key in ('script_path', 'path', 'script', 'question'):
            value = args.get(key)
            if value:
                details.append(f'{key}={value}')
        if not row.get('response_complete', True) or row.get('state') != 'returned':
            kind = '结果不完整/执行状态未知'
        elif isinstance(error, dict) and error.get('input_check'):
            kind = '输入检查拒绝 · 建模未执行'
            guard = payload(error['input_check'])
            details.append(f"guard_command={guard.get('command_id')}, exit={guard.get('exit_code')}")
            if error.get('reused_input_check'):
                details.append('复用检查回执，未重复执行 guard')
        elif isinstance(error, dict) and (error.get('command_dispatched') is False or
                error.get('kind') in {'argument_validation', 'workflow_state', 'step_contract'}):
            kind = '宿主拒绝 · 建模未执行'
        elif step.get('state') == 'finished' or isinstance(command.get('exit_code'), int):
            kind = '实际执行 · ' + ('成功返回' if command.get('exit_code') == 0 else '执行失败')
            details.append(f"command={command.get('command_id')}, exit={command.get('exit_code')}")
        elif step.get('state') in {'running', 'execution_unknown'} or command.get('status') == 'running':
            kind = '已派发 · 尚无完成回执'
        elif row.get('name') in {'planner', 'structure_builder', 'materials_project',
                'request_task_planner', 'request_task_structure_builder'}:
            kind = '委派返回'
        elif error or result.get('ok') is False:
            kind = '工具错误 · 不推断命令完成'
        else:
            kind = '工具返回 · 不代表目标验收通过'
        if error:
            details.append('error=' + str(error.get('code') if isinstance(error, dict) else error))
        if step.get('stage'):
            details.append('stage=' + str(step['stage']))
        timeline.append((row.get('started_at', ''), f'T{index:03d} {row.get("name")}',
                         row.get('role'), row.get('id'), kind, '; '.join(details)))
    lines = ['# Agent 执行路径', '',
        '按调用开始时间排序；委派与内部调用可能重叠。T 编号对应完整 tool calls.json，原始参数与结果通过记录 ID 查阅。',
        '本表由 OAW 原生调用记录整理；无命令回执的工具返回、思考和宿主拒绝均不计作建模执行。', '',
        '| 开始时间 | 调用 | Agent | 结果分类 | 动作与回执 | 原始记录 ID |',
        '| --- | --- | --- | --- | --- | --- |']
    for stamp, name, role, identity, kind, detail in sorted(timeline, key=lambda t: t[0]):
        lines.append('| ' + ' | '.join(cell(v) for v in (stamp, name, role, kind, detail, identity)) + ' |')
    lines += ['', f'工具调用 {len(tools)} 条；模型请求尝试 {len(models)} 条。',
        '输入/输出/思考 token 保留供应商原始口径，不相加。缺失或不完整的历史记录无法推断补全。', '']
    return '\n'.join(lines)


def activity(lifecycle, run_id):
    """Export existing OAW records, explicitly distinguish derived model rows."""
    events = []
    for entry in lifecycle.get("tool_trace", []):
        events.append({"id": entry.get("id"), "type": entry.get("type"),
            "timestamp": entry.get("timestamp", ""), "run_id": run_id,
            "source": "OAW Run lifecycle.tool_trace",
            "payload": {k: v for k, v in entry.items() if k not in {"id", "type", "timestamp"}}})
    for row in lifecycle.get("model_requests", []):
        role = row.get("role", "agent")
        request_id = row.get("model_request")
        for phase in ("started", "returned", "interrupted"):
            stamp = row.get(phase + "_at")
            if not stamp:
                continue
            payload = dict(row)
            if phase == "started":
                text = f"{role} · model request {request_id} started ({row.get('input_bytes', 0)} input bytes)"
            elif phase == "returned":
                text = f"{role} · model request {request_id} returned in {row.get('elapsed_ms', 0) / 1000:.1f}s"
            else:
                text = (f"{role} · model request {request_id}, attempt {row.get('model_attempt', 1)} "
                        f"interrupted ({row.get('error_type', 'unknown')}); "
                        f"origin={row.get('error_origin', 'unknown')}, idle={row.get('idle_seconds')}s")
            events.append({"type": "model_request_" + phase, "timestamp": stamp, "run_id": run_id,
                "source": "OAW Run lifecycle.model_requests", "derived_from_metadata": True,
                "payload": {**payload, "text": text}})
    return sorted(events, key=lambda item: item["timestamp"])


def experiment_metadata(run, prompt, models, tools, *, task_id=None, repeat=None, variant=None, acceptance="unknown"):
    """Version/configuration provenance comes from Run-time native records only."""
    provenances = [row["provenance"] for row in models if isinstance(row.get("provenance"), dict)
                   and row.get("provenance_complete") is not False]
    provenance = provenances[0] if provenances else None
    configurations, initial_configurations = [], {}
    for row in models:
        config = (row.get("request") or {}).get("config") or {}
        settings = {key: config[key] for key in ("temperature", "top_p", "top_k", "seed", "thinking_config") if key in config}
        value = {"role": row.get("role"), "model": row.get("model"), "max_output_tokens": row.get("max_output_tokens"),
                 "context_window": (row.get("context") or {}).get("input_capacity"), "settings": settings}
        if value not in configurations:
            configurations.append(value)
        if row.get("phase") not in {"context_compaction", "context_preflight"}:
            initial_configurations.setdefault(row.get("role"), value)
    scripts, inputs, validators = [], [], []
    for row in tools:
        result = row.get("response")
        if not isinstance(result, dict) or row.get("response_complete") is False:
            continue
        step = result.get("step") or {}
        if isinstance(step, dict):
            script = {k: step[k] for k in ("sandbox", "script_path", "source_version", "plan_revision", "subtask_id") if k in step}
            if script and script not in scripts:
                scripts.append(script)
            for artifact in step.get("input_artifacts", []):
                if artifact not in inputs:
                    inputs.append(artifact)
        def collect_validation(value):
            if not isinstance(value, dict):
                return
            if "validator_version" in value and "passed" in value:
                validators.append({k: value.get(k) for k in ("validator_version", "validation_scope", "distance_convention",
                                                           "passed", "file", "acceptance_thresholds")})
            if isinstance(value.get("stdout"), str):
                try:
                    parsed = json.loads(value["stdout"])
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    collect_validation(parsed)
            for child in value.values():
                if isinstance(child, dict):
                    collect_validation(child)
        collect_validation(result)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    return {"schema_version": 1, "run_id": run["run_id"], "task_id": task_id or prompt_hash,
            "repeat": repeat, "variant": variant, "prompt_sha256": prompt_hash,
            "provenance": provenance, "provenance_available": provenance is not None,
            "effective_model_configurations": sorted(configurations, key=lambda r: json.dumps(r, sort_keys=True)),
            "initial_model_configurations": sorted(initial_configurations.values(), key=lambda r: json.dumps(r, sort_keys=True)),
            "scripts": scripts, "input_artifacts": inputs,
            "validator_receipts": validators, "acceptance": acceptance,
            "acceptance_source": "export_annotation" if acceptance != "unknown" else "unavailable",
            "acceptance_note": "External task acceptance is separate from Run status and geometric validation; never inferred from them."}


def export_run(database, output_root, *, run_id=None, label="difficult problem", manual_source=None,
               task_id=None, repeat=None, variant=None, acceptance="unknown"):
    database = Path(database).resolve()
    output_root = Path(output_root).resolve()
    if acceptance not in {"passed", "failed", "unknown"} or (repeat is not None and (type(repeat) is not int or repeat < 1)):
        raise ValueError("Use acceptance passed/failed/unknown and a positive repeat number")
    if not label or label in {".", ".."} or Path(label).name != label or "\\" in label:
        raise ValueError("label must be a single directory name")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        records = rows(connection, """SELECT * FROM runs WHERE runtime_provider_id='atomsculptor.adk-team'
            AND parent_run_id IS NULL """ + ("AND run_id=? " if run_id else "") +
            "ORDER BY created_at DESC LIMIT 1", (run_id,) if run_id else ())
        if not records:
            raise ValueError("No matching AtomSculptor Run")
        run = records[0]
        run_id = run["run_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError("Run ID cannot safely form an archive directory name")
        if run["status"] not in {"succeeded", "failed", "cancelled", "interrupted"}:
            raise ValueError(f"Latest Run {run_id} is still {run['status']}; wait for it to settle")
        run["lifecycle"] = json.loads(run.pop("lifecycle_json", "{}"))
        events = activity(run["lifecycle"], run_id)
        saved = rows(connection, """SELECT v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id
            WHERE s.scope_kind='run' AND s.owner_id=? AND v.key='model_reasoning' AND v.deleted=0""", (run_id,))
        thinking = sorted(json.loads(saved[0]["value_json"]).values(), key=lambda row: row["created_at"]) if saved else []
        def invocation_records(key):
            saved_rows = rows(connection, """SELECT v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id
                WHERE s.scope_kind='run' AND s.owner_id=? AND v.key=? AND v.deleted=0""", (run_id, key))
            return sorted(json.loads(saved_rows[0]["value_json"]).values(),
                          key=lambda row: (row["started_at"], row["id"])) if saved_rows else []
        model_logs = invocation_records("model_request_logs")
        tool_logs = invocation_records("tool_invocation_logs")
        for row in thinking:
            events.append({"id": row["provider_message_id"], "type": "agent_progress", "run_id": run_id,
                "timestamp": row["created_at"], "source": "OAW Run StateStore.model_reasoning", "payload": row})
        events.sort(key=lambda item: item["timestamp"])
        messages = rows(connection, """SELECT * FROM conversation_messages WHERE run_id=? OR id IN
            (SELECT message_id FROM conversation_deliveries WHERE claimed_run_id=?) ORDER BY sequence""", (run_id, run_id))
        prompts = [message["content"] for message in messages if message["sender_kind"] == "user"]
        inputs = rows(connection, """SELECT v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id
            WHERE s.scope_kind='run' AND s.owner_id=? AND v.key='input' AND v.deleted=0""", (run_id,))
        runtime_input = json.loads(inputs[0]["value_json"]) if inputs else {}
        # Older OAW Runs stored RuntimeInput as the prompt string itself.
        # Preserve it verbatim; these historical records need no migration.
        if isinstance(runtime_input, str):
            input_prompt = runtime_input
        elif isinstance(runtime_input, dict):
            input_prompt = runtime_input.get("task_prompt") or runtime_input.get("prompt") or ""
        else:
            input_prompt = ""
        prompt = "\n\n".join(prompts) or input_prompt
        execution_state = rows(connection, """SELECT v.value_json FROM state_values v JOIN state_scopes s ON s.scope_id=v.scope_id
            WHERE s.scope_kind='run' AND s.owner_id=? AND v.key='execution_checkpoint' AND v.deleted=0""", (run_id,))
        execution_checkpoint = json.loads(execution_state[0]["value_json"]) if execution_state else None
        checkpoints = rows(connection, "SELECT * FROM run_model_checkpoints WHERE run_id=? ORDER BY role", (run_id,))
        for checkpoint in checkpoints:
            checkpoint["contents"] = json.loads(checkpoint.pop("contents_json") or "null")
        receipts = rows(connection, "SELECT * FROM run_tool_receipts WHERE run_id=? ORDER BY started_at", (run_id,))
        sandbox = []
        for setting in rows(connection, "SELECT key,value_json FROM application_settings WHERE key LIKE 'sandbox_history:%'"):
            for entry in json.loads(setting["value_json"]):
                if isinstance(entry, dict) and entry.get("run_id") == run_id:
                    sandbox.append({"sandbox_id": setting["key"].split(":")[1], **entry})
    manual = {}
    if manual_source is not None:
        manual_source = Path(manual_source)
        agent_file = manual_source / "agent logs.txt"
        exported = [item["run"] for item in objects(agent_file)
                    if isinstance(item, dict) and isinstance(item.get("run"), dict)]
        if not exported or exported[-1].get("run_id") != run_id:
            raise ValueError("Manual source does not match the selected Run; no files were copied")
        names = {"agent logs.txt": "manual agent logs.txt", "prompt.txt": "manual prompt.txt",
                 "sandbox logs.txt": "manual sandbox logs.txt", "conversation logs.txt": "manual conversation logs.txt",
                 "conversaion logs.txt": "manual conversation logs.txt"}
        manual = {name: manual_source / source for source, name in names.items() if (manual_source / source).is_file()}
    stamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Shanghai"))
    folder = output_root / label / (stamp.strftime("%Y-%m-%d_%H%M%S_") + run_id)
    if folder.exists():
        manifest = folder / "manifest.json"
        if manifest.is_file() and json.loads(manifest.read_text())["run_id"] == run_id:
            return folder
        raise ValueError("An incomplete archive already exists; use a separate output root instead of overwriting it")
    folder.mkdir(parents=True)
    def write_json(name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_json("run.json", run)
    write_json("events.json", events)
    write_json("agent logs.txt", [{"run": run}, *[{"event": e} for e in events]])
    write_json("conversation messages.json", messages)
    write_json("model checkpoints.json", checkpoints)
    if execution_checkpoint is not None:
        write_json("execution checkpoint.json", execution_checkpoint)
    write_json("model thinking.json", thinking)
    write_json("model requests.json", model_logs)
    write_json("tool calls.json", tool_logs)
    (folder / "execution path.md").write_text(execution_path(model_logs, tool_logs), encoding="utf-8")
    write_json("tool receipts.json", receipts)
    write_json("experiment.json", experiment_metadata(run, prompt, model_logs, tool_logs,
        task_id=task_id, repeat=repeat, variant=variant, acceptance=acceptance))
    write_json("sandbox logs.json", sandbox)
    (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
    (folder / "conversation logs.txt").write_text(
        "Exported from native OAW Run metadata and saved provider-exposed thinking.\n"
        "Missing historical thinking and unexposed provider thoughts cannot be reconstructed.\n\n"
        "Full redacted ADK requests/results: model requests.json; tool invocations: tool calls.json.\n"
        "The timeline below retains native public previews and their original bounds.\n\n"
        + render(events), encoding="utf-8")
    (folder / "sandbox logs.txt").write_text(json.dumps(sandbox, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, source in manual.items():
        shutil.copyfile(source, folder / name)
    requests = run["lifecycle"].get("model_requests", [])
    def identity(row):
        return (row.get("role"), row.get("model_request"), row.get("run_attempt", 0), row.get("model_attempt", 1))
    by_request = {identity(row): row for row in thinking}
    exposed = [row for row in requests if row.get("reasoning_available") is True]
    missing = [row for row in exposed if identity(row) not in by_request]
    complete = bool(requests) and not run["lifecycle"].get("model_requests_truncated") and not missing \
        and all("reasoning_available" in row for row in requests) \
        and all(not row.get("streaming") and not row.get("interrupted") and not row.get("truncated")
                and not row.get("suffix_withheld") for row in thinking)
    archived_requests = {identity(row): row for row in model_logs}
    missing_requests = [identity(row) for row in requests if identity(row) not in archived_requests]
    traced_calls = {row.get("call_id") for row in run["lifecycle"].get("tool_trace", []) if row.get("call_id")}
    missing_tools = sorted(traced_calls - {row["call_id"] for row in tool_logs})
    invocation_coverage = {
        "model_boundary_scope": "ADK application request/response; not raw provider HTTP",
        "tool_boundary_scope": "ADK accepted function exchanges and tool callbacks; native capability receipts remain separate",
        "metadata_comparison_complete": not bool(run["lifecycle"].get("model_requests_truncated")),
        "trace_comparison_complete": not bool(run["lifecycle"].get("tool_trace_truncated")),
        "saved_model_requests": len(model_logs), "saved_tool_calls": len(tool_logs),
        "missing_model_requests_in_metadata": missing_requests, "missing_tool_calls_in_trace": missing_tools,
        "model_requests_with_unknown_input_tokens": [row["id"] for row in model_logs if row.get("input_tokens") is None],
        "interrupted_model_requests": [row["id"] for row in model_logs if row["state"] == "interrupted"],
        "unfinished_tool_calls": [row["id"] for row in tool_logs if row["state"] != "returned"],
        "all_observed_model_requests_saved": bool(model_logs) and not missing_requests,
        "all_observed_tool_calls_saved": bool(tool_logs) and not missing_tools,
        "complete_model_request_payloads": bool(model_logs) and not missing_requests and
            all(row.get("request_complete") and not row.get("truncated") for row in model_logs),
        "complete_model_responses": bool(model_logs) and not missing_requests and
            all(row.get("response_complete") and row["state"] == "returned" for row in model_logs),
        "complete_tool_payloads": bool(tool_logs) and not missing_tools and all(
            row.get("arguments_complete") and row.get("effective_arguments_complete") and row.get("response_complete")
            and row["state"] == "returned" and not row.get("truncated") for row in tool_logs),
    }
    manifest = {"run_id": run_id, "status": run["status"], "source_database": str(database),
        "read_only_snapshot": True, "activity_source": "existing OAW Run lifecycle", "activity_records": len(events),
        "complete_model_thinking_available": complete,
        "thinking_scope": "provider-exposed text received by OAW only",
        "saved_thinking_requests": len(thinking), "missing_exposed_thinking_requests": len(missing),
        "thinking_coverage_known": bool(requests) and all("reasoning_available" in row for row in requests),
        "tool_trace_truncated": bool(run["lifecycle"].get("tool_trace_truncated")),
        "model_requests_truncated": bool(run["lifecycle"].get("model_requests_truncated")),
        "invocations": invocation_coverage,
        "manual_logs": sorted(manual), "sandbox_receipts": len(sandbox),
        "limits": ["Only exposed, received and committed thinking is available; historical gaps cannot be recovered.",
                   "Model timeline rows are derived from native metadata, not invented raw events.",
                   "Public tool events may be redacted/truncated; flags are preserved.",
                   "Invocation archives retain redacted ADK boundary payloads, not transport-internal transformations or unavailable usage.",
                   "Model checkpoints contain only the latest request per role; Sandbox history has bounded retention."],
        "sha256": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(folder.iterdir())}}
    write_json("manifest.json", manifest)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--label", default="difficult problem")
    parser.add_argument("--run-id")
    parser.add_argument("--manual-source", type=Path)
    parser.add_argument("--task-id", help="Stable task ID shared by baseline and candidate")
    parser.add_argument("--repeat", type=int, help="Matching repetition number for paired comparison")
    parser.add_argument("--variant", help="Baseline/candidate implementation label")
    parser.add_argument("--acceptance", choices=("passed", "failed", "unknown"), default="unknown",
                        help="Explicit external task verdict, independent of Run status")
    options = parser.parse_args()
    print(export_run(options.database, options.output_root, run_id=options.run_id,
                     label=options.label, manual_source=options.manual_source, task_id=options.task_id,
                     repeat=options.repeat, variant=options.variant, acceptance=options.acceptance))


if __name__ == "__main__":
    main()
