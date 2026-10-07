from copy import deepcopy

import pytest

from backend.agent_state_machine import agent_state_machine
from backend.state_machine_migrations import consolidate_agent_lifecycle
from backend.tests.conftest import create_node


def legacy_lifecycle(identity="status", card_id=None):
    system = agent_state_machine()["entities"][0]
    return {"version": 2, "status_entity_id": identity, "entities": [{
        "id": identity, "label": "Agent", "kind": "card", "ownership": "user",
        **({"card_id": card_id} if card_id else {}), "initial_state": "idle",
        "states": [{"id": state["id"], "label": state["label"]} for state in system["states"]],
    }], "rules": [{
        "id": item["event"].removeprefix("agent."), "name": item["label"], "enabled": True,
        "trigger": {"entity_id": identity, "event": item["event"]},
        "effects": [{"entity_id": identity, "from_state": "*", "to_state": item["to_state"]}],
    } for item in system["projection"]]}


def install_duplicate(client):
    agent = create_node(client, "agent")
    services = client.app.state.services
    client.portal.call(services.state_machine_runtime.shutdown)
    store = services.state_machines
    original = store.get(agent["id"])
    value = legacy_lifecycle("legacy_status", agent["id"])
    value["entities"].insert(0, original["definition"]["entities"][0])
    presentation = {"positions": {"status": {state: {"x": 140, "y": 90 + index * 150}
        for index, state in enumerate(["idle", "running", "waiting", "error"])}}}
    saved = store.save(agent["id"], value, presentation)
    instance = store.activate(agent["id"], saved["definition_version"])
    return services, agent, saved, instance


def test_pre_ownership_default_is_promoted_in_place_without_creating_a_user_copy(client):
    agent = create_node(client, "agent")
    store = client.app.state.services.state_machines
    saved = store.restore(agent["id"], {"definition": legacy_lifecycle(), "presentation": {}, "enabled": True}, {})
    assert [group["id"] for group in saved["definition"]["entities"]] == ["status"]
    assert saved["definition"]["status_entity_id"] == "status"
    assert saved["definition"]["rules"] == []
    assert saved["definition"]["entities"][0]["ownership"] == "system"


def test_legacy_import_preserves_a_real_layout_for_the_single_lifecycle(client):
    agent = create_node(client, "agent")
    store = client.app.state.services.state_machines
    store.import_legacy(agent["id"], legacy_lifecycle())
    saved = store.get(agent["id"])
    assert [group["id"] for group in saved["definition"]["entities"]] == ["status"]
    assert len({tuple(position.values()) for position in saved["presentation"]["positions"]["status"].values()}) == 4


def test_already_upgraded_duplicate_is_removed_from_definition_runtime_and_layout(client):
    services, agent, before, instance = install_duplicate(client)
    store = services.state_machines
    store.put_runtime(instance["id"], states={"status": "waiting", "legacy_status": "idle"}, _projection=True)
    store.initialize_node(agent["id"])
    current = store.get(agent["id"])
    assert current["enabled"]
    assert len(current["definition"]["entities"]) == 1 and current["definition"]["rules"] == []
    assert current["definition"]["status_entity_id"] == "status"
    assert store.node_instance(agent["id"])[0]["states"] == {"status": "waiting"}
    assert current["presentation"]["positions"]["status"]["running"]["x"] > current["presentation"]["positions"]["status"]["idle"]["x"]
    assert store.get(agent["id"], before["definition_version"])["definition"] == before["definition"]
    store.initialize_node(agent["id"])
    assert store.get(agent["id"]) == current
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    assert store.get_states(instance["id"]) == {"status": "running"}


def test_real_user_groups_and_reactions_survive_consolidation_without_touching_action_arguments(client):
    services, agent, before, _ = install_duplicate(client)
    value = before["definition"]
    value["entities"].append({"id": "workflow", "label": "Research", "kind": "group", "initial_state": "new",
        "states": [{"id": "new", "label": "New"}, {"id": "done", "label": "Done"}]})
    value["rules"].append({"id": "reaction", "name": "Finished research", "trigger": {"entity_id": "legacy_status", "state_id": "idle", "event": "state.entered"},
        "effects": [{"entity_id": "workflow", "from_state": "*", "to_state": "done"}],
        "actions": [{"id": "action", "kind": "run", "arguments": {"prompt": "Continue", "entity_id": "legacy_status"}}]})
    store = services.state_machines
    saved = store.save(agent["id"], value)
    store.activate(agent["id"], saved["definition_version"])
    store.initialize_node(agent["id"])
    current = store.get(agent["id"])["definition"]
    assert [group["id"] for group in current["entities"]] == ["status", "workflow"]
    assert [rule["id"] for rule in current["rules"]] == ["reaction"]
    assert current["rules"][0]["trigger"]["entity_id"] == "status"
    assert current["rules"][0]["actions"][0]["arguments"]["entity_id"] == "legacy_status"


@pytest.mark.parametrize("edit", ["state", "label", "effect", "action", "condition", "group_id"])
def test_customized_user_lifecycle_is_not_deleted_by_name_or_state_similarity(edit):
    value = legacy_lifecycle("legacy_status")
    group, rule = value["entities"][0], value["rules"][0]
    if edit == "state": group["states"].append({"id": "review", "label": "Review"})
    if edit == "label": group["states"][0]["label"] = "Available"
    if edit == "effect": rule["effects"][0]["from_state"] = "idle"
    if edit == "action": rule["actions"] = [{"id": "action", "kind": "run", "arguments": {"prompt": "Continue"}}]
    if edit == "condition": rule["conditions"] = [{"entity_id": "legacy_status", "state_id": "idle"}]
    if edit == "group_id": group["id"] = "user_lifecycle"
    before = deepcopy(value)
    assert consolidate_agent_lifecycle(value, agent_state_machine()["entities"]) == (before, {})
    assert value == before


def test_duplicate_in_draft_is_cleaned_without_applying_user_changes(client):
    services, agent, before, instance = install_duplicate(client)
    store = services.state_machines
    value = before["definition"]
    value["entities"].append({"id": "draft_only", "label": "Draft only", "kind": "group", "initial_state": "new", "states": [{"id": "new", "label": "New"}]})
    store.save(agent["id"], value)
    store.initialize_node(agent["id"])
    head = store.get(agent["id"])
    active = store.get_definition(agent["id"], store.get_instance(instance["id"])["definition_version"])
    assert not head["enabled"]
    assert [group["id"] for group in head["definition"]["entities"]] == ["status", "draft_only"]
    assert [group.id for group in active.entities] == ["status"]
    store.initialize_node(agent["id"])
    assert store.get(agent["id"]) == head


def test_member_read_references_and_runtime_bindings_follow_the_consolidated_state(client):
    services, agent, _, _ = install_duplicate(client)
    store = services.state_machines
    observer = create_node(client, "text")
    value = {"version": 2, "entities": [{"id": "watcher", "label": "Watcher", "kind": "group", "initial_state": "idle", "states": [{"id": "idle", "label": "Idle"}]}],
        "references": [{"entity_id": "member", "card_id": agent["id"], "state_group_id": "legacy_status"}],
        "rules": [{"id": "watch", "name": "Watch", "trigger": {"entity_id": "member", "state_id": "running", "event": "state.entered"},
                   "effects": [{"entity_id": "watcher", "from_state": "*", "to_state": "idle"}]}]}
    saved = store.save(observer["id"], value)
    watching = store.activate(observer["id"], saved["definition_version"])
    store.initialize_node(agent["id"])
    assert store.get(observer["id"])["definition"]["references"][0]["state_group_id"] == "status"
    assert store.get_instance(watching["id"])["bindings"]["member"]["entity_id"] == "status"
    assert store.get_instance(watching["id"])["enabled"]
    assert next(group for group in store.get_evaluator_definition(watching["id"]).entities if group.id == "member").ownership == "system"
    assert store.get(observer["id"], saved["definition_version"])["definition"]["references"][0]["state_group_id"] == "legacy_status"
