"""Small regression checks for the local research plugins, using temporary data."""
import base64

import pymupdf
import pytest

from backend.tests.conftest import create_node
from oaw_library import LibraryPlugin, import_pdf, annotate


def test_study_excerpt_upsert():
    value = {"page": 1, "pages": 2, "notes": "old note", "annotations": []}
    item = {"id": "excerpt-1", "text": "evidence", "rects": [[.1,.2,.3,.1]], "color": "#f4d144"}
    value = annotate(value, {"page": 1, "annotation": item})
    updated = {**value["annotations"][0], "title": "Evidence", "comment": "Check mechanism", "learning": True, "color": "#71c98b", "position": {"x": -120, "y": 400}}
    value = annotate(value, {"page": 1, "annotation": updated})
    assert len(value["annotations"]) == 1
    assert value["annotations"][0] == updated
    assert value["notes"] == "old note"
    styled = annotate(value, {"annotation": {**updated, "title_color": "#4477aa", "collapsed": True}})
    assert styled["annotations"][0]["title_color"] == "#4477aa"
    assert styled["annotations"][0]["collapsed"] is True
    assert styled["annotations"][0]["color"] == "#71c98b"
    arranged = annotate(value, {"study_positions": {"excerpt-1": {"x":480,"y":0}}})
    assert arranged["study_layout"] is True
    assert arranged["annotations"][0]["position"] == {"x":480,"y":0}
    assert arranged["annotations"][0]["comment"] == "Check mechanism"
    with pytest.raises(ResourceValidationError):
        annotate(value, {"study_positions": {}})
    with pytest.raises(ResourceValidationError):
        annotate(value, {"annotation": {**updated, "position": {"x": float("nan"), "y": 0}}})
from open_agent_world.plugin_api import ResourceValidationError


def sample_pdf():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "Research evidence page")
        return pdf.tobytes()


def test_pdf_document_membership_and_revision(client):
    paper = create_node(client, "library.paper")
    formed = client.post("/api/legion-groups", json={"name": "Research", "node_ids": [paper["id"]]})
    assert formed.status_code == 200, formed.text
    region = formed.json()[0]
    agent = create_node(client, "agent", parent_id=region["id"])
    path = f"/api/nodes/{paper['id']}"
    before = client.get(path + "/document").json()
    raw = sample_pdf()
    response = client.post(path + "/actions/import", json={
        "expected_revision": before["revision"],
        "arguments": {"filename": "evidence.pdf", "pdf": base64.b64encode(raw).decode()},
    })
    assert response.status_code == 200, response.text
    document = response.json()
    assert base64.b64decode(document["value"]["pdf"]) == raw
    assert document["value"]["pages"] == 1
    assert "Research evidence" in document["value"]["text"][0]
    notes = client.post(path + "/actions/annotate", json={
        "expected_revision": document["revision"], "arguments": {"notes": "Traceable", "page": 1}})
    assert notes.status_code == 200, notes.text
    stale = client.post(path + "/actions/annotate", json={
        "expected_revision": document["revision"], "arguments": {"notes": "stale"}})
    assert stale.status_code == 409
    edge = client.post("/api/edges", json={"source": agent["id"], "target": paper["id"], "relationship": "library.read"})
    assert edge.status_code == 201, edge.text
    # Membership is independent of identity and existing explicit connections.
    moved = client.patch(f"/api/nodes/{agent['id']}", json={"parent_id": None})
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == agent["id"]
    assert moved.json()["parent_id"] is None


def test_invalid_pdf_is_rejected():
    with pytest.raises(ResourceValidationError):
        import_pdf({}, {"pdf": base64.b64encode(b"not a PDF").decode()})


def test_annotations_round_trip_and_delete(client):
    paper = create_node(client, "library.paper")
    path = f"/api/nodes/{paper['id']}"
    doc = client.get(path + "/document").json()
    imported = client.post(path + "/actions/import", json={"expected_revision": doc["revision"],
        "arguments": {"pdf": base64.b64encode(sample_pdf()).decode()}}).json()
    response = client.post(path + "/actions/annotate", json={"expected_revision": imported["revision"],
        "arguments": {"page": 1, "annotation": {"text": "Research evidence", "comment": "Check", "translation": "研究证据", "rects": [[.1,.1,.2,.02]]}}})
    assert response.status_code == 200, response.text
    restored = client.get(path + "/document").json()
    annotation = restored["value"]["annotations"][0]
    assert annotation["translation"] == "研究证据"
    assert annotation["rects"] == [[.1,.1,.2,.02]]
    deleted = client.post(path + "/actions/annotate", json={"expected_revision": restored["revision"], "arguments": {"delete_annotation": annotation["id"]}})
    assert deleted.status_code == 200
    assert deleted.json()["value"]["annotations"] == []


def test_translation_without_credentials(client):
    response = client.post("/api/library/translate", json={"text": "Scientific evidence", "model": "test"})
    assert response.status_code == 422
    assert "密钥" in response.json()["detail"]


@pytest.mark.asyncio
async def test_paper_tool_returns_only_requested_text():
    value = import_pdf({}, {"pdf": base64.b64encode(sample_pdf()).decode()})
    value["notes"] = "note"

    class Context:
        async def node_document_action(self, capability, action, arguments):
            assert action == "read"
            return {"value": value}

    answer = await LibraryPlugin().read_paper(Context(), object(), {"page": 1})
    assert "Research evidence" in answer["text"]
    assert "pdf" not in answer and "thumbnail" not in answer
