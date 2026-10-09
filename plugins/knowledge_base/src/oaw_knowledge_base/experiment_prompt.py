"""Turning an assemble prompt into a model request, and its answer back into JSON.

Mirrors ``projection.py``: the host bridge holds the model credentials the plugin
never gets, so building the chat messages and parsing the reply live in one place,
shared between the bridge and an agent that produces the merge itself.
"""
from __future__ import annotations

import json

from .errors import KnowledgeError


def build_system_prompt(prompt):
    return prompt["system_prompt"] + "\n\nJSON Schema the merged \"data\" must match:\n" + \
        json.dumps(prompt["definition"], ensure_ascii=False)


def build_messages(prompt):
    """Chat messages for an OpenAI-compatible completion: the merge rules, then
    every contributing file's already-extracted data, each labelled with its source
    so the model can cite it back in ``conflicts``."""
    contributions = "\n\n".join(
        f"From {item['filename'] or item['source_id']} (source_id={item['source_id']}):\n"
        + json.dumps(item["data"], ensure_ascii=False)
        for item in prompt["contributions"])
    return [{"role": "system", "content": build_system_prompt(prompt)},
            {"role": "user", "content": contributions}]


def parse_assemble(answer):
    """The model's reply as ``{"data": {...}, "conflicts": [...]}``, or a ``KnowledgeError``."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            raise KnowledgeError("The model did not return JSON") from None
    if not isinstance(answer, dict) or not isinstance(answer.get("data"), dict):
        raise KnowledgeError('The model must return {"data": {...}, "conflicts": [...]}')
    conflicts = answer.get("conflicts") or []
    if not isinstance(conflicts, list):
        raise KnowledgeError('"conflicts" must be a list')
    return {"data": answer["data"], "conflicts": conflicts}
