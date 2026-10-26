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


def test_knowledge_bridge_needs_both_halves_published(monkeypatch):
    """The deployment route for the knowledge base's model bridge only runs once a
    release grants both the read half (knowledge_drafts, knowledge_extract_prompt) and
    the write half (knowledge_save) — either alone would let a release read without
    ever writing, or write without the plugin ever building the prompt it validates."""
    calls = []
    async def fake_run_knowledge_extract(node_id, request, services):
        calls.append((node_id, request.draft_id, request.replace))
        return {"draft": {"id": "draft-1"}, "sources": []}
    monkeypatch.setattr("backend.deployment_workspace.run_knowledge_extract", fake_run_knowledge_extract)

    body = {"draft_id": "draft-1", "model": "oaw:model:m1"}
    both = DeploymentSurface(resource_actions={"knowledge_drafts": set(), "knowledge_extract_prompt": set(),
                                               "knowledge_save": set()}).model_dump(mode="json")
    only_read = DeploymentSurface(resource_actions={"knowledge_drafts": set(), "knowledge_extract_prompt": set()}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {}, "plugin_access": {
        "granted": both, "half": only_read}}))
    with TestClient(app) as client:
        ok = client.post("/workspace/knowledge/granted/knowledge", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"draft": {"id": "draft-1"}, "sources": []}
        assert client.post("/workspace/knowledge/half/knowledge", json=body).status_code == 404
        assert client.post("/workspace/knowledge/unpublished/knowledge", json=body).status_code == 404
    assert calls == [("granted", "draft-1", False)]


def test_knowledge_records_bridge_needs_both_halves_published(monkeypatch):
    """Same gate for records read from a literature source: record_extract_prompt and
    record_save must both be published."""
    calls = []
    async def fake_run_record_extract(node_id, request, services):
        calls.append((node_id, request.schema_id, request.source_ids))
        return {"records": [], "invalid": 0, "sources": [], "schema_id": request.schema_id}
    monkeypatch.setattr("backend.deployment_workspace.run_record_extract", fake_run_record_extract)

    body = {"schema_id": "schema-1", "source_ids": ["source-1"], "model": "oaw:model:m1"}
    both = DeploymentSurface(resource_actions={"record_extract_prompt": set(), "record_save": set()}).model_dump(mode="json")
    only_read = DeploymentSurface(resource_actions={"record_extract_prompt": set()}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {}, "plugin_access": {
        "granted": both, "half": only_read}}))
    with TestClient(app) as client:
        assert client.post("/workspace/knowledge/granted/records", json=body).status_code == 200
        assert client.post("/workspace/knowledge/half/records", json=body).status_code == 404
    assert calls == [("granted", "schema-1", ["source-1"])]


def test_knowledge_extract_bridge_needs_both_halves_published(monkeypatch):
    """Same gate, for the agent-free experiment extraction: a release
    only reaches it once both experiment_extract_prompt and experiment_save are
    published for this node."""
    calls = []
    async def fake_run_experiment_extract(node_id, request, services):
        calls.append((node_id, request.bundle_id, request.replace_drafts))
        return {"records": [{"id": "record-1"}], "invalid": 0, "bundle_id": request.bundle_id, "truncated": False}
    monkeypatch.setattr("backend.deployment_workspace.run_experiment_extract", fake_run_experiment_extract)

    body = {"bundle_id": "bundle-1", "model": "oaw:model:m1", "replace_drafts": True}
    both = DeploymentSurface(resource_actions={
        "experiment_extract_prompt": set(), "experiment_save": set()}).model_dump(mode="json")
    only_read = DeploymentSurface(resource_actions={"experiment_extract_prompt": set()}).model_dump(mode="json")
    app = FastAPI()
    app.dependency_overrides[get_services] = lambda: None
    app.include_router(workspace_router({"permissions": {}, "plugin_access": {
        "granted": both, "half": only_read}}))
    with TestClient(app) as client:
        ok = client.post("/workspace/knowledge/granted/extract", json=body)
        assert ok.status_code == 200, ok.text
        assert ok.json()["records"] == [{"id": "record-1"}]
        assert client.post("/workspace/knowledge/half/extract", json=body).status_code == 404
        assert client.post("/workspace/knowledge/unpublished/extract", json=body).status_code == 404
    assert calls == [("granted", "bundle-1", True)]


def test_knowledge_chunks_group_sections_split_oversized_ones_and_stop_at_the_cap():
    """The agent-free knowledge extraction reads a long document a few sections at a
    time; a short one stays a single call, as it was before chunking existed."""
    from backend.api.knowledge_bridge import _chunks

    sections = [{"index": 0, "heading_path": "", "offset": 0, "length": 10},
                {"index": 1, "heading_path": "A", "offset": 10, "length": 30},
                {"index": 2, "heading_path": "B", "offset": 40, "length": 50},
                {"index": 3, "heading_path": "C", "offset": 90, "length": 10}]
    assert _chunks(sections, 100, chunk_chars=200) == [(0, 100, [])]
    assert _chunks(sections, 100, chunk_chars=45) == [(0, 40, ["A"]), (40, 45, ["B"]), (85, 5, ["B"]), (90, 10, ["C"])]
    assert _chunks(sections, 100, chunk_chars=45, max_chars=60) == [(0, 40, ["A"]), (40, 20, ["B"])]
    assert _chunks([], 0) == []


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
