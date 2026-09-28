"""Pure P1 Library invariants; no live profile, provider or PDF service."""
import hashlib
import json

import pytest
from pydantic import ValidationError

from oaw_library.contracts import (
    DocumentVersion, PaperMetadata, ResearchBudget, ResearchScope,
    ResearchScopeRevision, SourceAnchor, TextItemRange, append_scope_revision,
    normalize_doi, normalize_external_ids, paper_identity_keys, quote_sha256,
    source_anchor_status,
)


HASH = hashlib.sha256(b"original PDF bytes").hexdigest()
OTHER_HASH = hashlib.sha256(b"changed PDF bytes").hexdigest()


def version(**changes):
    return DocumentVersion(sha256=HASH, pages=4, **changes)


def anchor(**changes):
    value = {"document_version_id": HASH, "document_sha256": HASH, "page": 2,
             "quote": "precise evidence", "rects": [[.1, .2, .3, .02]]}
    return SourceAnchor(**{**value, **changes})


def test_metadata_only_is_valid_without_fabricated_identity_or_abstract():
    metadata = PaperMetadata(title="A field overview", year="", agent_abstract="An agent interpretation")
    assert metadata.year is None
    assert metadata.source_abstract == ""
    assert metadata.agent_abstract == "An agent interpretation"
    assert paper_identity_keys(metadata) == ()
    assert PaperMetadata.model_validate(json.loads(metadata.model_dump_json())) == metadata


def test_strong_identifiers_normalize_without_title_based_merging():
    bare = PaperMetadata(title="Original", doi="DOI: 10.1000/ABC(2024)", external_ids={"OpenAlex": "https://openalex.org/w123", "arxiv": "https://arxiv.org/abs/2602.16372v2"})
    url = PaperMetadata(title="Different display title", doi="https://doi.org/10.1000/ABC%282024%29", external_ids={"openalex": "W123", "arxiv": "2602.16372v2"})
    assert paper_identity_keys(bare) == paper_identity_keys(url)
    assert normalize_doi("http://dx.doi.org/10.1000/ABC") == "10.1000/abc"
    assert PaperMetadata(year="2024").year == 2024
    with pytest.raises(ValueError):
        normalize_external_ids({"ArXiv": "2602.16372v2", "arxiv": "2602.16372v3"})


@pytest.mark.parametrize("value", ["javascript:alert(1)", "data:text/plain,hello", "file:///private.pdf", "ftp://example.org/paper", "https://user:secret@example.org/paper", "https://example.org\\@other.test/paper", "https://example.org/a\nfile", "https://example.org:99999/paper", "https://.", "https://%zz/paper"])
def test_source_links_reject_unsafe_or_ambiguous_forms(value):
    with pytest.raises(ValidationError):
        PaperMetadata(source_url=value)
    with pytest.raises(ValidationError):
        PaperMetadata(abstract_source_url=value)


@pytest.mark.parametrize("doi", ["not-a-doi", "https://example.org/10.1000/test", "https://doi.org/10.1000/test?other=1", "10.1000/", "10.1000/space suffix"])
def test_invalid_doi_is_not_promoted_to_an_identity(doi):
    with pytest.raises(ValueError):
        normalize_doi(doi)


def test_external_namespaces_and_identity_formats_are_explicit():
    assert normalize_external_ids({"pmcid": "pmc123", "pmid": "456", "semantic_scholar": "A" * 40}) == {"pmcid": "PMC123", "pmid": "456", "semantic_scholar": "a" * 40}
    for invalid in ({"unknown": "whatever"}, {"pmid": "https://example.org/123"}, {"openalex": "A123"}, {"arxiv": "definitely-not-an-arxiv-id"}):
        with pytest.raises(ValueError):
            normalize_external_ids(invalid)


def test_document_versions_use_bytes_identity_and_keep_unknown_import_dates_unknown():
    first = version(filename="one.pdf")
    renamed = version(filename="renamed.pdf")
    assert first.id == renamed.id == HASH
    assert first.imported_at is None
    assert "pdf" not in first.model_dump(mode="json")
    assert DocumentVersion.model_validate_json(first.model_dump_json()) == first
    with pytest.raises(ValidationError):
        DocumentVersion(id=OTHER_HASH, sha256=HASH, pages=4)
    with pytest.raises(ValidationError):
        DocumentVersion(sha256=HASH.upper(), pages=4)
    with pytest.raises(ValidationError):
        DocumentVersion(sha256=HASH, pages=0)
    with pytest.raises(ValidationError):
        version(imported_at="2026-09-24T10:00:00")


def test_quote_hash_is_exact_and_cannot_claim_a_different_quote():
    composed = anchor(quote="café")
    assert composed.quote_sha256 == quote_sha256("café")
    assert quote_sha256("café") != quote_sha256("cafe\u0301")
    with pytest.raises(ValidationError):
        anchor(quote="new quote", quote_sha256=composed.quote_sha256)
    with pytest.raises(ValidationError):
        anchor(document_sha256=OTHER_HASH)


def test_anchor_status_checks_referenced_version_page_and_quote_without_scientific_verification():
    source = anchor(paper_id="old-paper-id")
    assert source_anchor_status(source, version()) == "current"
    assert source_anchor_status(source, None) == "missing"
    assert source_anchor_status(source, DocumentVersion(sha256=OTHER_HASH, pages=4)) == "needs_relocation"
    assert source_anchor_status(source, DocumentVersion(sha256=HASH, pages=1)) == "needs_relocation"
    assert source_anchor_status(source, version(), resolved_quote="different evidence") == "needs_relocation"
    assert source_anchor_status(source, version(), resolved_quote=source.quote) == "current"
    assert "verified" not in source.model_dump_json()
    assert source.paper_id == "old-paper-id"


@pytest.mark.parametrize("rect", [[.9, .2, .2, .1], [.1, .99, .1, .02], [-.1, 0, .2, .2], [.1, .1, 0, .2], [.1, float("nan"), .2, .2], [True, .1, .2, .2], [.1, .2, .3]])
def test_invalid_anchor_geometry_fails_instead_of_silently_clamping(rect):
    with pytest.raises(ValidationError):
        anchor(rects=[rect])


def test_utf16_ranges_preserve_surrogate_pairs_and_detect_text_index_changes():
    value = anchor(quote="🧪", text_parser_version="pdfjs-items-v1", text_ranges=[{"text_item_index": 0, "start_offset": 1, "end_offset": 3}])
    assert value.text_offset_unit == "utf16"
    assert source_anchor_status(value, version(), text_items=["A🧪B"], resolved_quote="🧪") == "current"
    assert source_anchor_status(value, version(), text_parser_version="pdfjs-items-v2") == "needs_relocation"
    assert source_anchor_status(value, version(), text_items=[]) == "needs_relocation"
    assert source_anchor_status(value, version(), text_items=["A"]) == "needs_relocation"
    assert source_anchor_status(value, version(), text_items=["A\ud800B"]) == "needs_relocation"
    split = anchor(quote="", text_parser_version="pdfjs-items-v1", text_ranges=[{"text_item_index": 0, "start_offset": 1, "end_offset": 2}])
    assert source_anchor_status(split, version(), text_items=["A🧪B"]) == "needs_relocation"
    with pytest.raises(ValidationError):
        anchor(text_ranges=[{"text_item_index": 0, "start_offset": 0, "end_offset": 2}])
    with pytest.raises(ValidationError):
        TextItemRange(text_item_index=0, start_offset=2, end_offset=1)


@pytest.mark.parametrize("changes", [{"max_searches": -1}, {"max_papers": True}, {"max_cost": float("inf"), "currency": "USD"}, {"max_cost": 10}, {"max_parallelism": 0}, {"max_duration_seconds": -1}])
def test_budget_boundaries_reject_ambiguous_or_unbounded_numeric_inputs(changes):
    with pytest.raises(ValidationError):
        ResearchBudget(**changes)


def test_scope_revisions_are_retained_without_mutating_previous_context():
    empty = ResearchScope(id="scope-1")
    one = ResearchScopeRevision(revision=1, question="Which methods apply?", start_year=2020, end_year=2026, seed_paper_ids=["paper-1"], budget={"max_searches": 5})
    first = append_scope_revision(empty, one, expected_revision=0)
    second = append_scope_revision(first, {"revision": 2, "question": "Which methods reproduce?", "budget": {"max_searches": 10}}, expected_revision=1)
    assert empty.revisions == []
    assert first.current_revision == 1 and len(first.revisions) == 1
    assert second.revisions[0].budget.max_searches == 5
    assert second.revisions[1].budget.max_searches == 10
    assert ResearchScope.model_validate_json(second.model_dump_json()) == second
    with pytest.raises(ValueError):
        append_scope_revision(second, {"revision": 3, "question": "Later"}, expected_revision=1)
    with pytest.raises(ValueError):
        append_scope_revision(second, {"revision": 3, "question": "Later"}, expected_revision=2.0)
    with pytest.raises(ValidationError):
        ResearchScope(current_revision=2, revisions=[one])
    with pytest.raises(ValidationError):
        ResearchScopeRevision(revision=1, question="Time reversed", start_year=2026, end_year=2020)
    with pytest.raises(ValidationError):
        ResearchScopeRevision(revision=1, question="Duplicate seeds", seed_paper_ids=["paper-1", "paper-1"])
