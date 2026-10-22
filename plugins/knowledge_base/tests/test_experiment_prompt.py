"""experiment_prompt.py: the one-call bridge shape of Extract records."""
import json

import pytest

from oaw_knowledge_base.errors import KnowledgeError
from oaw_knowledge_base.experiment_prompt import build_messages, parse_extract

PROMPT = {
    "bundle_name": "Batch 7", "notes": "units are S/cm",
    "system_prompt": "Extract specimens.", "instruction": "One record per row.",
    "definition": {"type": "object", "properties": {"sample_id": {"type": "string"}}},
    "field_descriptions": {"sample_id": "the label"},
    "files": [{"source_id": "s1", "filename": "a.csv"}, {"source_id": "s2", "filename": "b.md"}],
    "markdown": "## File: a.csv (source_id=s1)\n\n| A1 |\n",
}


def test_build_messages_carries_schema_notes_and_files():
    messages = build_messages(PROMPT)
    assert messages[0]["role"] == "system"
    assert "Extract specimens." in messages[0]["content"] and "One record per row." in messages[0]["content"]
    assert json.dumps(PROMPT["definition"], ensure_ascii=False) in messages[0]["content"]
    assert "the label" in messages[0]["content"]
    assert "units are S/cm" in messages[1]["content"]
    assert "a.csv (source_id=s1)" in messages[1]["content"] and "b.md (source_id=s2)" in messages[1]["content"]
    assert "| A1 |" in messages[1]["content"]
    # A caller that paged through the whole document passes it in place of the window.
    assert "WHOLE" in build_messages(PROMPT, markdown="WHOLE")[1]["content"]


def test_parse_extract_accepts_a_json_string_or_a_dict_and_fills_defaults():
    parsed = parse_extract(json.dumps({"records": [
        {"name": "A1", "data": {"sample_id": "A1"}, "source_ids": ["s1"], "note": "row 1"},
        {"data": {"sample_id": "A2"}}]}))
    assert parsed[0] == {"name": "A1", "data": {"sample_id": "A1"}, "source_ids": ["s1"], "note": "row 1"}
    assert parsed[1] == {"name": "record 2", "data": {"sample_id": "A2"}, "source_ids": [], "note": None}
    assert parse_extract([{"name": "x", "data": {}}])[0]["name"] == "x"


def test_parse_extract_rejects_malformed_replies():
    with pytest.raises(KnowledgeError):
        parse_extract("not json")
    with pytest.raises(KnowledgeError):
        parse_extract({"records": []})
    with pytest.raises(KnowledgeError):
        parse_extract({"records": [{"name": "x", "data": "not an object"}]})
