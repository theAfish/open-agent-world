"""Frozen source scopes, honest coverage and bounded explainable reading routes."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from oaw_library.contracts import ResearchScopeRevision
from oaw_literature.evidence import evidence_sha256, revise_evidence
from oaw_literature.search import SearchRun
from oaw_literature.snapshots import (FieldSnapshot, FrontierRoute, SnapshotPaper, append_snapshot,
    recommend_reading, snapshot_freshness, validate_frontier_route, validate_snapshot)
from backend.tests.test_literature_evidence import evidence, source

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=UTC)


def scope(**changes):
    return ResearchScopeRevision.model_validate({"revision": 1, "question": "Estimate uncertainty for independent synthetic samples",
        "boundaries": "A controlled fixture, not a scientific field assessment", "start_year": 2020, "end_year": 2026,
        "budget": {"max_searches": 10, "max_papers": 20}, **changes})


def search_record(status="succeeded", candidate_count=1, raw_item_count=1, **changes):
    request = {"scope_id": "scope-1", "scope_revision": 1, "request_id": "query-1", "query": "standard error independent samples"}
    run = SearchRun(operation="search", request_id="query-1", scope_id="scope-1", scope_revision=1,
        query=request["query"], requested_rows=5, request_url="https://api.crossref.org/works",
        request_parameters={"query.bibliographic": request["query"], "rows": 5},
        started_at=NOW - timedelta(minutes=2), completed_at=NOW - timedelta(minutes=1),
        status=status, raw_item_count=raw_item_count, candidate_count=candidate_count)
    return {"request_id": "query-1", "scope_revision": 1, "request": request,
        "started_at": (NOW - timedelta(minutes=2)).isoformat(), "completed_at": (NOW - timedelta(minutes=1)).isoformat(),
        "status": "complete", "provider_run": run.model_dump(mode="json"),
        "paper_ids": ["synthetic-paper"] if candidate_count else [], **changes}


def fixture():
    item = evidence(relation="supports")
    papers = {"synthetic-paper": {"metadata": {"title": "Synthetic statistical fixture", "year": 2026,
        "source_abstract": "An explicit synthetic source description", "agent_abstract": "A separate agent summary"},
        "current_document_version_id": "a" * 64, "versions": [{"id": "a" * 64, "sha256": "a" * 64, "pages": 1}]}}
    snapshot = {"id": "field-1", "scope_id": "scope-1", "scope_revision": 1, "version": 1,
        "created_at": NOW, "cutoff_at": NOW, "search_run_ids": ["query-1"],
        "sources": [{"paper_id": "synthetic-paper", "basis": "fulltext", "document_version_id": "a" * 64,
            "search_run_ids": ["query-1"], "inclusion_rationale": "Known-input statistical demonstration"}],
        "evidence": [{"id": item.id, "revision": item.revision, "sha256": evidence_sha256(item)}],
        "claims": [{"id": "claim-1", "text": "The method requires independent observations", "basis": "fulltext",
            "paper_ids": ["synthetic-paper"], "supporting_evidence_ids": [item.id], "limitations": ["Synthetic demonstration only"]}],
        "narrative": [{"text": "This bounded example illustrates source tracing.", "claim_ids": ["claim-1"]}],
        "limitations": ["One synthetic fixture and one simulated search receipt; no real field coverage claim."],
        "core_paper_ids": ["synthetic-paper"],
        "recommendations": [{"id": "read-1", "paper_id": "synthetic-paper", "level": "paragraph", "anchor": source(),
            "evidence_ids": [item.id], "reason": "located_evidence", "rationale": "Check the exact independence assumption."}]}
    inputs = {"scope_id": "scope-1", "scope_revision": scope(), "scope_paper_ids": ["synthetic-paper"],
        "paper_documents": papers, "evidence_records": [item], "search_runs": [search_record()],
        "source_validator": lambda anchor: "current", "allow_synthetic": True}
    return snapshot, inputs


def freshness_args(inputs):
    return {key: value for key, value in inputs.items() if key in {"scope_id", "scope_revision", "paper_documents", "evidence_records", "search_runs"}}


def test_validated_snapshot_freezes_exact_sources_without_scientific_upgrade():
    snapshot, inputs = fixture()
    result = validate_snapshot(snapshot, **inputs)
    assert result.scope_sha256 and result.sources[0].metadata_sha256 and result.search_run_sha256["query-1"]
    assert result.claims[0].review_state == "unreviewed"
    assert result.coverage[0].state == "found"
    assert "scope_sha256" not in snapshot
    assert FieldSnapshot.model_validate(result.model_dump(mode="json")) == result
    assert snapshot_freshness(result, **freshness_args(inputs)).status == "current"


@pytest.mark.parametrize("fault", ["foreign-paper", "wrong-run-binding", "wrong-scope", "unknown-run", "missing-provider", "stale-provider", "late-search"])
def test_snapshot_rejects_unscoped_or_unbacked_search_claims(fault):
    snapshot, inputs = fixture()
    if fault == "foreign-paper":
        inputs["scope_paper_ids"] = []
    elif fault == "wrong-run-binding":
        inputs["search_runs"][0]["paper_ids"] = []
    elif fault == "wrong-scope":
        inputs["search_runs"][0]["request"]["scope_id"] = "other-scope"
    elif fault == "unknown-run":
        snapshot["search_run_ids"] = ["invented"]
    elif fault == "missing-provider":
        inputs["search_runs"][0].pop("provider_run")
    elif fault == "stale-provider":
        inputs["search_runs"][0]["status"] = "completed_stale"
    else:
        inputs["search_runs"][0]["completed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, **inputs)


@pytest.mark.parametrize("provider,raw,count,state", [
    ("succeeded", 0, 0, "no_results"), ("succeeded", 4, 0, "filtered_empty"),
    ("failed", 0, 0, "failed"), ("cancelled", 0, 0, "cancelled"),
])
def test_empty_filtered_failed_and_cancelled_coverage_are_distinct(provider, raw, count, state):
    snapshot, inputs = fixture()
    snapshot.update(sources=[], evidence=[], claims=[], core_paper_ids=[], recommendations=[],
        narrative=[{"text": "A recorded search outcome is available; it does not prove a scientific gap.", "search_run_ids": ["query-1"]}])
    inputs["search_runs"] = [search_record(provider, count, raw)]
    result = validate_snapshot(snapshot, **inputs)
    assert result.coverage[0].state == state
    assert result.claims == []


def test_metadata_or_agent_summary_cannot_be_promoted_to_source_abstract_or_fulltext():
    snapshot, inputs = fixture()
    snapshot["sources"][0].update(basis="abstract", document_version_id=None)
    snapshot.update(evidence=[], recommendations=[])
    snapshot["claims"][0].update(basis="abstract", supporting_evidence_ids=[])
    result = validate_snapshot(snapshot, **inputs)
    assert result.claims[0].basis == "abstract" and result.claims[0].review_state == "unreviewed"
    inputs["paper_documents"]["synthetic-paper"]["metadata"]["source_abstract"] = ""
    with pytest.raises(ValueError, match="Agent summary"):
        validate_snapshot(snapshot, **inputs)
    snapshot["claims"][0].update(basis="fulltext")
    with pytest.raises(ValueError, match="Fulltext conclusions"):
        validate_snapshot(snapshot, **inputs)


def test_holding_pdf_does_not_invent_an_unavailable_source_abstract():
    snapshot, inputs = fixture()
    snapshot.update(evidence=[], recommendations=[])
    snapshot["claims"][0].update(basis="abstract", supporting_evidence_ids=[])
    inputs["paper_documents"]["synthetic-paper"]["metadata"]["source_abstract"] = ""
    with pytest.raises(ValueError, match="available source abstract"):
        validate_snapshot(snapshot, **inputs)


def test_failed_search_cannot_sneak_in_a_paper_binding():
    snapshot, inputs = fixture()
    inputs["search_runs"] = [search_record("failed", 1, 1)]
    with pytest.raises(ValueError, match="cannot supply Paper"):
        validate_snapshot(snapshot, **inputs)


@pytest.mark.parametrize("fault", ["evidence-revision", "stale-location", "forged-paragraph", "support-reversed", "review-claim", "invented-narrative"])
def test_fulltext_claims_and_paragraphs_require_exact_validated_evidence(fault):
    snapshot, inputs = fixture()
    if fault == "evidence-revision":
        snapshot["evidence"][0]["revision"] = 2
    elif fault == "stale-location":
        inputs["source_validator"] = lambda anchor: "needs_relocation"
    elif fault == "forged-paragraph":
        snapshot["recommendations"][0]["anchor"]["rects"] = [[.5, .5, .2, .1]]
    elif fault == "support-reversed":
        item = evidence(relation="contradicts")
        inputs["evidence_records"] = [item]
        snapshot["evidence"][0]["sha256"] = evidence_sha256(item)
    elif fault == "review-claim":
        snapshot["claims"][0]["review_state"] = "reviewed"
    else:
        snapshot["narrative"][0]["claim_ids"] = ["invented-claim"]
    with pytest.raises(ValueError):
        validate_snapshot(snapshot, **inputs)


def test_scope_pdf_evidence_and_search_changes_invalidate_freshness():
    snapshot, inputs = fixture()
    frozen = validate_snapshot(snapshot, **inputs)
    for kind in ("scope", "pdf", "evidence", "search", "metadata"):
        changed = deepcopy(freshness_args(inputs))
        if kind == "scope":
            changed["scope_revision"] = scope(revision=2, question="Another question")
        elif kind == "pdf":
            changed["paper_documents"]["synthetic-paper"]["current_document_version_id"] = "b" * 64
        elif kind == "evidence":
            changed["evidence_records"] = [revise_evidence(inputs["evidence_records"][0], {"claim": "Narrower condition"}, expected_revision=1)]
        elif kind == "search":
            changed["search_runs"][0]["paper_ids"] = []
        else:
            changed["paper_documents"]["synthetic-paper"]["metadata"]["title"] = "Corrected metadata"
        freshness = snapshot_freshness(frozen, **changed)
        assert freshness.status == "stale" and freshness.reasons


def test_append_preserves_old_snapshot_and_reports_real_evidence_diff():
    snapshot, inputs = fixture()
    first = validate_snapshot(snapshot, **inputs)
    history, initial = append_snapshot([], first, expected_version=0)
    assert initial.added_paper_ids == ["synthetic-paper"]
    old_json = history[0].model_dump(mode="json")
    revised = revise_evidence(inputs["evidence_records"][0], {"claim": "Independent observations with finite variance"}, expected_revision=1)
    second = deepcopy(snapshot)
    second["version"] = 2
    second["evidence"][0].update(revision=2, sha256=evidence_sha256(revised))
    second = validate_snapshot(second, **{**inputs, "evidence_records": [revised]})
    added, difference = append_snapshot(history, second, expected_version=1)
    assert len(added) == 2 and len(history) == 1 and history[0].model_dump(mode="json") == old_json
    assert difference.changed_evidence_ids == [revised.id]
    assert difference.changed_claim_ids == ["claim-1"]
    added[0].limitations.append("Caller edit to returned copy")
    assert history[0].model_dump(mode="json") == old_json
    with pytest.raises(ValueError, match="version conflict"):
        append_snapshot(history, second, expected_version=0)
    with pytest.raises(ValueError, match="Validate snapshot"):
        append_snapshot([], snapshot, expected_version=0)


def test_recommendations_use_located_conflict_and_explicit_missing_evidence_reasons():
    fulltext = SnapshotPaper(paper_id="synthetic-paper", basis="fulltext", document_version_id="a" * 64, inclusion_rationale="fixture")
    abstract = SnapshotPaper(paper_id="abstract-paper", basis="abstract", inclusion_rationale="metadata-only candidate")
    items = recommend_reading([abstract, fulltext], [evidence(relation="contradicts")])
    assert items[0].reason == "conflict" and items[0].level == "paragraph" and items[0].anchor.page == 1
    assert items[1].reason == "missing_evidence" and items[1].level == "abstract" and items[1].anchor is None
    method = recommend_reading([fulltext], [evidence(relation="supports")], method_paper_ids=[fulltext.paper_id])
    assert method[0].reason == "method_coverage"
    assert "probability" not in method[0].model_dump()


def route(**changes):
    return FrontierRoute.model_validate({"id": "route-1", "scope_id": "scope-1", "scope_revision": 1,
        "query": "independent observations standard error assumptions", "missing_evidence": ["An independently checked source passage"],
        "rationale": "Resolve the remaining condition before method reuse", "budget": {"max_searches": 2, "max_papers": 5},
        "cost": {"provenance": "Not estimated; no cost has been incurred"}, "proposed_by": "fixture-agent",
        "proposal_kind": "agent_hypothesis", **changes})


def test_route_discovery_and_evidence_state_are_separate_and_budgeted():
    proposed = route(discovery_state="no_results", evidence_state="insufficient")
    checked = validate_frontier_route(proposed, scope_id="scope-1", scope_revision=scope(), scope_paper_ids=[])
    assert checked.discovery_state == "no_results" and checked.evidence_state == "insufficient"
    assert checked.cost.amount is None and checked.cost.currency is None
    with pytest.raises(ValueError, match="max_searches"):
        validate_frontier_route(route(budget={"max_searches": 11, "max_papers": 5}), scope_id="scope-1", scope_revision=scope(), scope_paper_ids=[])
    with pytest.raises(ValueError, match="another scope"):
        validate_frontier_route(route(scope_revision=2), scope_id="scope-1", scope_revision=scope(), scope_paper_ids=[])
    with pytest.raises(ValueError, match="source Paper"):
        route(proposal_kind="perspective")
    with pytest.raises(ValueError, match="Estimated route cost"):
        validate_frontier_route(route(cost={"amount": 1.0, "currency": "USD", "provenance": "Provider estimate"}), scope_id="scope-1", scope_revision=scope(), scope_paper_ids=[])
