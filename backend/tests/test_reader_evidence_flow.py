"""Reader evidence actions use isolated fixture PDFs and TestClient worlds."""
import pytest

from backend.errors import PermissionDeniedError
from backend.literature_records import review_evidence
from backend.tests.test_literature_records_service import (
    anchors, document, evidence, invoke, pdf_bytes, setup_scope, upload,
)
from oaw_literature.evidence import evidence_sha256


def relocate(client, scope, paper, version, **changes):
    return invoke(client, scope, "paper", {"paper_id": paper, "view": "relocate", "page": 2,
        "selected_text": "independent samples", "document_version_id": version, **changes})


def test_reader_relocation_returns_host_paragraph_without_trusting_browser_locator(client):
    scope, paper, _, uploaded = setup_scope(client)
    before = document(client, scope)
    response = relocate(client, scope, paper, uploaded["value"]["current_document_version_id"],
        rects=[[0, 0, 1, 1]], source_anchor={"id": "invented", "quote": "invented"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["sources"] == anchors(client, scope, paper).json()["sources"]
    assert result["match_status"] == "unique_text_match"
    assert result["matching_source_ids"] == [result["sources"][0]["id"]]
    assert result["confirmation_required"] is True
    assert document(client, scope) == before


def test_unmatched_reader_text_stays_manual_and_stale_or_foreign_selection_rejected(client):
    scope, paper, _, uploaded = setup_scope(client)
    version = uploaded["value"]["current_document_version_id"]
    result = relocate(client, scope, paper, version, selected_text="Absent invented research result.").json()
    assert result["match_status"] == "manual_selection_required" and result["matching_source_ids"] == []
    assert result["sources"] and result["confirmation_required"]
    assert relocate(client, scope, paper, version, selected_text=" ").status_code == 422
    other_scope, _, _, _ = setup_scope(client)
    assert relocate(client, other_scope, paper, version).status_code == 403
    upload(client, paper, pdf_bytes(replacement=True))
    assert relocate(client, scope, paper, version).status_code == 409


def record_fixture(client):
    scope, paper, _, _ = setup_scope(client)
    source = anchors(client, scope, paper).json()["sources"][0]
    recorded = invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source)})
    assert recorded.status_code == 200, recorded.text
    return scope, paper, recorded.json()["item"]


def review_args(item, **changes):
    return {"evidence_id": item["id"], "item_revision": item["revision"], "decision": "insufficient",
        "rationale": "The fixture describes reporting but provides no validation results.", **changes}


def test_explicit_review_binds_host_identity_time_and_content_without_promoting_support(client):
    scope, _, item = record_fixture(client)
    response = invoke(client, scope, "review", review_args(item))
    assert response.status_code == 200, response.text
    reviewed = response.json()["item"]
    assert reviewed["scientific_verification"] == "reviewed"
    assert reviewed["relation"] == "insufficient"
    review = reviewed["scientific_reviews"][0]
    assert review["reviewer"] == "desktop" and review["reviewed_at"].endswith("Z")
    assert review["evidence_sha256"] == evidence_sha256(item)
    assert review["decision"] == "insufficient"
    revised = invoke(client, scope, "record", {"kind": "evidence", "item_revision": item["revision"],
        "value": evidence(item["sources"][0], kind="author_statement", relation="supports")})
    assert revised.status_code == 200, revised.text
    assert revised.json()["item"]["scientific_reviews"] == []
    assert revised.json()["item"]["scientific_verification"] == "unreviewed"
    assert invoke(client, scope, "review", review_args(item)).status_code == 409


@pytest.mark.parametrize("changes", [{"reviewer": "invented"}, {"reviewed_at": "2000-01-01T00:00:00Z"},
    {"rationale": " "}, {"decision": "verified"}])
def test_review_rejects_forged_authority_or_empty_decision_without_mutation(client, changes):
    scope, _, item = record_fixture(client)
    before = document(client, scope)
    response = invoke(client, scope, "review", review_args(item, **changes))
    assert response.status_code == 422, response.text
    assert document(client, scope) == before


def test_review_rejects_agent_authority_and_stale_source(client):
    scope, paper, item = record_fixture(client)
    with pytest.raises(PermissionDeniedError):
        review_evidence(client.app.state.services, scope, review_args(item), capability=object())
    upload(client, paper, pdf_bytes(replacement=True))
    before = document(client, scope)
    response = invoke(client, scope, "review", review_args(item))
    assert response.status_code == 422, response.text
    assert document(client, scope) == before
