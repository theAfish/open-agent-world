from dataclasses import replace

import pytest

from backend.errors import ResourceValidationError
from backend.plugins.registry import PluginDefinition, PluginDescriptor, PluginRegistration, PackDefinition
from backend.tests.conftest import create_node


def workflow(initial="collecting"):
    return {"version": 2, "entities": [{"id": "progress", "label": "Research", "kind": "card",
        "initial_state": initial, "states": [{"id": "collecting", "label": "Collecting evidence"},
                                               {"id": "published", "label": "Published"}]}], "rules": []}


def register_workflow(services):
    node = replace(services.plugins.node_type("agent"), id="research.worker", state_machine=workflow(),
                   traits=frozenset(), state_machine_editor=False, icon_asset=None, tutorials=(), frontend={}, lifecycle=None, deployment=None)

    def configure(registration):
        registration.register_node_type(node)
        registration.register_pack(PackDefinition(id="research", name="Research", cards=(node.id,)))

    services.plugins.install(PluginDefinition(
        PluginDescriptor(id="research", version="1.0", plugin_api_version="1.26"), configure))
    return node


@pytest.mark.parametrize("kind", ["sandbox", "text", "conversation"])
def test_ordinary_nodes_have_no_workflow_or_implicit_runtime(client, kind):
    node = create_node(client, kind)
    store = client.app.state.services.state_machines
    assert node["has_state_machine"] is False
    for _ in range(2):
        assert client.get(f"/api/state-machines/{node['id']}").json()["definition"] is None
    assert store.list_instances(False) == []
    with store.database.locked() as db:
        assert db.execute("SELECT COUNT(*) FROM state_machine_editors").fetchone()[0] == 0


def test_agent_and_legion_have_real_defaults_and_clearing_restores_them(client):
    services = client.app.state.services
    node = create_node(client, "agent")
    group = client.post("/api/legion-groups", json={"name": "Team", "node_ids": [node["id"]]}).json()[0]
    catalog = {item.id: item for item in services.plugins.catalog().node_types}
    for kind in ("agent", "legion"):
        assert catalog[kind].state_machine_editor
        assert catalog[kind].has_state_machine
    for kind in ("sandbox", "text", "image", "conversation"):
        assert not catalog[kind].state_machine_editor
    for card in (node, group):
        assert card["has_state_machine"]
        assert services.state_machines.has_editor(services.world.get_card(card["id"]))
        assert services.state_machines.get(card["id"])["definition"]["status_entity_id"] == "status"
        custom = services.state_machines.get(card["id"])["definition"]
        custom["entities"].extend(workflow()["entities"])
        services.state_machines.save(card["id"], custom)
        services.state_machines.clear(card["id"])
        assert services.state_machines.has_editor(services.world.get_card(card["id"]))
        assert services.state_machines.get(card["id"])["definition"]["status_entity_id"] == "status"
    member = client.get(f"/api/state-machines/{group['id']}/members").json()["members"][0]
    assert member["state_machine_editor"] and member["has_definition"]
    assert len(services.state_machines.list_instances(False)) == 2


def test_saved_legacy_definition_does_not_grant_sandbox_an_editor(client):
    services = client.app.state.services
    sandbox = create_node(client, "sandbox")
    services.state_machines.save(sandbox["id"], workflow())
    assert services.state_machines.has_definition(services.world.get_card(sandbox["id"]))
    assert not services.state_machines.has_editor(services.world.get_card(sandbox["id"]))


def test_developer_defaults_are_isolated_and_require_explicit_save_and_activation(client):
    services = client.app.state.services
    declared = register_workflow(services)
    first, second = create_node(client, declared.id), create_node(client, declared.id)
    store = services.state_machines
    assert first["has_state_machine"] and second["has_state_machine"]
    catalog = services.plugins.catalog()
    assert next(node for node in catalog.node_types if node.id == declared.id).has_state_machine
    assert next(node for node in catalog.node_types if node.id == declared.id).state_machine_editor
    default = store.get(first["id"])
    group = default["definition"]["entities"][0]
    assert group["initial_state"] == "collecting"
    assert [state["id"] for state in group["states"]] == ["collecting", "published"]
    assert group["card_id"] == first["id"]
    assert default["definition_version"] == 0 and not default["enabled"]
    assert default["presentation"]["positions"]["progress"]["collecting"] != default["presentation"]["positions"]["progress"]["published"]
    with pytest.raises(ResourceValidationError, match="Save"):
        store.activate(first["id"])
    with store.database.locked() as db:
        assert db.execute("SELECT COUNT(*) FROM state_machine_definitions").fetchone()[0] == 0
    saved = store.save(first["id"], default["definition"], default["presentation"], expected_revision=0)
    assert saved["definition_version"] == 1
    assert store.list_instances(False) == []
    group["initial_state"] = "published"
    edited = store.save(first["id"], default["definition"], expected_revision=1)
    assert edited["definition_version"] == 2
    assert store.activate(first["id"], 2)["states"] == {"progress": "published"}
    assert store.get(second["id"])["definition"]["entities"][0]["initial_state"] == "collecting"
    assert services.plugins.node_type(declared.id).state_machine.entities[0].initial_state == "collecting"
    store.clear(second["id"])
    assert store.get(second["id"])["definition"] is None
    assert client.get(f"/api/nodes/{second['id']}").json()["has_state_machine"] is False


def test_customizing_one_node_preserves_other_node_defaults(client):
    services = client.app.state.services
    first, second = create_node(client, "agent"), create_node(client, "agent")
    group = client.post("/api/legion-groups", json={"name": "Team", "node_ids": [first["id"], second["id"]]}).json()[0]
    custom = services.state_machines.get(first["id"])["definition"]
    custom["entities"].extend(workflow("published")["entities"])
    response = client.put(f"/api/state-machines/{first['id']}", json={"definition": custom, "expected_revision": 1})
    assert response.status_code == 200, response.text
    nodes = {node["id"]: node for node in client.get("/api/nodes").json()}
    assert nodes[first["id"]]["has_state_machine"]
    assert nodes[second["id"]]["has_state_machine"]
    assert nodes[group["id"]]["has_state_machine"]
    members = client.get(f"/api/state-machines/{group['id']}/members").json()["members"]
    assert {item["id"]: item["has_definition"] for item in members} == {first["id"]: True, second["id"]: True}
    assert services.state_machines.activate(first["id"], response.json()["definition_version"])["states"] == {"status": "idle", "progress": "published"}


def test_type_default_is_captured_and_restored_without_shared_progress(client):
    services = client.app.state.services
    declared = register_workflow(services)
    source, target = create_node(client, declared.id), create_node(client, declared.id)
    store = services.state_machines
    captured = store.capture(source["id"], {source["id"]: "source"})
    assert captured["definition"]["entities"][0]["card_id"] == "source"
    restored = store.restore(target["id"], captured, {"source": target["id"]})
    assert restored["definition_version"] == 1
    assert restored["definition"]["entities"][0]["card_id"] == target["id"]
    assert restored["definition"]["entities"][0]["initial_state"] == "collecting"
    assert store.get(source["id"])["definition_version"] == 0
    assert store.list_instances(False) == []


def test_legacy_import_responses_reflect_enabling_and_clearing_the_definition(client):
    node = create_node(client, "agent")
    response = client.patch(f"/api/nodes/{node['id']}", json={"config": {"state_machine": workflow()}})
    assert response.status_code == 200 and response.json()["has_state_machine"]
    response = client.post("/api/nodes/batch-update", json={"updates": [{"node_id": node["id"], "patch": {"config": {"state_machine": None}}}]})
    assert response.status_code == 200 and response.json()[0]["has_state_machine"]


@pytest.mark.parametrize("invalid", ["initial", "card", "target"])
def test_invalid_node_type_workflows_fail_at_registration(client, invalid):
    value = workflow()
    if invalid == "initial":
        value["entities"][0]["initial_state"] = "not-defined"
    elif invalid == "card":
        value["entities"][0]["card_id"] = "some-other-object"
    else:
        value["rules"] = [{"id": "r", "name": "r", "trigger": {"entity_id": "progress", "event": "run.completed", "target_card_id": "foreign"},
                           "effects": [{"entity_id": "progress", "from_state": "collecting", "to_state": "published"}]}]
    registration = PluginRegistration(PluginDescriptor(id="research", version="1.0", plugin_api_version="1.26"))
    with pytest.raises(ValueError):
        registration.register_node_type(replace(client.app.state.services.plugins.node_type("agent"), state_machine=value))
    assert not registration.nodes
