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
