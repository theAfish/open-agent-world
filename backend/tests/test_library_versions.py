"""Additive Paper lineage, local source binding and explicit write authority."""
import base64
from copy import deepcopy
import hashlib
from uuid import uuid4

import pymupdf
import pytest

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import DocumentActionRequest, invoke_document_action
from backend.state import StateContext
from backend.world.models import CardCreate
from backend.tests.conftest import create_node
from backend.tests.test_tool_projection import connect, invoke, tools
from oaw_library import PDFJS_TEXT_INDEX_VERSION, PaperDocument, import_pdf
from oaw_library.contracts import quote_sha256


def pdf_bytes(pages=2):
    with pymupdf.open() as document:
        for number in range(1, pages + 1):
            document.new_page().insert_text((50, 50), f"Source evidence on page {number}")
        return document.tobytes()


def document(client, paper):
    response = client.get(f"/api/nodes/{paper['id']}/document")
    assert response.status_code == 200, response.text
    return response.json()


def action(client, paper, operation, arguments, revision=None):
    if revision is None:
        revision = document(client, paper)["revision"]
    return client.post(f"/api/nodes/{paper['id']}/actions/{operation}", json={
        "arguments": arguments, "expected_revision": revision})


def upload(client, paper, raw, **arguments):
    response = action(client, paper, "import", {"pdf": base64.b64encode(raw).decode(),
        "filename": "original.pdf", **arguments})
    assert response.status_code == 200, response.text
    return response.json()


def source_anchor(value, **changes):
    digest = value["current_document_version_id"]
    return {"id": "selection-1", "document_version_id": digest, "document_sha256": digest,
        "page": 2, "quote": "Source evidence on page 2", "rects": [[.1, .2, .3, .02]], **changes}


def test_legacy_read_adds_unknown_date_manifest_without_migration_write(client):
    paper = create_node(client, "library.paper")
    raw = pdf_bytes()
    old = {"filename": "saved.pdf", "pdf": base64.b64encode(raw).decode(), "pages": 2,
        "text": ["first", "second"], "page": 2, "notes": "Keep my notes", "study_layout": True,
        "annotations": [{"id": "old-excerpt", "page": 2, "text": "second", "rects": [[.1, .2, .3, .04]], "custom_legacy": "keep"}]}
    unchanged = deepcopy(old)
    services = client.app.state.services
    scope = services.card_state.scope(paper["id"])
    services.state.set(scope, "document", old, expected_revision=document(client, paper)["revision"])
    before = services.state.resolve(StateContext((scope,)), "document")
    loaded = document(client, paper)
    projected = loaded["value"]
    digest = hashlib.sha256(raw).hexdigest()
    assert projected["current_document_version_id"] == digest
    assert projected["versions"] == [{"id": digest, "sha256": digest, "filename": "saved.pdf", "pages": 2,
        "imported_at": None, "text_parser_version": None, "kind": "main", "version_label": ""}]
    assert projected["page"] == 2 and projected["study_layout"] is True
    assert projected["notes"] == old["notes"]
    assert projected["annotations"] == [{**old["annotations"][0], "document_version_id": digest}]
    assert "source_anchor" not in projected["annotations"][0]
    after = services.state.resolve(StateContext((scope,)), "document")
    assert after.revision == before.revision == loaded["revision"]
    assert after.value == unchanged and old == unchanged


def test_metadata_only_paper_supports_normalized_metadata_without_fake_pdf(client):
    paper = create_node(client, "library.paper")
    result = action(client, paper, "metadata", {"metadata": {
        "title": "Solid electrolyte", "authors": ["A. Author"], "year": "2026",
        "doi": "https://doi.org/10.1234/MATERIALS", "source_abstract": "Published abstract",
        "abstract_source_url": "https://publisher.example/article", "agent_abstract": "Unverified agent summary"}})
    assert result.status_code == 200, result.text
    value = result.json()["value"]
    assert value["metadata"]["doi"] == "10.1234/materials"
    assert value["metadata"]["year"] == 2026
    assert value["metadata"]["source_abstract"] != value["metadata"]["agent_abstract"]
    assert not value["pdf"] and not value["versions"] and value["current_document_version_id"] is None
    changed = action(client, paper, "metadata", {"metadata": {"title": "Corrected title"}})
    assert changed.status_code == 200
    assert changed.json()["value"]["metadata"]["authors"] == ["A. Author"]
    assert action(client, paper, "read", {"view": "metadata"}).status_code == 200
    assert action(client, paper, "read", {"page": 1}).status_code == 422


@pytest.mark.parametrize("metadata", [
    {"source_url": "javascript:alert(1)"}, {"abstract_source_url": "https://name:secret@example.org/p"},
    {"external_ids": {"unknown_provider": "123"}}, {"doi": "https://untrusted.example/10.1234/test"},
])
def test_invalid_metadata_rejected_without_revision_change(client, metadata):
    paper = create_node(client, "library.paper")
    before = document(client, paper)
    assert action(client, paper, "metadata", {"metadata": metadata}).status_code == 422
    assert document(client, paper) == before


def test_identical_pdf_reimport_preserves_reading_and_manifest_exactly(client):
    paper = create_node(client, "library.paper")
    raw = pdf_bytes()
    upload(client, paper, raw)
    response = action(client, paper, "annotate", {"page": 2, "notes": "Durable notes", "annotation": {
        "id": "kept", "text": "Source evidence", "rects": [[.1, .1, .2, .02]], "comment": "My analysis",
        "learning": True, "position": {"x": -42, "y": 95}}})
    assert response.status_code == 200, response.text
    assert action(client, paper, "annotate", {"study_positions": {"kept": {"x": 11, "y": 22}}}).status_code == 200
    assert action(client, paper, "metadata", {"metadata": {"title": "Known Paper"}, "reading_status": "close_read"}).status_code == 200
    before = document(client, paper)["value"]
    again = upload(client, paper, raw, filename="network-retry.pdf")["value"]
    assert again == before
    assert len(again["versions"]) == 1
    assert again["versions"][0]["imported_at"] is not None
    assert again["versions"][0]["text_parser_version"].startswith("pymupdf/")
    assert "pdf" not in again["versions"][0]


def test_replacement_retains_manifests_and_stale_sources_but_clears_current_reading(client):
    paper = create_node(client, "library.paper")
    first = pdf_bytes()
    value = upload(client, paper, first)["value"]
    old_hash = value["current_document_version_id"]
    anchor = source_anchor(value)
    response = action(client, paper, "annotate", {"page": 2, "notes": "Old notes", "annotation": {
        "id": "old", "text": anchor["quote"], "rects": anchor["rects"], "source_anchor": anchor}})
    assert response.status_code == 200, response.text
    assert action(client, paper, "evidence", {"evidence": {"id": "claim-1", "claim": "Check transport mechanism",
        "kind": "agent_inference", "source_anchor_id": anchor["id"]}}).status_code == 200
    new_value = upload(client, paper, first + b"\n% revised original bytes\n")["value"]
    assert len(new_value["versions"]) == 2
    assert new_value["current_document_version_id"] != old_hash
    assert new_value["page"] == 1 and new_value["annotations"] == [] and new_value["notes"] == ""
    assert new_value["reading_status"] == "unread" and new_value["study_layout"] is False
    assert new_value["source_anchors"][0]["document_version_id"] == old_hash
    assert new_value["source_anchors"][0]["status"] == "needs_relocation"
    assert new_value["evidence"][0]["review_status"] == "unreviewed"
    history = f"/api/nodes/{paper['id']}/document/binaries/pdf/{old_hash}"
    assert client.get(history).content == first
    archived = client.get(history + "/snapshot")
    assert archived.status_code == 200, archived.text
    assert archived.json()["value"]["annotations"][0]["id"] == "old"
    assert archived.json()["value"]["notes"] == "Old notes"
    assert "pdf" not in archived.json()["value"]
    stale = action(client, paper, "annotate", {"page": 2, "annotation": {
        "id": "old", "text": anchor["quote"], "rects": anchor["rects"], "source_anchor": anchor}})
    assert stale.status_code == 422


def test_source_ranges_survive_annotation_edits_without_using_backend_text_parser(client):
    paper = create_node(client, "library.paper")
    value = upload(client, paper, pdf_bytes())["value"]
    anchor = source_anchor(value, text_parser_version=PDFJS_TEXT_INDEX_VERSION,
        text_ranges=[{"text_item_index": 0, "start_offset": 0, "end_offset": 24}])
    response = action(client, paper, "annotate", {"page": 2, "annotation": {
        "id": "ranged", "text": anchor["quote"], "rects": anchor["rects"], "source_anchor": anchor}})
    assert response.status_code == 200, response.text
    annotation = response.json()["value"]["annotations"][0]
    stored_anchor = annotation.pop("source_anchor")
    annotation["comment"] = "New comment from a client that omits source anchor"
    edited = action(client, paper, "annotate", {"page": 2, "annotation": annotation})
    assert edited.status_code == 200, edited.text
    updated = edited.json()["value"]["annotations"][0]
    assert updated["source_anchor"] == stored_anchor
    assert stored_anchor["quote_sha256"] == quote_sha256(anchor["quote"])
    assert stored_anchor["text_offset_unit"] == "utf16"
    assert stored_anchor["text_parser_version"] != value["versions"][0]["text_parser_version"]


@pytest.mark.parametrize("changes", [
    {"document_sha256": "0" * 64, "document_version_id": "0" * 64},
    {"page": 3}, {"quote_sha256": "0" * 64}, {"rects": [[.9, .2, .3, .1]]},
    {"text_parser_version": "pymupdf/page-text-v1", "text_ranges": [{"text_item_index": 0, "start_offset": 0, "end_offset": 2}]},
    {"text_parser_version": PDFJS_TEXT_INDEX_VERSION, "text_ranges": [{"text_item_index": 0, "start_offset": 2, "end_offset": 1}]},
])
def test_source_anchor_must_bind_current_bytes_page_and_selector_contract(client, changes):
    paper = create_node(client, "library.paper")
    value = upload(client, paper, pdf_bytes())["value"]
    before = document(client, paper)
    result = action(client, paper, "source_anchor", {"anchor": source_anchor(value, **changes)})
    assert result.status_code == 422, result.text
    assert document(client, paper) == before


def test_anchor_quote_must_match_annotation_and_legacy_annotation_acquires_geometry_only_anchor(client):
    paper = create_node(client, "library.paper")
    value = upload(client, paper, pdf_bytes())["value"]
    rejected = action(client, paper, "annotate", {"page": 2, "annotation": {
        "text": "Different text", "source_anchor": source_anchor(value)}})
    assert rejected.status_code == 422
    result = action(client, paper, "annotate", {"page": 2, "annotation": {
        "id": "legacy-client", "text": "Source evidence", "rects": [[.1, .2, .3, .02]]}})
    assert result.status_code == 200, result.text
    annotation = result.json()["value"]["annotations"][0]
    assert annotation["document_version_id"] == value["current_document_version_id"]
    assert annotation["source_anchor"]["text_ranges"] == []
    assert annotation["source_anchor"]["text_parser_version"] is None


def test_source_derived_evidence_is_linked_unreviewed_and_idempotent(client):
    paper = create_node(client, "library.paper")
    value = upload(client, paper, pdf_bytes())["value"]
    record = {"id": "finding", "claim": "An inference requiring review", "kind": "agent_inference", "source_anchor_id": "selection-1"}
    assert action(client, paper, "evidence", {"evidence": record}).status_code == 422
    assert action(client, paper, "source_anchor", {"anchor": source_anchor(value)}).status_code == 200
    for claim in ("Initial inference", "Revised inference"):
        result = action(client, paper, "evidence", {"evidence": {**record, "claim": claim}})
        assert result.status_code == 200, result.text
    assert result.json()["value"]["evidence"] == [{**record, "claim": "Revised inference", "review_status": "unreviewed"}]
    assert action(client, paper, "evidence", {"evidence": {**record, "review_status": "verified"}}).status_code == 422
    assert action(client, paper, "evidence", {"evidence": {"id": "missing", "claim": "Unanchored", "kind": "author_statement"}}).status_code == 422


def test_reusing_annotation_id_after_replacement_does_not_retarget_old_evidence(client):
    paper = create_node(client, "library.paper")
    raw = pdf_bytes()
    upload(client, paper, raw)
    item = {"id": "stable-ui-id", "text": "Old quote", "rects": [[.1, .2, .3, .02]]}
    first = action(client, paper, "annotate", {"page": 1, "annotation": item})
    assert first.status_code == 200, first.text
    old_anchor = first.json()["value"]["annotations"][0]["source_anchor"]
    record = {"id": "old-evidence", "claim": "An old-source inference", "kind": "agent_inference", "source_anchor_id": old_anchor["id"]}
    assert action(client, paper, "evidence", {"evidence": record}).status_code == 200
    current = upload(client, paper, raw + b"\n% new version\n")["value"]
    forged_reuse = source_anchor(current, id=old_anchor["id"], page=1, quote="New quote")
    assert action(client, paper, "annotate", {"page": 1, "annotation": {
        **item, "text": "New quote", "source_anchor": forged_reuse}}).status_code == 422
    second = action(client, paper, "annotate", {"page": 1, "annotation": {**item, "text": "New quote"}})
    assert second.status_code == 200, second.text
    value = second.json()["value"]
    assert value["annotations"][0]["source_anchor"]["id"] != old_anchor["id"]
    assert value["evidence"][0]["source_anchor_id"] == old_anchor["id"]
    retained = next(anchor for anchor in value["source_anchors"] if anchor["id"] == old_anchor["id"])
    assert retained["quote"] == "Old quote" and retained["status"] == "needs_relocation"


def test_current_version_cannot_disagree_with_original_bytes():
    value = import_pdf({}, {"pdf": base64.b64encode(pdf_bytes()).decode()})
    with pytest.raises(ValueError, match="uploaded bytes"):
        PaperDocument.model_validate({**value, "current_document_version_id": "0" * 64})
    with pytest.raises(ValueError, match="Duplicate PDF"):
        PaperDocument.model_validate({**value, "versions": value["versions"] * 2})
    with pytest.raises(ValueError, match="different PDF version"):
        PaperDocument.model_validate({**value, "annotations": [{"id": "stale", "document_version_id": "0" * 64}]})


def test_agent_writes_require_live_explicit_paper_grant_and_revision(client):
    paper = create_node(client, "library.paper", name="Source")
    agent = create_node(client, "agent")
    assert "update_paper" not in tools(client, agent)[1]
    edge = connect(client, agent, paper, "library.read")
    provider, definitions = tools(client, agent)
    assert "read_paper" in definitions and "update_paper" not in definitions
    services = client.app.state.services
    read_capability = services.capabilities.capability_for_id(agent["id"], f"library.read:{paper['id']}")
    current_revision = document(client, paper)["revision"]

    async def try_write_with_reader():
        return await invoke_document_action(services, paper["id"], "metadata", DocumentActionRequest(
            arguments={"metadata": {"title": "Forbidden"}}, expected_revision=current_revision),
            capability=read_capability)

    with pytest.raises(PermissionDeniedError):
        client.portal.call(try_write_with_reader)
    assert client.patch(f"/api/edges/{edge['id']}", json={"relationship": "library.write"}).status_code == 200
    provider, definitions = tools(client, agent)
    initial = invoke(client, provider, agent, definitions["read_paper"], target=paper["id"], view="metadata")
    assert initial["has_pdf"] is False and "pdf" not in initial and "revision" in initial
    result = invoke(client, provider, agent, definitions["update_paper"], target=paper["id"], operation="metadata",
        expected_revision=initial["revision"], metadata={"title": "Agent supplied", "doi": "10.1234/P1"})
    assert result["metadata"]["doi"] == "10.1234/p1" and "pdf" not in result
    with pytest.raises(RevisionConflictError):
        invoke(client, provider, agent, definitions["update_paper"], target=paper["id"], operation="metadata",
            expected_revision=initial["revision"], metadata={"title": "Stale overwrite"})
    # Null explicitly clears a metadata property; an omitted title remains intact.
    cleared = invoke(client, provider, agent, definitions["update_paper"], target=paper["id"], operation="metadata",
        expected_revision=result["revision"], metadata={"doi": None})
    assert cleared["metadata"]["doi"] is None and cleared["metadata"]["title"] == "Agent supplied"
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        invoke(client, provider, agent, definitions["update_paper"], target=paper["id"], operation="metadata",
            expected_revision=cleared["revision"], metadata={"title": "Revoked grant"})


def test_research_membership_does_not_grant_paper_write(client):
    # Library regions are legacy managed containers, restored rather than
    # created through the user-facing node endpoint.
    restored = client.portal.call(client.app.state.services.restore_card,
        CardCreate(id=str(uuid4()), type="library.region"))
    region = restored.model_dump(mode="json")
    create_node(client, "library.paper", parent_id=region["id"])
    agent = create_node(client, "agent", parent_id=region["id"])
    connect(client, agent, region, "library.research")
    assert "read_paper" not in tools(client, agent)[1]
    assert "update_paper" not in tools(client, agent)[1]


def test_write_capability_cannot_forge_cross_paper_anchor_or_import(client):
    paper = create_node(client, "library.paper")
    other = create_node(client, "library.paper")
    agent = create_node(client, "agent")
    value = upload(client, paper, pdf_bytes())["value"]
    connect(client, agent, paper, "library.write")
    provider, definitions = tools(client, agent)
    before = document(client, paper)
    with pytest.raises(ResourceValidationError, match="authorized Paper"):
        invoke(client, provider, agent, definitions["update_paper"], target=paper["id"], operation="source_anchor",
            expected_revision=before["revision"], anchor=source_anchor(value, paper_id=other["id"]))
    services = client.app.state.services
    capability = services.capabilities.capability_for_id(agent["id"], f"library.write:{paper['id']}")

    async def try_import():
        return await invoke_document_action(services, paper["id"], "import", DocumentActionRequest(
            arguments={"pdf": base64.b64encode(pdf_bytes()).decode()}, expected_revision=before["revision"]), capability=capability)

    with pytest.raises(PermissionDeniedError):
        client.portal.call(try_import)
    assert document(client, paper) == before
