from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_services
from backend.deployment_workspace import workspace_router
from backend.plugins.deployment import DeploymentSurface
from backend.publication_scope import public_value


def test_common_projection_removes_nested_credentials_without_mutating_business_data():
    original = {"answer": 42, "items": [{"name": "sample", "API_KEY": "private", "provider_config": {"token": "private"}}],
                "system_instruction": "private", "connection_secret": "private", "status": "ready"}
    assert public_value(original) == {"answer": 42, "items": [{"name": "sample"}], "status": "ready"}
    assert original["items"][0]["API_KEY"] == "private"


def test_outside_node_is_rejected_before_reading_session_or_document():
    def unavailable(node_id):
        raise AssertionError("Unpublished nodes must not be read")

    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(world=SimpleNamespace(get_card=unavailable))
    app.include_router(workspace_router({"permissions": {"published": []}}))
    with TestClient(app) as client:
        response = client.get("/workspace/nodes/outside/document", headers={"X-OAW-State-Session": "other-session"})
        assert response.status_code == 404


def test_declared_resource_results_also_cross_the_common_credential_boundary(monkeypatch):
    async def run(services, node_id, action, request):
        return {"answer": 42, "details": {"api_key": "private", "score": .5}, "password": "private"}

    monkeypatch.setattr("backend.deployment_workspace.invoke_resource_action", run)
    access = DeploymentSurface(resource_actions={"read": {"answer", "details", "password"}}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {"node": []}, "plugin_access": {"node": access}}))
    with TestClient(app) as client:
        assert client.post("/workspace/nodes/node/resource/read", json={"arguments": {}}).json() == {"answer": 42, "details": {"score": .5}}
        assert client.post("/workspace/nodes/outside/resource/read", json={"arguments": {}}).status_code == 404
