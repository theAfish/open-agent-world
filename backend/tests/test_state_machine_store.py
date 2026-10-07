from copy import deepcopy
import json

import pytest

from backend.errors import ConflictError, ResourceValidationError, RevisionConflictError
from backend.state_machine_store import StateMachineStore
from backend.tests.conftest import create_node


def definition(card_id):
    return {"version": 2, "entities": [{"id": "self", "label": "Progress", "kind": "card", "card_id": card_id,
        "initial_state": "planning", "states": [{"id": "planning", "label": "Planning", "position": {"x": 12, "y": 34}},
                                                 {"id": "review", "label": "Review", "position": {"x": 100, "y": 34}}]}],
        "rules": [{"id": "review", "name": "Review", "trigger": {"entity_id": "self", "event": "capability.succeeded"},
                   "effects": [{"entity_id": "self", "from_state": "planning", "to_state": "review"}]}]}


def test_versions_and_presentation_are_separate_and_instances_stay_pinned(client):
    card = create_node(client, "text")
    store = client.app.state.services.state_machines
    initial = definition(card["id"])
    first = store.save(card["id"], initial, expected_revision=0)
    assert first["definition_version"] == 1
    assert "position" not in first["definition"]["entities"][0]["states"][0]
    assert first["presentation"]["positions"]["self"]["planning"] == {"x": 12, "y": 34}
    instance = store.activate(card["id"], 1)
    updated = deepcopy(initial)
    updated["rules"][0]["effects"][0]["to_state"] = "planning"
    second = store.save(card["id"], updated, expected_revision=1)
    assert second["definition_version"] == 2
    assert second["enabled"] is False
    assert second["active_definition_versions"] == [1]
    assert store.get_instance(instance["id"])["definition_version"] == 1
    assert store.get_definition(card["id"], 1).rules[0].effects[0].to_state == "review"
    with pytest.raises(RevisionConflictError):
        store.save(card["id"], initial, expected_revision=1)
    with pytest.raises(ConflictError):
        store.activate(card["id"], 2)
    assert store.activate(card["id"], 2, scope_key="reviewed-version-2")["definition_version"] == 2
    assert "state_machine" not in client.get(f"/api/nodes/{card['id']}").json()["config"]


def test_layout_only_save_keeps_definition_version(client):
    card = create_node(client, "text")
    store = client.app.state.services.state_machines
    saved = store.save(card["id"], definition(card["id"]))
    moved = deepcopy(saved["presentation"])
    moved["coordinate_space"] = "owner"
    moved["positions"]["self"]["planning"] = {"x": 900, "y": 800}
    next_saved = store.save(card["id"], saved["definition"], moved, expected_revision=saved["revision"])
    assert next_saved["definition_version"] == saved["definition_version"]
    assert next_saved["revision"] == saved["revision"] + 1
    assert next_saved["presentation"] == moved


def test_legion_references_member_and_cross_object_changes_share_authoritative_state(client):
    member = create_node(client, "text")
    group = create_node(client, "text")
    store = client.app.state.services.state_machines
    store.save(member["id"], definition(member["id"]))
    global_definition = definition(group["id"])
    global_definition["references"] = [{"entity_id": "member", "card_id": member["id"], "state_group_id": "self"}]
    global_definition["rules"][0]["effects"].append({"entity_id": "member", "from_state": "planning", "to_state": "review"})
    store.save(group["id"], global_definition)
    instance = store.activate(group["id"], 1)
    target_id, target_group = store.resolve_binding(instance["id"], "member")
    assert target_group == "self"
    assert store.get_instance(target_id)["enabled"] is False
    assert len(store.get(group["id"])["definition"]["entities"]) == 1
    store.put_states(instance["id"], {"self": "review", "member": "review"})
    assert store.get_instance(target_id)["states"] == {"self": "review"}
    assert store.get_states(instance["id"]) == {"self": "review", "member": "review"}
    edited = definition(member["id"])
    edited["entities"][0]["states"][1]["label"] = "Reviewed by member"
    store.save(member["id"], edited)
    assert client.get(f"/api/state-machines/{member['id']}").json()["definition"]["entities"][0]["states"][1]["label"] == "Reviewed by member"
    assert store.get_instance(target_id)["definition_version"] == 1
    assert store.get(group["id"])["definition"]["references"] == global_definition["references"]


def test_foreign_definition_copies_rejected_and_failed_cross_update_is_atomic(client):
    owner, other = create_node(client, "text"), create_node(client, "text")
    store = client.app.state.services.state_machines
    foreign = definition(owner["id"])
    foreign["entities"][0]["card_id"] = other["id"]
    with pytest.raises(ResourceValidationError, match="referenced"):
        store.save(owner["id"], foreign)
    store.save(owner["id"], definition(owner["id"]))
    instance = store.activate(owner["id"], 1)
    before = store.get_instance(instance["id"])
    with pytest.raises(ResourceValidationError):
        store.put_states(instance["id"], {"self": "review", "unknown": "no-state"})
    assert store.get_instance(instance["id"]) == before


@pytest.mark.parametrize("reference_self", [False, True])
def test_activation_rejects_duplicate_reference_targets_without_creating_instances(client, reference_self):
    owner, member = create_node(client, "text"), create_node(client, "text")
    store = client.app.state.services.state_machines
    store.save(member["id"], definition(member["id"]))
    value = definition(owner["id"])
    target_id = owner["id"] if reference_self else member["id"]
    value["references"] = [{"entity_id": "member", "card_id": target_id, "state_group_id": "self"}]
    if not reference_self:
        value["references"].append({"entity_id": "duplicate", "card_id": target_id, "state_group_id": "self"})
    store.save(owner["id"], value)
    with pytest.raises(ResourceValidationError, match="only once"):
        store.activate(owner["id"], 1)
    assert store.list_instances(False) == []


def test_conflicting_alias_writes_are_rejected_before_changing_any_instance(client):
    owner, member = create_node(client, "text"), create_node(client, "text")
    store = client.app.state.services.state_machines
    store.save(member["id"], definition(member["id"]))
    value = definition(owner["id"])
    value["references"] = [{"entity_id": "member", "card_id": member["id"], "state_group_id": "self"}]
    store.save(owner["id"], value)
    instance = store.activate(owner["id"], 1)
    bindings = deepcopy(instance["bindings"])
    # Defend against aliases from previously persisted or manually supplied bindings.
    bindings["duplicate"] = dict(bindings["member"])
    store.put_runtime(instance["id"], bindings=bindings)
    target_id = bindings["member"]["instance_id"]
    before = {identity: store.get_instance(identity) for identity in (instance["id"], target_id)}
    with pytest.raises(ResourceValidationError, match="conflicting states"):
        store.put_states(instance["id"], {"self": "review", "member": "review", "duplicate": "planning"})
    assert {identity: store.get_instance(identity) for identity in before} == before
    store.put_states(instance["id"], {"member": "review", "duplicate": "review"})
    assert store.get_instance(target_id)["states"] == {"self": "review"}


def test_legacy_preview_migration_is_disabled_and_idempotent(client):
    card = create_node(client, "text")
    services = client.app.state.services
    with services.database.transaction(immediate=True) as db:
        config = json.loads(db.execute("SELECT config_json FROM cards WHERE id=?", (card["id"],)).fetchone()[0])
        config["state_machine"] = definition(card["id"])
        db.execute("UPDATE cards SET config_json=? WHERE id=?", (json.dumps(config), card["id"]))
    restarted = StateMachineStore(services)
    restarted.migrate_legacy()
    migrated = restarted.get(card["id"])
    assert migrated["definition_version"] == 1
    assert migrated["enabled"] is False
    assert restarted.list_instances(False) == []
    assert "state_machine" not in services.world.get_card(card["id"]).config
    restarted.migrate_legacy()
    assert restarted.get(card["id"]) == migrated


def test_member_only_legacy_copy_migrates_to_reference_without_alias_collision(client):
    owner, member = create_node(client, "text"), create_node(client, "text")
    store = client.app.state.services.state_machines
    store.import_legacy(owner["id"], definition(member["id"]))
    saved = store.get(owner["id"])
    assert saved["enabled"] is False
    assert saved["definition"]["references"] == [{"entity_id": "self", "card_id": member["id"], "state_group_id": "self"}]
    assert saved["definition"]["entities"][0]["id"] != "self"
    assert all(entity.get("card_id") in (None, owner["id"]) for entity in saved["definition"]["entities"])


def test_batch_and_container_move_import_legacy_to_host_boundary(client):
    member = create_node(client, "text")
    group = create_node(client, "text")
    response = client.post("/api/nodes/batch-update", json={"updates": [{"node_id": member["id"],
        "patch": {"config": {"state_machine": definition(member["id"])}}}]})
    assert response.status_code == 200, response.text
    response = client.patch(f"/api/nodes/{group['id']}", json={"position": {"x": 200, "y": 200},
        "config": {"state_machine": definition(group["id"])}})
    assert response.status_code == 200, response.text
    for card in (member, group):
        assert "state_machine" not in client.get(f"/api/nodes/{card['id']}").json()["config"]
        assert client.get(f"/api/state-machines/{card['id']}").json()["definition_version"] == 1


def test_legacy_template_decodes_host_sidecar_before_strict_plugin_validation(client):
    from backend.legions.models import LegionTemplateNode
    target = create_node(client, "text")
    raw = {"key": "source", "type": "agent", "plugin_id": "core", "name": "Legacy",
           "position": {"x": 0, "y": 0}, "size": {"width": 300, "height": 200}, "expanded": False,
           "status": "idle", "config": {"state_machine": definition("source")}}
    node = LegionTemplateNode.model_validate(raw)
    assert "state_machine" not in node.config
    assert "state_machine" in raw["config"]  # Read migration never mutates the caller's template.
    restored = client.app.state.services.state_machines.restore(target["id"], node.state_machine, {"source": target["id"]})
    assert restored["definition"]["entities"][0]["card_id"] == target["id"]
    assert restored["enabled"] is False


def test_clear_retains_bound_versions_and_delete_cleans_state_scope(client):
    card = create_node(client, "text")
    services = client.app.state.services
    store = services.state_machines
    store.save(card["id"], definition(card["id"]))
    instance = store.activate(card["id"], 1)
    scope_id = store._scope(instance["id"]).scope_id
    store.clear(card["id"])
    assert store.get(card["id"])["definition"] is None
    assert store.get(card["id"])["revision"] == 2
    assert store.get_definition(card["id"], 1).entities[0].id == "self"
    assert store.get_instance(instance["id"])["enabled"] is False
    assert store.save(card["id"], definition(card["id"]))["definition_version"] == 2
    with pytest.raises(RevisionConflictError):
        store.save(card["id"], definition(card["id"]), expected_revision=1)
    assert client.delete(f"/api/nodes/{card['id']}").status_code == 200
    with services.database.locked() as db:
        assert db.execute("SELECT 1 FROM state_scopes WHERE scope_id=?", (scope_id,)).fetchone() is None


def test_nested_member_picker_reads_only_one_level_without_loading_graphs(client, monkeypatch):
    leaf = create_node(client, "text")
    nested = client.post("/api/legion-groups", json={"name": "Nested", "node_ids": [leaf["id"]]}).json()[0]
    sibling = create_node(client, "text")
    top = client.post("/api/legion-groups", json={"name": "Top", "node_ids": [sibling["id"]]}).json()[0]
    # Imported hierarchies may be deeper than current interactive World policy.
    # Navigation must handle that hierarchy without changing membership policy.
    with client.app.state.services.database.transaction(immediate=True) as db:
        db.execute("UPDATE cards SET parent_id=? WHERE id=?", (top["id"], nested["id"]))
    store = client.app.state.services.state_machines
    store.save(leaf["id"], definition(leaf["id"]))
    def no_graph_load(*args, **kwargs):
        raise AssertionError("Navigation must not load every member's state graph")
    monkeypatch.setattr(store, "get_definition", no_graph_load)
    monkeypatch.setattr(store, "get", no_graph_load)
    members = client.get(f"/api/state-machines/{top['id']}/members").json()["members"]
    assert {item["id"] for item in members} == {nested["id"], sibling["id"]}
    assert all("definition" not in item for item in members)
    assert next(item for item in members if item["id"] == nested["id"])["has_members"]
    members = client.get(f"/api/state-machines/{nested['id']}/members").json()["members"]
    assert members[0]["id"] == leaf["id"] and members[0]["has_definition"]


def test_reference_preview_hydrates_same_owner_version_without_runtime_writes(client):
    owner, member = create_node(client, "text"), create_node(client, "text")
    store = client.app.state.services.state_machines
    store.save(member["id"], definition(member["id"]))
    value = definition(owner["id"])
    value["references"] = [{"entity_id": "member", "card_id": member["id"], "definition_version": 1, "state_group_id": "self"}]
    value["rules"][0]["effects"].append({"entity_id": "member", "from_state": "planning", "to_state": "review"})
    preview = store.resolve_preview_definition(value)
    assert preview.references == []
    assert [entity.id for entity in preview.entities] == ["self", "member"]
    assert preview.entities[1].card_id == member["id"]
    assert store.list_instances(False) == []


def test_activation_requires_supported_phase_and_reviewed_run_arguments(client):
    card = create_node(client, "text")
    store = client.app.state.services.state_machines
    value = definition(card["id"])
    value["rules"][0]["trigger"]["event"] = "unknown.phase"
    store.save(card["id"], value)
    with pytest.raises(ResourceValidationError, match="supported trigger"):
        store.activate(card["id"], 1)
    value["rules"][0]["trigger"]["event"] = "capability.succeeded"
    value["rules"][0]["actions"] = [{"id": "run", "kind": "run", "arguments": {}}]
    store.save(card["id"], value)
    with pytest.raises(ResourceValidationError, match="prompt"):
        store.activate(card["id"], 2)
    assert store.list_instances(False) == []


@pytest.mark.parametrize("actions", [
    [{"id": "invalid", "kind": "capability"}],
    [{"id": "invalid", "kind": "node_action"}],
    [{"id": "duplicate", "kind": "run"}, {"id": "duplicate", "kind": "run"}],
])
def test_invalid_action_contracts_rejected(client, actions):
    card = create_node(client, "text")
    value = definition(card["id"])
    value["rules"][0]["actions"] = actions
    response = client.put(f"/api/state-machines/{card['id']}", json={"definition": value})
    assert response.status_code == 422
