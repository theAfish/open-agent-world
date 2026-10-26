"""Turning an extract prompt into one model request, and its answer back into records.

The host bridge holds the model credentials the plugin never gets, so building the
chat messages and parsing the reply live in one place. This is the agent-free path
for **Extract records** — the same ``experiment_extract_prompt`` → ``experiment_save``
pair a connected Lab assistant drives through its tools, collapsed into a single
JSON-mode completion. ``record_extract_prompt`` → ``record_save`` (one literature
source onto a schema) reuses the same builders: its prompt has one file and no notes.
"""
from __future__ import annotations

import json

from .errors import KnowledgeError


def build_system_prompt(prompt):
    return (prompt["system_prompt"] + "\n\n" + prompt["instruction"]
            + "\n\nJSON Schema each record's \"data\" must match:\n"
            + json.dumps(prompt["definition"], ensure_ascii=False)
            + (("\n\nField notes:\n" + json.dumps(prompt["field_descriptions"], ensure_ascii=False))
               if prompt.get("field_descriptions") else ""))


def build_messages(prompt, markdown=None):
    """Chat messages for an OpenAI-compatible completion. ``markdown`` lets a caller
    that paged through ``has_more`` pass the whole document instead of one window."""
    files = prompt.get("files") or [{"filename": prompt.get("filename"), "source_id": prompt.get("source_id")}]
    listing = "\n".join(f"- {item['filename']} (source_id={item['source_id']})" for item in files)
    notes = prompt.get("notes") or ""
    user = (f"{'Upload: ' + prompt['bundle_name'] if prompt.get('bundle_name') else 'Source'}\nFiles:\n{listing}\n"
            + (f"\nUploader's notes:\n{notes}\n" if notes.strip() else "")
            + "\nDocument:\n\n" + (markdown if markdown is not None else prompt["markdown"]))
    return [{"role": "system", "content": build_system_prompt(prompt)},
            {"role": "user", "content": user}]


def parse_extract(answer, *, allow_empty=False):
    """The model's reply as a list of ``{"name", "data", "source_ids", "note"}``.
    ``allow_empty`` is for a literature source that may report nothing for a schema."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            raise KnowledgeError("The model did not return JSON") from None
    records = answer.get("records") if isinstance(answer, dict) else answer
    if not isinstance(records, list) or (not records and not allow_empty):
        raise KnowledgeError('The model must return {"records": [{"name": ..., "data": {...}}, ...]}')
    cleaned = []
    for index, item in enumerate(records):
        if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
            raise KnowledgeError(f'Record {index + 1} must be an object with a "data" object')
        name = str(item.get("name") or "").strip() or f"record {index + 1}"
        source_ids = [str(value) for value in item.get("source_ids") or [] if value]
        note = item.get("note")
        cleaned.append({"name": name[:500], "data": item["data"], "source_ids": source_ids,
                        "note": str(note)[:4000] if note else None})
    return cleaned
