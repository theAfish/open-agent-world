"""Versioned bytes must survive replacement without crossing document ownership."""
import base64
import hashlib

from backend.tests.conftest import create_node
from backend.tests.test_research_plugins import sample_pdf


def upload(client, node, raw, revision, filename="source.pdf"):
    return client.post(f"/api/nodes/{node['id']}/actions/import", json={
        "expected_revision": revision, "arguments": {"filename": filename, "pdf": base64.b64encode(raw).decode()}})


def history_url(node):
    return f"/api/nodes/{node['id']}/document/binaries/pdf"


def revision(client, node):
    return client.get(f"/api/nodes/{node['id']}/document").json()["revision"]


def test_binary_history_survives_replacement_and_is_deduplicated(client):
    paper = create_node(client, "library.paper")
    first, second = sample_pdf(), sample_pdf()
    # PDF document IDs may be identical within a very short interval.
    if second == first:
        second += b"\n% second document version\n"
    one = upload(client, paper, first, revision(client, paper))
    assert one.status_code == 200, one.text
    two = upload(client, paper, second, one.json()["revision"])
    assert two.status_code == 200, two.text
    repeated = upload(client, paper, second, two.json()["revision"])
    assert repeated.status_code == 200, repeated.text
    versions = client.get(history_url(paper)).json()
    assert len(versions["items"]) == 2
    assert versions["current"] == hashlib.sha256(second).hexdigest()
    for raw in (first, second):
        response = client.get(history_url(paper) + "/" + hashlib.sha256(raw).hexdigest())
        assert response.status_code == 200
        assert response.content == raw
    other = create_node(client, "library.paper")
    denied = client.get(history_url(other) + "/" + hashlib.sha256(first).hexdigest())
    assert denied.status_code == 404
    assert len(client.get(history_url(other)).json()["items"]) == 0


def test_stale_import_cannot_publish_a_binary_or_change_history(client):
    paper = create_node(client, "library.paper")
    initial = revision(client, paper)
    first = sample_pdf()
    assert upload(client, paper, first, initial).status_code == 200
    second = first + b"\n% rejected version\n"
    rejected = upload(client, paper, second, initial)
    assert rejected.status_code == 409
    assert len(client.get(history_url(paper)).json()["items"]) == 1
    assert client.get(history_url(paper) + "/" + hashlib.sha256(second).hexdigest()).status_code == 404


def test_history_does_not_expose_unregistered_fields_or_invalid_hashes(client):
    paper = create_node(client, "library.paper")
    root = f"/api/nodes/{paper['id']}/document/binaries"
    assert client.get(root + "/notes").status_code == 422
    assert client.get(root + "/pdf/not-a-sha256").status_code == 422
    assert client.get(root + "/pdf/" + "0" * 64).status_code == 404


def test_replacement_archives_notes_and_annotations_outside_active_document(client):
    paper = create_node(client, "library.paper")
    first = sample_pdf()
    digest = hashlib.sha256(first).hexdigest()
    uploaded = upload(client, paper, first, revision(client, paper)).json()
    annotated = client.post(f"/api/nodes/{paper['id']}/actions/annotate", json={
        "expected_revision": uploaded["revision"], "arguments": {
            "notes": "Keep my exact reading notes", "annotation": {
                "id": "excerpt", "text": "A selected quote", "rects": [[.1, .2, .3, .02]]}}})
    assert annotated.status_code == 200, annotated.text
    second = first + b"\n% a revised source\n"
    result = upload(client, paper, second, annotated.json()["revision"])
    assert result.status_code == 200, result.text
    archived = client.get(history_url(paper) + f"/{digest}/snapshot")
    assert archived.status_code == 200, archived.text
    snapshot = archived.json()
    assert snapshot["current"] is False
    assert snapshot["value"]["notes"] == "Keep my exact reading notes"
    assert snapshot["value"]["annotations"][0]["text"] == "A selected quote"
    assert "pdf" not in snapshot["value"]
    other = create_node(client, "library.paper")
    assert client.get(history_url(other) + f"/{digest}/snapshot").status_code == 404


def action(client, paper, current, name, arguments):
    response = client.post(f"/api/nodes/{paper['id']}/actions/{name}", json={
        "expected_revision": current["revision"], "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()


def test_returning_pdf_restores_reading_state_without_rolling_back_provenance(client):
    import pymupdf

    paper = create_node(client, "library.paper")
    with pymupdf.open() as pdf:
        for index in range(2):
            pdf.new_page().insert_text((50, 50), f"Original evidence page {index + 1}")
        first = pdf.tobytes()
    second, third = first + b"\n% revision B\n", first + b"\n% revision C\n"
    first_hash, second_hash = [hashlib.sha256(raw).hexdigest() for raw in (first, second)]
    current = upload(client, paper, first, revision(client, paper), "original.pdf").json()
    for identifier in ("first-excerpt", "second-excerpt"):
        current = action(client, paper, current, "annotate", {
            "page": 2, "notes": "A: keep exact notes", "study_title": "A reading canvas",
            "annotation": {"id": identifier, "text": "Exact A quote", "learning": True,
                           "rects": [[.1, .2, .3, .02]]}})
    current = action(client, paper, current, "annotate", {
        "study_positions": {"first-excerpt": {"x": 120, "y": -20}, "second-excerpt": {"x": 500, "y": 40}},
        "study_relationships": [{"id": "a-relation", "source": "first-excerpt", "target": "second-excerpt", "type": "supports"}]})
    current = action(client, paper, current, "metadata", {"reading_status": "close_read", "metadata": {"title": "Original title"}})
    first_anchor = current["value"]["annotations"][0]["source_anchor"]["id"]
    current = action(client, paper, current, "evidence", {"evidence": {
        "id": "a-evidence", "claim": "Exact A quote", "kind": "author_statement", "source_anchor_id": first_anchor}})
    expected = current["value"]
    current = upload(client, paper, second, current["revision"], "revised.pdf").json()
    current = action(client, paper, current, "annotate", {
        "notes": "B notes", "study_title": "B reading canvas", "annotation": {"id": "b-excerpt", "text": "Exact B quote"}})
    second_anchor = current["value"]["annotations"][0]["source_anchor"]["id"]
    current = action(client, paper, current, "evidence", {"evidence": {
        "id": "b-evidence", "claim": "Exact B quote", "kind": "author_statement", "source_anchor_id": second_anchor}})
    current = action(client, paper, current, "metadata", {"metadata": {"title": "Curated current title"}})

    returned = upload(client, paper, first, current["revision"], "returned-A.pdf")
    assert returned.status_code == 200, returned.text
    current = returned.json()
    restored = current["value"]
    reading_fields = ("page", "notes", "annotations", "study_layout", "study_title", "study_relationships", "reading_status")
    for field in reading_fields:
        assert restored[field] == expected[field], field
    assert restored["filename"] == "returned-A.pdf"
    assert restored["metadata"]["title"] == "Curated current title"
    assert {item["id"] for item in restored["versions"]} == {first_hash, second_hash}
    assert next(item for item in restored["versions"] if item["id"] == first_hash)["filename"] == "original.pdf"
    assert {item["id"] for item in restored["evidence"]} == {"a-evidence", "b-evidence"}
    by_id = {item["id"]: item for item in restored["source_anchors"]}
    assert by_id[first_anchor]["status"] == "current"
    assert by_id[first_anchor]["quote"] == "Exact A quote"
    assert by_id[second_anchor]["status"] == "needs_relocation"
    assert by_id[second_anchor]["document_sha256"] == second_hash
    assert by_id[second_anchor]["quote"] == "Exact B quote"

    changed = upload(client, paper, third, current["revision"])
    assert changed.status_code == 200, changed.text
    archived = client.get(history_url(paper) + f"/{first_hash}/snapshot").json()
    assert archived["current"] is False
    for field in reading_fields:
        assert archived["value"][field] == expected[field], field
    assert len(client.get(history_url(paper)).json()["items"]) == 3


def test_returned_version_can_be_explicitly_cleared_and_stale_return_cannot_change_it(client):
    paper = create_node(client, "library.paper")
    first = sample_pdf()
    second = first + b"\n% another version\n"
    first_hash = hashlib.sha256(first).hexdigest()
    current = upload(client, paper, first, revision(client, paper)).json()
    current = action(client, paper, current, "annotate", {
        "notes": "A notes", "annotation": {"id": "a", "text": "Original A quote"}})
    current = upload(client, paper, second, current["revision"]).json()
    stale_revision = current["revision"]
    current = action(client, paper, current, "annotate", {"notes": "Current B work"})
    assert upload(client, paper, first, stale_revision).status_code == 409
    assert client.get(f"/api/nodes/{paper['id']}/document").json()["value"]["notes"] == "Current B work"
    assert client.get(history_url(paper) + f"/{first_hash}/snapshot").json()["value"]["notes"] == "A notes"
    current = upload(client, paper, first, current["revision"]).json()
    assert current["value"]["notes"] == "A notes"
    current = action(client, paper, current, "annotate", {"notes": "", "delete_annotation": "a"})
    assert current["value"]["notes"] == ""
    assert current["value"]["annotations"] == []
    current = upload(client, paper, second, current["revision"]).json()
    current = upload(client, paper, first, current["revision"]).json()
    assert current["value"]["notes"] == ""
    assert current["value"]["annotations"] == []


def test_known_bytes_never_restore_another_papers_notes(client):
    first, second = sample_pdf(), sample_pdf() + b"\n% other bytes\n"
    papers = [create_node(client, "library.paper") for _ in range(2)]
    for number, paper in enumerate(papers):
        current = upload(client, paper, first, revision(client, paper)).json()
        assert current["value"]["notes"] == ""
        current = action(client, paper, current, "annotate", {"notes": f"Private notes {number}"})
        assert upload(client, paper, second, current["revision"]).status_code == 200
    for number, paper in enumerate(papers):
        current = upload(client, paper, first, revision(client, paper)).json()
        assert current["value"]["notes"] == f"Private notes {number}"
