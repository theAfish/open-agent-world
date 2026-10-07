from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.state_machine import StateMachineConfig, remap_definition
from backend.tests.conftest import create_node


def machine(card_id="worker-card", target_id="document-card"):
    def entity(identity, kind, parent=None, card=None):
        return {
            "id": identity, "label": identity, "kind": kind,
            **({"parent_id": parent} if parent else {}),
            **({"card_id": card} if card else {}),
            "initial_state": "idle",
            "states": [
                {"id": "idle", "label": "Idle", "position": {"x": 0, "y": 0}},
                {"id": "done", "label": "Done", "position": {"x": 180, "y": 0}},
            ],
        }
    return {
        "version": 1,
        "entities": [entity("team", "group"), entity("nested", "group", "team"),
                     entity("worker", "card", "nested", card_id),
                     entity("summoned", "spawn", "worker")],
        "rules": [{
            "id": "completed", "name": "Mark team and worker complete", "enabled": True,
            "trigger": {"entity_id": "worker", "event": "capability.completed",
                        "capability": "text.read", "target_card_id": target_id},
            "conditions": [{"entity_id": "team", "state_id": "idle"}],
            "effects": [{"entity_id": "worker", "from_state": "idle", "to_state": "done"},
                        {"entity_id": "team", "from_state": "*", "to_state": "done"}],
        }],
    }


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(version=3),
    lambda value: value.update(entities=[]),
    lambda value: value["entities"].append(deepcopy(value["entities"][0])),
    lambda value: value["entities"][0].update(parent_id="summoned"),
    lambda value: value["entities"][0].update(parent_id="absent"),
    lambda value: value["entities"][0].update(initial_state="absent"),
    lambda value: value["entities"][0]["states"][0].update(id="*"),
    lambda value: value["entities"][0]["states"][0]["position"].update(x=float("inf")),
    lambda value: value["entities"][0]["states"][0].update(label=" "),
    lambda value: value["entities"][0]["states"].append(deepcopy(value["entities"][0]["states"][0])),
    lambda value: value["rules"].append(deepcopy(value["rules"][0])),
    lambda value: value["rules"][0]["trigger"].update(entity_id="absent"),
    lambda value: value["rules"][0]["conditions"][0].update(state_id="absent"),
    lambda value: value["rules"][0]["effects"][0].update(entity_id="absent"),
    lambda value: value["rules"][0]["effects"][0].update(from_state="absent"),
    lambda value: value["rules"][0]["effects"][0].update(to_state="absent"),
    lambda value: value["rules"][0]["effects"].append(deepcopy(value["rules"][0]["effects"][0])),
    lambda value: value["rules"][0].update(effects=[]),
    lambda value: value["rules"][0].update(enabled="yes"),
    lambda value: value["rules"][0].update(script="unexpected code"),
])
def test_state_machine_rejects_invalid_graphs(mutate):
    value = machine()
    mutate(value)
    with pytest.raises(ValidationError):
        StateMachineConfig.model_validate(value)


@pytest.mark.parametrize("card_type", ["agent", "text", "image", "conversation", "sandbox", "legion"])
def test_state_machine_config_persists_and_can_be_cleared_without_runtime_changes(client, card_type):
    if card_type == "legion":
        member = create_node(client, "agent")
        card = client.post("/api/legion-groups", json={"name": "Team", "node_ids": [member["id"]]}).json()[0]
    else:
        card = create_node(client, card_type)
    value = machine(card["id"])
    response = client.patch(f"/api/nodes/{card['id']}", json={"config": {"state_machine": value}})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert "state_machine" not in saved["config"]
    stored = client.get(f"/api/state-machines/{card['id']}").json()
    assert stored["definition"]["rules"][0]["trigger"] == value["rules"][0]["trigger"]
    assert stored["enabled"] is False
    assert "position" not in stored["definition"]["entities"][0]["states"][0]
    assert stored["presentation"]["positions"]["team"]["done"] == {"x": 180, "y": 0}
    assert saved["status"] == card["status"]
    assert client.get(f"/api/state-machines/{card['id']}").json() == stored
    # A normal patch must preserve the separate host definition.
    renamed = client.patch(f"/api/nodes/{card['id']}", json={"name": "Renamed"})
    assert "state_machine" not in renamed.json()["config"]
    assert client.get(f"/api/state-machines/{card['id']}").json() == stored
    cleared = client.patch(f"/api/nodes/{card['id']}", json={"config": {"state_machine": None}})
    assert cleared.status_code == 200, cleared.text
    assert (client.get(f"/api/state-machines/{card['id']}").json()["definition"] is not None) == (card_type in {"agent", "legion"})


def test_rejected_state_machine_update_preserves_saved_configuration(client):
    card = create_node(client, "agent", config={"state_machine": machine()})
    invalid = machine()
    invalid["entities"][0]["parent_id"] = "worker"
    response = client.patch(f"/api/nodes/{card['id']}", json={"config": {"state_machine": invalid}})
    assert response.status_code == 422, response.text
    retained = client.get(f"/api/nodes/{card['id']}").json()
    assert retained["config"] == card["config"]
    assert retained["revision"] == card["revision"]


def test_remap_changes_only_world_references_without_mutating_source():
    config = machine()
    original = deepcopy(config)
    copied = remap_definition(config, {"worker-card": "new-worker", "document-card": "new-document"})
    assert config == original
    assert copied["entities"][2]["card_id"] == "new-worker"
    assert copied["entities"][2]["id"] == "worker"
    assert copied["rules"][0]["trigger"]["target_card_id"] == "new-document"
    assert copied["rules"][0]["effects"] == original["rules"][0]["effects"]
    assert remap_definition(config, {}) == config


def test_template_capture_and_instantiation_remap_state_machines_on_all_cards(client):
    worker = create_node(client, "agent", name="Worker")
    document = create_node(client, "text", name="Document")
    group = client.post("/api/legion-groups", json={"name": "Team", "node_ids": [worker["id"], document["id"]]}).json()[0]
    value = machine(worker["id"], document["id"])
    for card in (worker, document, group):
        response = client.patch(f"/api/nodes/{card['id']}", json={"config": {"state_machine": value}})
        assert response.status_code == 200, response.text
    captured = client.post("/api/legions", json={"name": "Reusable", "node_ids": [group["id"], worker["id"], document["id"]]})
    assert captured.status_code == 201, captured.text
    record = client.app.state.services.legions.get(captured.json()["id"])
    serialized = record.blueprint.model_dump_json()
    assert worker["id"] not in serialized
    assert document["id"] not in serialized
    deployed = client.post(f"/api/legions/{record.id}/instances", json={})
    assert deployed.status_code == 201, deployed.text
    nodes = deployed.json()["nodes"]
    new_worker = next(node for node in nodes if node["name"] == "Worker")
    new_document = next(node for node in nodes if node["name"] == "Document")
    for node in nodes:
        copied = client.get(f"/api/state-machines/{node['id']}").json()["definition"]
        assert "state_machine" not in node["config"]
        assert copied["rules"][0]["trigger"]["target_card_id"] == new_document["id"]
        if node["id"] == new_worker["id"]:
            assert next(entity for entity in copied["entities"] if entity["id"] == "worker")["card_id"] == new_worker["id"]
        else:
            assert all(entity["id"] != "worker" for entity in copied["entities"])
            assert copied["references"] == [
                {"entity_id": "worker", "card_id": new_worker["id"], "state_group_id": "worker"},
                {"entity_id": "summoned", "card_id": new_worker["id"], "state_group_id": "summoned"},
            ]
        assert node["status"] in ("idle", "available")


def test_host_configuration_is_not_forwarded_to_agent_runtime(client):
    from backend.runs.manager import RunManager

    card = create_node(client, "agent", config={"state_machine": machine()})
    stored = client.app.state.services.world.get_card(card["id"])
    assert "state_machine" not in RunManager._agent_config(stored).provider_config


def test_strict_plugin_cards_keep_working_and_export_their_state_machine(client):
    import json

    from backend.packs.archive import inspect_archive
    from backend.tests.test_card_factory import call, factory, patch

    devices = factory(client)
    for device in devices.values():
        patch(client, device, {"state_machine": machine(device["id"], device["id"])})
    printed = call(client, devices["printer"], "print", expected=201).json()
    value = machine(printed["id"], printed["id"])
    patch(client, printed, {"state_machine": value})
    assert "OAW" in call(client, printed, "run", {"values": {"name": "OAW"}}).json()["result"]
    packer = call(client, devices["packer"], "items", {"kind": "node", "id": printed["id"]}).json()
    assert client.get(f"/api/state-machines/{packer['id']}").json()["definition"]["rules"][0]["trigger"]["target_card_id"] == packer["id"]
    archive = inspect_archive(call(client, packer, "export").content)
    assert len(archive.manifest.content.legions) == 1
    template = json.loads(archive.files[archive.manifest.content.legions[0]])
    node = template["blueprint"]["nodes"][0]
    assert "state_machine" not in node["config"]
    assert node["state_machine"]["definition"]["rules"][0]["trigger"]["target_card_id"] == node["key"]
