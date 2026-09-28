"""Source freshness, scientific review and revision identities remain distinct."""
from datetime import UTC, datetime

import pytest

from oaw_library.contracts import quote_sha256
from oaw_literature.evidence import (Evidence, EvidenceSource, evidence_sha256,
    record_scientific_review, revise_evidence, validate_evidence_sources)


def source(**changes):
    quote = "Synthetic fixture: independent observations use the sample standard deviation."
    return {"id": "source-1", "paper_id": "synthetic-paper", "document_version_id": "a" * 64,
        "document_sha256": "a" * 64, "page": 1, "quote": quote, "quote_sha256": quote_sha256(quote),
        "rects": [[.1, .2, .7, .05]], "source_kind": "synthetic_fixture", **changes}


def evidence(**changes):
    return Evidence.model_validate({"id": "evidence-1", "claim": "Estimate mean uncertainty only for independent observations",
        "kind": "agent_inference", "conditions": ["Independent observations"], "sources": [source()],
        "extracted_by": "fixture-extractor", **changes})


def review(value, **changes):
    return {"id": "review-1", "evidence_sha256": evidence_sha256(value), "reviewer": "fixture-reviewer",
        "reviewed_at": datetime.now(UTC), "decision": "insufficient", "rationale": "The synthetic fixture is not a scientific paper.", **changes}


@pytest.mark.parametrize("change", [
    {"paper_id": ""}, {"document_sha256": "b" * 64}, {"page": 0}, {"quote_sha256": "0" * 64},
    {"quote": ""}, {"rects": [], "text_ranges": []},
    {"text_ranges": [{"text_item_index": 0, "start_offset": 0, "end_offset": 2}], "text_parser_version": None},
])
def test_evidence_source_requires_original_identity_quote_hash_and_locator(change):
    with pytest.raises(ValueError):
        EvidenceSource.model_validate(source(**change))


def test_current_anchor_does_not_implicitly_scientifically_review_evidence():
    value = validate_evidence_sources(evidence(), lambda anchor: "current", allow_synthetic=True)
    assert value.sources[0].status == "current"
    assert value.scientific_verification == "unreviewed"
    with pytest.raises(ValueError, match="derived"):
        Evidence.model_validate({**value.model_dump(mode="json"), "scientific_verification": "reviewed"})
    reviewed = record_scientific_review(value, review(value))
    assert reviewed.scientific_verification == "reviewed"
    assert reviewed.scientific_reviews[0].decision == "insufficient"
    assert Evidence.model_validate(reviewed.model_dump(mode="json")) == reviewed


def test_source_validator_checks_every_paper_and_preserves_failure_boundaries():
    value = evidence(sources=[source(), source(id="source-2", paper_id="another-fixture")])
    visited = []

    def resolve(anchor):
        visited.append(anchor.paper_id)
        return "current" if anchor.paper_id == "synthetic-paper" else "needs_relocation"

    with pytest.raises(ValueError, match="needs_relocation"):
        validate_evidence_sources(value, resolve, allow_synthetic=True)
    assert visited == ["synthetic-paper", "another-fixture"]
    viewed = validate_evidence_sources(value, resolve, allow_synthetic=True, require_current=False)
    assert [item.status for item in viewed.sources] == ["current", "needs_relocation"]
    assert value.sources[1].status == "current"  # caller's original snapshot is not mutated
    with pytest.raises(ValueError, match="boolean"):
        validate_evidence_sources(value, lambda anchor: True, allow_synthetic=True)
    with pytest.raises(ValueError, match="production"):
        validate_evidence_sources(value, lambda anchor: "current")


def test_review_is_bound_to_claim_revision_and_cannot_be_retargeted():
    original = evidence()
    reviewed = record_scientific_review(original, review(original))
    assert record_scientific_review(reviewed, reviewed.scientific_reviews[0]) == reviewed
    with pytest.raises(ValueError, match="immutable"):
        record_scientific_review(reviewed, review(reviewed, decision="supports"))
    with pytest.raises(ValueError, match="different evidence"):
        Evidence.model_validate({**reviewed.model_dump(mode="json"), "claim": "A stronger unsupported claim"})
    revised = revise_evidence(reviewed, {"claim": "A narrower claim"}, expected_revision=1)
    assert revised.revision == 2 and revised.scientific_verification == "unreviewed"
    assert reviewed.revision == 1 and reviewed.scientific_verification == "reviewed"
    with pytest.raises(ValueError, match="revision conflict"):
        revise_evidence(revised, {"claim": "Stale mutation"}, expected_revision=1)
    with pytest.raises(ValueError, match="separate authority"):
        revise_evidence(revised, {"scientific_reviews": [review(original)]}, expected_revision=2)


def test_stale_location_does_not_rewrite_the_historical_scientific_review():
    original = evidence()
    reviewed = record_scientific_review(original, review(original))
    stale = validate_evidence_sources(reviewed, lambda anchor: "missing", allow_synthetic=True, require_current=False)
    assert stale.sources[0].status == "missing"
    assert stale.scientific_verification == "reviewed"
    assert evidence_sha256(stale) == evidence_sha256(reviewed)
    assert stale.scientific_reviews == reviewed.scientific_reviews
