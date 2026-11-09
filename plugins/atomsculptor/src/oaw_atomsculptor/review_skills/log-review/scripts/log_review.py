"""Read native exports; render saved data, never execute archived programs."""
from collections import Counter, defaultdict
from datetime import datetime
from graphlib import TopologicalSorter
from html import escape
import csv
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote
from zipfile import ZipFile, ZIP_DEFLATED

from oaw_runtime import build_cli_parser, run_cli, resolve_output_path, display_path
from operation_details import operations, operation_html, annotation_html, operations_markdown, explanation_complete
from oaw_run_evidence import command_ids, resolve_commands
from review_plan import ReviewValidationError, aliases, compile_notes
from report_presentation import flowchart, story_html, compact_story, decision_text, operation_text, wrapped

NATIVE_FILES = ("run.json", "tool calls.json", "model requests.json", "model thinking.json",
    "sandbox logs.json", "tool receipts.json", "events.json", "manifest.json", "prompt.txt",
    "conversation messages.json", "conversation logs.txt", "agent logs.txt", "sandbox logs.txt",
    "execution path.md", "execution checkpoint.json", "model checkpoints.json", "experiment.json",
    "command evidence.json", "record catalog.json", "timeline.jsonl", "run activity.json")
FORMATS = {".extxyz", ".xyz", ".cif", ".vasp", ".poscar", ".pdb", ".traj", ".json"}


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Expected an archive-relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Reference escapes the selected archive")
    return path


def load_archive(folder):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Select an exported task directory")
    task = read_json(inside(root, "task.json"))
    if task is not None:
        directories = [inside(root, a["directory"]) for a in task["attempts"]]
    elif (root / "run.json").is_file():
        directories = [root]
    else:
        raise ValueError("Expected task.json with explicit attempts or a single exported run.json")
    attempts = []
    for directory in directories:
        # Every native input obeys the same folder boundary, including links.
        for name in NATIVE_FILES:
            inside(root, str((directory / name).relative_to(root)))
        run = read_json(directory / "run.json")
        if not isinstance(run, dict) or not run.get("run_id"):
            raise ValueError("Attempt is missing its native Run identity")
        tools = read_json(directory / "tool calls.json", [])
        raw_commands = read_json(directory / "sandbox logs.json", [])
        commands, gaps = resolve_commands(raw_commands, tools)
        attempts.append({"directory": directory, "run": run,
            "record_availability": {name: (directory / name).is_file() for name in
                ("tool calls.json", "model requests.json", "sandbox logs.json")},
            "tools": tools, "command_gaps": gaps,
            "activity": read_json(directory / 'run activity.json', []),
            "models": read_json(directory / "model requests.json", []),
            "commands": commands})
    if len({a["run"]["run_id"] for a in attempts}) != len(attempts):
        raise ValueError("Duplicate Run in task attempts")
    return root, attempts


def elapsed(row):
    end = row.get("returned_at") or row.get("interrupted_at") or row.get("finished_at")
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(row["started_at"])).total_seconds()
    except (ValueError, TypeError, KeyError):
        return None


def statistics(attempts):
    models = [m for a in attempts for m in a["models"]]
    commands = [c for a in attempts for c in a["commands"]]
    return {"attempts": len(attempts), "run_seconds": sum(elapsed(a["run"]) or 0 for a in attempts),
        "model_attempts_including_compaction": len(models), "tool_calls": sum(len(a["tools"]) for a in attempts),
        "run_duration_unknown_attempts": sum(elapsed(a["run"]) is None for a in attempts),
        "commands": len(commands), "command_seconds": sum(c.get("duration_seconds") or 0 for c in commands),
        "command_duration_unknown": sum(c.get("duration_seconds") is None for c in commands),
        "interrupted_requests": sum(m.get("state") == "interrupted" for m in models),
        "known_input_tokens": sum(m.get("input_tokens") or 0 for m in models),
        "input_usage_unknown_attempts": sum(m.get("input_tokens") is None for m in models),
        "command_evidence_gaps": [{"run_id": a["run"]["run_id"], **gap} for a in attempts for gap in a.get("command_gaps", [])],
        "record_gaps": [{"run_id": a["run"]["run_id"], "missing": name} for a in attempts
            for name, present in a["record_availability"].items() if not present]}


def destination(name, root):
    # The shared resolver creates parent directories. Check non-mutation of
    # source archives before calling it, including for nested output paths.
    candidate = (Path.cwd() / name).resolve()
    if candidate.is_relative_to(root) or root.is_relative_to(candidate):
        raise ValueError("Write outputs outside the source archive")
    return resolve_output_path(name)


def is_structure_file(path):
    if path.suffix.lower() not in FORMATS:
        return path.name.upper() in {"POSCAR", "CONTCAR"}
    if path.suffix.lower() == ".json":
        if path.name in NATIVE_FILES:
            return False
        try:
            value = read_json(path)
        except (ValueError, OSError):
            return False
        return isinstance(value, dict) and isinstance(value.get("atoms"), list)
    return True


def inspect_archive(folder: str, output_file: str = "review-input.json", manifest_file: str = "review-manifest.json") -> dict:
    """Index key structure operations and exact model-call links; no manifest editing needed."""
    root, attempts = load_archive(folder)
    output = destination(output_file, root)
    draft_path = destination(manifest_file, root)
    if output == draft_path or output.exists() or draft_path.exists():
        raise ValueError("Use fresh, distinct inspection and manifest output files")
    records = [{"run": {k: a["run"].get(k) for k in ("run_id", "status", "started_at", "finished_at", "error", "task_id")},
        "records_folder": str(a["directory"].relative_to(root)) or ".",
        "tools": [{"id": t["id"], "name": t["name"],
        "state": t.get("state"), "command_ids": sorted(command_ids(t.get("response"))),
        "error": (t.get("response") or {}).get("error") if isinstance(t.get("response"), dict) else None}
        for t in a["tools"]], "commands": [{k: c.get(k) for k in
        ("id", "exit_code", "started_at", "finished_at", "duration_seconds")} for c in a["commands"]]}
        for a in attempts]
    candidates = sorted({str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.resolve().is_relative_to(root) and is_structure_file(p)})
    operation_rows = operations(attempts)
    compact = [{k: row[k] for k in ("short_id", "run_id", "tool_id", "action", "target_label", "result_status",
        "requested", "observed", "stated_purpose")} for row in operation_rows]
    collection = read_json(inside(root, "collection.json"))
    value = {"archive": display_path(root), "statistics": statistics(attempts), "attempts": records,
        "candidate_structure_paths": candidates, "operations": compact,
        "collection_summary": ({"files": len(collection.get("files", [])), "warnings": collection.get("warnings", [])} if collection else None),
        "note": "Use modification_index to summarize key structure operations, then build_review with optional steps. Routine reads remain in operations; collection snapshots do not prove earlier versions."}
    # One native planner serves inspection and rendering. Routine reads stay in
    # the evidence index instead of becoming a second graph/manifest to rewrite.
    draft = compile_notes(root, attempts, operation_rows,
        {'format': 'oaw.review-notes.v1', 'steps': {}})
    by_tool = {(a['run']['run_id'], t['id']): t for a in attempts for t in a['tools']}
    identities = aliases(attempts)
    modification_index = []
    for node in draft['nodes']:
        tool = by_tool.get((node['run_id'], node.get('tool_id')), {})
        related_models = []
        for step, ref in identities.items():
            if ref['run_id'] != node['run_id'] or ref['kind'] != 'model' or not tool.get('call_id'):
                continue
            model = next(m for a in attempts if a['run']['run_id'] == ref['run_id'] for m in a['models'] if m['id'] == ref['id'])
            if (model.get('state') != 'returned' or model.get('role') != tool.get('role')
                    or model.get('run_attempt') != tool.get('run_attempt')):
                continue
            # Link only the response that actually issued this call, not an
            # adjacent request or a request replaying earlier calls in history.
            if any((part.get('function_call') or {}).get('id') == tool['call_id']
                   for response in (model.get('response') or []) if isinstance(response, dict)
                   for part in (response.get('content') or {}).get('parts', []) if isinstance(part, dict)):
                related_models.append(step)
        modification_index.append({k: node[k] for k in ('step', 'title', 'outcome', 'structures') if k in node}
            | {'model_steps': related_models})
    value['modification_index'] = modification_index
    write_json(output, value)
    write_json(draft_path, draft)
    return {"inspection_file": display_path(output), "manifest_file": display_path(draft_path), "statistics": value["statistics"],
        "runs": [a["run"]["run_id"] for a in attempts], "candidate_structure_paths": candidates,
        "collection_summary": ({"files": len(collection.get("files", [])), "warnings": len(collection.get("warnings", [])), "note": "Read collection.json for complete provenance and warnings."} if collection is not None else None),
        "operation_index": [{k: row[k] for k in ("short_id", "run_id", "tool_id", "action", "target_label", "result_status")}
                            for row in compact[:20]], "operation_count": len(compact),
        "modification_index": modification_index[:20], "modification_count": len(modification_index),
        "modification_index_complete": len(modification_index) <= 20,
        "index_preview_complete": len(compact) <= 20,
        "next": "Use modification_index for a short decision summary of key steps; read linked model_steps only as needed. Call build_review with folder, output_folder and optional steps. No manifest or explanation of every read is required. Missing versions stay missing."}


def validate_manifest(root, attempts, manifest):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("nodes"), list):
        raise ValueError("Manifest requires a nodes array")
    by_run = {a["run"]["run_id"]: a for a in attempts}
    nodes, graph = [], {}
    for index, raw in enumerate(manifest["nodes"]):
        pointer = f"/nodes/{index}"
        node = dict(raw)
        identity = node.get("id", "")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", identity) or identity in graph:
            raise ValueError("Node IDs must be unique portable identifiers")
        if not isinstance(node.get("title"), str) or not node["title"].strip():
            raise ValueError("Each node needs a title")
        attempt = by_run.get(node.get("run_id"))
        if attempt is None:
            raise ValueError("Node Run is not in the selected archive")
        tool = next((t for t in attempt["tools"] if node.get("tool_id") and t.get("id") == node["tool_id"]), None)
        command = next((c for c in attempt["commands"] if node.get("command_id") and c.get("id") == node["command_id"]), None)
        if node.get("tool_id") and tool is None:
            raise ReviewValidationError("Node tool ID does not belong to its Run", pointer + "/tool_id", node=identity, tool_id=node.get("tool_id"))
        if not tool and not command:
            raise ValueError("Node needs an existing native tool or command")
        if node.get("command_id"):
            if tool and node["command_id"] not in command_ids(tool.get("response")):
                raise ReviewValidationError("Node command is not recorded by the selected tool", pointer + "/command_id",
                    node=identity, command_id=node["command_id"], tool_id=node.get("tool_id"),
                    recorded_command_ids=sorted(command_ids(tool.get("response"))))
            if command is None and not tool:
                raise ReviewValidationError("Unknown native command ID", pointer + "/command_id", node=identity, command_id=node["command_id"])
        response = tool.get("response") if tool else {}
        response = response if isinstance(response, dict) else {}
        node["native_status"] = (f"exit {command['exit_code']}" if command and command.get("exit_code") is not None
            else tool.get("state", "unknown") if tool else "unknown")
        if response.get("error"):
            node["native_status"] += " / rejected"
        if response.get("constraint_failures"):
            node["native_status"] += " / self-check failed"
        parents = node.get("parents", [])
        if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
            raise ValueError("parents must be node IDs")
        graph[identity] = parents
        structures = node.get("structures", [])
        if not isinstance(structures, list):
            raise ValueError("structures must be an array")
        for item in structures:
            if item.get("provenance") not in {"historical", "collection_snapshot", "missing"}:
                raise ValueError("Structure provenance must be explicit")
            if item.get("relation", "output") not in {"input", "output", "unchanged"}:
                raise ValueError("Structure relation must be input, output or unchanged")
            if item["provenance"] == "missing":
                if item.get("path"):
                    raise ValueError("Missing versions cannot substitute a present file")
            else:
                path = inside(root, item.get("path"))
                if path.suffix.lower() not in FORMATS and path.name.upper() not in {"POSCAR", "CONTCAR"}:
                    raise ValueError("Structure previews require a supported data format; archived programs are not inputs")
        # The inspection and build paths must agree on file classification.
        # A statistics JSON is still useful evidence, but has no atom preview.
        attachments = node.get("attachments", [])
        if not isinstance(attachments, list):
            raise ValueError("attachments must be an array")
        node["structures"] = []
        node["attachments"] = list(attachments)
        for item in structures:
            source = inside(root, item["path"]) if item.get("path") else None
            if source and source.is_file() and not is_structure_file(source):
                node["attachments"].append({**item, "file_kind": "data_attachment"})
            else:
                node["structures"].append(item)
        for item in node["attachments"]:
            if not isinstance(item, dict) or item.get("provenance") not in {"historical", "collection_snapshot", "missing"}:
                raise ValueError("Attachment provenance must be explicit")
            if item["provenance"] == "missing":
                if item.get("path"):
                    raise ValueError("Missing versions cannot substitute a present file")
            else:
                inside(root, item.get("path"))
        nodes.append(node)
    if any(parent not in graph for parents in graph.values() for parent in parents):
        raise ValueError("Unknown parent node")
    order = tuple(TopologicalSorter(graph).static_order())  # Reject cycles before writing outputs.
    return sorted(nodes, key=lambda n: order.index(n["id"]))


def structure_story(nodes, operation_rows):
    """Saved structures alternate with short, evidence-backed action summaries."""
    by_tool = {(row['run_id'], row['tool_id']): row for row in operation_rows}
    entries, seen = [], set()
    used_ids = {node['id'] for node in nodes}
    for node in nodes:
        row = by_tool.get((node['run_id'], node.get('tool_id')), {})
        note = row.get('annotation') or node.get('annotation', {})
        identity = {key: node[key] for key in ('run_id', 'tool_id', 'command_id', 'step') if node.get(key)}
        inputs, outputs, unchanged = [], [], []
        for item in node['structures']:
            relation = item.get('relation', 'output')
            # A process with no declared geometry is an action, not a new frame.
            if item['provenance'] == 'missing' and 'relation' not in item:
                continue
            key = (item.get('path'), item['provenance'])
            if relation == 'output':
                outputs.append(item)
            elif relation == 'unchanged' and any(entry['kind'] == 'structure' for entry in entries):
                unchanged.append(item)
            elif item.get('path') is None or key not in seen:
                inputs.append(item)
        def frame(items):
            if not items:
                return
            number = sum(entry['kind'] == 'structure' for entry in entries) + 1
            frame_id = f'structure-{number}'
            while frame_id in used_ids:
                frame_id = 'frame-' + frame_id
            used_ids.add(frame_id)
            entries.append({'id': frame_id, 'kind': 'structure', 'title': f'结构 S{number}',
                'structures': items, **identity})
            seen.update((item.get('path'), item['provenance']) for item in items if item.get('path'))
        frame(inputs)
        entries.append({'id': node['id'], 'kind': 'action', 'title': node['title'], **identity,
            'reasoning_summary': note.get('purpose') or row.get('stated_purpose') or '',
            'summary_basis': note.get('basis', 'stated' if row.get('stated_purpose') else 'unknown'),
            'summary_evidence': note.get('evidence', []),
            'structures': unchanged,
            'result': node.get('outcome', ''), 'native_status': node['native_status']})
        frame(outputs)
    return entries


def export_story(output, entries):
    """Flat rows and original file links for figures/tables in a later article."""
    write_json(output / 'trajectory.json', {'schema_version': 1, 'entries': entries,
        'note': 'Recorded order, not inferred causality. Collection snapshots are not preserved historical versions.'})
    fields = ['order', 'kind', 'step', 'operation', 'reasoning_summary', 'summary_basis', 'result',
        'file', 'preview', 'provenance', 'atom_count', 'elements', 'cell', 'pbc', 'run_id', 'tool_id', 'command_id']
    with (output / 'trajectory.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for ordinal, entry in enumerate(entries, 1):
            for item in entry.get('structures') or [{}]:
                saved = item.get('structure_summary', {})
                writer.writerow({'order': ordinal, 'kind': entry['kind'], 'operation': entry['title'],
                    **{key: entry.get(key, '') for key in ('step', 'reasoning_summary', 'summary_basis', 'result', 'run_id', 'tool_id', 'command_id')},
                    'file': item.get('copied_file', ''), 'preview': item.get('preview', ''), 'provenance': item.get('provenance', ''),
                    'atom_count': saved.get('atom_count', ''), **{key: json.dumps(saved[key], ensure_ascii=False) if key in saved else '' for key in ('elements', 'cell', 'pbc')}})

def link(path, label):
    return f'<a href="{quote(path)}">{escape(str(label))}</a>'


def review_progress(attempts, rows):
    return {'operations': len(rows), 'unexplained_operations': sum(not explanation_complete(row) for row in rows),
        'remaining_steps': [row['short_id'] for row in rows if not explanation_complete(row)],
        'evidence_ids': list(aliases(attempts))}


def prepare_review(folder: str, output_folder: str = "review-initial", notes_file: str = "review-notes.json") -> dict:
    """Build the factual graph/files/previews first; create small editable notes."""
    root, attempts = load_archive(folder)
    notes_path = destination(notes_file, root)
    if notes_path.exists():
        raise ReviewValidationError('Notes already exist; continue with update_review_notes/build_review', '/notes_file', notes_file=notes_file)
    notes = {'format': 'oaw.review-notes.v1', 'title': '结构修改与交付报告', 'summary': '', 'steps': {}}
    # Native identities/structure associations are computed, not editable JSON
    # that the model must copy back before it can create any output.
    write_json(notes_path, notes)
    receipt = build_review(folder, str(notes_path), output_folder)
    rows = operations(attempts)
    receipt.update(notes_file=display_path(notes_path), operation_index=[{
        key: row[key] for key in ('short_id', 'action', 'target_label', 'result_status', 'observed')
        } for row in rows[:20]], index_preview_complete=len(rows) <= 20,
        evidence_ids=list(aliases(attempts)),
        next='The report exists. Deliver it, or optionally add short summaries for key structure actions and rebuild once. Routine reads do not need individual annotations; missing versions remain findings.')
    return receipt


def update_review_notes(folder: str, notes_file: str, updates: dict) -> dict:
    """Merge reviewer notes by short step ID; keep native IDs and other notes intact."""
    root, attempts = load_archive(folder)
    path = destination(notes_file, root)
    notes = read_json(path)
    if not isinstance(notes, dict) or notes.get('format') != 'oaw.review-notes.v1':
        raise ReviewValidationError('Select the notes_file returned by prepare_review', '/notes_file')
    if not isinstance(updates, dict) or set(updates) - {'title', 'summary', 'steps'}:
        raise ReviewValidationError('updates supports title, summary and steps', '/updates')
    merged = {**notes, **{k: updates[k] for k in ('title', 'summary') if k in updates}, 'steps': dict(notes.get('steps', {}))}
    if not isinstance(updates.get('steps', {}), dict):
        raise ReviewValidationError('steps must map short IDs to explanations', '/updates/steps')
    for step, change in updates.get('steps', {}).items():
        if not isinstance(change, dict):
            raise ReviewValidationError('Each step update must be an object', '/updates/steps/' + step)
        previous = merged['steps'].get(step, {})
        merged['steps'][step] = {**previous, **change}
        if isinstance(change.get('node'), dict):
            merged['steps'][step]['node'] = {**previous.get('node', {}), **change['node']}
    rows = operations(attempts)
    plan = compile_notes(root, attempts, rows, merged)
    validate_manifest(root, attempts, plan)
    annotated = operations(attempts, plan['operations'])
    pending_commands = [node['step'] for node in plan['nodes'] if not node.get('tool_id')
        and not explanation_complete({'annotation': node.get('annotation', {})})]
    # Validate before replacing an accepted note. A refused annotation cannot
    # corrupt previous work or partially alter the source archive.
    temporary = path.with_name(path.name + '.pending')
    try:
        write_json(temporary, merged)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'notes_file': display_path(path), 'updated_steps': list(updates.get('steps', {})),
        **review_progress(attempts, annotated), 'remaining_command_steps': pending_commands,
        'next': 'Build once to render changed notes in a fresh folder. Routine-read annotations are optional; previously built reports are unchanged.'}


def read_review_step(folder: str, step: str, pointer: str = "", offset: int = 0, limit: int = 6000) -> dict:
    """Read one native tool/model/command by short ID and JSON pointer, not a log dump."""
    _, attempts = load_archive(folder)
    reference = aliases(attempts).get(step)
    if not reference:
        raise ReviewValidationError('Unknown evidence short ID', '/step', available_evidence=list(aliases(attempts)))
    if offset < 0 or not 1 <= limit <= 24000:
        raise ReviewValidationError('offset >= 0 and 1 <= limit <= 24000', '/limit')
    attempt = next(a for a in attempts if a['run']['run_id'] == reference['run_id'])
    value = next(r for r in attempt[{'tool': 'tools', 'model': 'models', 'command': 'commands'}[reference['kind']]] if r['id'] == reference['id'])
    if pointer:
        if not pointer.startswith('/'):
            raise ReviewValidationError('Use a JSON pointer beginning with /', '/pointer')
        try:
            for component in pointer[1:].split('/'):
                key = component.replace('~1', '/').replace('~0', '~')
                value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ReviewValidationError('Pointer not found in this record', '/pointer', step=step, pointer=pointer)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    end = min(offset + limit, len(text))
    return {'step': step, 'reference': reference, 'pointer': pointer, 'text': text[offset:end],
        'offset': offset, 'total_chars': len(text), 'next_offset': end if end < len(text) else None}


def build_review(folder: str, manifest_file: str = "", output_folder: str = "review-output",
                 steps: dict | None = None, summary: str = "") -> dict:
    """Build structure → short decision/action → structure, with article data files."""
    root, attempts = load_archive(folder)
    if manifest_file and (steps is not None or summary):
        raise ReviewValidationError('Use either an existing manifest_file or inline steps/summary', '/manifest_file')
    simple = not manifest_file
    manifest = read_json(Path(manifest_file)) if manifest_file else {
        'format': 'oaw.review-notes.v1', 'steps': steps if steps is not None else {}, 'summary': summary}
    if isinstance(manifest, dict) and manifest.get('format') == 'oaw.review-notes.v1':
        manifest = compile_notes(root, attempts, operations(attempts), manifest)
    nodes = validate_manifest(root, attempts, manifest)
    operation_rows = operations(attempts, manifest.get("operations"))
    operation_by_tool = {(row["run_id"], row["tool_id"]): row for row in operation_rows}
    output = destination(output_folder, root)
    if output.exists():
        raise ValueError("Use a fresh output directory separate from the source archive")
    output.mkdir()
    warnings, rows, source_links = list(statistics(attempts)["record_gaps"]), [], []
    warnings.extend(statistics(attempts)['command_evidence_gaps'])
    unexplained = [row["short_id"] for row in operation_rows if not explanation_complete(row)]
    if unexplained and not simple:
        warnings.append({"kind": "operations_unexplained", "count": len(unexplained), "operations": unexplained,
                         "note": "Native action facts are available; reviewer purpose/findings/follow-up explanations are still absent."})
    unexplained_commands = [node.get('step', node['id']) for node in nodes if manifest.get('sequence_edges')
        and not node.get('tool_id') and not explanation_complete({'annotation': node.get('annotation', {})})]
    if unexplained_commands and not simple:
        warnings.append({'kind': 'commands_unexplained', 'steps': unexplained_commands})
    for name in ("task.json", "collection.json"):
        source = inside(root, name)
        if source.is_file():
            (output / "raw").mkdir(exist_ok=True)
            shutil.copy2(source, output / "raw" / name)
            source_links.append(link("raw/" + name, name))
    collection = read_json(inside(root, "collection.json"), {})
    warnings.extend({"source": "collection.json", **warning} for warning in collection.get("warnings", []))
    snapshot_root = inside(root, "sandbox files")
    if snapshot_root.is_dir():
        for path in snapshot_root.rglob("*"):
            if path.is_file():
                source = inside(root, str(path.relative_to(root)))
                copied = output / path.relative_to(root)
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, copied)
    for ordinal, attempt in enumerate(attempts, 1):
        target = output / "raw" / f"attempt-{ordinal:02d}"
        target.mkdir(parents=True)
        for name in NATIVE_FILES:
            source = attempt["directory"] / name
            if source.is_file():
                shutil.copy2(source, target / name)
        source_links.append(link(str(target.relative_to(output)) + "/run.json", attempt["run"]["run_id"]))
    rendered_files = {}
    for ordinal, node in enumerate(nodes, 1):
        for number, item in enumerate(node["attachments"], 1):
            if item["provenance"] == "missing":
                warnings.append({"node": node["id"], "kind": "missing_version", "note": item.get("note", "")})
                continue
            source = inside(root, item["path"])
            if not source.is_file():
                item["available"] = False
                warnings.append({"node": node["id"], "kind": "file_unavailable", "path": item["path"]})
                continue
            copied = output / "attachments" / f"A{ordinal:03d}-{number:02d}-{source.name}"
            copied.parent.mkdir(exist_ok=True)
            shutil.copy2(source, copied)
            item.update(available=True, copied_file=str(copied.relative_to(output)), file_kind="data_attachment")
        for number, item in enumerate(node["structures"], 1):
            if item["provenance"] == "missing":
                warnings.append({"node": node["id"], "kind": "missing_version", "note": item.get("note", "")})
                continue
            source = inside(root, item["path"])
            if not source.is_file():
                item["available"] = False
                warnings.append({"node": node["id"], "kind": "file_unavailable", "path": item["path"]})
                continue
            if source in rendered_files:
                item.update(rendered_files[source])
                continue
            extension = source.suffix.lower() or ".vasp"
            name = f"S{ordinal:03d}-{number:02d}{extension}"
            copied = output / "structures" / name
            copied.parent.mkdir(exist_ok=True)
            shutil.copy2(source, copied)
            item.update(available=True, copied_file=str(copied.relative_to(output)), preview_kind="saved_file_render_not_ui_capture")
            preview = output / "previews" / f"S{ordinal:03d}-{number:02d}.png"
            try:
                # The package includes the original maintained structure-image
                # script, not a fork of its reader/renderer.
                os.environ.setdefault("MPLBACKEND", "Agg")
                from structure_image import generate_structure_image
                from ase.io import read
                render_input = copied
                if extension == ".json":
                    # Native Atom Structure JSON is data, not an ASE database.
                    # Keep the original file; a labelled format-only rendering
                    # intermediate preserves its supplied coordinates/cell.
                    from ase import Atoms
                    from ase.io import write
                    doc = read_json(copied)
                    atoms = Atoms([a["symbol"] for a in doc["atoms"]],
                        positions=[[a["x"], a["y"], a["z"]] for a in doc["atoms"]],
                        cell=doc.get("cell"), pbc=doc.get("pbc", False))
                    render_input = output / "render-inputs" / f"S{ordinal:03d}-{number:02d}.extxyz"
                    render_input.parent.mkdir(exist_ok=True)
                    write(render_input, atoms, format="extxyz")
                    item["render_input"] = str(render_input.relative_to(output))
                    item["render_conversion"] = "Native JSON coordinates/cell only; format conversion for preview, not a historical original."
                saved_atoms = read(render_input)
                item["structure_summary"] = {"atom_count": len(saved_atoms),
                    "elements": dict(Counter(saved_atoms.get_chemical_symbols())),
                    "cell": saved_atoms.cell.array.tolist(), "cell_lengths": saved_atoms.cell.lengths().tolist(),
                    "pbc": saved_atoms.pbc.tolist(), "source": "saved_file_metadata_no_calculation"}
                result = generate_structure_image(str(render_input.parent), render_input.name,
                    str(preview.relative_to(Path.cwd().resolve())), rotation="15x,25y", dpi=300)
                if result.get("error") or not preview.is_file():
                    raise ValueError(result.get("error") or "Renderer produced no image")
                item["preview"] = str(preview.relative_to(output))
            except Exception as exc:
                warnings.append({"node": node["id"], "kind": "preview_unavailable", "message": str(exc)})
            rendered_files[source] = {k: item[k] for k in ('available', 'copied_file', 'preview_kind',
                'structure_summary', 'preview', 'render_input', 'render_conversion') if k in item}
        fragments = []
        for item in [*node["structures"], *node["attachments"]]:
            body = f'<p>{escape(item["provenance"])} · {escape(item.get("relation", "output"))} · {escape(str(item.get("note", "")))}</p>'
            if item.get("copied_file"):
                body += link(item["copied_file"], item["path"])
            if item.get("structure_summary"):
                saved = item["structure_summary"]
                body += '<p>' + escape(f'{saved["atom_count"]} atoms · {saved["elements"]} · cell lengths Å: {[round(v, 5) for v in saved["cell_lengths"]]} · PBC: {saved["pbc"]}') + '</p>'
            if item.get("preview"):
                body += f'<figure><img src="{quote(item["preview"])}" alt="Saved structure file preview"><figcaption>文件预览 · 来自上述保存文件；非历史界面截图</figcaption></figure>'
            elif item.get("file_kind") == "data_attachment" and item.get("available"):
                body += '<strong>数据附件 · 无结构预览</strong>'
            elif item.get("provenance") == "missing":
                body += '<strong>历史版本缺失；未用最终结构替代</strong>'
            else:
                body += '<strong>文件或预览不可用，详见报告告警</strong>'
            fragments.append('<div class="structure">' + body + '</div>')
        if not fragments:
            fragments = ['<p>该节点未单独绑定结构文件；结果可能列于分支节点，具体以执行证据和结构来源说明为准。</p>']
        evidence = {k: node[k] for k in ("run_id", "tool_id", "command_id") if node.get(k)}
        operation = operation_by_tool.get((node["run_id"], node.get("tool_id")))
        explanation = annotation_html(operation) if operation else ""
        if not operation and node.get('annotation'):
            explanation = annotation_html({'annotation': node['annotation']})
        if operation:
            explanation += '<p><a href="#op-' + operation["short_id"].replace(":", "-") + '">查看本次操作的对象、范围和实际反馈</a></p>'
        rows.append(f'<section id="details-{node["id"]}"><h2>{escape(node["title"])}</h2><p class="status">{escape(node["native_status"])}</p><p>{escape(str(node.get("outcome", "")))}</p>'
            + explanation + ''.join(fragments) + '<details><summary>原始证据身份</summary><pre>' + escape(json.dumps(evidence, ensure_ascii=False, indent=2)) + '</pre></details></section>')
    story = structure_story(nodes, operation_rows)
    export_story(output, story)
    svg, mermaid = flowchart(story, image_root=output)
    (output / "flowchart.svg").write_text(svg, encoding="utf-8")
    (output / "flowchart.mmd").write_text(mermaid, encoding="utf-8")
    # Complete tools and all logical request groups remain available on demand.
    timeline = []
    for ordinal, attempt in enumerate(attempts, 1):
        prefix = f"raw/attempt-{ordinal:02d}"
        tools = "".join(operation_html(row, prefix) for row in operation_rows if row["run_id"] == attempt["run"]["run_id"])
        groups = defaultdict(list)
        for model in attempt["models"]:
            groups[(model.get("run_attempt"), model.get("scope"), model.get("role"), model.get("model_request"))].append(model)
        requests = []
        for key, models in groups.items():
            interrupted = sum(m.get("state") == "interrupted" for m in models)
            items = ''.join('<li>' + escape(f"attempt {m.get('model_attempt')} · {m.get('state')} · {elapsed(m)}s · input={m.get('input_tokens', 'unknown')} · {m['id']}") + '</li>' for m in models)
            requests.append(f'<details><summary>{escape(str(key[2]))} request {escape(str(key[3]))} · {len(models)} attempts / {interrupted} interrupted</summary><ol>{items}</ol></details>')
        progress = [row for row in attempt.get('activity', []) if row.get('metadata', {}).get('type') == 'agent_progress'
            and row.get('metadata', {}).get('kind') != 'model_reasoning']
        host_progress = ('<details><summary>宿主进度与恢复 · ' + str(len(progress)) + '条</summary><ol>' + ''.join(
            '<li>' + escape(str(row.get('timestamp')) + ' · ' + str(row['metadata'].get('text', '')))
            + '</li>' for row in progress) + '</ol>' + link(prefix + '/run activity.json', '完整持久活动记录') + '</details>') if progress else ''
        timeline.append(f'<details><summary>尝试 {ordinal} · {escape(attempt["run"]["run_id"])} · {len(attempt["tools"])} 工具调用</summary>' + tools
            + ''.join(requests) + host_progress + link(prefix + '/tool calls.json', '完整工具参数/回执') + ' · ' + link(prefix + '/model requests.json', '完整模型输入/响应') + '</details>')
    stats = statistics(attempts)
    report = {"schema_version": 2, "title": str(manifest.get("title", "Modelling review")),
        "summary": str(manifest.get("summary", "")), "statistics": stats, "nodes": nodes, "operations": operation_rows, "warnings": warnings,
        "provenance_note": "Native IDs are checked against the selected export. Titles, outcomes, edges and historical-file attribution are the review Agent's annotations; file existence alone is not historical proof.",
        "preview_note": "Previews render saved files now; no archived program or scientific calculation was executed.",
        "story": story, "annotations_optional": simple,
        "explanation_status": "optional" if simple else "complete" if not unexplained and not unexplained_commands else "incomplete",
        "sequence_note": "Dashed arrows indicate recorded order only, not data derivation."}
    write_json(output / "review.json", report)
    (output / "操作说明.md").write_text(operations_markdown(operation_rows), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name('report.css'), output / 'report.css')
    html = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    html += '<title>' + escape(report['title']) + '</title><link rel="stylesheet" href="report.css"></head><body><div class="page">'
    document_head = html
    html += '<header><h1>' + escape(report['title']) + '</h1></header><main id="sequence">'
    html += story_html(story) if compact_story(story) else '<p>未保留可展示的结构，详见完整记录。</p>'
    html += '</main><footer>' + link('records.html', '完整记录与文件来源') + link('review.zip', '报告 ZIP') + link('trajectory.csv', '数据 CSV') + link('flowchart.svg', '流程图 SVG') + '</footer></div></body></html>'
    (output / "index.html").write_text(html, encoding="utf-8")
    records = document_head + '<header><h1>完整记录与文件来源</h1><p>' + link('index.html', '返回结构演变') + '</p></header>'
    records += '<p>' + escape(report['summary']) + '</p><aside class="appendix">'
    records += '<details><summary>文件来源与步骤细节</summary>' + ''.join(rows) + '</details>'
    records += '<details><summary>缺失与告警 · ' + str(len(warnings)) + '</summary><pre>' + escape(json.dumps(warnings, ensure_ascii=False, indent=2)) + '</pre></details>'
    records += '<details id="timeline"><summary>完整执行记录 · ' + str(len(operation_rows)) + ' 次工具调用</summary>' + ''.join(timeline) + '</details>'
    records += '<details id="sources"><summary>原始记录</summary>' + '<br>'.join(source_links) + '<p>raw/按尝试保存原生导出。</p>' + link('trajectory.json', '完整结构序列 JSON') + '</details></aside></div></body></html>'
    (output / 'records.html').write_text(records, encoding='utf-8')
    markdown = [f'# {report["title"]}', '',
        '[结构演变报告](index.html) · [修改流程图](flowchart.svg) · [可编辑序列](结构演变.md)', '',
        '[完整记录与文件来源](records.html) · [文章数据 CSV](trajectory.csv) · [结构序列 JSON](trajectory.json)', '',
        '图片为保存文件预览；缺失历史版本不补造。']
    (output / "README.md").write_text('\n'.join(markdown) + '\n', encoding="utf-8")
    story_markdown = [f'# {report["title"]}', '']
    for index, entry in enumerate(compact_story(story)):
        if index:
            story_markdown += ['↓', '']
        if entry['kind'] == 'action':
            story_markdown += ['思考：' + ' '.join(wrapped(decision_text(entry), 100, 2)), '',
                '操作：' + ' '.join(wrapped(operation_text(entry), 100, 2)), '']
        else:
            for item in entry['structures']:
                if item.get('preview'):
                    story_markdown += ['![结构文件预览](' + quote(item['preview']) + ')', '']
                story_markdown += [('[' + Path(item['path']).name + '](' + quote(item['copied_file']) + ')' if item.get('copied_file') else '结构版本缺失') + ' · ' + item['provenance'], '']
    story_markdown += ['[完整记录与文件来源](records.html)', '']
    (output / '结构演变.md').write_text('\n'.join(story_markdown), encoding='utf-8')
    archive = output / "review.zip"
    files = [p for p in output.rglob('*') if p.is_file()]
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, str(path.relative_to(output)))
    return {"report": display_path(output / "index.html"), "flowchart": display_path(output / "flowchart.svg"),
        "story": display_path(output / '结构演变.md'), "data_csv": display_path(output / 'trajectory.csv'), "data_json": display_path(output / 'trajectory.json'),
        "bundle": display_path(archive), "operations": len(operation_rows), "unexplained_operations": len(unexplained), "nodes": len(nodes), "previews": sum(bool(s.get('preview')) for n in nodes for s in n['structures']),
        "warnings": warnings, "statistics": stats, 'explanation_status': report['explanation_status'],
        'remaining_steps': [] if simple else [row['short_id'] for row in operation_rows if not explanation_complete(row)],
        'remaining_command_steps': [] if simple else unexplained_commands,
        'next': 'Deliver the report, structure files/images and trajectory data. Missing historical structures or optional summaries do not require rebuilding or annotating every tool call.'}


if __name__ == "__main__":
    def cli_function(function):
        from functools import wraps
        @wraps(function)
        def checked(**arguments):
            try:
                return function(**arguments)
            except (ValueError, OSError) as error:
                return {'error': {'code': 'review_input', 'message': str(error),
                    'issues': getattr(error, 'issues', [{'path': '/', 'message': str(error)}]),
                    'next': 'Correct only the identified notes/reference. Do not recreate the index or rerun archived code.'}}
        return checked
    tools = {name: cli_function(function) for name, function in {
        "inspect_archive": inspect_archive, "prepare_review": prepare_review,
        "update_review_notes": update_review_notes, "read_review_step": read_review_step,
        "build_review": build_review}.items()}
    parser = build_cli_parser(prog="log_review.py", description_lines=["Review native OAW exports; render only saved structures."], tool_functions=tools)
    raise SystemExit(run_cli(argv=None, parser=parser, tool_functions=tools))
