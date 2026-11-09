"""Sample files for the schema editor's "Draft with AI" panel.

``schema_sample`` turns one uploaded sample file into markdown without storing anything;
the panel hands it to the connected agent, which drafts and saves the schema itself.
"""
from __future__ import annotations

from pydantic import Field

from .actions import Request, _bounded, settings_of
from .errors import KnowledgeError
from .file_io import input_file
from .markdown import to_markdown

MAX_SAMPLES = 5
MAX_SAMPLE_BYTES = 2 * 1024 * 1024
MAX_SAMPLE_CHARS = 30_000


class SchemaSample(Request):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str | None = Field(default=None, min_length=1)
    media_type: str = Field(default="application/octet-stream", max_length=200)


def schema_sample(context, arguments):
    """One sample file as markdown, for the schema draft conversation. Not stored."""
    request = SchemaSample.model_validate(arguments)
    if "/" in request.filename or "\\" in request.filename:
        raise KnowledgeError("filename must not contain a path separator")
    file = input_file(context, request.filename, request.content_base64)
    if file.size_bytes > MAX_SAMPLE_BYTES:
        raise KnowledgeError("Sample files are limited to 2 MiB")
    settings = settings_of(context)
    text, engine, _ = to_markdown(
        file.stream, request.filename, request.media_type, engine=settings["pdf_engine"],
        mineru_base_url=settings["mineru_base_url"] or None, vision_model=settings["vision_model"],
        resolve_secret=getattr(context, "resolve_secret", None), resolve_model=getattr(context, "resolve_model", None))
    truncated = len(text) > MAX_SAMPLE_CHARS
    return _bounded({"filename": request.filename, "engine": engine, "truncated": truncated,
                     "markdown": text[:MAX_SAMPLE_CHARS]})
