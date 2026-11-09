"""The evidence chain of the experiment workflow: original file → record → insight.

Three plugin tables already hold the links (``oaw_kb_experiment_bundle_source``,
``oaw_kb_experiment_record_source``, ``oaw_kb_insight_record``); this module walks
them from any end and returns the same shape every time — the sources, records and
insights involved, each record naming its sources and each insight its records — so
a person or an agent can go from a published insight down to the retained bytes, or
from one uploaded file up to everything that was ever concluded from it.

A source is identified by its MKB id **and** its sha256: the id names the upload,
the hash names the content. Record links store the hash they were made against, so
a chain can be checked (``intact``) rather than trusted.
"""
from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .actions import Request, _bounded, _client, _record_link
from .errors import KnowledgeError


def source_identity(kb, source_id, *, source=None):
    """Everything that identifies one retained original and says whether its bytes
    are still there."""
    source = source or kb.sources.get(source_id)
    if source is None:
        return {"id": str(source_id), "missing": True}
    metadata = source.metadata or {}
    markdown = next((a for a in kb.artifacts.list(source_id=source.id, limit=10)
                     if a.processing_type == "MARKDOWN"), None)
    link = _record_link(kb, markdown.id) if markdown is not None else None
    try:
        available = kb.sources.content_exists(source.id)
    except Exception:  # an external reference has no managed bytes
        available = False
    return {
        "id": str(source.id), "missing": False,
        "filename": source.filename, "media_type": source.media_type, "size": source.size,
        "sha256": source.sha256, "status": source.status, "uri": source.uri,
        "uploaded_by": metadata.get("uploaded_by"), "uploaded_at": source.created_at,
        "workflow": metadata.get("workflow") or "project",
        "bundle_id": metadata.get("bundle_id") or kb.oaw_experiments.bundle_of_source(source.id),
        "content_available": available,
        "markdown": None if markdown is None else {
            "artifact_id": str(markdown.id), "size": markdown.size,
            "engine": (markdown.metadata or {}).get("engine"),
            "record_id": str(link.output_id) if link else None},
    }


def _record_node(kb, record_id, links, sources):
    record = kb.oaw_experiments.get(record_id)
    cited = []
    for link in links:
        source = sources.get(link["source_id"])
        intact = bool(source and not source.get("missing") and source.get("content_available")
                      and (link.get("sha256") in (None, source.get("sha256"))))
        cited.append({"source_id": link["source_id"], "artifact_id": link.get("artifact_id"),
                      "sha256": link.get("sha256"), "intact": intact})
    if record is None:
        return {"id": str(record_id), "missing": True, "sources": cited}
    return {"id": record["id"], "missing": False, "name": record["name"],
            "status": record["status"], "schema_id": record["schema_id"],
            "bundle_id": record["bundle_id"], "revision": record["revision"],
            "updated_at": record["updated_at"], "sources": cited}


def _insight_node(kb, insight_id, links):
    insight = kb.oaw_insights.get(insight_id)
    records = [{"record_id": link["record_id"], "role": link["role"]} for link in links]
    if insight is None:
        return {"id": str(insight_id), "missing": True, "records": records}
    return {"id": insight["id"], "missing": False, "kind": insight["kind"],
            "title": insight["title"], "status": insight["status"],
            "revision": insight["revision"], "updated_at": insight["updated_at"],
            "records": records}


def _assemble(kb, root, source_links_by_record, insight_links):
    """``source_links_by_record``: record_id → its source links (every record in the
    chain); ``insight_links``: every insight↔record link in the chain."""
    source_ids = sorted({link["source_id"] for links in source_links_by_record.values() for link in links})
    sources = {source_id: source_identity(kb, source_id) for source_id in source_ids}
    records = [_record_node(kb, record_id, links, sources)
               for record_id, links in sorted(source_links_by_record.items())]
    by_insight = {}
    for link in insight_links:
        by_insight.setdefault(link["insight_id"], []).append(link)
    insights = [_insight_node(kb, insight_id, links) for insight_id, links in sorted(by_insight.items())]
    bundle_ids = {record["bundle_id"] for record in records if record.get("bundle_id")}
    bundle_ids |= {source["bundle_id"] for source in sources.values() if source.get("bundle_id")}
    bundles = [bundle for bundle in (kb.oaw_experiments.get_bundle(b) for b in sorted(bundle_ids)) if bundle]
    return {"root": root, "sources": list(sources.values()), "records": records,
            "insights": insights, "bundles": bundles}


def trace_source(kb, source_id):
    source_id = str(source_id)
    links = kb.oaw_experiments.links_for_sources([source_id])
    by_record = {}
    for link in links:
        by_record.setdefault(link["record_id"], []).append(link)
    # Each cited record's *other* sources too: a record read from three files is
    # evidence-linked to all three, and the chain should say so.
    for record_id in list(by_record):
        by_record[record_id] = kb.oaw_experiments.sources_for(record_id)
    insight_links = kb.oaw_insights.links_for_records(list(by_record))
    chain = _assemble(kb, {"type": "source", "id": source_id}, by_record, insight_links)
    if source_id not in {source["id"] for source in chain["sources"]}:
        chain["sources"].insert(0, source_identity(kb, source_id))
    return chain


def trace_record(kb, record_id):
    record_id = str(record_id)
    if kb.oaw_experiments.get(record_id) is None:
        raise KnowledgeError(f"Experiment record not found: {record_id}")
    by_record = {record_id: kb.oaw_experiments.sources_for(record_id)}
    insight_links = kb.oaw_insights.links_for_records([record_id])
    return _assemble(kb, {"type": "record", "id": record_id}, by_record, insight_links)


def trace_insight(kb, insight_id):
    insight = kb.oaw_insights.require(insight_id)
    evidence = kb.oaw_insights.evidence_for(insight["id"])
    by_record = {link["record_id"]: kb.oaw_experiments.sources_for(link["record_id"]) for link in evidence}
    insight_links = [{"insight_id": insight["id"], **link} for link in evidence]
    return _assemble(kb, {"type": "insight", "id": insight["id"]}, by_record, insight_links)


class Lineage(Request):
    """Exactly one of the three ids: the end of the chain to start from."""
    source_id: str | None = None
    record_id: str | None = None
    insight_id: str | None = None

    @model_validator(mode="after")
    def _one_root(self):
        given = [name for name in ("source_id", "record_id", "insight_id") if getattr(self, name)]
        if len(given) != 1:
            raise ValueError("Give exactly one of source_id, record_id or insight_id")
        return self

    @property
    def root(self) -> Literal["source", "record", "insight"]:
        return "source" if self.source_id else "record" if self.record_id else "insight"


def lineage(context, arguments):
    """Walk the evidence chain from one source, record or insight: the retained
    originals (id, sha256, whether the bytes are still there), the records read from
    them and the insights resting on those records."""
    request = Lineage.model_validate(arguments)
    kb = _client(context)
    if request.source_id:
        chain = trace_source(kb, request.source_id)
    elif request.record_id:
        chain = trace_record(kb, request.record_id)
    else:
        chain = trace_insight(kb, request.insight_id)
    return _bounded(chain)
