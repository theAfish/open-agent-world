"""Drafting a schema from a conversation: sample conversion, prompt and draft validation."""
import base64
import json
import hashlib
import io
from types import SimpleNamespace

import pytest

from oaw_knowledge_base import schema_draft
from oaw_knowledge_base.errors import KnowledgeError

CONTEXT = SimpleNamespace(state=None)
GOOD = {"reply": "Drafted.", "draft": {
    "name": "Batch", "description": "One batch", "system_prompt": "One row is one batch.",
    "definition": {"required": ["batch_no"], "properties": {
        "batch_no": {"value": "string", "description": "Batch id"},
        "conductivity": {"value": "number[]", "unit": "mS/cm"},
        "xrd": {"value": ["pass", "fail"]}}}}}


def sample(name, data):
    return {"filename": name, "content_base64": base64.b64encode(data).decode(), "media_type": "text/csv"}


def test_a_sample_becomes_capped_markdown_and_bad_files_are_refused():
    result = schema_draft.schema_sample(CONTEXT, sample("log.csv", b"batch,yield\nA1,76.1\n"))
    assert "| batch | yield |" in result["markdown"] and result["truncated"] is False
    big = schema_draft.schema_sample(CONTEXT, sample("big.csv", b"a,b\n" + b"1,2\n" * 20_000))
    assert big["truncated"] is True and len(big["markdown"]) == schema_draft.MAX_SAMPLE_CHARS
    for bad in (sample("empty.csv", b""), {**sample("a.csv", b"x"), "content_base64": "***"},
                sample("dir/a.csv", b"x"), sample("huge.csv", b"x" * (schema_draft.MAX_SAMPLE_BYTES + 1))):
        with pytest.raises(ValueError):  # KnowledgeError, or a pydantic rejection of the request
            schema_draft.schema_sample(CONTEXT, bad)


def test_messages_carry_samples_as_data_and_the_current_schema_for_follow_ups():
    history = [{"role": "user", "content": "make a schema"}, {"role": "assistant", "content": "ok"},
               {"role": "user", "content": "add moisture"}]
    messages = schema_draft.build_messages(history, [{"filename": "a.csv", "markdown": "| x |"}], {"name": "Old"})
    assert messages[0]["role"] == "system"
    assert '<sample_file name="a.csv">' in messages[0]["content"] and '"name": "Old"' in messages[0]["content"]
    assert [m["role"] for m in messages[1:]] == ["user", "assistant", "user"]
    with pytest.raises(KnowledgeError):
        schema_draft.build_messages([{"role": "assistant", "content": "hi"}])


def test_binary_sample_uses_borrowed_stream_and_keeps_its_parser_budget():
    data = b'batch,yield\nA1,76.1\n'
    file = SimpleNamespace(stream=io.BytesIO(data), size_bytes=len(data),
                           sha256=hashlib.sha256(data).hexdigest())
    context = SimpleNamespace(state=None, files={'file': file})
    result = schema_draft.schema_sample(context, {'filename': 'sample.csv', 'media_type': 'text/csv'})
    assert 'A1' in result['markdown'] and not file.stream.closed
    file.size_bytes = schema_draft.MAX_SAMPLE_BYTES + 1
    with pytest.raises(KnowledgeError, match='2 MiB'):
        schema_draft.schema_sample(context, {'filename': 'sample.csv'})


def test_a_valid_answer_yields_a_clean_draft_and_a_question_has_no_draft():
    result = schema_draft.parse_draft(json.dumps(GOOD))
    assert result["draft"]["definition"]["required"] == ["batch_no"]
    assert result["draft"]["definition"]["properties"]["xrd"] == {"value": ["pass", "fail"]}
    assert schema_draft.parse_draft({"reply": "Which unit?", "draft": None}) == {"reply": "Which unit?", "draft": None}


@pytest.mark.parametrize("mutate", [
    lambda d: d["definition"]["properties"].update({"bad key": {"value": "string"}}),
    lambda d: d["definition"]["properties"].update({"x": {"value": "date"}}),
    lambda d: d["definition"]["properties"].update({"x": {"value": []}}),
    lambda d: d["definition"].update({"properties": {}}),
    lambda d: d.update({"system_prompt": ""}),
])
def test_unusable_drafts_are_rejected_with_a_reason(mutate):
    answer = json.loads(json.dumps(GOOD))
    mutate(answer["draft"])
    with pytest.raises(KnowledgeError):
        schema_draft.parse_draft(answer)
    with pytest.raises(KnowledgeError):
        schema_draft.parse_draft("not json")
