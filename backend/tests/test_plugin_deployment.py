from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_services
from backend.deployment_workspace import workspace_router
from backend.deployments import PublishRequest, release_spec
from backend.errors import ResourceValidationError
from open_agent_world.plugin_api import DeploymentSurface, NodeDeploymentDefinition


def test_plugin_sections_freeze_only_visible_access():
    contract = NodeDeploymentDefinition(
        surface=DeploymentSurface(config_fields={"heading"}),
        sections={"public": DeploymentSurface(document_fields={"text"}, document_actions={"save"}),
                  "private": DeploymentSurface(document_fields={"secret"}, document_actions={"configure"})})
    legion = SimpleNamespace(id="legion", type="legion", name="Test", config={"workspace_layout": {
        "version": 2, "root": {"kind": "pane", "view": {"card_id": "plugin"}},
        "hidden_sections": [{"card_id": "plugin", "section_id": "private"}]}})
    node = SimpleNamespace(id="plugin", type="vendor.widget", name="Widget", parent_id="legion")
    definition = SimpleNamespace(deployment=contract)
    services = SimpleNamespace(
        world=SimpleNamespace(get_card=lambda id: legion if id == "legion" else node),
        plugins=SimpleNamespace(node_type=lambda type: definition, catalog=lambda: SimpleNamespace(plugins=[])),
        settings=SimpleNamespace(agent_runtime="core.mock", sandbox_runtime=None, plugin_directories=[]))
    release = release_spec(services, PublishRequest(legion_id="legion", name="Test"))
    assert release["permissions"]["plugin"] == ["public"]
    assert release["plugin_access"]["plugin"]["document_actions"] == ["save"]
    assert release["plugin_access"]["plugin"]["document_fields"] == ["text"]
    assert release["plugin_access"]["plugin"]["config_fields"] == ["heading"]
    # Publishing just a section does not grant whole-card fields or other sections.
    legion.config["workspace_layout"]["root"]["view"]["section_id"] = "public"
    release = release_spec(services, PublishRequest(legion_id="legion", name="Test"))
    assert release["plugin_access"]["plugin"]["config_fields"] == []
    legion.config["workspace_layout"]["root"]["view"]["section_id"] = "unknown"
    with pytest.raises(ResourceValidationError):
        release_spec(services, PublishRequest(legion_id="legion", name="Test"))


def test_resource_results_projected_and_section_names_cannot_grant_builtin_access(monkeypatch):
    calls = []
    async def resource(services, node_id, action, request):
        calls.append(action)
        return {"answer": 42, "connection": "private"}
    monkeypatch.setattr("backend.deployment_workspace.invoke_resource_action", resource)
    access = DeploymentSurface(resource_actions={"query": {"answer"}}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {"plugin": ["tasks", "terminal"]}, "plugin_access": {"plugin": access}}))
    with TestClient(app) as client:
        assert client.post("/workspace/nodes/plugin/resource/query", json={"arguments": {}}).json() == {"answer": 42}
        for node, action in (("plugin", "delete"), ("other", "query")):
            assert client.post(f"/workspace/nodes/{node}/resource/{action}", json={"arguments": {}}).status_code == 404
        assert client.get("/workspace/nodes/plugin/execution").status_code == 404
        assert client.get("/workspace/sandboxes/plugin").status_code == 404
        assert client.post("/workspace/nodes/plugin/actions/upsert", json={"arguments": {}}).status_code == 404
    assert calls == ["query"]


def test_knowledge_project_bridge_needs_both_halves_published(monkeypatch):
    """The deployment route for the knowledge base's model-projection bridge only
    runs once a release grants both the read half (projection_prompt) and the write
    half (save_projection) — either alone would let a release read without ever
    writing, or write without the plugin ever building the prompt it validates."""
    calls = []
    async def fake_run_projection(node_id, request, services):
        calls.append((node_id, request.schema_id))
        return {"projection": {"id": "projection-1"}, "truncated": False, "record_id": "record-1"}
    monkeypatch.setattr("backend.deployment_workspace.run_projection", fake_run_projection)

    body = {"schema_id": "schema-1", "model": "oaw:model:m1", "record_id": "record-1"}
    both = DeploymentSurface(resource_actions={"projection_prompt": set(), "save_projection": set()}).model_dump(mode="json")
    only_read = DeploymentSurface(resource_actions={"projection_prompt": set()}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {}, "plugin_access": {
        "granted": both, "half": only_read}}))
    with TestClient(app) as client:
        ok = client.post("/workspace/knowledge/granted/project", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"projection": {"id": "projection-1"}, "truncated": False, "record_id": "record-1"}
        assert client.post("/workspace/knowledge/half/project", json=body).status_code == 404
        assert client.post("/workspace/knowledge/unpublished/project", json=body).status_code == 404
    assert calls == [("granted", "schema-1")]


def test_knowledge_assemble_bridge_needs_both_halves_published(monkeypatch):
    """Same gate as projection, for the experiment-record merge bridge: a release
    only reaches it once both experiment_assemble_prompt and experiment_save are
    published for this node."""
    calls = []
    async def fake_run_experiment_assemble(node_id, request, services):
        calls.append((node_id, tuple(request.projection_ids)))
        return {"record": {"id": "record-1", "status": "draft"}}
    monkeypatch.setattr("backend.deployment_workspace.run_experiment_assemble",
                        fake_run_experiment_assemble)

    body = {"projection_ids": ["p1", "p2"], "model": "oaw:model:m1"}
    both = DeploymentSurface(resource_actions={
        "experiment_assemble_prompt": set(), "experiment_save": set()}).model_dump(mode="json")
    only_read = DeploymentSurface(
        resource_actions={"experiment_assemble_prompt": set()}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {}, "plugin_access": {
        "granted": both, "half": only_read}}))
    with TestClient(app) as client:
        ok = client.post("/workspace/knowledge/granted/assemble", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"record": {"id": "record-1", "status": "draft"}}
        assert client.post("/workspace/knowledge/half/assemble", json=body).status_code == 404
        assert client.post("/workspace/knowledge/unpublished/assemble", json=body).status_code == 404
    assert calls == [("granted", ("p1", "p2"))]
