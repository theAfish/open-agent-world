from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from backend.tests.conftest import create_node
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.main import create_app
from backend.config import Settings


def board(client):
    node = create_node(client, "oaw.tasks")
    return node, f"/api/nodes/{node['id']}"


def action(client, url, name, arguments, revision):
    return client.post(f"{url}/actions/{name}", json={"arguments": arguments, "expected_revision": revision})


def test_dependency_validation_progress_and_conflicts(client):
    node, url = board(client)
    assert client.get(url + "/document").json()["value"] == {"tasks": []}
    response = action(client, url, "upsert", {"tasks": [{"id": "a", "title": "Research"}, {"id": "b", "title": "Write", "depends_on": ["a"]}]}, 0)
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["ready_ids"] == ["a"]
    assert action(client, url, "progress", {"task_id": "b", "status": "done"}, 1).status_code == 422
    assert action(client, url, "upsert", {"tasks": [{"id": "a", "title": "Research", "depends_on": ["b"]}]}, 1).status_code == 422
    assert action(client, url, "upsert", {"tasks": [{"id": "c", "title": "Missing", "depends_on": ["missing"]}]}, 1).status_code == 422
    assert action(client, url, "remove", {"task_id": "a"}, 1).status_code == 422
    assert action(client, url, "progress", {"task_id": "a", "status": "done", "note": "Sources verified"}, 1).json()["summary"]["ready_ids"] == ["b"]
    assert action(client, url, "progress", {"task_id": "b", "status": "done"}, 1).status_code == 409
    assert action(client, url, "progress", {"task_id": "b", "status": "doing"}, 2).status_code == 200
    assert action(client, url, "progress", {"task_id": "a", "status": "todo"}, 3).status_code == 422
    state = client.get(url + "/document").json()
    assert state["revision"] == 3
    assert state["value"]["tasks"][0]["note"] == "Sources verified"


def test_live_connection_permissions_are_scoped_and_revocable(client):
    node, url = board(client)
    other, _ = board(client)
    agent = create_node(client, "agent")
    edge = client.post("/api/edges", json={"source": agent["id"], "target": node["id"], "relationship": "oaw.tasks.edit"}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    def capabilities():
        return {c["kind"]: c for c in client.get(f"/api/agents/{agent['id']}/capabilities").json()["capabilities"]}
    original = capabilities()
    def invoke(kind, arguments):
        return client.portal.call(provider.invoke_tool, agent["id"], original[kind]["id"], arguments)
    invoke("oaw.tasks.upsert", {"tasks": [{"id": "a", "title": "A"}], "expected_revision": 0})
    assert client.patch(f"/api/edges/{edge['id']}", json={"relationship": "oaw.tasks.progress"}).status_code == 200
    assert set(capabilities()) == {"oaw.tasks.read", "oaw.tasks.progress"}
    with pytest.raises(PermissionDeniedError):
        invoke("oaw.tasks.upsert", {"tasks": [{"id": "b", "title": "B"}], "expected_revision": 1})
    with pytest.raises(ResourceValidationError):
        invoke("oaw.tasks.progress", {"task_id": "a", "status": "done", "title": "Cannot rename", "expected_revision": 1})
    invoke("oaw.tasks.progress", {"task_id": "a", "status": "done", "expected_revision": 1})
    assert client.get(f"/api/nodes/{other['id']}/document").json()["value"]["tasks"] == []
    client.patch(f"/api/edges/{edge['id']}", json={"relationship": "oaw.tasks.read"})
    with pytest.raises(PermissionDeniedError):
        invoke("oaw.tasks.progress", {"task_id": "a", "status": "todo", "expected_revision": 2})
    assert invoke("oaw.tasks.read", {})["summary"]["done"] == 1
    client.delete(f"/api/edges/{edge['id']}")
    with pytest.raises(PermissionDeniedError):
        invoke("oaw.tasks.read", {})


def test_legion_presets_keep_plan_reset_progress_and_isolate_instances(client):
    node, url = board(client)
    agent = create_node(client, "agent")
    action(client, url, "upsert", {"tasks": [{"id": "a", "title": "Research", "status": "done", "note": "Old result"}, {"id": "b", "title": "Write", "depends_on": ["a"]}]}, 0)
    response = client.post("/api/legions", json={"name": "Research team", "node_ids": [node["id"], agent["id"]]})
    assert response.status_code == 201, response.text
    template = response.json()
    instance = client.post(f"/api/legions/{template['id']}/instances", json={"as_group": True})
    assert instance.status_code == 201, instance.text
    copied = next(n for n in instance.json()["nodes"] if n["type"] == "oaw.tasks")
    copied_url = f"/api/nodes/{copied['id']}"
    value = client.get(copied_url + "/document").json()
    assert value["summary"]["done"] == 0
    assert value["value"]["tasks"][0]["note"] == ""
    assert value["value"]["tasks"][1]["depends_on"] == ["a"]
    assert client.get(url + "/document").json()["summary"]["done"] == 1
    action(client, copied_url, "upsert", {"tasks": [{"id": "a", "title": "Changed"}]}, value["revision"])
    assert client.get(url + "/document").json()["value"]["tasks"][0]["title"] == "Research"
    assert client.delete(copied_url).status_code == 200
    # Recreating the same ID must not inherit a deleted board's document.
    create_node(client, "oaw.tasks", id=copied["id"])
    assert client.get(copied_url + "/document").json()["value"]["tasks"] == []


def test_board_survives_restart(tmp_path):
    settings = Settings.for_data_root(tmp_path / "world")
    with TestClient(create_app(settings)) as client:
        node, url = board(client)
        assert action(client, url, "upsert", {"tasks": [{"id": "a", "title": "Persisted"}]}, 0).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get(url + "/document").json()["value"]["tasks"][0]["title"] == "Persisted"


def test_agent_tool_callables_support_optional_fields_and_scoped_invocation(client):
    from backend.agents.tools import build_scoped_tool_callables
    node, url = board(client)
    agent = create_node(client, "agent")
    client.post("/api/edges", json={"source": agent["id"], "target": node["id"], "relationship": "oaw.tasks.edit"})
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, agent["id"])
    tools = build_scoped_tool_callables(provider, agent["id"], definitions)
    writer = next(tool for tool in tools if tool.__name__.startswith("write_tasks_"))
    update = next(tool for tool in tools if tool.__name__.startswith("update_task_progress_"))
    reader = next(tool for tool in tools if tool.__name__.startswith("read_tasks_"))
    client.portal.call(lambda: writer(tasks=[{"id": "a", "title": "Work", "note": "Preserve me"}], expected_revision=0))
    result = client.portal.call(lambda: update(task_id="a", status="done", expected_revision=1))
    assert result["summary"]["done"] == 1
    assert client.portal.call(reader)["value"]["tasks"][0]["note"] == "Preserve me"
