"""Turning an insight prompt into one model request, and its answer back into insights.

Mirrors ``experiment_prompt.py``: the host bridge holds the model credentials the
plugin never gets, so building the chat messages and parsing the reply live in one
place. This is the agent-free path for **Extract insights** — the same
``insight_prompt`` → ``insight_save`` pair a connected Lab assistant drives through
its tools, collapsed into a single JSON-mode completion.
"""
from __future__ import annotations

import json

from .errors import KnowledgeError
from .insight_store import KINDS, ROLES


def _schemas(prompt):
    schemas = prompt.get("schemas")
    if schemas:
        return schemas
    return [{"schema_id": prompt["schema_id"], "schema_name": prompt["schema_name"],
             "definition": prompt["definition"], "field_descriptions": prompt.get("field_descriptions"),
             "record_count": prompt.get("record_count")}]


def build_system_prompt(prompt):
    parts = [prompt["instruction"]]
    for schema in _schemas(prompt):
        parts.append(f"\nJSON Schema of one \"{schema['schema_name']}\" record "
                     f"(schema_id={schema['schema_id']}; field names may be dotted for nested objects):\n"
                     + json.dumps(schema["definition"], ensure_ascii=False))
        if schema.get("field_descriptions"):
            parts.append("Field notes:\n" + json.dumps(schema["field_descriptions"], ensure_ascii=False))
    return "\n".join(parts)


def build_messages(prompt, table=None):
    """Chat messages for an OpenAI-compatible completion. ``table`` lets a caller that
    paged through ``has_more`` pass the whole record table instead of one window."""
    schemas = _schemas(prompt)
    existing = "\n".join(f"- [{item['kind']}] {item['title']}" for item in prompt.get("existing") or [])
    heading = "\n".join(f"- {item['schema_name']} (schema_id={item['schema_id']}): "
                        f"{item.get('record_count', '?')} published records" for item in schemas)
    statistics = prompt.get("statistics_by_schema") or [{"schema_name": schemas[0]["schema_name"],
                                                          "statistics": prompt["statistics"]}]
    stats_text = "\n".join(f"{item['schema_name']}:\n" + json.dumps(item["statistics"], ensure_ascii=False)
                           for item in statistics)
    cross = prompt.get("cross_schema")
    user = (f"Schemas:\n{heading}\n"
            + "\nStatistics computed per schema:\n" + stats_text
            + (("\n\nAcross schemas (records matched by name):\n" + json.dumps(cross, ensure_ascii=False))
               if cross else "")
            + (f"\n\nAlready published (do not propose again):\n{existing}\n" if existing else "")
            + "\n\nRecords:\n\n" + (table if table is not None else prompt["markdown"]))
    return [{"role": "system", "content": build_system_prompt(prompt)},
            {"role": "user", "content": user}]


def parse_insights(answer):
    """The model's reply as a list of insight inputs ``insight_save`` accepts."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            raise KnowledgeError("The model did not return JSON") from None
    items = answer.get("insights") if isinstance(answer, dict) else answer
    if not isinstance(items, list) or not items:
        raise KnowledgeError('The model must return {"insights": [{"kind": ..., "title": ..., '
                             '"statement": ..., "evidence": [...]}, ...]}')
    cleaned = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise KnowledgeError(f"Insight {index + 1} must be an object")
        kind = str(item.get("kind") or "note").strip().lower()
        if kind not in KINDS:
            kind = "note"
        title = str(item.get("title") or "").strip() or f"insight {index + 1}"
        statement = str(item.get("statement") or "").strip() or title
        evidence = []
        for link in item.get("evidence") or []:
            if isinstance(link, dict) and link.get("record_id"):
                role = str(link.get("role") or "supports").strip().lower()
                evidence.append({"record_id": str(link["record_id"]),
                                 "role": role if role in ROLES else "supports"})
            elif isinstance(link, str) and link:
                evidence.append({"record_id": link, "role": "supports"})
        if not evidence:
            raise KnowledgeError(f'Insight "{title}" cites no record_id as evidence')
        confidence = item.get("confidence")
        try:
            confidence = None if confidence is None else min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None
        properties = item.get("properties")
        note = item.get("note")
        cleaned.append({
            "kind": kind, "title": title[:500], "statement": statement[:4000],
            "fields": [str(value) for value in item.get("fields") or [] if value][:40],
            "confidence": confidence, "properties": properties if isinstance(properties, dict) else {},
            "evidence": evidence, "note": str(note)[:4000] if note else None,
        })
    return cleaned
