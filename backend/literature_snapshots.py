"""Host-validated, immutable Scope snapshots; no search or generated synthesis."""
from __future__ import annotations

from datetime import UTC, datetime

from backend.errors import NotFoundError, PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.literature_records import allowed_papers, source_validator
from backend.node_documents import read_document, write_document


def snapshot_payload(value):
    """Keep host audit metadata separate from the strict portable contract."""
    from oaw_literature.snapshots import FieldSnapshot
    return {key: item for key, item in value.items() if key in FieldSnapshot.model_fields}


def _context(services, scope_id):
    if services.world.get_card(scope_id).type != "literature.scope":
        raise ResourceValidationError("Select a literature research scope")
    current = read_document(services, scope_id)
    value = current["value"]
    if not value["revisions"]:
        raise ResourceValidationError("Save the research scope before creating a snapshot")
    papers = {}
    for paper_id in sorted(allowed_papers(value)):
        try:
            if services.world.get_card(paper_id).type == "library.paper":
                document = read_document(services, paper_id)["value"]
                # Validate through the host one Paper at a time; do not retain all
                # uploaded base64 PDFs/thumbnails in the snapshot working set.
                papers[paper_id] = {key: document.get(key) for key in ("metadata", "current_document_version_id", "versions")}
                papers[paper_id]["has_pdf"] = bool(document.get("pdf"))
        except NotFoundError:
            # Deleted scoped sources make retained snapshots stale, not unreadable.
            continue
    return current, papers


def _completed(value, scope_id):
    return [run for run in value["search_runs"]
            if run.get("scope_revision") == value["current_revision"]
            and run.get("request", {}).get("scope_id") == scope_id
            and run.get("request", {}).get("scope_revision") == value["current_revision"]
            and run.get("status") in {"complete", "failed", "cancelled"}
            and run.get("completed_at")]


def _bootstrap(services, scope_id, value, papers, timestamp):
    from oaw_library.contracts import PaperMetadata
    from oaw_literature.evidence import evidence_sha256, validate_evidence_sources
    from oaw_literature.snapshots import recommend_reading

    runs = _completed(value, scope_id)
    if not runs:
        raise ValueError("A snapshot requires an actual completed search in the current scope revision")
    scope = value["revisions"][-1]
    run_ids = [run["request_id"] for run in runs]
    seeds = set(scope["seed_paper_ids"])
    produced = {paper_id for run in runs for paper_id in run.get("paper_ids", [])}
    sources, omitted_papers = [], 0
    for paper_id in sorted((seeds | produced) & allowed_papers(value)):
        document = papers.get(paper_id)
        if document is None:
            omitted_papers += 1
            continue
        metadata = PaperMetadata.model_validate(document.get("metadata", {}))
        if metadata.year is not None and ((scope.get("start_year") is not None and metadata.year < scope["start_year"])
                                         or (scope.get("end_year") is not None and metadata.year > scope["end_year"])):
            omitted_papers += 1
            continue
        digest = document.get("current_document_version_id")
        fulltext = bool(document.get("has_pdf") and digest and any(version.get("id") == digest for version in document.get("versions", [])))
        sources.append({"paper_id": paper_id, "basis": "fulltext" if fulltext else "abstract" if metadata.source_abstract.strip() else "metadata",
                        "document_version_id": digest if fulltext else None,
                        "search_run_ids": [run["request_id"] for run in runs if paper_id in run.get("paper_ids", [])],
                        "inclusion_rationale": "用户纳入的种子文献；具体结论尚待核验。" if paper_id in seeds else "当前范围已完成检索返回的文献；尚未据此认定相关性或科学结论。"})
    by_paper = {item["paper_id"]: item for item in sources}
    validator = source_validator(services, value)
    evidence, omitted_evidence = [], 0
    for item in value["evidence"]:
        try:
            checked = validate_evidence_sources(item, validator)
            if any(anchor.paper_id not in by_paper or by_paper[anchor.paper_id]["basis"] != "fulltext"
                   or by_paper[anchor.paper_id]["document_version_id"] != anchor.document_version_id for anchor in checked.sources):
                raise ValueError("Evidence outside the current snapshot source set")
            evidence.append(checked)
        except (ValueError, PermissionDeniedError, NotFoundError, ResourceValidationError):
            omitted_evidence += 1
    limitations = ["此版本仅冻结当前范围的检索、文献和可定位证据记录，未自动生成科学综述或认定核心文献。",
                   "来源题录、来源摘要与持有全文分别标记；持有全文或定位成功不等于科学核验。",
                   "搜过无结果、失败和取消均保留覆盖状态，不据此推断该方向不存在研究。"]
    if omitted_papers:
        limitations.append(f"未纳入 {omitted_papers} 篇已删除或超出当前年份边界的文献。")
    if omitted_evidence:
        limitations.append(f"未纳入 {omitted_evidence} 条来源失效、不可核对或不属于本次文献集的证据。")
    recommendations = recommend_reading(sources, evidence)
    if len(recommendations) > 500:
        limitations.append("阅读建议仅显示按已定位证据优先排序的前 500 项；完整来源集仍保留。")
    history = value["snapshots"]
    return {"id": history[0]["id"] if history else f"snapshot-{scope_id}", "scope_id": scope_id,
            "scope_revision": value["current_revision"], "version": len(history)+1,
            "created_at": timestamp, "cutoff_at": timestamp, "search_run_ids": run_ids,
            "sources": sources, "evidence": [{"id": item.id, "revision": item.revision, "sha256": evidence_sha256(item)} for item in evidence],
            "claims": [], "core_paper_ids": [],
            "narrative": [{"text": f"冻结 {len(runs)} 条当前范围的已结束检索、{len(sources)} 篇可访问来源和 {len(evidence)} 条当前可定位证据。", "search_run_ids": run_ids}],
            "limitations": limitations, "recommendations": [item.model_dump(mode="json") for item in recommendations[:500]]}


def create_snapshot(services, scope_id, arguments, capability=None):
    """Call inside the host mutation lock after literature.research authorization."""
    from oaw_literature.snapshots import append_snapshot, validate_snapshot
    current, papers = _context(services, scope_id)
    if type(arguments.get("expected_revision")) is not int or arguments["expected_revision"] != current["revision"]:
        raise RevisionConflictError("Reload the research scope before creating a snapshot")
    value, mode = current["value"], arguments.get("mode", "bootstrap")
    history = value["snapshots"]
    if type(arguments.get("expected_version")) is not int or arguments["expected_version"] != len(history):
        raise RevisionConflictError("Snapshot version changed; reload before appending")
    if len(history) >= 100:
        raise ResourceValidationError("This scope already retains 100 immutable snapshot versions")
    if mode == "bootstrap" and capability is not None:
        raise PermissionDeniedError("Desktop record snapshots require the desktop; Agents submit a source-backed version")
    timestamp = datetime.now(UTC)
    try:
        if set(arguments) - {"expected_revision", "expected_version", "mode", "value"}:
            raise ValueError("Unexpected snapshot arguments")
        if mode == "bootstrap":
            if "value" in arguments:
                raise ValueError("Bootstrap uses stored records, not caller-authored snapshot data")
            candidate = _bootstrap(services, scope_id, value, papers, timestamp)
        elif mode == "submit":
            candidate = arguments.get("value")
            if not isinstance(candidate, dict):
                raise ValueError("Submit a FieldSnapshot value")
            from oaw_literature.snapshots import FieldSnapshot
            proposed = FieldSnapshot.model_validate(candidate)
            if proposed.created_at > timestamp or proposed.cutoff_at > timestamp:
                raise ValueError("Snapshot timestamps cannot be in the future")
        else:
            raise ValueError("Choose bootstrap or submit snapshot mode")
        validated = validate_snapshot(candidate, scope_id=scope_id, scope_revision=value["revisions"][-1],
            scope_paper_ids=value["paper_ids"], paper_documents=papers, evidence_records=value["evidence"],
            search_runs=value["search_runs"], source_validator=source_validator(services, value))
        _, difference = append_snapshot([snapshot_payload(item) for item in history], validated,
                                        expected_version=arguments["expected_version"])
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc
    actor = capability.agent_id if capability else "desktop"
    item = {**validated.model_dump(mode="json"), "recorded_by": actor, "recorded_at": timestamp.isoformat(),
            "mode": mode, "diff": difference.model_dump(mode="json")}
    # Copy the old bytes as-is; never revalidate-and-rewrite an earlier version.
    updated = write_document(services, scope_id, {**value, "snapshots": [*history, item]}, current["revision"], actor_id=actor)
    return {"revision": updated["revision"], "version": item["version"], "item": item}


def list_snapshots(services, scope_id):
    """Resolve freshness without mutating frozen history or reading foreign Papers."""
    from oaw_literature.evidence import validate_evidence_sources
    from oaw_literature.snapshots import snapshot_freshness
    current, papers = _context(services, scope_id)
    value = current["value"]
    validator = source_validator(services, value)
    evidence = []
    for item in value["evidence"]:
        try:
            evidence.append(validate_evidence_sources(item, validator, require_current=False))
        except (ValueError, PermissionDeniedError, NotFoundError, ResourceValidationError):
            # Missing evidence must contribute a stale reason, not an invented receipt.
            continue
    completed = {run["request_id"] for run in _completed(value, scope_id)}
    items = []
    for stored in value["snapshots"]:
        snapshot = snapshot_payload(stored)
        freshness = snapshot_freshness(snapshot, scope_id=scope_id, scope_revision=value["revisions"][-1],
            paper_documents=papers, evidence_records=evidence, search_runs=value["search_runs"]).model_dump(mode="json")
        additions = completed - set(snapshot["search_run_ids"])
        if additions:
            freshness = {"status": "stale", "reasons": [*freshness["reasons"], f"New completed current-scope searches: {len(additions)}"]}
        items.append({"snapshot": snapshot, "recorded_by": stored.get("recorded_by", "unknown"),
                      "recorded_at": stored.get("recorded_at"), "mode": stored.get("mode", "submit"),
                      "diff": stored.get("diff"), "freshness": freshness})
    return {"revision": current["revision"], "current_version": len(items), "items": items}
