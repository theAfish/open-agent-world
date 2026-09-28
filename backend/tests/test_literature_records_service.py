"""Exercise host evidence binding with real local PDF bytes in temporary worlds.

The PDF prose is a controlled fixture, never a scientific validation or an
external publication. Every mutation uses the isolated TestClient data root.
"""
import base64
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import io
import json
from zipfile import ZipFile

import pymupdf
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.node_documents import read_document, write_document
from backend.tests.conftest import create_node
from oaw_library.contracts import quote_sha256
from oaw_literature.search import CrossrefClient


@pytest.fixture(autouse=True)
def no_metadata_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Local evidence/Method operations must not query an external index")
    monkeypatch.setattr(CrossrefClient, "search", forbidden)
    monkeypatch.setattr(CrossrefClient, "resolve", forbidden)


def document(client, node_id):
    response = client.get(f"/api/nodes/{node_id}/document")
    assert response.status_code == 200, response.text
    return response.json()


def invoke(client, scope_id, operation, arguments=None, revision=None):
    return client.post(f"/api/literature/scopes/{scope_id}/{operation}", json={
        "expected_revision": document(client, scope_id)["revision"] if revision is None else revision,
        "arguments": arguments or {}})


def pdf_bytes(rotation=0, replacement=False):
    with pymupdf.open() as pdf:
        pdf.new_page(width=600, height=800).insert_text((60, 80), "Controlled evidence fixture. No research claim.")
        page = pdf.new_page(width=600, height=800)
        page.insert_text((60, 100), "Replacement source." if replacement else "Measure independent samples and report their units.")
        page.set_rotation(rotation)
        return pdf.tobytes()


def upload(client, paper_id, raw):
    response = client.post(f"/api/nodes/{paper_id}/actions/import", json={
        "expected_revision": document(client, paper_id)["revision"],
        "arguments": {"pdf": base64.b64encode(raw).decode(), "filename": "controlled-source.pdf"}})
    assert response.status_code == 200, response.text
    return response.json()


def setup_scope(client, rotation=0):
    paper_id = create_node(client, "library.paper", name="Controlled local source")["id"]
    raw = pdf_bytes(rotation)
    uploaded = upload(client, paper_id, raw)
    scope_id = create_node(client, "literature.scope")["id"]
    response = client.post(f"/api/nodes/{scope_id}/actions/revise", json={
        "expected_revision": document(client, scope_id)["revision"],
        "arguments": {"question": "How is uncertainty reported in this controlled example?",
            "seed_paper_ids": [paper_id], "budget": {"max_searches": 0, "max_papers": 1}}})
    assert response.status_code == 200, response.text
    return scope_id, paper_id, raw, uploaded


def anchors(client, scope_id, paper_id, page=2):
    return invoke(client, scope_id, "paper", {"paper_id": paper_id, "view": "anchors", "page": page})


def evidence(source, **changes):
    return {"id": "local-evidence", "claim": "The fixture requests units; its scientific adequacy is unreviewed.",
        "kind": "user_hypothesis", "relation": "insufficient", "sources": [source],
        "extracted_by": "caller-cannot-impersonate-reviewer", **changes}


def method(source, **changes):
    return {"id": "local-method", "name": "Controlled source method draft",
        "purpose": "Keep a traceable draft without claiming an executed or validated method.", "sources": [source],
        "inputs": [{"name": "samples", "data_type": "array[number]", "description": "Independent samples"}],
        "outputs": [{"name": "report", "data_type": "object", "description": "Units and measurements"}],
        "steps": [{"instruction": "Record measurements with their units.", "origin": "source", "source_anchor_ids": [source["id"]]}],
        "missing": ["No executable implementation or acceptance baseline was supplied."], **changes}


def test_real_pdf_paragraphs_bind_identity_quote_and_reader_location(client):
    scope, paper, raw, uploaded = setup_scope(client)
    response = anchors(client, scope, paper)
    assert response.status_code == 200, response.text
    resolved = response.json()
    source = resolved["sources"][0]
    digest = hashlib.sha256(raw).hexdigest()
    assert source["paper_id"] == paper and source["page"] == 2
    assert source["document_version_id"] == source["document_sha256"] == digest
    assert source["document_version_id"] == uploaded["value"]["current_document_version_id"]
    assert source["quote"].strip() == "Measure independent samples and report their units."
    assert source["quote_sha256"] == quote_sha256(source["quote"])
    assert source["coordinate_space"] == "pdfjs-default-viewport-normalized-v1"
    assert source["text_parser_version"].startswith("pymupdf/")
    assert source["status"] == "current" and resolved["coverage"] == "text_blocks"
    assert all(0 <= number <= 1 for rect in source["rects"] for number in rect)
    assert anchors(client, scope, paper).json()["sources"] == resolved["sources"]
    recorded = invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source)})
    assert recorded.status_code == 200, recorded.text
    item = recorded.json()["item"]
    assert item["extracted_by"] == "desktop" and item["scientific_verification"] == "unreviewed"
    assert item["sources"][0] == source
    assert {key: item["sources"][0][key] for key in ("page", "document_version_id", "rects")} == {
        "page": 2, "document_version_id": digest, "rects": source["rects"]}


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_pdf_paragraph_geometry_matches_default_viewport(client, rotation):
    scope, paper, raw, _ = setup_scope(client, rotation)
    response = anchors(client, scope, paper)
    assert response.status_code == 200, response.text
    source = response.json()["sources"][0]
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        page = pdf[1]
        bounds = pymupdf.Rect(page.get_text("blocks")[0][:4]) * page.rotation_matrix
        expected = [bounds.x0 / page.rect.width, bounds.y0 / page.rect.height,
                    bounds.width / page.rect.width, bounds.height / page.rect.height]
    assert source["page_rotation"] == rotation
    assert source["rects"][0] == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("tamper", ["quote", "quote_hash", "rect", "version", "id", "parser", "rotation", "text_ranges"])
def test_forged_source_fields_are_rejected_without_scope_mutation(client, tamper):
    scope, paper, _, _ = setup_scope(client)
    source = anchors(client, scope, paper).json()["sources"][0]
    changed = deepcopy(source)
    if tamper == "quote":
        changed["quote"] = "A claim that is absent from the PDF."
        changed["quote_sha256"] = quote_sha256(changed["quote"])
    elif tamper == "quote_hash": changed["quote_sha256"] = "0" * 64
    elif tamper == "rect": changed["rects"] = [[.2, .3, .1, .1]]
    elif tamper == "version": changed.update(document_version_id="0" * 64, document_sha256="0" * 64)
    elif tamper == "id": changed["id"] = "another-paragraph"
    elif tamper == "parser": changed["text_parser_version"] = "invented/v9"
    elif tamper == "rotation": changed["page_rotation"] = 90
    elif tamper == "text_ranges": changed["text_ranges"] = [{"text_item_index": 0, "start_offset": 0, "end_offset": 2}]
    before = document(client, scope)
    response = invoke(client, scope, "record", {"kind": "evidence", "value": evidence(changed)})
    assert response.status_code == 422, response.text
    assert document(client, scope) == before


def test_foreign_paper_and_stale_document_sources_are_rejected(client):
    scope, paper, _, _ = setup_scope(client)
    other_scope, foreign, _, _ = setup_scope(client)
    source = anchors(client, other_scope, foreign).json()["sources"][0]
    assert anchors(client, scope, foreign).status_code == 403
    assert invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source)}).status_code == 403
    source = anchors(client, scope, paper).json()["sources"][0]
    upload(client, paper, pdf_bytes(replacement=True))
    response = invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source)})
    assert response.status_code == 422, response.text
    assert document(client, scope)["value"]["evidence"] == []
    metadata_only = create_node(client, "library.paper")["id"]
    # Scope membership is explicit; a metadata-only Paper has no selectable PDF page.
    services = client.app.state.services
    current = read_document(services, scope)
    write_document(services, scope, {**current["value"], "paper_ids": [metadata_only]}, current["revision"])
    assert anchors(client, scope, metadata_only, 1).status_code == 422


def test_scientific_review_cannot_be_forged_or_carried_into_a_new_revision(client):
    scope, paper, _, _ = setup_scope(client)
    source = anchors(client, scope, paper).json()["sources"][0]
    forged = evidence(source, scientific_reviews=[{"id": "fake", "reviewer": "invented"}])
    assert invoke(client, scope, "record", {"kind": "evidence", "value": forged}).status_code == 422
    assert invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source, scientific_verification="reviewed")}).status_code == 422
    initial = invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source)})
    assert initial.status_code == 200, initial.text
    # Simulate a legitimate previous host review, then exercise the public update route.
    from oaw_literature.evidence import record_scientific_review, evidence_sha256
    reviewed = record_scientific_review(initial.json()["item"], {"id": "real-review",
        "reviewer": "actual-desktop-reviewer", "reviewed_at": datetime.now(UTC),
        "evidence_sha256": evidence_sha256(initial.json()["item"]),
        "decision": "insufficient", "rationale": "Controlled fixture only."})
    services = client.app.state.services
    current = read_document(services, scope)
    write_document(services, scope, {**current["value"], "evidence": [reviewed.model_dump(mode="json")]}, current["revision"])
    attempted = invoke(client, scope, "record", {"kind": "evidence", "item_revision": 1, "value": reviewed.model_dump(mode="json")})
    assert attempted.status_code == 422
    revised = invoke(client, scope, "record", {"kind": "evidence", "item_revision": 1,
        "value": evidence(source, claim="Revised and still unreviewed claim.")})
    assert revised.status_code == 200, revised.text
    assert revised.json()["item"]["revision"] == 2
    assert revised.json()["item"]["scientific_reviews"] == []
    assert revised.json()["item"]["scientific_verification"] == "unreviewed"


def test_method_draft_exports_standard_zip_and_explicit_kdg_import_keeps_lineage(client):
    scope, paper, _, _ = setup_scope(client)
    source = anchors(client, scope, paper).json()["sources"][0]
    recorded = invoke(client, scope, "record", {"kind": "method", "value": method(source)})
    assert recorded.status_code == 200, recorded.text
    item = recorded.json()["item"]
    assert item["status"] == "draft" and item["recorded_by"] == "desktop"
    assert item["package"]["skills"][0]["defaults"]["method_status"] == "draft"
    exported = client.get(f"/api/literature/scopes/{scope}/methods/local-method/export")
    assert exported.status_code == 200, exported.text
    assert "attachment" in exported.headers["content-disposition"]
    with ZipFile(io.BytesIO(exported.content)) as archive:
        names = archive.namelist()
        assert any(name.endswith("/pyproject.toml") for name in names)
        assert any(name.endswith("/SKILL.md") for name in names)
        sources = json.loads(archive.read(next(name for name in names if name.endswith("sources.json"))))
        assert source["document_version_id"] in json.dumps(sources)
        assert source["quote"] in json.dumps(sources, ensure_ascii=False).replace("\\n", "\n")
        manifest = json.loads(archive.read(next(name for name in names if name.endswith("method.json"))))
        assert manifest["spec"]["sources"][0] == source
        assert not any("validation/receipt" in name for name in names)
    knowledge = create_node(client, "matcreator.kdg")["id"]
    before = document(client, knowledge)
    arguments = {"method_id": "local-method", "knowledge_id": knowledge}
    assert invoke(client, scope, "assimilate_method", arguments).status_code == 409
    assert invoke(client, scope, "assimilate_method", {**arguments, "knowledge_revision": before["revision"] + 1}).status_code == 409
    imported = invoke(client, scope, "assimilate_method", {**arguments, "knowledge_revision": before["revision"]})
    assert imported.status_code == 200, imported.text
    graph = document(client, knowledge)["value"]
    assert len(graph["snapshots"]) == 1
    snapshot = next(iter(graph["snapshots"].values()))
    assert snapshot["provenance"]["node_id"] == scope
    assert snapshot["provenance"]["method_id"] == "local-method"
    assert snapshot["package"] == item["package"]
    assert graph["entries"][0]["verification"] == "unverified"
    # A genuine scoped research grant does not turn an Agent into a desktop curator.
    agent = create_node(client, "agent")["id"]
    edge = client.post("/api/edges", json={"source": agent, "target": scope, "relationship": "literature.research"})
    assert edge.status_code == 201, edge.text
    capability = next(cap for cap in client.app.state.services.capabilities.derive(agent).capabilities if cap.kind == "literature.research")
    from backend.literature_records import assimilate_method
    with pytest.raises(PermissionDeniedError):
        assimilate_method(client.app.state.services, scope, "local-method", {
            **arguments, "expected_revision": document(client, scope)["revision"], "knowledge_revision": imported.json()["revision"]}, capability)
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    with pytest.raises((PermissionDeniedError, ValueError)):
        client.portal.call(provider.invoke_tool, agent, "operation:literature_research", {
            "scope": scope, "operation": "assimilate_method", "arguments": {
                **arguments, "expected_revision": document(client, scope)["revision"],
                "knowledge_revision": imported.json()["revision"]}})
    assert document(client, knowledge)["revision"] == imported.json()["revision"]
    for extra in ({"status": "validated"}, {"execution_receipt": {"exit_code": 0}}):
        rejected = invoke(client, scope, "record", {"kind": "method", "value": method(source, revision=2, **extra)})
        assert rejected.status_code == 422, rejected.text
    upload(client, paper, pdf_bytes(replacement=True))
    stale = client.get(f"/api/literature/scopes/{scope}/methods/local-method/export")
    assert stale.status_code == 422, stale.text


def test_filled_scope_copy_archives_old_results_without_claiming_new_paper_identity(client):
    scope, paper, _, _ = setup_scope(client)
    source = anchors(client, scope, paper).json()["sources"][0]
    assert invoke(client, scope, "record", {"kind": "evidence", "value": evidence(source)}).status_code == 200
    assert invoke(client, scope, "record", {"kind": "method", "value": method(source)}).status_code == 200
    services = client.app.state.services
    before = read_document(services, scope)
    fields = {"paper_ids": [paper], "search_runs": [{"request_id": "old-run", "status": "complete",
        "scope_revision": 1, "paper_ids": [paper], "request": {"query": "Controlled prior fixture"},
        "candidates": [{"paper_id": paper, "metadata": {"title": "Controlled local source"}}]}],
        "snapshots": [{"id": "historical-snapshot", "scope_id": scope, "paper_ids": [paper], "evidence_ids": ["local-evidence"]}],
        "frontiers": [{"id": "historical-frontier", "scope_id": scope, "paper_ids": [paper]}]}
    write_document(services, scope, {**before["value"], **fields}, before["revision"])
    original = document(client, scope)
    saved = client.post("/api/legions", json={"name": "Controlled filled literature template", "node_ids": [scope, paper]})
    assert saved.status_code == 201, saved.text
    copied = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
    assert copied.status_code == 201, copied.text
    nodes = copied.json()["nodes"]
    copy_scope = next(node["id"] for node in nodes if node["type"] == "literature.scope")
    copy_paper = next(node["id"] for node in nodes if node["type"] == "library.paper")
    value = document(client, copy_scope)["value"]
    assert copy_scope != scope and copy_paper != paper
    assert value["paused"] and value["paper_ids"] == [copy_paper]
    assert value["revisions"][-1]["seed_paper_ids"] == [copy_paper]
    for field in ("search_runs", "evidence", "methods", "snapshots", "frontiers"):
        assert value[field] == []
    assert value["search_budgets"] == {}
    assert len(value["archived_results"]) == 1
    archive = value["archived_results"][0]
    assert archive["source_scope_id"] == scope and archive["source_scope_revision"] == 1
    for field in ("search_runs", "evidence", "methods", "snapshots", "frontiers", "paper_ids", "revisions"):
        assert archive[field] == original["value"][field]
    assert copy_paper not in json.dumps(archive)
    assert archive["evidence"][0]["sources"][0]["paper_id"] == paper
    # Bytes may retain their real content identity; node authorization cannot.
    assert document(client, copy_paper)["value"]["current_document_version_id"] == source["document_version_id"]
    assert anchors(client, copy_scope, paper).status_code == 403
    assert invoke(client, copy_scope, "record", {"kind": "evidence", "value": evidence(source)}).status_code == 403
    relocated = anchors(client, copy_scope, copy_paper).json()["sources"][0]
    assert relocated["paper_id"] == copy_paper
    assert relocated["document_sha256"] == source["document_sha256"]
    assert invoke(client, copy_scope, "record", {"kind": "evidence", "value": evidence(relocated)}).status_code == 200
    assert document(client, scope) == original


def test_scope_copy_archive_is_bounded_and_independent_of_source_mutation():
    from oaw_literature.scope import ScopeDocument, remap
    source = ScopeDocument(id="source").model_dump(mode="json")
    source["evidence"] = [{"id": "old", "sources": [{"paper_id": "paper"}]}]
    copied = remap(source, {"source": "copy", "paper": "copy-paper"})
    source["evidence"][0]["sources"][0]["paper_id"] = "modified-after-copy"
    assert copied["archived_results"][0]["evidence"][0]["sources"][0]["paper_id"] == "paper"
    source["archived_results"] = [{} for _ in range(100)]
    with pytest.raises(ValueError, match="archive limit"):
        remap(source, {"source": "copy"})
