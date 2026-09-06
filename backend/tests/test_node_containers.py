"""A third plugin container exercises the shared contract beyond Legion/Skills."""
from dataclasses import dataclass, replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.config import Settings
from backend.main import create_app
from backend.plugins.loader import load_plugin_registry
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_legion_groups import CaptureRuntime
from backend.world.models import CardCreate
from open_agent_world.plugin_api import NodeContainerDefinition


class FolderConfig(BaseModel):
    # Same spelling as a Legion setting, with no team semantics.
    paused: bool = True


@dataclass(frozen=True, slots=True)
class FolderContainer(NodeContainerDefinition):
    connectable: bool = False


def folder_registry():
    registry = load_plugin_registry()
    definition = replace(registry.node_type("oaw.skills"), id="example.folder", label="Folder",
                         config_model=FolderConfig, traits=frozenset(), document=None,
                         container=FolderContainer())
    install_test_plugin(registry, "example.folders", lambda registration: registration.register_node_type(definition))
    return registry


def test_nested_plugin_containers_move_copy_and_reject_a_batch_cycle(tmp_path):
    settings = Settings.for_data_root(tmp_path / "world")
    services = create_services(settings, plugins=folder_registry())
    try:
        with TestClient(create_app(settings, services=services)) as client:
            outer = create_node(client, "example.folder", name="Outer", position={"x": 0, "y": 0})
            inner = create_node(client, "example.folder", name="Inner", parent_id=outer["id"], position={"x": 200, "y": 150})
            toolbox = create_node(client, "oaw.skills", parent_id=inner["id"], position={"x": 400, "y": 300})
            skill = create_node(client, "oaw.skills.skill", parent_id=toolbox["id"], position={"x": 550, "y": 450})
            agent = create_node(client, "agent", parent_id=inner["id"])
            client.post("/api/edges", json={"source": agent["id"], "target": skill["id"], "relationship": "oaw.skills.skill.use"})
            response = client.post("/api/nodes/batch-update", json={"updates": [
                {"node_id": outer["id"], "patch": {"position": {"x": 100, "y": 100}}},
                {"node_id": inner["id"], "patch": {"position": {"x": 500, "y": 350}}},
            ]})
            assert response.status_code == 200, response.text
            assert client.get(f"/api/nodes/{skill['id']}").json()["position"] == {"x": 850, "y": 650}

            capture = client.post("/api/legions", json={"name": "Nested folders", "node_ids": [inner["id"], outer["id"]]})
            assert capture.status_code == 201, capture.text
            template = capture.json()
            copied = client.post(f"/api/legions/{template['id']}/instances", json={"as_group": False})
            assert copied.status_code == 201, copied.text
            nodes = copied.json()["nodes"]
            copy_outer = next(node for node in nodes if node["name"] == "Outer")
            copy_inner = next(node for node in nodes if node["name"] == "Inner")
            assert copy_inner["parent_id"] == copy_outer["id"]
            copy_box = next(node for node in nodes if node["type"] == "oaw.skills")
            assert copy_box["parent_id"] == copy_inner["id"]
            assert len(client.get(f"/api/nodes/{copy_box['id']}/document").json()["value"]["skills"]) == 1
            assert len(copied.json()["edges"]) == 1

            peer = create_node(client, "example.folder")
            response = client.post("/api/nodes/batch-update", json={"updates": [
                {"node_id": outer["id"], "patch": {"parent_id": peer["id"]}},
                {"node_id": peer["id"], "patch": {"parent_id": outer["id"]}},
            ]})
            assert response.status_code == 422
            assert client.get(f"/api/nodes/{outer['id']}").json()["parent_id"] is None
            assert client.get(f"/api/nodes/{peer['id']}").json()["parent_id"] is None
    finally:
        services.close()


@pytest.mark.asyncio
async def test_ordinary_membership_does_not_inherit_legion_runtime_or_permissions(tmp_path):
    services = create_services(Settings.for_data_root(tmp_path / "runtime"), plugins=folder_registry(), default_runtime_provider_id="core.mock")
    manager = services.run_manager
    runtime = CaptureRuntime(manager.capability_provider)
    manager.install_provider("core.mock", runtime)
    try:
        folder = await services.create_card(CardCreate(type="example.folder"))
        agent = await services.create_card(CardCreate(type="agent", parent_id=folder.id))
        assert await manager.capability_provider.list_tools(agent.id) == []
        run = await manager.start_run(agent.id, "hello")
        await manager.wait_execution(run.run_id)
        assert runtime.captured_context.group_context is None
        assert [scope.scope_kind for scope in runtime.captured_context.state_context.scope_stack] == ["world", "agent", "run"]
    finally:
        await services.shutdown()
