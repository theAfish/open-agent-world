import base64
import threading

from backend.tests.conftest import create_node
from backend.tests.test_conversation_attachments import room, upload
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def connect(client, viewer, source):
    # The ordinary connection gesture may be drawn in either direction.
    response = client.post("/api/edges", json={"source": source["id"], "target": viewer["id"], "relationship": "core.file-preview"})
    assert response.status_code == 201, response.text
    edge = response.json()
    assert edge["source"] == viewer["id"] and edge["target"] == source["id"]
    return edge


def test_conversation_complete_bytes_session_scope_and_revocation(client):
    viewer = create_node(client, "science.structure-viewer")
    conversation, session, base = room(client)
    content = b"2\nwater fragment\nO 0 0 0\nH 0 0 1\n" + b" " * 70000
    file = upload(client, base, "crystal.xyz", content)
    reference = {"kind": "conversation", "source_id": conversation["id"], "session_id": session["id"],
                 "version_id": file["version_id"], "path": file["path"]}
    url = f"/api/nodes/{viewer['id']}/file-preview"
    assert client.post(url, json=reference).status_code == 403
    edge = connect(client, viewer, conversation)
    response = client.post(url, json=reference)
    assert response.status_code == 200, response.text
    assert base64.b64decode(response.json()["data"]) == content
    assert response.json()["size_bytes"] == len(content)
    other = client.post(f"/api/conversations/{conversation['id']}/sessions", json={"title": "Other"}).json()
    assert client.post(url, json={**reference, "session_id": other["id"]}).status_code == 403
    assert client.post(url, json={**reference, "path": "../crystal.xyz"}).status_code == 422
    assert client.post(url, json={**reference, "kind": "sandbox"}).status_code == 422
    assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    assert client.post(url, json=reference).status_code == 403


def test_sandbox_uses_existing_path_boundary_and_full_download(runtime_client):
    client, backend, _ = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    viewer = create_node(client, "science.structure-viewer")
    connect(client, viewer, sandbox)
    workspace = client.portal.call(backend.get, sandbox["id"]).workspace
    content = b"structure\n" + b" " * (1024 * 1024 + 1)
    (workspace / "POSCAR").write_bytes(content)
    reference = {"kind": "sandbox", "source_id": sandbox["id"], "root": "workspace", "path": "POSCAR"}
    url = f"/api/nodes/{viewer['id']}/file-preview"
    response = client.post(url, json=reference)
    assert response.status_code == 200, response.text
    assert base64.b64decode(response.json()["data"]) == content
    for path in ["../outside", "C:/Windows/win.ini", "missing.cif"]:
        assert client.post(url, json={**reference, "path": path}).status_code in {403, 422}
    with (workspace / "large.cif").open("wb") as output:
        output.truncate(16 * 1024 * 1024 + 1)
    assert client.post(url, json={**reference, "path": "large.cif"}).status_code == 422
    # A viewer does not receive execute or lifecycle management authority.
    services = client.app.state.services
    relation = services.plugins.relationship("core.file-preview")
    assert [grant.kind for grant in relation.capabilities] == ["file.preview"]


def test_disconnect_while_sandbox_read_is_in_flight_rejects_bytes(runtime_client, monkeypatch):
    import asyncio
    client, backend, _ = runtime_client
    _, sandbox, _, _, _ = setup_skill(client)
    viewer = create_node(client, "science.structure-viewer")
    edge = connect(client, viewer, sandbox)
    entered, release = threading.Event(), threading.Event()

    async def delayed(*args, **kwargs):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return {"state": "ready", "data": base64.b64encode(b"private bytes").decode()}

    monkeypatch.setattr(backend, "file_operation", delayed)
    responses = []
    task = threading.Thread(target=lambda: responses.append(client.post(f"/api/nodes/{viewer['id']}/file-preview",
        json={"kind": "sandbox", "source_id": sandbox["id"], "path": "secret.cif"})))
    task.start()
    try:
        assert entered.wait(5)
        assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    finally:
        release.set()
        task.join(5)
    assert not task.is_alive()
    assert responses[0].status_code == 403
    assert "private bytes" not in responses[0].text
