"""Readable per-operation facts and explicitly attributed review annotations."""
from html import escape
import json

from oaw_tool_activity import public_tool_payload
from review_plan import ReviewValidationError

LABELS = {
    "inspect_sandbox": "检查 Sandbox 与命令历史", "list_sandbox_files": "列出工作区文件",
    "read_sandbox_file": "读取文件", "read_modelling_source": "读取已保存源码",
    "read_tool_result": "读取历史工具结果", "read_skills": "阅读 Skill 指引或契约",
    "run_modelling_step": "保存并执行建模脚本", "rewrite_modelling_step": "重写并执行脚本",
    "resume_modelling_step": "恢复并执行建模步骤",
    "patch_modelling_step": "修改并执行脚本", "rerun_modelling_step": "再次执行已保存脚本",
    "execute_command": "执行命令", "advance_modelling_stage": "记录评估或推进阶段",
    "validate_modelling_artifact": "独立验证结构", "run_delivery_step": "转换、导入并回读",
    "planner": "委派规划", "structure_builder": "委派建模", "finish_task": "提交当前结果",
}
BASIS = {"stated": "记录明确表述", "inferred": "审阅者推断", "unknown": "日志未明确"}


def short(value, length=240):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= length else text[:length] + "…（节选，原文另存）"


def native_summary(tool):
    """Describe what was requested/returned, never invent a reason for doing it."""
    args = tool.get("effective_arguments") or tool.get("arguments") or {}
    args = args if isinstance(args, dict) else {}
    response = tool.get("response")
    result = response if isinstance(response, dict) else {}
    if isinstance(result.get("result"), dict):
        result = result["result"]
    name = tool.get("name", "tool")
    label = LABELS.get(name, "调用工具 " + name)
    path = args.get("path") or args.get("script_path") or args.get("structure_path")
    if name == "read_sandbox_file" and isinstance(path, str) and path.lower().endswith((".py", ".js", ".ts", ".sh")):
        label = "读取源代码文件"
    fields = {k: args[k] for k in ("sandbox", "path", "script_path", "structure_path", "target", "skill",
        "skill_id", "file_path", "invocation_id", "section", "pointer", "offset", "limit", "start_line",
        "end_line", "query", "symbol", "history_scope", "history_limit", "include_output", "stage", "argv") if k in args}
    source = args.get("source")
    if isinstance(source, str):
        fields["source_characters"] = len(source)
    if isinstance(args.get("edits"), list):
        fields["edit_count"] = len(args["edits"])
    safe = public_tool_payload({"name": name, "arguments": fields, "response": response})
    facts = []
    flags = []
    if tool.get("state") != "returned" or tool.get("response_complete") is False:
        facts.append("工具未完整返回；结果或部分副作用仍未知")
    if result.get("error"):
        error = public_tool_payload({"name": name, "error": result["error"]})["error"]
        facts.append("返回错误：" + short(error))
        flags.append("请求被拒绝/报错")
    if result.get("ok") is False:
        facts.append("工具报告未成功；不能据请求参数声称已读到内容或完成修改")
        if not flags:
            flags.append("未成功")
    execution = result.get("execution")
    execution = execution if isinstance(execution, dict) else result
    if execution.get("exit_code") is not None:
        facts.append("命令退出码：" + str(execution["exit_code"]))
        if execution["exit_code"] != 0:
            flags.append("命令失败")
    for field, label_text in (("constraint_failures", "自报检查未过"), ("measurement_issues", "测量格式问题")):
        if isinstance(result.get(field), list) and result[field]:
            facts.append(f"{label_text}：{len(result[field])}项")
            flags.append(label_text)
    for field, label_text in (("recent_commands", "返回的历史命令"), ("entries", "返回的目录项")):
        if isinstance(result.get(field), list):
            facts.append(f"{label_text}：{len(result[field])}条")
    if "next_offset" in result:
        facts.append("后续分页offset：" + str(result["next_offset"]))
    returned_range = {k: result[k] for k in ("offset", "total_chars", "start_line", "end_line", "total_lines", "version") if k in result}
    if returned_range:
        facts.append("返回范围/版本：" + short(returned_range))
    if name == "inspect_sandbox":
        runtime = {k: result[k] for k in ("state", "commands_accepted", "history_scope") if k in result}
        if runtime:
            facts.append("运行状态/历史范围：" + short(runtime))
    # A denied read is not a successful observation, even if it echoed arguments.
    if not result.get("error") and result.get("ok") is not False:
        texts = [(field, result[field]) for field in ("text", "content", "stdout") if isinstance(result.get(field), str)]
        if not texts and isinstance(result.get("file"), dict) and isinstance(result["file"].get("content"), str):
            texts = [("file.content", result["file"]["content"])]
        for field, value in texts[:1]:
            cleaned = public_tool_payload({"name": name, "response": {field: value}})["response"][field]
            facts.append(f"{field}返回节选：" + short(cleaned))
    if not facts:
        facts.append("原生状态：" + str(tool.get("state", "unknown")))
    stated = next((args[k] for k in ("reason", "question", "rewrite_reason") if isinstance(args.get(k), str) and args[k].strip()), None)
    return {"action": label, "target_label": short(path or args.get("file_path") or args.get("invocation_id") or "", 110),
        "result_status": " / ".join([str(tool.get("state", "unknown")), *flags]),
        "requested": safe.get("arguments", {}), "observed": facts,
        "stated_purpose": short(stated) if stated else None,
        "native_preview": public_tool_payload({"name": name, "arguments": args, "response": response})}


def operations(attempts, annotations=None):
    known = {}
    for a in attempts:
        run_id = a["run"]["run_id"]
        for kind, rows in (("tool", a["tools"]), ("model", a["models"]), ("command", a["commands"])):
            for row in rows:
                if isinstance(row.get("id"), str):
                    known[(run_id, kind, row["id"])] = row
    notes = {}
    if annotations is not None and not isinstance(annotations, list):
        raise ValueError("operations must be an array of review annotations")
    evidence_issues = []
    for index, raw in enumerate(annotations or []):
        pointer = f"/operations/{index}"
        if not isinstance(raw, dict):
            raise ReviewValidationError("Each operation annotation must be an object", pointer)
        key = (raw.get("run_id"), "tool", raw.get("tool_id"))
        if key not in known or key in notes:
            raise ReviewValidationError("Operation annotations need a unique existing Run/tool identity", pointer,
                run_id=raw.get("run_id"), tool_id=raw.get("tool_id"))
        if raw.get("basis", "unknown") not in BASIS:
            raise ReviewValidationError("Annotation basis must be stated, inferred or unknown", pointer + '/basis')
        for field in ("purpose", "finding", "follow_up"):
            if field in raw and not isinstance(raw[field], str):
                raise ReviewValidationError("Operation explanation fields must be text", pointer + '/' + field)
        refs = raw.get("evidence", [])
        if not isinstance(refs, list):
            raise ReviewValidationError("Operation evidence must be an array", pointer + '/evidence')
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict) or (ref.get("run_id"), ref.get("kind", "tool"), ref.get("id")) not in known:
                evidence_issues.append({'path': pointer + f'/evidence/{ref_index}',
                    'message': 'Operation evidence references an unavailable native record', 'reference': ref,
                    'tool_id': raw.get('tool_id'), 'available_command_ids':
                        [identity for run, kind, identity in known if run == raw.get('run_id') and kind == 'command']})
        if raw.get("basis") == "stated" and not refs:
            raise ReviewValidationError("A stated explanation needs its native evidence reference", pointer + '/evidence')
        notes[key] = {k: raw[k] for k in ("purpose", "finding", "follow_up", "basis", "evidence") if k in raw}
    if evidence_issues:
        failure = ReviewValidationError('Operation evidence references an unavailable native record', evidence_issues[0]['path'])
        failure.issues = evidence_issues
        raise failure
    rows = []
    for number, attempt in enumerate(attempts, 1):
        run_id = attempt["run"]["run_id"]
        for index, tool in enumerate(attempt["tools"], 1):
            row = {"short_id": f"A{number}:T{index:03d}", "run_id": run_id, "tool_id": tool["id"],
                "name": tool["name"], "role": tool.get("role"), "state": tool.get("state"),
                "started_at": tool.get("started_at"), "returned_at": tool.get("returned_at") or tool.get("interrupted_at"),
                **native_summary(tool), "annotation": notes.get((run_id, "tool", tool["id"]), {})}
            args = tool.get("effective_arguments") or tool.get("arguments") or {}
            if tool.get("name") == "read_tool_result" and isinstance(args, dict):
                target = known.get((run_id, "tool", args.get("invocation_id")))
                if target:
                    row["target_label"] = short(target.get("name", "tool") + " " + str(args.get("pointer", "")), 110)
            if index < len(attempt["tools"]):
                following = attempt["tools"][index]
                row["next_record"] = {"short_id": f"A{number}:T{index+1:03d}", "tool_id": following["id"], "name": following["name"]}
            rows.append(row)
    return rows


def explanation_complete(row):
    note = row["annotation"]
    return all(isinstance(note.get(key), str) and note[key].strip() for key in ("purpose", "finding", "follow_up"))


def annotation_html(row):
    note = row["annotation"]
    text = []
    if row.get("stated_purpose"):
        text.append('<p><b>工具参数中陈述的目的：</b>' + escape(row["stated_purpose"]) + '</p>')
    for field, label in (("purpose", "目的"), ("finding", "获得的信息"), ("follow_up", "后续用途或动作")):
        if note.get(field):
            text.append(f'<p><b>{label}：</b>{escape(note[field])}</p>')
    if note:
        text.append('<p class="status">解读依据：' + BASIS[note.get("basis", "unknown")] + '；说明为Report Agent标注，须与证据区分。</p>')
    else:
        text.append('<p class="status">尚无逐步解读；原生参数和回执不能单独证明内部动机。</p>')
    if note and not explanation_complete(row):
        text.append('<p class="status">逐步解读尚不完整；空白草稿字段不代表已完成解释。</p>')
    return ''.join(text)


def operation_html(row, raw_prefix):
    slug = row["short_id"].replace(":", "-")
    body = '<details id="op-' + slug + '"><summary>' + escape(row["short_id"] + ' · ' + row["action"] + ' · ' + row["target_label"] + ' · ' + row["result_status"]) + '</summary>'
    body += '<p>' + escape(str(row.get("started_at")) + ' → ' + str(row.get("returned_at")) + ' · ' + str(row.get("role"))) + '</p>'
    body += '<p><b>实际对象与范围：</b>' + escape(short(row["requested"], 600)) + '</p><ul>'
    body += ''.join('<li>' + escape(fact) + '</li>' for fact in row["observed"]) + '</ul>' + annotation_html(row)
    if row.get("next_record"):
        next_row = row["next_record"]
        body += '<p>下一条原生记录（时间顺序，不代表因果）：<a href="#op-' + next_row["short_id"].replace(":", "-") + '">' + escape(next_row["short_id"] + ' · ' + next_row["name"]) + '</a></p>'
    evidence = {"run_id": row["run_id"], "tool_id": row["tool_id"], "review_evidence": row["annotation"].get("evidence", []), "preview": row["native_preview"]}
    body += '<details><summary>参数、回执预览及原始身份</summary><pre>' + escape(json.dumps(evidence, ensure_ascii=False, indent=2)) + '</pre><p>展示预览可能截断；完整内容见 <a href="' + raw_prefix + '/tool%20calls.json">原始工具记录</a>。</p></details></details>'
    return body


def operations_markdown(rows):
    lines = ['# 逐步操作说明', '', '原生事实与Report Agent的解释分开列出。读取历史、源码、结果与实际执行均保留；相邻记录不自动建立因果。', '']
    for row in rows:
        lines += ['## ' + row['short_id'] + ' · ' + row['action'], '',
            '**对象/范围：** ' + escape(short(row['requested'], 600)), '',
            '**实际反馈：** ' + escape('；'.join(row['observed'])), '']
        if row.get('stated_purpose'):
            lines += ['**参数中陈述的目的：** ' + escape(row['stated_purpose']), '']
        note = row['annotation']
        for field, label in (('purpose', '目的'), ('finding', '获得的信息'), ('follow_up', '后续用途或动作')):
            if note.get(field):
                lines += ['**' + label + '：** ' + escape(note[field]), '']
        lines += ['**解释依据：** ' + BASIS[note.get('basis', 'unknown')], '']
        if row.get('next_record'):
            lines += ['下一条原生记录（不代表因果）：' + row['next_record']['short_id'] + ' · ' + row['next_record']['name'], '']
        lines += ['完整身份：`' + row['run_id'] + '` / `' + row['tool_id'] + '`。', '']
    return '\n'.join(lines) + '\n'
