"""insight_prompt.py: the one-call bridge shape of Extract insights."""
import json

import pytest

from oaw_knowledge_base.errors import KnowledgeError
from oaw_knowledge_base.insight_prompt import build_messages, parse_insights

PROMPT = {
    "schema_id": "sch1", "schema_name": "Electrolyte batch", "record_count": 8,
    "instruction": "Propose insights.", "definition": {"type": "object", "properties": {"m": {"type": "number"}}},
    "field_descriptions": {"m": "moisture in %"},
    "statistics": {"fields": [{"field": "m", "kind": "numeric", "mean": 0.5}], "correlations": []},
    "existing": [{"id": "i1", "kind": "anomaly", "title": "B7 is wet"}],
    "markdown": "| record_id | name | m |\n|---|---|---|\n| r1 | B1 | 0.5 |\n",
}


def test_build_messages_carries_schema_statistics_existing_and_table():
    messages = build_messages(PROMPT)
    assert messages[0]["role"] == "system" and "Propose insights." in messages[0]["content"]
    assert json.dumps(PROMPT["definition"], ensure_ascii=False) in messages[0]["content"]
    assert "moisture in %" in messages[0]["content"]
    user = messages[1]["content"]
    assert "schema_id=sch1): 8 published records" in user
    assert '"mean": 0.5' in user and "[anomaly] B7 is wet" in user and "| r1 | B1 |" in user
    assert "Across schemas" not in user
    assert "WHOLE" in build_messages(PROMPT, table="WHOLE")[1]["content"]


def test_build_messages_lists_every_schema_and_the_cross_schema_statistics():
    prompt = {**PROMPT, "schemas": [
        {"schema_id": "sch1", "schema_name": "Synthesis", "definition": {"properties": {"t": {}}}, "field_descriptions": None, "record_count": 8},
        {"schema_id": "sch2", "schema_name": "XRD", "definition": {"properties": {"phase": {}}}, "field_descriptions": {"phase": "main phase"}, "record_count": 6},
    ], "statistics_by_schema": [{"schema_name": "Synthesis", "statistics": {"fields": [{"field": "t"}]}},
                                {"schema_name": "XRD", "statistics": {"fields": [{"field": "phase"}]}}],
        "cross_schema": {"matched": 5, "correlations": [{"a": "Synthesis.t", "b": "XRD.crystallinity", "r": 0.8, "n": 5}]}}
    messages = build_messages(prompt)
    system = messages[0]["content"]
    assert '"Synthesis" record (schema_id=sch1' in system and '"XRD" record (schema_id=sch2' in system
    assert "main phase" in system
    user = messages[1]["content"]
    assert "- Synthesis (schema_id=sch1): 8 published records" in user and "- XRD (schema_id=sch2): 6" in user
    assert "Across schemas (records matched by name)" in user and "Synthesis.t" in user


def test_parse_insights_normalizes_kinds_roles_confidence_and_evidence():
    parsed = parse_insights(json.dumps({"insights": [
        {"kind": "Anomaly", "title": " B7 ", "statement": "wet", "fields": ["m", ""],
         "confidence": 1.7, "properties": {"z": 3}, "evidence": [{"record_id": "r1", "role": "OBSERVED_IN"}]},
        {"kind": "prophecy", "statement": "", "evidence": ["r2"], "confidence": "high", "properties": "no"},
    ]}))
    assert parsed[0] == {"kind": "anomaly", "title": "B7", "statement": "wet", "fields": ["m"],
                         "confidence": 1.0, "properties": {"z": 3},
                         "evidence": [{"record_id": "r1", "role": "observed_in"}], "note": None}
    assert parsed[1]["kind"] == "note" and parsed[1]["title"] == "insight 2"
    assert parsed[1]["statement"] == "insight 2" and parsed[1]["confidence"] is None
    assert parsed[1]["properties"] == {} and parsed[1]["evidence"] == [{"record_id": "r2", "role": "supports"}]
    assert parse_insights([{"title": "x", "evidence": ["r1"]}])[0]["kind"] == "note"


def test_parse_insights_rejects_non_json_empty_and_unevidenced_replies():
    with pytest.raises(KnowledgeError):
        parse_insights("not json")
    with pytest.raises(KnowledgeError):
        parse_insights({"insights": []})
    with pytest.raises(KnowledgeError):
        parse_insights({"insights": ["text"]})
    with pytest.raises(KnowledgeError):
        parse_insights({"insights": [{"title": "ungrounded", "statement": "s"}]})
