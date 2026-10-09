"""experiment_prompt.py in isolation: building the merge request and parsing its reply."""
import pytest

from oaw_knowledge_base.experiment_prompt import build_messages, parse_assemble
from oaw_knowledge_base.errors import KnowledgeError

PROMPT = {
    "system_prompt": "Merge these.", "definition": {"type": "object"},
    "contributions": [
        {"filename": "a.md", "source_id": "s1", "data": {"conductivity": 1.2}},
        {"filename": "b.md", "source_id": "s2", "data": {"conductivity": 1.4}},
    ],
}


def test_build_messages_labels_each_contribution_with_its_source():
    messages = build_messages(PROMPT)
    assert messages[0]["role"] == "system"
    assert "Merge these." in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "a.md" in messages[1]["content"] and "s1" in messages[1]["content"]
    assert "b.md" in messages[1]["content"] and "s2" in messages[1]["content"]


def test_parse_assemble_accepts_a_json_string_or_a_dict():
    assert parse_assemble('{"data": {"x": 1}, "conflicts": []}') == {"data": {"x": 1}, "conflicts": []}
    assert parse_assemble({"data": {"x": 1}}) == {"data": {"x": 1}, "conflicts": []}


def test_parse_assemble_rejects_malformed_replies():
    with pytest.raises(KnowledgeError):
        parse_assemble("not json")
    with pytest.raises(KnowledgeError):
        parse_assemble({"conflicts": []})
    with pytest.raises(KnowledgeError):
        parse_assemble({"data": {}, "conflicts": "not a list"})
