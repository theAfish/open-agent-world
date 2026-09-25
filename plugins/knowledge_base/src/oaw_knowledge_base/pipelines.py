"""The durable ``oaw.to_markdown`` pipeline.

``backend/node_resources.py`` holds the global node mutation lock for the whole duration
of a resource action, so PDF conversion cannot run inside one. It runs here instead, on
the job thread the MKB client owns, and the card polls ``jobs`` for progress.

Every write uses an id derived from the source with ``uuid5``, so a retried step
re-targets the same rows instead of creating duplicates.
"""
from __future__ import annotations

import uuid

PIPELINE_NAME = "oaw.to_markdown"
NAMESPACE = uuid.UUID("7c5b2f9a-1d64-4a3e-9c8b-0f2a6d31e4c7")


def derived_id(source_id, purpose):
    return uuid.uuid5(NAMESPACE, f"{purpose}:{source_id}")


def _convert(context, state):
    from .markdown import to_markdown

    kb = context.knowledge_base
    source_id = str(state["source_id"])
    source = kb.sources.require(source_id)
    context.check_cancelled()
    context.log(f"Reading {source.filename}")

    data = kb.sources.read_bytes(source_id)
    context.check_cancelled()
    context.log(f"Converting {len(data)} bytes to markdown")
    text, engine, metadata = to_markdown(
        data, source.filename, source.media_type,
        engine=context.parameters.get("engine", "auto"),
        mineru_base_url=context.parameters.get("mineru_base_url"))
    context.check_cancelled()

    artifact_id = derived_id(source_id, "artifact")
    existing = kb.artifacts.get(artifact_id)
    if existing is None:
        kb.artifacts.add_bytes(
            source_id, text.encode("utf-8"), processing_type="MARKDOWN", format="md",
            primary_path=f"{source.filename}.md", metadata=metadata, artifact_id=artifact_id)
    context.log(f"Stored markdown artifact via {engine}")
    return {"artifact_id": str(artifact_id), "engine": engine,
            "characters": metadata["characters"], "truncated": metadata["truncated"]}


def _index(context, state):
    kb = context.knowledge_base
    source_id = str(state["source_id"])
    artifact_id = str(state["artifact_id"])
    collection_id = str(state["collection_id"])
    source = kb.sources.require(source_id)
    text = kb.artifacts.read_bytes(artifact_id).decode("utf-8", errors="replace")
    context.check_cancelled()

    record_id = derived_id(source_id, "record")
    evidence_id = derived_id(source_id, "evidence")
    # The record carries the markdown a projection will read; the evidence link ties it
    # back to the uploaded file so every downstream fact stays traceable (rule 4.2).
    with kb.transaction():
        if kb.records.get(record_id) is None:
            kb.records.create(
                collection_id=collection_id,
                data={"markdown": text, "filename": source.filename,
                      "engine": state.get("engine"), "source_id": source_id},
                summary=f"Markdown of {source.filename}",
                source_metadata={"source_id": source_id, "artifact_id": artifact_id},
                record_id=record_id)
        if kb.evidence.get(evidence_id) is None:
            kb.evidence.create(
                output_type="record", output_id=record_id, source_id=source_id,
                artifact_id=artifact_id, locator={"kind": "document", "path": source.filename},
                excerpt=text[:500], evidence_id=evidence_id)
    context.log("Linked markdown record to its source")
    return {"record_id": str(record_id), "evidence_id": str(evidence_id)}


def build_pipeline():
    from mkb.pipelines import Pipeline, Step

    return Pipeline(
        name=PIPELINE_NAME,
        description="Convert an uploaded source into a markdown record with evidence",
        steps=(
            Step(name="convert", handler=_convert),
            Step(name="index", handler=_index, depends_on=frozenset({"convert"})),
        ),
    )


def register_pipelines(kb):
    kb.pipelines.register(build_pipeline(), replace=True)


def submit_markdown_job(kb, source, collection_id, *, engine="auto", mineru_base_url=None):
    return kb.pipelines.submit(
        PIPELINE_NAME,
        inputs={"source_id": str(source.id), "collection_id": str(collection_id)},
        parameters={"engine": engine, "mineru_base_url": mineru_base_url},
        idempotency_key=f"markdown:{source.id}:{(source.sha256 or '')[:16]}",
    )
