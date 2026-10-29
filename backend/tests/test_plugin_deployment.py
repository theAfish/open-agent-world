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


def test_project_bridge_requires_prepare_prompt_save_and_failure_grants(monkeypatch):
    import json
    from types import SimpleNamespace
    calls = []
    async def run(node_id, request, services, progress=None):
        calls.append((node_id, request.space_id, request.project_ids, request.rerun))
        assert request.model == "oaw:model:pinned"
        return {"projections": [{"id": "result-1"}]}
    monkeypatch.setattr("backend.deployment_workspace.run_project_space", run)
    required = {"projection_prepare", "projection_prompt", "projection_save", "projection_fail"}
    access = {"granted": DeploymentSurface(resource_actions={k: set() for k in required}).model_dump(mode="json")}
    for missing in required:
        access[missing] = DeploymentSurface(resource_actions={k: set() for k in required - {missing}}).model_dump(mode="json")
    app = FastAPI()
    from types import SimpleNamespace
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(world=SimpleNamespace(
        get_card=lambda node: SimpleNamespace(config={"default_model": "oaw:model:pinned"})))
    app.include_router(workspace_router({"permissions": {}, "plugin_access": access}))
    body = {"space_id": "space-1", "project_ids": ["project-1"], "model": "oaw:model:m1"}
    with TestClient(app) as client:
        ok = client.post("/workspace/knowledge/granted/project-space", json=body)
        assert ok.status_code == 200, ok.text
        assert json.loads(ok.text.splitlines()[-1])["result"] == {"projections": [{"id": "result-1"}]}
        for missing in required | {"unpublished"}:
            assert client.post(f"/workspace/knowledge/{missing}/project-space", json=body).status_code == 404
        assert client.post("/workspace/knowledge/granted/knowledge", json={"draft_id": "old", "model": "m"}).status_code == 404
    assert calls == [("granted", "space-1", ["project-1"], False)]


def test_optional_projection_review_requires_read_and_revision_grants(monkeypatch):
    calls = []
    async def run(node_id, request, services, progress=None):
        calls.append((node_id, request.projection_id, request.instruction))
        assert request.model == "oaw:model:pinned"
        return {"projection": {"id": "revision-2"}}
    monkeypatch.setattr("backend.deployment_workspace.run_projection_review", run)
    required = {"projection_review_prompt", "projection_revise"}
    access = {"granted": DeploymentSurface(resource_actions={k: set() for k in required}).model_dump(mode="json")}
    for missing in required:
        access[missing] = DeploymentSurface(resource_actions={k: set() for k in required - {missing}}).model_dump(mode="json")
    app = FastAPI()
    from types import SimpleNamespace
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(world=SimpleNamespace(
        get_card=lambda node: SimpleNamespace(config={"default_model": "oaw:model:pinned"})))
    app.include_router(workspace_router({"permissions": {}, "plugin_access": access}))
    body = {"projection_id": "result-1", "model": "oaw:model:m1", "instruction": "Normalize reported units"}
    with TestClient(app) as client:
        assert client.post("/workspace/knowledge/granted/review-projection", json=body).status_code == 200
        for missing in required | {"unpublished"}:
            assert client.post(f"/workspace/knowledge/{missing}/review-projection", json=body).status_code == 404
    assert calls == [("granted", "result-1", "Normalize reported units")]


def test_knowledge_extract_bridge_needs_both_halves_published(monkeypatch):
    """Same gate, for the agent-free experiment extraction: a release
    only reaches it once both experiment_extract_prompt and experiment_save are
    published for this node."""
    from types import SimpleNamespace
    calls = []
    async def fake_run_experiment_extract(node_id, request, services):
        calls.append((node_id, request.bundle_id, request.replace_drafts))
        assert request.model == "oaw:model:pinned"
        return {"records": [{"id": "record-1"}], "invalid": 0, "bundle_id": request.bundle_id, "truncated": False}
    monkeypatch.setattr("backend.deployment_workspace.run_experiment_extract", fake_run_experiment_extract)

    body = {"bundle_id": "bundle-1", "model": "oaw:model:m1", "replace_drafts": True}
    both = DeploymentSurface(resource_actions={
        "experiment_extract_prompt": set(), "experiment_save": set()}).model_dump(mode="json")
    only_read = DeploymentSurface(resource_actions={"experiment_extract_prompt": set()}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(world=SimpleNamespace(
        get_card=lambda node: SimpleNamespace(config={"default_model": "oaw:model:pinned"})))
    app.include_router(workspace_router({"permissions": {}, "plugin_access": {
        "granted": both, "half": only_read}}))
    with TestClient(app) as client:
        ok = client.post("/workspace/knowledge/granted/extract", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json()["records"] == [{"id": "record-1"}]
        assert client.post("/workspace/knowledge/half/extract", json=body).status_code == 404
        assert client.post("/workspace/knowledge/unpublished/extract", json=body).status_code == 404
    assert calls == [("granted", "bundle-1", True)]


def test_knowledge_bridge_streams_progress_then_the_result_or_the_error():
    """The engineering routes answer as NDJSON so the card can show where a long
    extraction is; the last line always carries the result or the failure."""
    import json

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from backend.api.knowledge_bridge import _stream

    app = FastAPI()

    @app.post("/ok")
    async def ok():
        async def run(progress):
            await progress({"stage": "read", "done": 0, "total": 2, "message": "first"})
            await progress({"stage": "saved", "done": 1, "total": 2})
            return {"sources": [{"entities": 3}]}
        return _stream(run)

    @app.post("/fail")
    async def fail():
        async def run(progress):
            await progress({"stage": "findings", "done": 0, "total": 3})
            raise HTTPException(422, "Nothing published yet")
        return _stream(run)

    with TestClient(app) as client:
        response = client.post("/ok")
        assert response.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(line) for line in response.text.splitlines()]
        assert lines == [{"event": "progress", "stage": "read", "done": 0, "total": 2, "message": "first"},
                         {"event": "progress", "stage": "saved", "done": 1, "total": 2},
                         {"event": "done", "result": {"sources": [{"entities": 3}]}}]
        failed = [json.loads(line) for line in client.post("/fail").text.splitlines()]
        assert failed[-1] == {"event": "error", "status": 422, "detail": "Nothing published yet"}
