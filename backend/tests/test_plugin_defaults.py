"""Built-in skills use native equipment, persistence and capabilities without models."""
from contextlib import contextmanager
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.services import create_services
from backend.plugins.loader import load_plugin_registry
from backend.plugins.registry import PluginDescriptor
from backend.plugins.equipment import DefaultEquipment
from backend.tests.conftest import create_node
from open_agent_world.skill_packages import Skill, SkillPackage, register_skill_package


class Example:
    def __init__(self, registry, version="1.0.0", *, equipment=True, text=None):
        self.descriptor = PluginDescriptor(id="test.defaults", version=version, plugin_api_version="1.26")
        self.agent = registry.node_type("agent")
        self.equipment = equipment
        self.text = text or version

    def register(self, registration):
        register_skill_package(registration, node_type="test.defaults.tools", package=SkillPackage(
            package_id="test.defaults.tools", version=self.descriptor.version, skills=[
                Skill(id="check", name="Check", instructions=self.text,
                    files={"scripts/check.py": "print('fixture')"})]))
        registration.register_node_type(replace(self.agent, id="test.defaults.agent", lifecycle=None,
            default_equipment=(DefaultEquipment(key="tools", type="test.defaults.tools", relationship="test.defaults.tools.use"),) if self.equipment else ()))


@contextmanager
def app(tmp_path, version="1.0.0", **options):
    settings = replace(Settings.for_data_root(tmp_path), agent_runtime="core.mock")
    registry = load_plugin_registry()
    registry.install(Example(registry, version, **options))
    services = create_services(settings, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        yield client


def owned(client, agent):
    return [n for n in client.get("/api/world").json()["nodes"]
        if (n.get("equipment") or {}).get("owner_id") == agent]


def document(client, node):
    return client.get(f"/api/nodes/{node}/document").json()


def test_native_equipment_and_duplicate_no_external_skill_edge(tmp_path):
    with app(tmp_path) as client:
        agent = create_node(client, "test.defaults.agent")["id"]
        box = owned(client, agent)[0]
        assert len(owned(client, agent)) == 1
        assert client.get("/api/edges").json() == []
        assert document(client, box["id"])["value"]["skills"][0]["instructions"] == "1.0.0"
        copied = client.post(f"/api/nodes/{agent}/duplicate")
        assert copied.status_code == 200, copied.text
        copied_agent = next(n for n in copied.json()["nodes"] if n["type"] == "test.defaults.agent")
        copied_boxes = owned(client, copied_agent["id"])
        assert len(copied_boxes) == 1
        assert len(document(client, copied_boxes[0]["id"])["value"]["skills"]) == 1


@pytest.mark.parametrize("action", ["unequip", "delete"])
def test_unequip_or_delete_not_reinstalled_and_duplicate_disabled_slot(tmp_path, action):
    with app(tmp_path) as client:
        agent = create_node(client, "test.defaults.agent")["id"]
        box = owned(client, agent)[0]["id"]
        response = (client.patch(f"/api/nodes/{box}", json={"equipment": None}) if action == "unequip"
            else client.post("/api/nodes/batch-delete", json={"node_ids": [box,
                *[item["node_id"] for item in document(client, box)["value"]["skills"]]]}))
        assert response.status_code == 200
    with app(tmp_path, "2.0.0") as client:
        assert owned(client, agent) == []
        copied = client.post(f"/api/nodes/{agent}/duplicate")
        assert copied.status_code == 200, copied.text
        new_agent = next(n for n in copied.json()["nodes"] if n["type"] == "test.defaults.agent")
        assert owned(client, new_agent["id"]) == []
        if action == "unequip":
            assert document(client, box)["value"]["version"] == "1.0.0"


def test_startup_migrates_old_agents_once_without_changing_external_card(tmp_path):
    with app(tmp_path, equipment=False) as client:
        agent = create_node(client, "test.defaults.agent")["id"]
        external = create_node(client, "test.defaults.tools")["id"]
        assert client.post("/api/edges", json={"source": agent, "target": external,
            "relationship": "test.defaults.tools.use"}).status_code == 201
    with app(tmp_path, "2.0.0") as client:
        internal = owned(client, agent)[0]["id"]
        assert internal != external
        assert document(client, external)["value"]["version"] == "1.0.0"
    with app(tmp_path, "2.0.0") as client:
        assert [n["id"] for n in owned(client, agent)] == [internal]


def test_default_creation_failure_rolls_back_graph_state_and_events(tmp_path, monkeypatch):
    with app(tmp_path) as client:
        services = client.app.state.services
        from backend import plugin_defaults
        original = plugin_defaults.write_document
        def fail_after_members(services, node_id, *args):
            original(services, node_id, *args)
            if services.world.get_card(node_id).type == "test.defaults.tools":
                raise ValueError("fixture failure")
        monkeypatch.setattr(plugin_defaults, "write_document", fail_after_members)
        cards_before = {n.id for n in services.world.list_cards()}
        events_before = services.events.sequence
        with pytest.raises(ValueError, match="fixture failure"):
            client.post("/api/nodes", json={"type": "test.defaults.agent"})
        assert {n.id for n in services.world.list_cards()} == cards_before
        assert services.events.sequence == events_before


def test_native_capability_can_read_equipped_skills_without_world_edge(tmp_path):
    with app(tmp_path) as client:
        from backend.capabilities.provider import WorldAgentCapabilityProvider
        agent = create_node(client, "test.defaults.agent")["id"]
        box = owned(client, agent)[0]["id"]
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        tools = client.portal.call(provider.list_tools, agent)
        tool = next(t for t in tools if t.name == "read_skills")
        assert "equipped" in tool.input_schema["properties"]["toolbox"]["description"]
        result = client.portal.call(provider.invoke_tool, agent, tool.capability_id, {"toolbox": box})
        assert result["skills"][0]["skill_id"] == "check"
        result = client.portal.call(provider.invoke_tool, agent, tool.capability_id,
            {"toolbox": box, "skill_id": "check", "include_file_contents": False})
        assert result["skill"]["instructions"] == "1.0.0"
        assert isinstance(result["skill"]["files"]["scripts/check.py"], dict)


def test_native_restore_preserves_default_slots(tmp_path):
    with app(tmp_path) as client:
        agent = create_node(client, "test.defaults.agent")["id"]
        nodes = client.get("/api/world").json()["nodes"]
        box = owned(client, agent)[0]["id"]
        nodes = [n for n in nodes if n["id"] in {agent, box} or n.get("parent_id") == box]
        documents = {n["id"]: document(client, n["id"])["value"] for n in nodes if n["id"] != agent}
        defaults = {n["id"]: client.get(f"/api/nodes/{n['id']}/defaults").json() for n in nodes}
        assert client.delete(f"/api/nodes/{agent}").status_code == 200
        for node in sorted(nodes, key=lambda n: 0 if n["id"] == agent else 1 if n["id"] == box else 2):
            payload = {k: node[k] for k in ("id", "type", "name", "parent_id", "equipment", "config", "position")}
            response = client.post("/api/nodes/restore", json=payload)
            assert response.status_code == 201, response.text
            if node["id"] in documents:
                value = documents[node["id"]]
                if node["id"] == box:
                    value = {**value, "skills": []}
                result = client.post(f"/api/nodes/{node['id']}/actions/replace", json={
                    "expected_revision": document(client, node["id"])["revision"], "arguments": value})
                assert result.status_code == 200, result.text
            if defaults[node["id"]]:
                result = client.post(f"/api/nodes/{node['id']}/defaults", json={"value": defaults[node["id"]]})
                assert result.status_code == 200, result.text
        assert len(owned(client, agent)) == 1
    with app(tmp_path, "2.0.0") as client:
        assert len(owned(client, agent)) == 1
        assert document(client, box)["value"]["version"] == "1.0.0"


def test_lifecycle_creation_failure_also_removes_equipment_and_events(tmp_path, monkeypatch):
    with app(tmp_path) as client:
        from backend.plugins.lifecycle import NodeLifecycleHandler, NodeLifecycleTransaction
        class Broken(NodeLifecycleHandler):
            async def prepare_create(self, *args):
                class Failure(NodeLifecycleTransaction):
                    async def commit(self):
                        raise ValueError("lifecycle fixture")
                return Failure()
        services = client.app.state.services
        original = services.plugins.node_type("test.defaults.agent")
        monkeypatch.setitem(services.plugins._nodes, original.id, replace(original, lifecycle=Broken()))
        before = services.events.sequence
        with pytest.raises(ValueError, match="lifecycle fixture"):
            client.post("/api/nodes", json={"type": original.id})
        assert not any(n.type.startswith("test.defaults.") for n in services.world.list_cards())
        assert services.events.sequence == before


def test_atomsculptor_uses_the_same_default_equipment_contract(tmp_path):
    with app(tmp_path) as client:
        from backend.capabilities.provider import WorldAgentCapabilityProvider
        agent = create_node(client, "atomsculptor.agent")["id"]
        box = owned(client, agent)[0]
        assert box["type"] == "atomsculptor.skills"
        assert len(document(client, box["id"])["value"]["skills"]) == 18
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        tools = client.portal.call(provider.list_tools, agent)
        read = next(tool for tool in tools if tool.name == "read_skills")
        listing = client.portal.call(provider.invoke_tool, agent, read.capability_id, {"toolbox": box["id"]})
        assert len(listing["skills"]) == 18
        assert any(row["skill_id"] == "structure-inspect" for row in listing["skills"])
        assert not any(tool.name == "run_skill_script" for tool in tools)
        assert not client.get("/api/edges").json()


def test_existing_builtin_skills_are_not_updated_on_plugin_changes(tmp_path):
    with app(tmp_path) as client:
        agent = create_node(client, "test.defaults.agent")["id"]
        box = owned(client, agent)[0]["id"]
        before = document(client, box)
    with app(tmp_path, "2.0.0") as client:
        assert owned(client, agent)[0]["id"] == box
        assert document(client, box) == before
        new_box = create_node(client, "test.defaults.tools")["id"]
        assert document(client, new_box)["value"]["version"] == "2.0.0"
        assert client.get(f"/api/nodes/{box}/document/source").status_code == 404
        assert client.post(f"/api/nodes/{box}/document/refresh", json={}).status_code == 404
