from fastapi.testclient import TestClient

from backend.tests.conftest import create_node


def test_large_selection_deletes_atomically_and_preserves_unselected_nodes(client: TestClient):
    kept = create_node(client, "text", id="kept")
    nodes = [create_node(client, "text", id=f"bulk-{i}") for i in range(150)]
    ids = [node["id"] for node in nodes]
    rejected = client.post("/api/nodes/batch-delete", json={"node_ids": [*ids, "missing"]})
    assert rejected.status_code == 404, rejected.text
    assert len(client.get("/api/world").json()["nodes"]) == 151
    deleted = client.post("/api/nodes/batch-delete", json={"node_ids": ids})
    assert deleted.status_code == 200, deleted.text
    assert {node["id"] for node in deleted.json()} == set(ids)
    assert [node["id"] for node in client.get("/api/world").json()["nodes"]] == [kept["id"]]


def test_batch_member_deletion_invalidates_the_parent_document_once(client: TestClient):
    parent = create_node(client, "oaw.skills")
    members = [create_node(client, "oaw.skills.skill", parent_id=parent["id"]) for _ in range(3)]
    path = f"/api/nodes/{parent['id']}/document"
    before = client.get(path).json()
    ids = [node["id"] for node in members]
    deleted = client.post("/api/nodes/batch-delete", json={"node_ids": ids})
    assert deleted.status_code == 200, deleted.text
    after = client.get(path).json()
    assert after["revision"] == before["revision"] + 1
    assert len(after["value"]["skills"]) == len(before["value"]["skills"]) - 3
