"""Host snapshot authority, revision, retained source and freshness boundaries."""
import base64
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.literature_service import service
from backend.literature_snapshots import create_snapshot, list_snapshots, snapshot_payload
from backend.node_documents import read_document, write_document
from backend.tests.conftest import create_node
from backend.tests.test_literature_service import configure, search, response
from oaw_literature.search import CrossrefClient


@pytest.fixture
def searched(client):
    services = client.app.state.services
    service(services).client = CrossrefClient(transport=httpx.MockTransport(lambda request: response()))
    scope = configure(client)
    result = search(client, scope)
    assert result.status_code == 200, result.text
    return scope["id"], result.json()["run"]["paper_ids"][0]


def make(client, scope_id, **arguments):
    services = client.app.state.services
    current = read_document(services, scope_id)
    args = {"expected_revision": current["revision"], "expected_version": len(current["value"]["snapshots"]), **arguments}
    async def invoke():
        async with services._node_mutation():
            return create_snapshot(services, scope_id, args)
    return client.portal.call(invoke)


def update_paper(client, paper_id, operation, arguments):
    document = client.get(f"/api/nodes/{paper_id}/document").json()
    result = client.post(f"/api/nodes/{paper_id}/actions/{operation}", json={"expected_revision": document["revision"], "arguments": arguments})
    assert result.status_code == 200, result.text
    return result.json()


def pdf_bytes(text="Exact retained paragraph for local snapshot source validation."):
    import pymupdf
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((36, 70), text)
        return pdf.tobytes()


def test_desktop_snapshot_requires_actual_completed_current_scope_search(client):
    scope = configure(client)
    with pytest.raises(ResourceValidationError, match="actual completed search"):
        make(client, scope["id"])
    services = client.app.state.services
    service(services).client = CrossrefClient(transport=httpx.MockTransport(lambda request: response()))
    search(client, scope)
    current = read_document(services, scope["id"])
    result = client.post(f"/api/nodes/{scope['id']}/actions/revise", json={"expected_revision": current["revision"],
        "arguments": {"question": "A different bounded problem", "budget": {"max_searches": 3, "max_papers": 10}}})
    assert result.status_code == 200
    with pytest.raises(ResourceValidationError, match="actual completed search"):
        make(client, scope["id"])


def test_bootstrap_has_actual_metadata_provenance_without_fabricated_claims(client, searched):
    scope_id, paper_id = searched
    item = make(client, scope_id)["item"]
    assert item["sources"][0]["paper_id"] == paper_id
    assert item["sources"][0]["basis"] == "metadata"
    assert item["sources"][0]["metadata_sha256"]
    assert item["search_run_sha256"] and item["coverage"][0]["state"] == "found"
    assert item["claims"] == [] and item["core_paper_ids"] == []
    assert item["recorded_by"] == "desktop" and item["mode"] == "bootstrap"
    assert item["recommendations"][0]["level"] == "metadata"
    assert list_snapshots(client.app.state.services, scope_id)["items"][0]["freshness"]["status"] == "current"


def test_freshness_changes_and_old_versions_are_immutable(client, searched):
    scope_id, paper_id = searched
    first = deepcopy(make(client, scope_id)["item"])
    update_paper(client, paper_id, "metadata", {"metadata": {"source_abstract": "Actual recorded publisher abstract", "abstract_source_url": "https://example.org/source"}})
    listed = list_snapshots(client.app.state.services, scope_id)
    assert listed["items"][0]["freshness"]["status"] == "stale"
    assert "metadata changed" in listed["items"][0]["freshness"]["reasons"][0]
    second = make(client, scope_id)["item"]
    assert second["version"] == 2 and second["sources"][0]["basis"] == "abstract"
    assert second["recommendations"][0]["level"] == "abstract"
    assert read_document(client.app.state.services, scope_id)["value"]["snapshots"][0] == first


def test_snapshot_creation_rejects_stale_cas_and_version(client, searched):
    scope_id, _ = searched
    services = client.app.state.services
    current = read_document(services, scope_id)
    with pytest.raises(RevisionConflictError):
        make(client, scope_id, expected_revision=current["revision"]-1)
    with pytest.raises(RevisionConflictError):
        make(client, scope_id, expected_version=1)
    with pytest.raises(RevisionConflictError):
        make(client, scope_id, expected_version=False)
    assert read_document(services, scope_id) == current


def test_submitted_snapshot_records_real_actor_and_rejects_foreign_sources(client, searched):
    scope_id, _ = searched
    services = client.app.state.services
    first = make(client, scope_id)["item"]
    candidate = snapshot_payload(first)
    candidate["version"] = 2
    current = read_document(services, scope_id)
    actor = SimpleNamespace(agent_id="actual-agent")
    result = create_snapshot(services, scope_id, {"mode": "submit", "expected_revision": current["revision"], "expected_version": 1, "value": candidate}, actor)
    assert result["item"]["recorded_by"] == "actual-agent"
    foreign = create_node(client, "library.paper")
    candidate["version"] = 3
    candidate["sources"][0]["paper_id"] = foreign["id"]
    candidate["recommendations"][0]["paper_id"] = foreign["id"]
    with pytest.raises(ResourceValidationError, match="outside the authorized scope"):
        make(client, scope_id, mode="submit", value=candidate)
    current = read_document(services, scope_id)
    with pytest.raises(PermissionDeniedError, match="desktop"):
        create_snapshot(services, scope_id, {"expected_revision": current["revision"], "expected_version": 2}, actor)


def test_snapshot_uses_host_paragraph_fidelity_and_stales_on_pdf_replace(client, searched):
    from backend.literature_records import paragraph_sources, record
    scope_id, paper_id = searched
    services = client.app.state.services
    update_paper(client, paper_id, "import", {"filename": "source.pdf", "pdf": base64.b64encode(pdf_bytes()).decode()})
    source = paragraph_sources(services, paper_id, 1)["sources"][0]
    current = read_document(services, scope_id)
    record(services, scope_id, {"expected_revision": current["revision"], "kind": "evidence", "value": {
        "id": "located-evidence", "claim": "A locally located statement awaiting review", "kind": "user_hypothesis",
        "relation": "insufficient", "sources": [source], "extracted_by": "caller-supplied"}})
    first = make(client, scope_id)["item"]
    assert first["sources"][0]["basis"] == "fulltext"
    assert first["recommendations"][0]["level"] == "paragraph"
    assert first["recommendations"][0]["anchor"]["rects"] == source["rects"]
    assert first["claims"] == []
    # A caller cannot submit a different quote/rectangle under a validated ID.
    candidate = snapshot_payload(first)
    candidate["version"] = 2
    candidate["recommendations"][0]["anchor"]["rects"] = [[.1,.1,.2,.2]]
    with pytest.raises(ResourceValidationError, match="exact validated evidence anchor"):
        make(client, scope_id, mode="submit", value=candidate)
    update_paper(client, paper_id, "import", {"filename": "replacement.pdf", "pdf": base64.b64encode(pdf_bytes("Changed source PDF.")).decode()})
    listed = list_snapshots(services, scope_id)
    assert listed["items"][0]["freshness"]["status"] == "stale"
    assert any("PDF version changed" in reason for reason in listed["items"][0]["freshness"]["reasons"])
    second = make(client, scope_id)["item"]
    assert not second["evidence"] and second["recommendations"][0]["level"] == "metadata"
    assert any("未纳入 1 条" in limit for limit in second["limitations"])


def test_failed_and_empty_real_search_receipts_remain_honest_coverage(client):
    services = client.app.state.services
    scope = configure(client)
    service(services).client = CrossrefClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok", "message": {"items": [], "total-results": 0}})))
    assert search(client, scope).status_code == 200
    item = make(client, scope["id"])["item"]
    assert item["coverage"][0]["state"] == "no_results" and not item["sources"]
    service(services).client = CrossrefClient(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    assert search(client, scope, "failed-next").status_code == 422
    listed = list_snapshots(services, scope["id"])
    assert listed["items"][0]["freshness"]["status"] == "stale"
    item = make(client, scope["id"])["item"]
    assert [entry["state"] for entry in item["coverage"]] == ["no_results", "failed"]


def test_listing_deleted_paper_does_not_fetch_an_unrelated_paper_or_rewrite_history(client, searched, monkeypatch):
    scope_id, paper_id = searched
    services = client.app.state.services
    first = deepcopy(make(client, scope_id)["item"])
    client.delete(f"/api/nodes/{paper_id}")
    listed = list_snapshots(services, scope_id)
    assert listed["items"][0]["freshness"]["status"] == "stale"
    assert read_document(services, scope_id)["value"]["snapshots"][0] == first
