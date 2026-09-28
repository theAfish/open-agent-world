"""Frozen field snapshots and explainable routes, using existing host records.

These helpers do not search, write a world, infer scientific truth, or spend a
budget. Hosts supply authorized Paper documents, persisted search runs and the
source validator; narrative text remains an attributed synthesis to review.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from oaw_library.contracts import PaperMetadata, ResearchBudget, ResearchScopeRevision, Sha256, normalize_source_url
from .evidence import (Evidence, EvidenceSource, Model, SourceValidator, aware_timestamp,
    canonical_sha256, evidence_sha256, validate_evidence_sources)
from .search import SearchRun

DiscoveryState = Literal["unsearched", "searching", "found", "no_results", "filtered_empty", "failed", "cancelled"]
EvidenceState = Literal["none", "abstract_only", "located_unreviewed", "reviewed_support", "conflicted", "insufficient"]


class SnapshotPaper(Model):
    paper_id: str = Field(min_length=1, max_length=200)
    basis: Literal["metadata", "abstract", "fulltext"]
    document_version_id: Sha256 | None = None
    metadata_sha256: Sha256 | None = None
    search_run_ids: list[str] = Field(default_factory=list, max_length=1000)
    inclusion_rationale: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def source_level(self):
        if self.basis == "fulltext" and self.document_version_id is None:
            raise ValueError("A fulltext snapshot source requires its immutable PDF version")
        if self.basis != "fulltext" and self.document_version_id is not None:
            raise ValueError("Metadata/abstract observations must not claim a PDF version")
        if not self.inclusion_rationale.strip() or len(set(self.search_run_ids)) != len(self.search_run_ids):
            raise ValueError("Snapshot source needs a rationale and unique search run IDs")
        return self


class EvidenceReference(Model):
    id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, strict=True)
    sha256: Sha256


class SnapshotClaim(Model):
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20_000)
    basis: Literal["metadata", "abstract", "fulltext"]
    kind: Literal["author_summary", "synthesis", "hypothesis"] = "synthesis"
    paper_ids: list[str] = Field(min_length=1, max_length=100)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    opposing_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=100)
    review_state: Literal["unreviewed", "reviewed"] = "unreviewed"

    @model_validator(mode="after")
    def claim_sources(self):
        support, oppose = set(self.supporting_evidence_ids), set(self.opposing_evidence_ids)
        if not self.text.strip() or support & oppose:
            raise ValueError("A claim needs text and cannot use the same evidence on both sides")
        if self.basis == "fulltext" and not (support or oppose):
            raise ValueError("Fulltext conclusions require located evidence; metadata is insufficient")
        if self.basis != "fulltext" and (support or oppose or self.review_state == "reviewed"):
            raise ValueError("Metadata/abstract summaries cannot claim fulltext evidence review")
        for values in (self.paper_ids, self.supporting_evidence_ids, self.opposing_evidence_ids):
            if len(set(values)) != len(values):
                raise ValueError("Claim source references must be unique")
        return self


class ReadingRecommendation(Model):
    id: str = Field(min_length=1, max_length=128)
    paper_id: str = Field(min_length=1, max_length=200)
    level: Literal["metadata", "abstract", "paragraph"]
    anchor: EvidenceSource | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: Literal["located_evidence", "method_coverage", "conflict", "missing_evidence"]
    rationale: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def locate_paragraph(self):
        if self.level == "paragraph":
            if self.anchor is None or self.anchor.paper_id != self.paper_id or not self.evidence_ids:
                raise ValueError("A paragraph recommendation requires exact evidence on the same Paper")
        elif self.anchor is not None or self.evidence_ids:
            raise ValueError("Metadata/abstract recommendations cannot invent a paragraph locator")
        if not self.rationale.strip():
            raise ValueError("Reading recommendations require an explanation")
        return self


class NarrativeBlock(Model):
    text: str = Field(min_length=1, max_length=20_000)
    claim_ids: list[str] = Field(default_factory=list, max_length=100)
    search_run_ids: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def grounded_narrative(self):
        if not self.text.strip() or not (self.claim_ids or self.search_run_ids):
            raise ValueError("Narrative requires a real claim or search record reference")
        return self


class SearchCoverage(Model):
    request_id: str
    query: str
    state: DiscoveryState
    candidate_count: int = Field(ge=0, strict=True)
    paper_ids: list[str] = Field(default_factory=list)


class FieldSnapshot(Model):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=128)
    scope_id: str = Field(min_length=1, max_length=200)
    scope_revision: int = Field(ge=1, strict=True)
    scope_sha256: Sha256 | None = None
    version: int = Field(ge=1, strict=True)
    created_at: datetime
    cutoff_at: datetime
    sources: list[SnapshotPaper] = Field(default_factory=list, max_length=5000)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=2000)
    search_run_ids: list[str] = Field(min_length=1, max_length=1000)
    search_run_sha256: dict[str, Sha256] = Field(default_factory=dict, max_length=1000)
    coverage: list[SearchCoverage] = Field(default_factory=list, max_length=1000)
    claims: list[SnapshotClaim] = Field(default_factory=list, max_length=1000)
    narrative: list[NarrativeBlock] = Field(default_factory=list, max_length=1000)
    limitations: list[str] = Field(min_length=1, max_length=100)
    core_paper_ids: list[str] = Field(default_factory=list, max_length=500)
    recommendations: list[ReadingRecommendation] = Field(default_factory=list, max_length=500)

    @field_validator("created_at", "cutoff_at")
    @classmethod
    def timestamp(cls, value):
        return aware_timestamp(value)

    @model_validator(mode="after")
    def frozen_references(self):
        if self.cutoff_at > self.created_at:
            raise ValueError("Snapshot cutoff cannot follow its creation")
        for values in (self.sources, self.evidence, self.claims, self.recommendations):
            keys = [item.paper_id if isinstance(item, SnapshotPaper) else item.id for item in values]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate snapshot identity")
        for values in (self.search_run_ids, self.core_paper_ids):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate snapshot source references")
        paper_ids, evidence_ids = {paper.paper_id for paper in self.sources}, {item.id for item in self.evidence}
        claim_ids, run_ids = {claim.id for claim in self.claims}, set(self.search_run_ids)
        if set(self.core_paper_ids) - paper_ids:
            raise ValueError("Core Papers must belong to the frozen source set")
        for claim in self.claims:
            if set(claim.paper_ids) - paper_ids or (set(claim.supporting_evidence_ids) | set(claim.opposing_evidence_ids)) - evidence_ids:
                raise ValueError("Claim refers to sources outside this snapshot")
        for recommendation in self.recommendations:
            if recommendation.paper_id not in paper_ids or set(recommendation.evidence_ids) - evidence_ids:
                raise ValueError("Reading recommendation refers outside this snapshot")
        for block in self.narrative:
            if set(block.claim_ids) - claim_ids or set(block.search_run_ids) - run_ids:
                raise ValueError("Narrative refers to unknown claim or search IDs")
        if any(not item.strip() or len(item) > 5000 for item in self.limitations):
            raise ValueError("Snapshot limitations must explicitly state coverage boundaries")
        return self


def _run_index(records: Sequence[Mapping]) -> dict[str, dict]:
    result = {}
    for value in records:
        value = dict(value)
        identifier = value.get("request_id")
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError("Persisted search records require unique request IDs")
        result[identifier] = value
    return result


def _coverage(record: Mapping, snapshot: FieldSnapshot) -> SearchCoverage:
    if record.get("scope_revision") != snapshot.scope_revision:
        raise ValueError("Search record belongs to another scope revision")
    request = record.get("request", {})
    if request.get("scope_id") != snapshot.scope_id or request.get("scope_revision") != snapshot.scope_revision or request.get("request_id") != record["request_id"]:
        raise ValueError("Search request belongs to another research scope")
    completed = record.get("completed_at")
    if completed is None or record.get("status") not in {"complete", "failed", "cancelled"}:
        raise ValueError("Snapshot can only freeze completed current-scope search records")
    completed = aware_timestamp(datetime.fromisoformat(completed.replace("Z", "+00:00"))) if isinstance(completed, str) else aware_timestamp(completed)
    if completed > snapshot.cutoff_at:
        raise ValueError("Search completed after the snapshot cutoff")
    provider = SearchRun.model_validate(record["provider_run"]) if record.get("provider_run") else None
    if provider:
        if (provider.request_id, provider.scope_id, provider.scope_revision) != (record["request_id"], snapshot.scope_id, snapshot.scope_revision):
            raise ValueError("Provider receipt disagrees with the persisted search identity")
        if provider.status == "running" or provider.completed_at is None:
            raise ValueError("A running provider request is not completed coverage")
        if aware_timestamp(provider.started_at) > aware_timestamp(provider.completed_at):
            raise ValueError("Search response cannot precede its request")
        if aware_timestamp(provider.completed_at) > completed:
            raise ValueError("Provider response follows the persisted completion time")
        if provider.status == "succeeded":
            admitted = record.get("admitted_candidate_count",provider.candidate_count)
            if type(admitted) is not int or not 0 <= admitted <= provider.candidate_count:
                raise ValueError("Invalid host-admitted candidate count")
            state = "found" if admitted else "filtered_empty" if provider.raw_item_count else "no_results"
        elif provider.status == "not_in_crossref":
            state = "no_results"
        else:
            state = provider.status
        candidates = record.get("admitted_candidate_count",provider.candidate_count)
    else:
        if record.get("status") not in {"failed", "cancelled"}:
            raise ValueError("Successful coverage requires the real provider search receipt")
        state, candidates = record["status"], 0
    if state != "found" and record.get("paper_ids"):
        raise ValueError("A failed or empty search cannot supply Paper provenance")
    return SearchCoverage(request_id=record["request_id"], query=str(request.get("query") or request.get("doi") or ""),
        state=state, candidate_count=candidates, paper_ids=list(record.get("paper_ids", [])))


def _evidence_index(records: Sequence[Evidence | dict]) -> dict[str, Evidence]:
    result = {}
    for value in records:
        value = Evidence.model_validate(value)
        if value.id in result:
            raise ValueError("Provide one current revision for each evidence ID")
        result[value.id] = value
    return result


def validate_snapshot(snapshot: FieldSnapshot | dict, *, scope_id: str,
    scope_revision: ResearchScopeRevision | dict, scope_paper_ids: Iterable[str],
    paper_documents: Mapping[str, Mapping], evidence_records: Sequence[Evidence | dict],
    search_runs: Sequence[Mapping], source_validator: SourceValidator,
    allow_synthetic: bool = False) -> FieldSnapshot:
    snapshot = FieldSnapshot.model_validate(snapshot)
    scope = ResearchScopeRevision.model_validate(scope_revision)
    if (snapshot.scope_id, snapshot.scope_revision) != (scope_id, scope.revision):
        raise ValueError("Snapshot does not match the active research scope revision")
    scope_digest = canonical_sha256(scope.model_dump(mode="json"))
    if snapshot.scope_sha256 not in {None, scope_digest}:
        raise ValueError("Frozen scope content changed without a new snapshot version")
    allowed = set(scope_paper_ids) | set(scope.seed_paper_ids)
    runs, evidence = _run_index(search_runs), _evidence_index(evidence_records)
    if set(snapshot.search_run_ids) - runs.keys():
        raise ValueError("Snapshot cites an unknown search run")
    selected_runs = {key: runs[key] for key in snapshot.search_run_ids}
    coverage = [_coverage(record, snapshot) for record in selected_runs.values()]
    run_hashes = {key: canonical_sha256(record) for key, record in selected_runs.items()}
    if snapshot.search_run_sha256 and snapshot.search_run_sha256 != run_hashes:
        raise ValueError("Frozen search records changed")
    if snapshot.coverage and snapshot.coverage != coverage:
        raise ValueError("Discovery coverage must come from actual persisted search results")
    papers = {}
    for paper in snapshot.sources:
        if paper.paper_id not in allowed or paper.paper_id not in paper_documents:
            raise ValueError("Snapshot Paper is missing or outside the authorized scope")
        if set(paper.search_run_ids) - selected_runs.keys():
            raise ValueError("Paper refers to a search not included in this snapshot")
        if not paper.search_run_ids and paper.paper_id not in scope.seed_paper_ids:
            raise ValueError("A non-seed Paper requires its actual search provenance")
        for run_id in paper.search_run_ids:
            if paper.paper_id not in selected_runs[run_id].get("paper_ids", []):
                raise ValueError("Search record did not produce this Paper")
        document = paper_documents[paper.paper_id]
        metadata = PaperMetadata.model_validate(document.get("metadata", {}))
        metadata_hash = canonical_sha256(metadata.model_dump(mode="json"))
        if paper.metadata_sha256 not in {None, metadata_hash}:
            raise ValueError("Frozen Paper metadata changed")
        if metadata.year is not None and ((scope.start_year is not None and metadata.year < scope.start_year) or (scope.end_year is not None and metadata.year > scope.end_year)):
            raise ValueError("Snapshot Paper publication year is outside the research scope")
        if paper.basis == "abstract" and not metadata.source_abstract.strip():
            raise ValueError("An Agent summary is not a source abstract")
        if paper.basis == "fulltext":
            current = document.get("current_document_version_id")
            if current != paper.document_version_id or not any(version.get("id") == current for version in document.get("versions", [])):
                raise ValueError("Fulltext source no longer identifies the current PDF version")
        papers[paper.paper_id] = paper.model_copy(update={"metadata_sha256": metadata_hash})
    checked_evidence = {}
    for reference in snapshot.evidence:
        value = evidence.get(reference.id)
        if value is None or value.revision != reference.revision or evidence_sha256(value) != reference.sha256:
            raise ValueError("Snapshot evidence revision or payload does not match the stored evidence")
        value = validate_evidence_sources(value, source_validator, allow_synthetic=allow_synthetic)
        for source in value.sources:
            if source.paper_id not in papers or papers[source.paper_id].basis != "fulltext" or source.document_version_id != papers[source.paper_id].document_version_id:
                raise ValueError("Evidence is not bound to the snapshot's current fulltext source set")
        checked_evidence[value.id] = value
    claims = []
    for claim in snapshot.claims:
        level = {"metadata": 0, "abstract": 1, "fulltext": 2}
        if any(level[claim.basis] > level[papers[key].basis] for key in claim.paper_ids):
            raise ValueError("Claim exceeds the available source detail")
        if claim.basis == "abstract" and any(not PaperMetadata.model_validate(paper_documents[key].get("metadata", {})).source_abstract.strip() for key in claim.paper_ids):
            raise ValueError("An abstract claim requires an available source abstract")
        used = [checked_evidence[key] for key in [*claim.supporting_evidence_ids, *claim.opposing_evidence_ids]]
        if used:
            if any(not {source.paper_id for source in item.sources} <= set(claim.paper_ids) for item in used):
                raise ValueError("Claim evidence belongs to a Paper not cited by that claim")
            if {source.paper_id for item in used for source in item.sources} != set(claim.paper_ids):
                raise ValueError("Every Paper supporting a fulltext claim requires its located evidence")
            if any(checked_evidence[key].relation != "supports" for key in claim.supporting_evidence_ids) or any(checked_evidence[key].relation != "contradicts" for key in claim.opposing_evidence_ids):
                raise ValueError("Claim support/opposition disagrees with the recorded evidence relation")
            review_state = "reviewed" if all(item.scientific_verification == "reviewed" for item in used) else "unreviewed"
            if claim.review_state == "reviewed" and review_state != "reviewed":
                raise ValueError("An unreviewed source cannot be presented as scientifically reviewed")
            claim = claim.model_copy(update={"review_state": review_state})
        claims.append(claim)
    recommendations = []
    for recommendation in snapshot.recommendations:
        paper = papers[recommendation.paper_id]
        if recommendation.level == "abstract" and (paper.basis == "metadata" or not PaperMetadata.model_validate(paper_documents[paper.paper_id].get("metadata", {})).source_abstract.strip()):
            raise ValueError("An abstract recommendation requires an available source abstract")
        if recommendation.level == "paragraph":
            if paper.basis != "fulltext":
                raise ValueError("A paragraph recommendation requires held fulltext")
            anchors = [anchor for key in recommendation.evidence_ids for anchor in checked_evidence[key].sources]
            needle = recommendation.anchor.model_dump(mode="json", exclude={"status"})
            if not any(anchor.model_dump(mode="json", exclude={"status"}) == needle for anchor in anchors):
                raise ValueError("Recommendation must reuse an exact validated evidence anchor")
            recommendation = recommendation.model_copy(update={"anchor": recommendation.anchor.model_copy(update={"status": "current"})})
        recommendations.append(recommendation)
    result = snapshot.model_dump(mode="json")
    result.update(scope_sha256=scope_digest, search_run_sha256=run_hashes,
        coverage=[item.model_dump(mode="json") for item in coverage],
        sources=[paper.model_dump(mode="json") for paper in papers.values()],
        claims=[claim.model_dump(mode="json") for claim in claims],
        recommendations=[item.model_dump(mode="json") for item in recommendations])
    return FieldSnapshot.model_validate(result)


class SnapshotDiff(Model):
    from_version: int
    to_version: int
    scope_revision_changed: bool
    added_paper_ids: list[str]
    removed_paper_ids: list[str]
    changed_evidence_ids: list[str]
    added_claim_ids: list[str]
    removed_claim_ids: list[str]
    changed_claim_ids: list[str]


def append_snapshot(history: Sequence[FieldSnapshot | dict], candidate: FieldSnapshot | dict, *, expected_version: int) -> tuple[list[FieldSnapshot], SnapshotDiff]:
    snapshots = [FieldSnapshot.model_validate(value).model_copy(deep=True) for value in history]
    candidate = FieldSnapshot.model_validate(candidate).model_copy(deep=True)
    if type(expected_version) is not int or expected_version != len(snapshots):
        raise ValueError("Snapshot version conflict")
    if [snapshot.version for snapshot in snapshots] != list(range(1, len(snapshots) + 1)) or candidate.version != len(snapshots) + 1:
        raise ValueError("Snapshots must retain contiguous immutable versions")
    if any((snapshot.id, snapshot.scope_id) != (candidate.id, candidate.scope_id) for snapshot in snapshots):
        raise ValueError("Snapshot history belongs to a different field/scope")
    if candidate.scope_sha256 is None or set(candidate.search_run_sha256) != set(candidate.search_run_ids) or any(source.metadata_sha256 is None for source in candidate.sources):
        raise ValueError("Validate snapshot sources before appending a frozen version")
    previous = snapshots[-1] if snapshots else None
    if previous and (candidate.scope_revision < previous.scope_revision or candidate.created_at < previous.created_at or candidate.cutoff_at < previous.cutoff_at):
        raise ValueError("New snapshots cannot move the scope or coverage clock backwards")
    old_papers = {source.paper_id for source in previous.sources} if previous else set()
    new_papers = {source.paper_id for source in candidate.sources}
    old_evidence = {item.id: (item.revision, item.sha256) for item in previous.evidence} if previous else {}
    new_evidence = {item.id: (item.revision, item.sha256) for item in candidate.evidence}
    changed_evidence = sorted(key for key in old_evidence.keys() | new_evidence.keys() if old_evidence.get(key) != new_evidence.get(key))
    old_claims = {item.id: item for item in previous.claims} if previous else {}
    new_claims = {item.id: item for item in candidate.claims}
    changed_claims = sorted(key for key in old_claims.keys() & new_claims.keys() if old_claims[key] != new_claims[key] or
        set([*new_claims[key].supporting_evidence_ids, *new_claims[key].opposing_evidence_ids]) & set(changed_evidence))
    difference = SnapshotDiff(from_version=expected_version, to_version=candidate.version,
        scope_revision_changed=bool(previous and previous.scope_revision != candidate.scope_revision),
        added_paper_ids=sorted(new_papers - old_papers), removed_paper_ids=sorted(old_papers - new_papers),
        changed_evidence_ids=changed_evidence, added_claim_ids=sorted(new_claims.keys() - old_claims.keys()),
        removed_claim_ids=sorted(old_claims.keys() - new_claims.keys()), changed_claim_ids=changed_claims)
    return [*snapshots, candidate], difference


class SnapshotFreshness(Model):
    status: Literal["current", "stale"]
    reasons: list[str]


def snapshot_freshness(snapshot: FieldSnapshot | dict, *, scope_id: str, scope_revision: ResearchScopeRevision | dict,
    paper_documents: Mapping[str, Mapping], evidence_records: Sequence[Evidence | dict], search_runs: Sequence[Mapping]) -> SnapshotFreshness:
    snapshot, scope = FieldSnapshot.model_validate(snapshot), ResearchScopeRevision.model_validate(scope_revision)
    reasons = []
    if (snapshot.scope_id, snapshot.scope_revision, snapshot.scope_sha256) != (scope_id, scope.revision, canonical_sha256(scope.model_dump(mode="json"))):
        reasons.append("Research scope revision or boundaries changed")
    for source in snapshot.sources:
        document = paper_documents.get(source.paper_id)
        if document is None:
            reasons.append(f"Paper missing: {source.paper_id}")
            continue
        metadata = PaperMetadata.model_validate(document.get("metadata", {}))
        if source.metadata_sha256 != canonical_sha256(metadata.model_dump(mode="json")):
            reasons.append(f"Paper metadata changed: {source.paper_id}")
        if source.basis == "fulltext" and source.document_version_id != document.get("current_document_version_id"):
            reasons.append(f"PDF version changed: {source.paper_id}")
    evidence, runs = _evidence_index(evidence_records), _run_index(search_runs)
    for reference in snapshot.evidence:
        item = evidence.get(reference.id)
        if item is None or (item.revision, evidence_sha256(item)) != (reference.revision, reference.sha256):
            reasons.append(f"Evidence changed or missing: {reference.id}")
        elif any(source.status != "current" for source in item.sources):
            reasons.append(f"Evidence location requires resolution: {reference.id}")
    for identifier, digest in snapshot.search_run_sha256.items():
        if identifier not in runs or canonical_sha256(runs[identifier]) != digest:
            reasons.append(f"Search record changed or missing: {identifier}")
    return SnapshotFreshness(status="stale" if reasons else "current", reasons=reasons)


def recommend_reading(sources: Sequence[SnapshotPaper | dict], evidence_records: Sequence[Evidence | dict], *, method_paper_ids: Iterable[str] = ()) -> list[ReadingRecommendation]:
    """Deterministic reasons, not learned probabilities or automatic execution."""
    records = _evidence_index(evidence_records)
    methods, result = set(method_paper_ids), []
    for value in sources:
        source = SnapshotPaper.model_validate(value)
        located = [(item, anchor) for item in records.values() for anchor in item.sources if anchor.paper_id == source.paper_id
            and anchor.document_version_id == source.document_version_id and anchor.status == "current"] if source.basis == "fulltext" else []
        if located:
            conflict = any(item.relation == "contradicts" for item, _ in located)
            item, anchor = next(((item, anchor) for item, anchor in located if item.relation == "contradicts"), located[0])
            reason = "conflict" if conflict else "method_coverage" if source.paper_id in methods else "located_evidence"
            rationale = {"conflict": "Inspect the located opposing evidence and its conditions before resolving the disagreement.",
                "method_coverage": "Read the located passage associated with a method in this source set.",
                "located_evidence": "Read the exact passage underlying this evidence record."}[reason]
            result.append(ReadingRecommendation(id=f"read-{source.paper_id}"[:128], paper_id=source.paper_id, level="paragraph",
                anchor=anchor, evidence_ids=[item.id], reason=reason, rationale=rationale))
        else:
            level = "abstract" if source.basis == "abstract" else "metadata"
            result.append(ReadingRecommendation(id=f"read-{source.paper_id}"[:128], paper_id=source.paper_id, level=level,
                reason="missing_evidence", rationale="No validated passage is available; inspect the available source description and obtain/locate fulltext before making detailed method claims."))
    priority = {"conflict": 0, "method_coverage": 1, "located_evidence": 2, "missing_evidence": 3}
    return sorted(result, key=lambda item: (priority[item.reason], item.paper_id))


class RouteCost(Model):
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    provenance: str = Field(min_length=1, max_length=5000)
    source_url: str | None = None

    @field_validator("source_url")
    @classmethod
    def safe_link(cls, value):
        return normalize_source_url(value)

    @model_validator(mode="after")
    def cost_is_not_guessed(self):
        if (self.amount is None) != (self.currency is None) or not self.provenance.strip():
            raise ValueError("Estimated costs require units and provenance; unknown costs stay null")
        return self


class FrontierRoute(Model):
    id: str = Field(min_length=1, max_length=128)
    scope_id: str = Field(min_length=1, max_length=200)
    scope_revision: int = Field(ge=1, strict=True)
    query: str = Field(min_length=1, max_length=1000)
    missing_evidence: list[str] = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=5000)
    budget: ResearchBudget
    cost: RouteCost
    proposed_by: str = Field(min_length=1, max_length=200)
    proposal_kind: Literal["user", "agent_hypothesis", "perspective", "snapshot"]
    source_paper_ids: list[str] = Field(default_factory=list, max_length=100)
    continued_from: str | None = Field(default=None, min_length=1, max_length=128)
    discovery_state: DiscoveryState = "unsearched"
    evidence_state: EvidenceState = "none"

    @model_validator(mode="after")
    def bounded_next_step(self):
        if self.budget.max_searches is None or self.budget.max_searches < 1 or self.budget.max_papers is None:
            raise ValueError("A proposed route requires finite search and Paper budgets")
        if not self.query.strip() or not self.rationale.strip() or any(not item.strip() or len(item) > 5000 for item in self.missing_evidence):
            raise ValueError("A proposed route needs a concrete query, gap and rationale")
        if self.proposal_kind == "perspective" and not self.source_paper_ids:
            raise ValueError("A Perspective route must identify its source Paper")
        return self


def validate_frontier_route(route: FrontierRoute | dict, *, scope_id: str, scope_revision: ResearchScopeRevision | dict,
    scope_paper_ids: Iterable[str]) -> FrontierRoute:
    route, scope = FrontierRoute.model_validate(route), ResearchScopeRevision.model_validate(scope_revision)
    if (route.scope_id, route.scope_revision) != (scope_id, scope.revision):
        raise ValueError("Route belongs to another scope revision")
    if set(route.source_paper_ids) - (set(scope_paper_ids) | set(scope.seed_paper_ids)):
        raise ValueError("Route cites Papers outside its research scope")
    for field in ("max_searches", "max_papers", "max_cost", "max_duration_seconds", "max_parallelism"):
        proposed, ceiling = getattr(route.budget, field), getattr(scope.budget, field)
        if ceiling is not None and (proposed is None or proposed > ceiling):
            raise ValueError(f"Route omits or exceeds the scope's {field} budget")
    if scope.budget.max_cost is not None and route.budget.currency != scope.budget.currency:
        raise ValueError("Route budget currency differs from the approved scope")
    if route.cost.amount is not None and (route.budget.max_cost is None or route.cost.currency != route.budget.currency or route.cost.amount > route.budget.max_cost):
        raise ValueError("Estimated route cost exceeds its explicit monetary budget")
    return route.model_copy(deep=True)
