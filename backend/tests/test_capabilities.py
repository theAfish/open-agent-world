from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.conftest import create_node


def test_capability_derivation_permission_changes_and_revocation(
    client: TestClient,
) -> None:
    agent = create_node(client, "agent", name="Researcher")
    text = create_node(
        client,
        "text",
        name="Notes",
        config={"filename": "notes.txt"},
        content="first draft",
    )
    edge_response = client.post(
        "/api/edges",
        json={"source": agent["id"], "target": text["id"], "relationship": "read"},
    )
    assert edge_response.status_code == 201
    edge = edge_response.json()

    capabilities = client.get(f"/api/agents/{agent['id']}/capabilities").json()
    assert [capability["kind"] for capability in capabilities["capabilities"]] == [
        "text.read"
    ]
    assert client.get(
        f"/api/agents/{agent['id']}/resources/{text['id']}/text"
    ).json()["content"] == "first draft"

    denied = client.put(
        f"/api/agents/{agent['id']}/resources/{text['id']}/text",
        json={"content": "not allowed"},
    )
    assert denied.status_code == 403

    updated = client.patch(
        f"/api/edges/{edge['id']}", json={"relationship": "read_edit"}
    )
    assert updated.status_code == 200
    kinds = {
        capability["kind"]
        for capability in client.get(
            f"/api/agents/{agent['id']}/capabilities"
        ).json()["capabilities"]
    }
    assert kinds == {"text.read", "text.edit"}
    modified = client.put(
        f"/api/agents/{agent['id']}/resources/{text['id']}/text",
        json={"content": "authorized"},
    )
    assert modified.status_code == 200
    assert modified.json()["content"] == "authorized"

    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    assert client.get(
        f"/api/agents/{agent['id']}/resources/{text['id']}/text"
    ).status_code == 403
    assert client.get(f"/api/agents/{agent['id']}/capabilities").json()[
        "capabilities"
    ] == []


def test_sandbox_attachment_permissions_are_live(client: TestClient) -> None:
    text = create_node(client, "text", content="mounted")
    sandbox = create_node(client, "sandbox")
    edge = client.post(
        "/api/edges",
        json={
            "source": text["id"],
            "target": sandbox["id"],
            "relationship": "mount_read_only",
        },
    ).json()
    services = client.app.state.services
    services.capabilities.require_sandbox_resource(sandbox["id"], text["id"])
    try:
        services.capabilities.require_sandbox_resource(
            sandbox["id"], text["id"], write=True
        )
    except Exception as exc:
        assert getattr(exc, "code", None) == "permission_denied"
    else:
        raise AssertionError("read-only mount unexpectedly granted write access")

    client.patch(
        f"/api/edges/{edge['id']}", json={"relationship": "mount_read_write"}
    )
    services.capabilities.require_sandbox_resource(
        sandbox["id"], text["id"], write=True
    )
    client.delete(f"/api/edges/{edge['id']}")
    try:
        services.capabilities.require_sandbox_resource(sandbox["id"], text["id"])
    except Exception as exc:
        assert getattr(exc, "code", None) == "permission_denied"
    else:
        raise AssertionError("deleted mount unexpectedly remained authorized")
