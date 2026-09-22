from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import hashlib
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import Settings
from backend.deploy import create_deployment, _rebind_native_sandboxes
from backend.errors import ConflictError, PermissionDeniedError
from backend.main import create_app
from backend.world.models import CardCreate, CardPatch, EdgePatch

PASSWORD = "test-operator-password-93841"


def settings(path):
    return replace(Settings.for_data_root(path), agent_runtime="core.mock", sandbox_runtime=None)


def node(client, kind, name, **extra):
    response = client.post("/api/nodes", json={"type": kind, "name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def prepare_source(path, *, plugin_directories=()):
    app = create_app(replace(settings(path), plugin_directories=plugin_directories))
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        chat = node(client, "conversation", "Ask the assistant")
        agent = node(client, "agent", "Assistant", config={"system_instruction": "PRIVATE INSTRUCTION NEVER SENT TO BROWSER"})
        text = node(client, "text", "Guide", content="# Welcome\nThis is the published guide.")
        hidden = node(client, "text", "Hidden internal notes", content="NOT PUBLIC")
        board = node(client, "oaw.tasks", "Tasks")
        plugin_note = node(client, "example.deployment-notes", "Plugin notes") if plugin_directories else None
        edge = client.post("/api/edges", json={"source": agent["id"], "target": chat["id"], "relationship": "participate"})
        assert edge.status_code == 201, edge.text
        response = client.post("/api/legion-groups", json={"name": "Published studio", "node_ids": [chat["id"], agent["id"], text["id"], board["id"]] + ([plugin_note["id"]] if plugin_note else [])})
        assert response.status_code == 200, response.text
        legion = next(c for c in response.json() if c["type"] == "legion")
        pane = lambda card: {"kind": "pane", "view": {"card_id": card["id"]}}
        layout = {"version": 2, "root": {"kind": "split", "axis": "horizontal", "ratio": .6,
                  "first": pane(chat), "second": {"kind": "tabs", "views": [{"card_id": text["id"]}, {"card_id": board["id"]}], "active_view": {"card_id": text["id"]}}}}
        if plugin_note:
            layout["root"]["second"]["views"].append({"card_id": plugin_note["id"]})
        assert client.patch(f"/api/nodes/{legion['id']}", json={"config": {"workspace_layout": layout}}).status_code == 200
        response = client.post("/api/deployments", json={"legion_id": legion["id"], "name": "Customer workspace"})
        assert response.status_code == 201, response.text
        release = response.json()
        # The normal management process stays usable after publishing a recipe.
        assert client.get("/api/deployment").json() == {"mode": "builder"}
        assert client.get("/api/world").status_code == 200
        assert client.get("/api/deployments").json()[0]["id"] == release["id"]
        with pytest.raises(ConflictError, match="in use"):
            create_deployment(path, path.with_name("busy-target"), release["id"], password=PASSWORD)
    return {"release": release, "chat": chat, "agent": agent, "text": text, "hidden": hidden, "board": board, "edge": edge.json(), "legion": legion}


@pytest.fixture
def deployed(tmp_path):
    source, target = tmp_path / "source", tmp_path / "deployed"
    records = prepare_source(source)
    create_deployment(source, target, records["release"]["id"], password=PASSWORD)
    return source, target, records


def sign_in(client):
    response = client.post("/api/deployment/session", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()


def test_runtime_auth_projection_and_management_absence(deployed):
    source, target, records = deployed
    with TestClient(create_app(settings(target)), client=("203.0.113.9", 3000)) as client:
        assert client.get("/api/deployment").json()["mode"] == "runtime"
        assert client.get("/api/runtime-app").status_code == 401
        assert client.post("/api/deployment/session", json={"password": "wrong"}).status_code == 401
        sign_in(client)
        response = client.get("/api/runtime-app")
        assert response.status_code == 200
        for private in ("PRIVATE INSTRUCTION", "NOT PUBLIC", "password", "source_path", "plugin_versions", records["hidden"]["id"], records["agent"]["id"]):
            assert private not in response.text
        assert response.json()["layout"] == records["release"]["layout"]
        assert response.headers["cache-control"] == "no-store"
        for path in ("/api/world", "/api/nodes", "/api/edges", "/api/settings/models", "/api/catalog", "/api/application", "/api/deployments", "/docs", "/openapi.json"):
            assert client.get(path).status_code == 404, path
        assert client.patch(f"/api/nodes/{records['agent']['id']}", json={"config": {"model": "bad"}}).status_code == 404
        assert client.get(f"/api/runtime-app/workspace/resources/{records['hidden']['id']}").status_code == 404
        assert client.get(f"/api/runtime-app/workspace/resources/{records['text']['id']}/text").json()["content"].startswith("# Welcome")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/events"):
                pytest.fail("Management event stream must not be mounted")
        assert client.delete("/api/runtime-app/session", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.delete("/api/runtime-app/session").status_code == 200
        assert client.get("/api/runtime-app").status_code == 401
    # Credentials in the deployment are password hashes, and the source remains a builder.
    manifest = (target / "deployment.json").read_text(encoding="utf-8")
    assert PASSWORD not in manifest
    with TestClient(create_app(settings(source))) as client:
        assert client.get("/api/deployment").json()["mode"] == "builder"


def test_runtime_conversation_tasks_and_restart(deployed):
    source, target, records = deployed
    chat, board = records["chat"]["id"], records["board"]["id"]
    with TestClient(create_app(settings(target))) as client:
        sign_in(client)
        response = client.post(f"/api/runtime-app/workspace/conversations/{chat}/sessions", json={"title": "Customer session", "participant_ids": [records["agent"]["id"]]})
        assert response.status_code == 201, response.text
        session = response.json()["id"]
        base = f"/api/runtime-app/workspace/conversations/{chat}/sessions/{session}/messages"
        result = client.post(base, json={"content": "Hello from the published app", "mention_agent_ids": [records["agent"]["id"]]})
        assert result.status_code == 202, result.text
        assert result.json()["accepted_agent_ids"] == [records["agent"]["id"]]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            messages = client.get(base.replace("/messages", "/timeline")).json()
            if any(m["sender_kind"] == "agent" for m in messages["items"]):
                break
            time.sleep(.05)
        assert any(m["sender_kind"] == "agent" for m in messages["items"]), messages
        assert client.post(base, json={"content": "bad", "mention_agent_ids": [records["hidden"]["id"]]}).status_code in {403, 404, 422}
        document = f"/api/runtime-app/workspace/nodes/{board}/document"
        action = f"/api/runtime-app/workspace/nodes/{board}/actions/upsert"
        initial = client.get(document).json()
        payload = {"arguments": {"tasks": [{"id": "report", "title": "Deliver report"}]}, "expected_revision": initial["revision"]}
        updated = client.post(action, json=payload)
        assert updated.status_code == 200, updated.text
        assert client.post(action, json=payload).status_code == 409
        assert client.post(action.replace("upsert", "configure_execution"), json={"arguments": {}}).status_code == 403
        assert client.post(action, json={"arguments": {"tasks": [{"id": "report", "title": "Bad", "executor_id": records["hidden"]["id"]}]}}).status_code == 403
    with TestClient(create_app(settings(target))) as client:
        sign_in(client)
        assert client.get(base).json()[0]["content"] == "Hello from the published app"
        assert client.get(document).json()["value"]["tasks"][0]["title"] == "Deliver report"
    with TestClient(create_app(settings(source))) as client:
        assert "Customer session" not in client.get(f"/api/conversations/{chat}").text


def test_locked_graph_rejects_internal_mutations_but_accepts_status(deployed):
    _, target, records = deployed
    app = create_app(settings(target))
    with TestClient(app):
        world = app.state.services.world
        with pytest.raises(PermissionDeniedError):
            world.create_card(CardCreate(type="text"))
        with pytest.raises(PermissionDeniedError):
            world.update_card(records["agent"]["id"], CardPatch(config={"system_instruction": "changed"}))
        with pytest.raises(PermissionDeniedError):
            world.delete_card(records["text"]["id"])
        with pytest.raises(PermissionDeniedError):
            world.update_edge(records["edge"]["id"], EdgePatch(direction="bidirectional"))
        assert world.update_card(records["agent"]["id"], CardPatch(status="idle")).status == "idle"


def test_shared_workspace_routes_keep_scope_and_configuration_locked(deployed):
    _, target, records = deployed
    with TestClient(create_app(settings(target))) as client:
        root = "/api/runtime-app/workspace"
        chat, text_id, board = (records[key]["id"] for key in ("chat", "text", "board"))
        assert client.get(f"{root}/conversations/{chat}").status_code == 401
        sign_in(client)
        summary = client.get(f"{root}/conversations/{chat}").json()
        assert summary["agents"][0]["model"] == "" and summary["context_statuses"] == {}
        for path in ("/world", "/nodes", "/catalog", "/settings/models", f"/agents/{records['agent']['id']}"):
            assert client.get(root + path).status_code == 404
        assert client.get(f"{root}/conversations/{records['hidden']['id']}").status_code == 404
        assert client.get(f"{root}/resources/{records['hidden']['id']}/text").status_code == 404
        assert client.put(f"{root}/resources/{text_id}/text", json={"content": "overwrite"}).status_code in {404, 405}
        assert client.get(f"{root}/resources/{text_id}/history").json() == []
        assert client.post(f"{root}/nodes/{board}/actions/configure_execution", json={"arguments": {}}).status_code == 403
        assert client.post(f"{root}/nodes/{board}/actions/upsert", json={"arguments": {"tasks": None}}).status_code == 422
        response = client.post(f"{root}/conversations/{chat}/sessions", json={"title": "Shared session", "participant_ids": [records["agent"]["id"]]})
        assert response.status_code == 201
        session = response.json()["id"]
        assert client.patch(f"{root}/conversations/{chat}/sessions/{session}", json={"title": "Renamed"}).json()["title"] == "Renamed"
        attachment = client.post(f"{root}/conversations/{chat}/sessions/{session}/attachments?filename=hello.txt", content=b"hello workspace")
        assert attachment.status_code == 201, attachment.text
        version = attachment.json()["version_id"]
        assert client.get(f"{root}/conversations/{chat}/sessions/{session}/attachments/{version}?path=hello.txt").content == b"hello workspace"
        assert client.delete(f"{root}/conversations/{chat}/sessions/{session}", headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.delete(f"{root}/conversations/{chat}/sessions/{session}").status_code == 204


def test_shared_sandbox_sections_are_scoped_and_hide_host_configuration(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "runtime"
    records = prepare_source(source)
    with TestClient(create_app(settings(source))) as client:
        sandbox = node(client, "sandbox", "Files", parent_id=records["legion"]["id"])
        layout = {"version": 2, "root": {"kind": "pane", "view": {"card_id": sandbox["id"]}}}
        assert client.patch(f"/api/nodes/{records['legion']['id']}", json={"config": {"workspace_layout": layout}}).status_code == 200
        release = client.post("/api/deployments", json={"legion_id": records["legion"]["id"], "name": "Files app"}).json()
    create_deployment(source, target, release["id"], password=PASSWORD)
    app = create_app(settings(target))
    with TestClient(app) as client:
        async def get_sandbox(self, node_id):
            return SimpleNamespace(state="ready", available=True, workspace_access="read_write", workspace_path="PRIVATE_HOST_PATH")
        async def file_operation(*args, **kwargs):
            return [{"id": "workspace", "label": "PRIVATE_HOST_PATH", "directory": True}, {"id": "resource:private", "label": "Hidden mount"}]
        monkeypatch.setattr(type(app.state.services), "get_sandbox", get_sandbox)
        monkeypatch.setattr(type(app.state.services), "_require_sandbox_backend", lambda self: SimpleNamespace(file_operation=file_operation))
        sign_in(client)
        base = f"/api/runtime-app/workspace/sandboxes/{sandbox['id']}"
        assert "PRIVATE_HOST_PATH" not in client.get(base).text
        roots = client.get(base + "/files").json()
        assert roots == [{"id": "workspace", "label": "Workspace", "directory": True, "access": "read_only"}]
        assert client.get(base + "/files?root=resource:private").status_code == 404
        assert client.get(base + "/configuration").status_code == 404
        assert client.get(base + "/history").status_code == 404
        assert client.post(base + "/execute", json={"command": "echo denied"}).status_code == 404
        bootstrap = client.get("/api/runtime-app").json()
        assert bootstrap["legion"]["config"]["workspace_layout"]["hidden_sections"] == [{"card_id": sandbox["id"], "section_id": "terminal"}]
        monkeypatch.undo()


def test_changed_release_nonempty_destination_and_nested_copy_fail_closed(tmp_path):
    source = tmp_path / "source"
    records = prepare_source(source)
    with pytest.raises(ValueError, match="outside"):
        create_deployment(source, source / "nested", records["release"]["id"], password=PASSWORD)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        create_deployment(source, occupied, records["release"]["id"], password=PASSWORD)
    with TestClient(create_app(settings(source))) as client:
        assert client.patch(f"/api/nodes/{records['agent']['id']}", json={"config": {"system_instruction": "new release"}}).status_code == 200
    with pytest.raises(ValueError, match="changed after publication"):
        create_deployment(source, tmp_path / "rejected", records["release"]["id"], password=PASSWORD)
    assert not (tmp_path / "rejected").exists()
    assert (occupied / "keep.txt").read_text() == "keep"


def test_cli_password_rotation_preserves_release_and_requires_https_cookie(deployed, monkeypatch):
    from backend.deploy import main
    import getpass
    _, target, records = deployed
    monkeypatch.setattr(sys, "argv", ["deploy", "--serve", str(target), "--reset-password", "--ask-password", "--secure-cookie", "--prepare-only"])
    monkeypatch.setattr(getpass, "getpass", lambda _: "replacement-password-846295")
    main()
    with TestClient(create_app(settings(target)), base_url="https://testserver") as client:
        assert client.post("/api/deployment/session", json={"password": PASSWORD}).status_code == 401
        result = client.post("/api/deployment/session", json={"password": "replacement-password-846295"})
        assert result.status_code == 200 and "secure" in result.headers["set-cookie"].lower()
        assert client.get("/api/runtime-app").json()["id"] == records["release"]["id"]


def test_storage_pointer_cannot_open_deployment_as_management_server(deployed, tmp_path):
    _, target, _ = deployed
    pointer = tmp_path / "startup.json"
    pointer.write_text(json.dumps({"current_path": str(target)}), encoding="utf-8")
    selected = replace(settings(tmp_path / "normal-profile"), storage_config_path=pointer)
    with pytest.raises(RuntimeError, match="storage pointer refers to a deployment"):
        with TestClient(create_app(selected)):
            pass


def test_publication_requires_supported_nonempty_saved_layout(tmp_path):
    with TestClient(create_app(settings(tmp_path / "source"))) as client:
        card = node(client, "sandbox", "Compute")
        response = client.post("/api/legion-groups", json={"name": "Workspace", "node_ids": [card["id"]]})
        legion = next(c for c in response.json() if c["type"] == "legion")
        request = {"legion_id": legion["id"], "name": "App"}
        assert client.post("/api/deployments", json=request).status_code == 422
        layout = {"version": 2, "root": {"kind": "pane", "view": {"card_id": card["id"], "section_id": "terminal"}}}
        client.patch(f"/api/nodes/{legion['id']}", json={"config": {"workspace_layout": layout}})
        assert client.post("/api/deployments", json=request).status_code == 422
        published = client.post("/api/deployments", json={**request, "allow_terminal": True})
        assert published.status_code == 201, published.text
        assert published.json()["permissions"][card["id"]] == ["terminal"]


def test_cloned_windows_sandbox_identity_and_permissions_only_touch_copy(tmp_path):
    source, stage, target = tmp_path / "source", tmp_path / "stage", tmp_path / "deployed"
    source.mkdir()
    sandbox = stage / "sandboxes" / "compute"
    (sandbox / "workspace").mkdir(parents=True)
    (sandbox / "workspace" / "result.txt").write_text("keep", encoding="utf-8")
    identity = lambda root: "OpenAgentWorld." + hashlib.sha256(f"{root}|compute".encode()).hexdigest()[:40]
    manifest = sandbox / "sandbox.json"
    manifest.write_text(json.dumps({"sandbox_id": "compute", "identity": identity(source), "state": "stopped"}), encoding="utf-8")
    operations = []

    class Native:
        def ensure_appcontainer(self, name):
            return SimpleNamespace(sid=name)

        def free_appcontainer_sid(self, profile):
            pass

        def revoke_path(self, path, sid):
            operations.append(("revoke", path, sid))

        def grant_path(self, path, sid, *, read_only):
            operations.append(("grant", path, sid))
            assert not read_only

    _rebind_native_sandboxes(stage, source, target, native=Native())
    saved = json.loads(manifest.read_text())
    assert saved["identity"] == identity(target)
    assert saved["workspace_authorized"] is False
    assert all(path.is_relative_to(stage) for _, path, _ in operations)
    assert ("grant", sandbox / "workspace", identity(target)) in operations
    assert (sandbox / "workspace" / "result.txt").read_text() == "keep"


def test_unsupported_surface_and_hidden_terminal_permissions(tmp_path):
    with TestClient(create_app(settings(tmp_path / "source"))) as client:
        card = node(client, "sandbox", "Compute")
        response = client.post("/api/legion-groups", json={"name": "Workspace", "node_ids": [card["id"]]})
        legion = next(c for c in response.json() if c["type"] == "legion")
        request = {"legion_id": legion["id"], "name": "App", "allow_terminal": True}
        layout = {"version": 2, "root": {"kind": "pane", "view": {"card_id": card["id"], "section_id": "custom-admin-panel"}}}
        client.patch(f"/api/nodes/{legion['id']}", json={"config": {"workspace_layout": layout}})
        denied = client.post("/api/deployments", json=request)
        assert denied.status_code == 422 and "public runtime surface" in denied.text
        layout["root"]["view"].pop("section_id")
        layout["hidden_sections"] = [{"card_id": card["id"], "section_id": "terminal"}]
        client.patch(f"/api/nodes/{legion['id']}", json={"config": {"workspace_layout": layout}})
        published = client.post("/api/deployments", json=request)
        assert published.status_code == 201, published.text
        assert published.json()["permissions"][card["id"]] == ["files", "preview"]
        layout["root"] = {"kind": "tabs", "views": [{"card_id": card["id"]}, {"card_id": card["id"], "section_id": "files"}], "active_view": {"card_id": card["id"]}}
        client.patch(f"/api/nodes/{legion['id']}", json={"config": {"workspace_layout": layout}})
        mixed = client.post("/api/deployments", json=request).json()
        assert mixed["panels"][0]["sections"] == ["preview"]
        assert mixed["panels"][1]["sections"] == ["files"]


@pytest.mark.skipif(os.name != "nt" or os.environ.get("OAW_DEPLOY_NATIVE") != "1", reason="opt-in native Windows clone acceptance")
def test_native_windows_sandbox_copy_runs_with_new_identity(tmp_path):
    source, target = tmp_path / "source", tmp_path / "deployed"
    source_settings = replace(settings(source), sandbox_runtime="windows")
    with TestClient(create_app(source_settings)) as client:
        card = node(client, "sandbox", "Workspace", config={"runtime": "windows"})
        base = f"/api/sandboxes/{card['id']}"
        started = client.post(base + "/start")
        assert started.status_code == 200, started.text
        written = client.post(base + "/execute", json={"command": "echo source-data > result.txt"})
        assert written.status_code == 200 and written.json()["exit_code"] == 0, written.text
        assert client.post(base + "/stop").status_code == 200
        group = client.post("/api/legion-groups", json={"name": "Native app", "node_ids": [card["id"]]}).json()[0]
        layout = {"version": 2, "root": {"kind": "pane", "view": {"card_id": card["id"]}}}
        assert client.patch(f"/api/nodes/{group['id']}", json={"config": {"workspace_layout": layout}}).status_code == 200
        response = client.post("/api/deployments", json={"legion_id": group["id"], "name": "Native app", "allow_terminal": True})
        assert response.status_code == 201, response.text
        release = response.json()
    create_deployment(source, target, release["id"], password=PASSWORD)
    runtime = replace(settings(target), sandbox_runtime="windows")
    with TestClient(create_app(runtime)) as client:
        sign_in(client)
        executed = client.post(f"/api/runtime-app/workspace/sandboxes/{card['id']}/execute", json={"command": "type result.txt & echo deployed > deployment.txt"})
        assert executed.status_code == 200, executed.text
        assert executed.json()["exit_code"] == 0 and "source-data" in executed.json()["stdout"], executed.text
        preview = client.get(f"/api/runtime-app/workspace/sandboxes/{card['id']}/files", params={"operation": "preview", "path": "deployment.txt"})
        assert preview.status_code == 200 and "deployed" in preview.json()["text"], preview.text
    assert not (source / "sandboxes" / card["id"] / "workspace/deployment.txt").exists()
