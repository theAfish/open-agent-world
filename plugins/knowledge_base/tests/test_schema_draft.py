"""Sample conversion for the schema editor's Draft with AI panel."""
import base64
import hashlib
import io
from types import SimpleNamespace

import pytest

from oaw_knowledge_base import schema_draft
from oaw_knowledge_base.errors import KnowledgeError

CONTEXT = SimpleNamespace(state=None)

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
