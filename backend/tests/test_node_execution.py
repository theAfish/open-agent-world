from __future__ import annotations

import asyncio
from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.tests.conftest import create_node
from backend.tests.test_task_board import board, action
from backend.tests.test_runs import RecordingProvider
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError


def setup_board(client, tasks=None):
    node, url = board(client)
    agent = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    edge = client.post("/api/edges", json={"source": node["id"], "target": agent["id"], "relationship": "oaw.tasks.executor"})
    assert edge.status_code == 201, edge.text
    result = action(client, url, "configure_execution", {"default_executor_id": agent["id"]}, 0)
    assert result.status_code == 200, result.text
    result = action(client, url, "upsert", {"tasks": tasks or [
        {"id": "a", "title": "Research"}, {"id": "b", "title": "Summarize", "depends_on": ["a"]}]}, 1)
    assert result.status_code == 200, result.text
    return node, url, agent, edge.json()


def start(client, url, item=None):
    revision = client.get(url + "/document").json()["revision"]
    return client.post(url + "/execution/start", json={"expected_revision": revision, "item_id": item})


def settled(client, node):
    async def wait():
        worker = client.app.state.services.node_execution.workers.get(node["id"])
        if worker:
            await asyncio.wait_for(asyncio.shield(worker), 5)
    client.portal.call(wait)
    return client.get(f"/api/nodes/{node['id']}/execution").json()


def test_dependency_execution_durable_results_and_template_bindings(tmp_path):
    settings = Settings.for_data_root(tmp_path / "world")
    with TestClient(create_app(settings)) as client:
        node, url, agent, _ = setup_board(client)
        assert start(client, url).status_code == 200
        result = settled(client, node)
        assert [a["item_id"] for a in result["attempts"]] == ["a", "b"]
        assert all(a["status"] == "succeeded" for a in result["attempts"])
        document = client.get(url + "/document").json()
        assert document["summary"]["done"] == 2
        text = document["value"]["tasks"][1]["note"]
        assert result["attempts"][0]["run_id"] in text
        assert "Research" in text
        assert start(client, url).status_code == 422
        template = client.post("/api/legions", json={"name": "Reusable team", "node_ids": [node["id"], agent["id"]]})
        assert template.status_code == 201, template.text
        restored = client.post(f"/api/legions/{template.json()['id']}/instances", json={"as_group": True})
        assert restored.status_code == 201, restored.text
        copied = next(n for n in restored.json()["nodes"] if n["type"] == "oaw.tasks")
        copied_agent = next(n for n in restored.json()["nodes"] if n["type"] == "agent")
        copied_doc = client.get(f"/api/nodes/{copied['id']}/document").json()
        assert copied_doc["value"]["execution"]["default_executor_id"] == copied_agent["id"]
        assert copied_doc["summary"]["done"] == 0
        assert all(t["last_run_id"] is None for t in copied_doc["value"]["tasks"])
        assert client.get(f"/api/nodes/{copied['id']}/execution").json()["attempts"] == []
    with TestClient(create_app(settings)) as client:
        assert client.get(url + "/execution").json()["attempts"] == result["attempts"]
        manager = client.app.state.services.run_manager
        assert manager.final_text(result["attempts"][1]["run_id"]) == text


def test_stop_duplicate_dispatch_and_retry_preserve_completed_work(client):
    node, url, agent, _ = setup_board(client)
    assert start(client, url, "a").status_code == 200
    settled(client, node)
    provider = RecordingProvider(mode="block")
    manager = client.app.state.services.run_manager
    manager.install_provider("core.mock", provider)
    assert start(client, url).status_code == 200
    client.portal.call(lambda: asyncio.wait_for(provider.started.wait(), 3))
    assert start(client, url).status_code == 409
    revision = client.get(url + "/document").json()["revision"]
    assert action(client, url, "remove", {"task_id": "b"}, revision).status_code == 409
    assert client.delete(url).status_code == 409
    assert client.post(url + "/execution/stop").status_code == 200
    document = client.get(url + "/document").json()
    assert document["value"]["tasks"][0]["status"] == "done"
    assert document["value"]["tasks"][1]["execution_status"] == "cancelled"
    provider.mode = "success"
    assert start(client, url, "b").status_code == 200
    state = settled(client, node)
    assert [attempt["item_id"] for attempt in state["attempts"]] == ["a", "b", "b"]
    assert client.get(url + "/document").json()["summary"]["done"] == 2


def test_execution_is_separate_revocable_permission(client):
    node, url, agent, executor_edge = setup_board(client)
    controller = create_node(client, "agent")
    edge = client.post("/api/edges", json={"source": controller["id"], "target": node["id"], "relationship": "oaw.tasks.edit"}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    def capabilities():
        return client.get(f"/api/agents/{controller['id']}/capabilities").json()["capabilities"]
    assert "oaw.tasks.execute" not in {cap["kind"] for cap in capabilities()}
    client.patch(f"/api/edges/{edge['id']}", json={"relationship": "oaw.tasks.control"})
    cap = next(cap for cap in capabilities() if cap["kind"] == "oaw.tasks.execute")
    client.delete(f"/api/edges/{executor_edge['id']}")
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, controller["id"], cap["id"], {"action": "start", "expected_revision": 2})
    client.patch(f"/api/edges/{edge['id']}", json={"relationship": "oaw.tasks.edit"})
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, controller["id"], cap["id"], {"action": "read"})


def test_controller_tools_start_read_stop_and_validate_arguments(client):
    from backend.agents.tools import build_scoped_tool_callables
    node, url, _, _ = setup_board(client)
    controller = create_node(client, "agent")
    client.post("/api/edges", json={"source": controller["id"], "target": node["id"], "relationship": "oaw.tasks.control"})
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, controller["id"])
    tool = next(tool for tool in build_scoped_tool_callables(provider, controller["id"], definitions)
                if tool.__name__.startswith("control_task_execution"))
    assert client.portal.call(lambda: tool(target=node["id"], action="read"))["status"] == "idle"
    invalid = client.portal.call(lambda: tool(target=node["id"], action="start"))
    assert invalid["ok"] is False and "expected_revision" in invalid["error"]["message"]
    runtime = RecordingProvider(mode="block")
    client.app.state.services.run_manager.install_provider("core.mock", runtime)
    assert client.portal.call(lambda: tool(target=node["id"], action="start", expected_revision=2))["active"]
    client.portal.call(lambda: asyncio.wait_for(runtime.started.wait(), 3))
    assert client.portal.call(lambda: tool(target=node["id"], action="stop"))["status"] == "stopped"


def test_failure_pauses_new_admission_and_explicit_retry(client):
    node, url, _, _ = setup_board(client, [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}])
    provider = RecordingProvider(mode="failure")
    client.app.state.services.run_manager.install_provider("core.mock", provider)
    assert start(client, url).status_code == 200
    assert [a["item_id"] for a in settled(client, node)["attempts"]] == ["a"]
    provider.mode = "success"
    assert start(client, url, "a").status_code == 200
    settled(client, node)
    assert start(client, url).status_code == 200
    assert [a["item_id"] for a in settled(client, node)["attempts"]] == ["a", "a", "b"]


def test_parallel_executors_join_before_dependent_work(client):
    node, url, first, _ = setup_board(client)
    second = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    client.post("/api/edges", json={"source": node["id"], "target": second["id"], "relationship": "oaw.tasks.executor"})
    action(client, url, "configure_execution", {"default_executor_id": first["id"], "max_parallel": 2}, 2)
    action(client, url, "upsert", {"tasks": [{"id": "b", "title": "Independent", "executor_id": second["id"]},
        {"id": "c", "title": "Join", "depends_on": ["a", "b"]}]}, 3)
    provider = RecordingProvider(mode="block")
    client.app.state.services.run_manager.install_provider("core.mock", provider)
    assert start(client, url).status_code == 200
    async def wait_two():
        async with asyncio.timeout(3):
            while len(provider.contexts) < 2:
                await asyncio.sleep(.01)
    client.portal.call(wait_two)
    state = client.get(url + "/execution").json()
    assert {a["item_id"] for a in state["attempts"]} == {"a", "b"}
    assert all(a["status"] == "running" for a in state["attempts"])
    client.post(url + "/execution/stop")
    provider.mode = "success"
    for key in ("a", "b"):
        assert start(client, url, key).status_code == 200
        settled(client, node)
    assert start(client, url).status_code == 200
    assert settled(client, node)["attempts"][-1]["item_id"] == "c"


def test_restart_reconciles_admission_window_without_automatic_dispatch(tmp_path):
    settings = Settings.for_data_root(tmp_path / "recovery")
    with TestClient(create_app(settings)) as client:
        node, url, agent, _ = setup_board(client)
        services = client.app.state.services
        # Simulate process loss after Run persistence but before linking its ID.
        record = services.run_manager.store.create(agent_id=agent["id"], runtime_provider_id="core.mock",
            caller_kind="work", caller_id="crashed-batch", task_id=f"{node['id']}:a")
        action(client, url, "progress", {"task_id": "a", "status": "doing"}, 2)
        services.node_execution.save(node["id"], {"status": "running", "batch_id": "crashed-batch", "error": None,
            "attempts": [{"item_id": "a", "agent_id": agent["id"], "run_id": None, "status": "running",
                          "batch_id": "crashed-batch", "applied": False}]})
        # Prevent normal shutdown from converting the simulated crash to cancel.
        services.run_manager.store.interrupt_incomplete()
    with TestClient(create_app(settings)) as client:
        state = client.get(url + "/execution").json()
        assert state["status"] == "interrupted"
        assert not state["active"]
        assert state["attempts"][0]["run_id"] == record.run_id
        assert state["attempts"][0]["status"] == "interrupted"
        assert client.get(url + "/document").json()["value"]["tasks"][0]["status"] == "blocked"
        assert start(client, url, "a").status_code == 200
        assert len(settled(client, node)["attempts"]) == 2


def test_host_runs_non_dag_plugin_and_leaves_acceptance_to_plugin(client):
    from dataclasses import replace
    from pydantic import BaseModel
    from open_agent_world.plugin_api import NodeDocumentDefinition, NodeExecutionDefinition, WorkItem, ExecutionPolicy, RelationshipDefinition
    from backend.tests.plugin_support import install_test_plugin

    class Deliveries(BaseModel):
        executor: str | None = None
        delivered: int = 0
        review: str = "pending"

    def items(value):
        return [WorkItem(id=str(value["delivered"]), prompt="Prepare a delivery for review", agent_id=value["executor"], ready=True)] if value["delivered"] < 2 else []

    def apply(value, outcome):
        return {**value, "delivered": value["delivered"] + 1, "review": "awaiting_human"} if outcome.status == "succeeded" else value

    registry = client.app.state.services.plugins
    def register(registration):
        registration.register_relationship(RelationshipDefinition(id="example.deliveries.executor", label="Deliver with", short_label="deliver",
            description="Executor binding", source_types=frozenset({"example.deliveries"}), target_traits=frozenset({"core.agent"})))
        registration.register_node_type(replace(registry.node_type("oaw.tasks"), id="example.deliveries", traits=frozenset(),
            document=NodeDocumentDefinition(model=Deliveries), execution=NodeExecutionDefinition(items=items, apply_outcome=apply,
            policy=lambda value: ExecutionPolicy(), executor_relationship="example.deliveries.executor")))
    install_test_plugin(registry, "example.deliveries", register)
    node = create_node(client, "example.deliveries")
    agent = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    client.post("/api/edges", json={"source": node["id"], "target": agent["id"], "relationship": "example.deliveries.executor"})
    url = f"/api/nodes/{node['id']}"
    assert action(client, url, "replace", {"executor": agent["id"]}, 0).status_code == 200
    assert start(client, url).status_code == 200
    assert len(settled(client, node)["attempts"]) == 2
    assert client.get(url + "/document").json()["value"] == {"executor": agent["id"], "delivered": 2, "review": "awaiting_human"}
