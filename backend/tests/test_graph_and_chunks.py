from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.conftest import create_node


def test_invalid_graph_relationships_are_rejected(client: TestClient) -> None:
    agent = create_node(client, "agent")
    other_agent = create_node(client, "agent")
    text = create_node(client, "text")
    image = create_node(client, "image")
    sandbox = create_node(client, "sandbox")

    reversed_edge = client.post(
        "/api/edges",
        json={"source": text["id"], "target": agent["id"], "relationship": "read"},
    )
    assert reversed_edge.status_code == 422
    assert reversed_edge.json()["error"]["code"] == "invalid_relationship"

    writable_image = client.post(
        "/api/edges",
        json={
            "source": image["id"],
            "target": sandbox["id"],
            "relationship": "mount_read_write",
        },
    )
    assert writable_image.status_code == 422

    communication = client.post(
        "/api/edges",
        json={
            "source": agent["id"],
            "target": other_agent["id"],
            "relationship": "communicate",
        },
    )
    assert communication.status_code == 201

    valid = client.post(
        "/api/edges",
        json={"source": agent["id"], "target": text["id"], "relationship": "read"},
    )
    assert valid.status_code == 201
    duplicate = client.post(
        "/api/edges",
        json={
            "source": agent["id"],
            "target": text["id"],
            "relationship": "read_edit",
        },
    )
    assert duplicate.status_code == 409


def test_chunk_loading_handles_negative_world_coordinates(client: TestClient) -> None:
    left = create_node(client, "agent", name="Left", position={"x": -1, "y": -2049})
    origin = create_node(client, "sandbox", name="Origin", position={"x": 1, "y": 1})
    far = create_node(client, "text", name="Far", position={"x": 4096, "y": 0})

    assert left["chunk"] == [-1, -2]
    assert origin["chunk"] == [0, 0]
    assert far["chunk"] == [2, 0]

    response = client.get("/api/world", params={"chunks": "-1:-2,0:0"})
    assert response.status_code == 200
    body = response.json()
    assert {node["name"] for node in body["nodes"]} == {"Left", "Origin"}
    assert body["chunks"] == [[-1, -2], [0, 0]]

    malformed = client.get("/api/world", params={"chunks": "../../etc"})
    assert malformed.status_code == 422
