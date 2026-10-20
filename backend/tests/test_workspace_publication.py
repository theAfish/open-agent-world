"""Publish the unchanged research composition through the ordinary card APIs."""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.deploy import create_deployment
from backend.main import create_app
from backend.plugins.deployment import merged_surfaces, project_document, resolve_surface
from backend.tests.test_conversation_attachments import upload
from backend.tests.test_deployments import PASSWORD, node, settings, sign_in
from open_agent_world.plugin_api import (
    DeploymentSurface, NodeDeploymentDefinition, NodeDocumentAction,
    NodeDocumentDefinition, NodeDocumentDownload,
)

ROOT = "/api/runtime-app/workspace"
STRUCTURE = b"2\nwater fragment\nO 0 0 0\nH 0 0 1\n"


def prepare_research_source(path, *, offline_sandbox=False):
    with TestClient(create_app(settings(path)), client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/legions/presets/matcreator.native-research/instances", json={})
        assert response.status_code == 201, response.text
        nodes = {item["type"]: item for item in response.json()["nodes"]}
        if offline_sandbox:
            assert client.patch(f"/api/nodes/{nodes['sandbox']['id']}",
                                json={"config": {"network_enabled": False}}).status_code == 200
        chat, legion = nodes["conversation"], nodes["legion"]
        hidden = node(client, "conversation", "Private conversation")
        stranger = node(client, "agent", "Unconnected agent")
        session = client.get(f"/api/conversations/{chat['id']}").json()["sessions"][0]["id"]
        hidden_session = client.get(f"/api/conversations/{hidden['id']}").json()["sessions"][0]["id"]
        file = upload(client, f"/api/conversations/{chat['id']}/sessions/{session}", "crystal.xyz", STRUCTURE)
        hidden_file = upload(client, f"/api/conversations/{hidden['id']}/sessions/{hidden_session}", "private.txt", b"PRIVATE FILE")
        collection = nodes["core.artifact-collection"]["id"]
        assert client.put(f"/api/artifact-collections/{collection}/references/{file['version_id']}").status_code == 200
        request = {"legion_id": legion["id"], "name": "Research workspace"}
        before = client.get("/api/deployments").json()
        preview = client.post("/api/deployments/preview", json=request)
        assert preview.status_code == 200 and preview.json()["ready"], preview.text
        assert len(preview.json()["panels"]) == 10
        assert client.get("/api/deployments").json() == before
        response = client.post("/api/deployments", json=request)
        assert response.status_code == 201, response.text
        release = response.json()
        assert release["layout"]["root"] == legion["config"]["workspace_layout"]["root"]
        # Preview/publish do not alter local engineering access or its saved layout.
        assert client.get("/api/world").status_code == 200
    return dict(nodes=nodes, session=session, hidden_session=hidden_session, hidden=hidden,
                stranger=stranger, file=file, hidden_file=hidden_file, release=release)


def test_full_research_workspace_reuses_handlers_and_scopes_cross_card_access(tmp_path):
    source, target = tmp_path / "source", tmp_path / "runtime"
    records = prepare_research_source(source)
    nodes = records["nodes"]
    create_deployment(source, target, records["release"]["id"], password=PASSWORD)
    with TestClient(create_app(settings(target))) as client:
        sign_in(client)
        bootstrap = client.get("/api/runtime-app").json()
        assert len(bootstrap["edges"]) == 2
        assert all(edge["relationship"] == "core.file-preview" for edge in bootstrap["edges"])
        assert {edge["target"] for edge in bootstrap["edges"]} == {nodes["conversation"]["id"], nodes["sandbox"]["id"]}
        assert records["stranger"]["id"] not in str(bootstrap)
        assert records["hidden"]["id"] not in str(bootstrap)
        graph = nodes["core.execution-graph"]["id"]
        jobs = nodes["core.jobs"]["id"]
        knowledge = nodes["matcreator.kdg"]["id"]
        viewer = nodes["science.structure-viewer"]["id"]
        collection = nodes["core.artifact-collection"]["id"]
        headers = {"X-OAW-State-Session": records["session"]}
        agent = nodes["matcreator.agent"]["id"]
        for path in (f"/nodes/{graph}/execution-sources", f"/nodes/{jobs}/jobs"):
            response = client.get(ROOT + path, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["sources"][0]["id"] == agent
            assert client.get(ROOT + path, headers={"X-OAW-State-Session": records["hidden_session"]}).status_code == 404
        assert client.get(f"{ROOT}/nodes/{graph}/execution-sources/{agent}", headers=headers).status_code == 200
        assert client.get(f"{ROOT}/nodes/{graph}/execution-sources/{records['stranger']['id']}", headers=headers).status_code == 403
        assert client.get(f"{ROOT}/nodes/{agent}/execution-sources").status_code == 404
        assert client.get(f"{ROOT}/nodes/{agent}/jobs").status_code == 404
        assert client.post(f"{ROOT}/nodes/{jobs}/jobs/unknown/actions",
                           json={"action": "cancel", "revision": 1}, headers=headers).status_code == 404

        # A projected read has a different schema from the stored document.
        search = client.post(f"{ROOT}/nodes/{knowledge}/actions/search", json={"arguments": {}})
        assert search.status_code == 200, search.text
        assert search.json()["value"]["nodes"]
        assert "statistics" in search.json()["value"]
        entry = {"title": "Published review", "type": "memory", "summary": "Original document handler"}
        edited = client.post(f"{ROOT}/nodes/{knowledge}/actions/edit",
                            json={"arguments": entry, "expected_revision": search.json()["revision"]})
        assert edited.status_code == 200, edited.text
        assert any(value["title"] == entry["title"] for value in edited.json()["value"]["entries"])
        assert client.post(f"{ROOT}/nodes/{knowledge}/actions/edit",
                           json={"arguments": entry, "expected_revision": search.json()["revision"]}).status_code == 409
        assert client.get(f"{ROOT}/nodes/{knowledge}/document/downloads/snapshots").status_code == 200
        for action in ("replace", "configure"):
            assert client.post(f"{ROOT}/nodes/{knowledge}/actions/{action}",
                               json={"arguments": {}, "expected_revision": 0}).status_code == 404
        assert client.post(f"{ROOT}/nodes/{knowledge}/transformations/assimilate", json={}).status_code == 404

        reference = {"kind": "conversation", "source_id": nodes["conversation"]["id"],
                     "session_id": records["session"], **{key: records["file"][key] for key in ("version_id", "path")}}
        response = client.post(f"{ROOT}/nodes/{viewer}/file-preview", json=reference)
        assert response.status_code == 200, response.text
        assert base64.b64decode(response.json()["data"]) == STRUCTURE
        private_ref = {**reference, "source_id": records["hidden"]["id"], "session_id": records["hidden_session"],
                       **{key: records["hidden_file"][key] for key in ("version_id", "path")}}
        assert client.post(f"{ROOT}/nodes/{viewer}/file-preview", json=private_ref).status_code == 404
        assert client.post(f"{ROOT}/nodes/{knowledge}/file-preview", json=reference).status_code == 404
        assert client.post(f"{ROOT}/nodes/{viewer}/file-preview",
                           json={"kind": "sandbox", "source_id": nodes["sandbox"]["id"], "root": "resource:private", "path": "key"}).status_code == 404
        assert client.post(f"{ROOT}/nodes/{viewer}/file-preview", json={**reference, "path": "../private"}).status_code == 422

        versions = client.get(f"{ROOT}/artifact-collections/{collection}/versions").json()
        assert [v["version_id"] for v in versions] == [records["file"]["version_id"]]
        version_url = f"{ROOT}/artifact-collections/{collection}/versions/{records['file']['version_id']}"
        assert client.get(version_url + "/content", params={"path": records["file"]["path"]}).content == STRUCTURE
        assert client.get(version_url + "/preview", params={"path": records["file"]["path"]}).status_code == 200
        assert client.get(f"{ROOT}/artifact-collections/{collection}/versions/{records['hidden_file']['version_id']}/content",
                          params={"path": records["hidden_file"]["path"]}).status_code == 403
        assert client.delete(version_url).status_code == 404
        assert client.get(ROOT + "/artifacts/retained").status_code == 404
        for path in ("/world", "/settings/models", f"/agents/{agent}"):
            assert client.get(ROOT + path).status_code == 404
    with TestClient(create_app(settings(target))) as client:
        sign_in(client)
        assert "Published review" in client.get(f"{ROOT}/nodes/{knowledge}/document").text


def test_conversation_run_details_reuse_archives_without_exposing_unpublished_runs_or_model_requests(tmp_path):
    from urllib.parse import quote
    from backend.runs.history import RunActivityHistory
    from backend.runs.invocation_logs import RunInvocationLogs
    from backend.runs.reasoning import RunReasoning

    source, target = tmp_path / "source", tmp_path / "runtime"
    records = prepare_research_source(source)
    create_deployment(source, target, records["release"]["id"], password=PASSWORD)
    app = create_app(settings(target))
    with TestClient(app) as client:
        services = app.state.services
        agent = records["nodes"]["matcreator.agent"]["id"]
        store = services.run_manager.store
        visible = store.create(agent_id=agent, runtime_provider_id="core.mock", caller_kind="conversation",
                               caller_id=records["nodes"]["conversation"]["id"], context_id=records["session"])
        child = store.create(agent_id=agent, runtime_provider_id="core.mock", caller_kind="agent",
                             caller_id=agent, parent_run_id=visible.run_id)
        hidden = store.create(agent_id=agent, runtime_provider_id="core.mock", caller_kind="conversation",
                              caller_id=records["hidden"]["id"], context_id=records["hidden_session"])
        unrelated = store.create(agent_id=agent, runtime_provider_id="core.mock", caller_kind="user",
                                 context_id=records["session"])
        logs = RunInvocationLogs(services.state, visible.run_id)
        identity = logs.start_tool("research", "call/one", 0, "execute_command", {"command": "python probe.py"})
        logs.finish_tool(identity, {"exit_code": 0, "stdout": "NETWORK_OK"})
        history = RunActivityHistory(services.database, visible.run_id)
        history.public("tool_completed", {"call_id": "public", "name": "inspect_sandbox",
                       "response": {"network_enabled": True}}, "event", visible.created_at.isoformat())
        public_id = next(row["id"] for row in history.page(0, 50)["items"] if row.get("activity_id"))
        RunReasoning(services.state, visible.run_id).save({
            "provider_message_id": "thought/one", "text": "Checking the result.", "streaming": False})
        base = ROOT + "/runs/" + visible.run_id
        assert client.get(base + "/activity").status_code == 401
        sign_in(client)
        assert client.get(base + "/activity").json()["total"] == 3
        assert client.get(base + "/activity/" + quote(public_id, safe="")).json()["response"]["network_enabled"]
        detail = client.get(base + "/invocations/tools/" + quote(identity, safe=""))
        assert detail.status_code == 200, detail.text
        assert detail.json()["response"]["stdout"] == "NETWORK_OK"
        assert client.get(base + "/invocations/tools").json()["total"] == 1
        assert client.get(base + "/reasoning/thought%2Fone").json()["text"] == "Checking the result."
        assert client.get(base + "/reasoning").json()["total"] == 1
        assert client.get(ROOT + "/runs/" + child.run_id + "/activity").status_code == 200
        for run in (hidden, unrelated):
            assert client.get(ROOT + "/runs/" + run.run_id + "/activity").status_code == 404
            assert client.get(ROOT + "/runs/" + run.run_id + "/invocations/tools").status_code == 404
        for path in ("/invocations/models", "/invocations/models/any", "/invocations/unknown", "/cancel", ""):
            assert client.get(base + path).status_code == 404
        assert client.post(base + "/cancel").status_code == 404
        assert client.get(ROOT + "/runs").status_code == 404
        assert client.get(ROOT + "/runs/missing/activity").status_code == 404


def test_document_shorthand_is_explicit_freezes_registered_operations_and_preserves_projections():
    class Document(BaseModel):
        text: str = ""

    definition = SimpleNamespace(document=NodeDocumentDefinition(
        model=Document, actions={
            "save": NodeDocumentAction(lambda value, args: args),
            "search": NodeDocumentAction(lambda value, args: {"hits": [value]}, read_only=True, project=True),
        }, downloads={"text": lambda value: NodeDocumentDownload("text.txt", value["text"].encode())}))
    edit = resolve_surface(NodeDeploymentDefinition.from_document().surface, definition)
    read = resolve_surface(NodeDeploymentDefinition.from_document(read_only=True).surface, definition)
    assert edit.document_actions == {"save", "search"}
    assert read.document_actions == {"search"}
    assert read.downloads == {"text"}
    assert not edit.config_fields and not edit.resource_actions and not edit.execution
    assert "replace" not in edit.document_actions
    snapshot = {"revision": 7, "value": {"hits": ["business projection"]}}
    assert project_document(snapshot, merged_surfaces([read])) == snapshot
    definition.document.actions["later"] = NodeDocumentAction(lambda value, args: value)
    assert "later" not in edit.document_actions
    # Existing fine-grained declarations never inherit the whole document.
    explicit = resolve_surface(DeploymentSurface(document_fields={"text"}), definition)
    assert project_document({"revision": 0, "value": {"text": "public", "secret": "private"}},
                            merged_surfaces([explicit]))["value"] == {"text": "public"}


def test_preview_collects_all_blockers_and_publish_rechecks(client):
    first = node(client, "data.sqlite", "Database")
    second = node(client, "sandbox", "Terminal")
    grouped = client.post("/api/legion-groups", json={"name": "Check", "node_ids": [first["id"], second["id"]]}).json()
    legion = next(item for item in grouped if item["type"] == "legion")
    views = [{"card_id": first["id"]}, {"card_id": second["id"], "section_id": "terminal"}]
    layout = {"version": 2, "root": {"kind": "tabs", "views": views, "active_view": views[0]}}
    assert client.patch(f"/api/nodes/{legion['id']}", json={"config": {"workspace_layout": layout}}).status_code == 200
    request = {"legion_id": legion["id"], "name": "Check"}
    preview = client.post("/api/deployments/preview", json=request).json()
    assert not preview["ready"] and len(preview["issues"]) == 2
    assert {issue["card_id"] for issue in preview["issues"]} == {first["id"], second["id"]}
    assert client.get("/api/deployments").json() == []
    assert client.post("/api/deployments", json=request).status_code == 422
