from copy import deepcopy
from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from backend.errors import ResourceValidationError
from backend.plugins.registry import PluginDefinition, PluginDescriptor
from backend.state_machine import StateMachineConfig
from backend.state_machine_preview import PreviewRequest, replay_preview
from backend.tests.conftest import create_node
from backend.tests.test_state_machine_runtime import emit


def user_group(identity="workflow"):
    return {"id": identity, "label": identity.title(), "kind": "group", "ownership": "user", "initial_state": "idle",
            "states": [{"id": "idle", "label": "Idle"}, {"id": "researching", "label": "Researching"}, {"id": "done", "label": "Done"}]}


def reaction(identity="workflow", phase="entered", state="running", target="researching"):
    return {"id": identity, "name": identity, "trigger": {"entity_id": "status", "event": "state." + phase, "state_id": state},
            "effects": [{"entity_id": identity, "from_state": "*", "to_state": target}]}


def install(client, *, groups=("workflow",), rules=None):
    agent = create_node(client, "agent")
    services = client.app.state.services
    client.portal.call(services.state_machine_runtime.shutdown)
    definition = services.state_machines.get(agent["id"])["definition"]
    definition["entities"].extend(user_group(group) for group in groups)
    definition["rules"] = rules if rules is not None else [reaction(group) for group in groups]
    saved = services.state_machines.save(agent["id"], definition)
    instance = services.state_machines.activate(agent["id"], saved["definition_version"])
    return services, agent, definition, instance


def test_default_execution_is_the_only_group_and_system_owned(client):
    agent = create_node(client, "agent")
    value = client.get(f"/api/state-machines/{agent['id']}").json()["definition"]
    assert len(value["entities"]) == 1 and value["rules"] == []
    group = value["entities"][0]
    assert (group["id"], group["label"], group["ownership"], group["owner"]) == ("status", "Execution", "system", "host.run_manager")
    assert group["commands"][0]["operation_id"] == "host:run"


@pytest.mark.parametrize("mutation", ["rename", "delete_state", "delete_group", "ownership", "projection", "command"])
def test_api_rejects_system_semantic_edits_but_allows_layout(client, mutation):
    services, agent, definition, _ = install(client)
    before = services.state_machines.get(agent["id"])
    group = definition["entities"][0]
    if mutation == "rename": group["states"][1]["label"] = "Forged"
    if mutation == "delete_state": group["states"].pop()
    if mutation == "delete_group": definition["entities"].pop(0); definition["status_entity_id"] = "workflow"; definition["rules"] = []
    if mutation == "ownership": group["ownership"] = "user"
    if mutation == "projection": group["projection"][0]["to_state"] = "idle"
    if mutation == "command": group["commands"][0]["operation_id"] = "fake:run"
    response = client.put(f"/api/state-machines/{agent['id']}", json={"definition": definition})
    assert response.status_code == 422, response.text
    assert services.state_machines.get(agent["id"]) == before
    saved = services.state_machines.save(agent["id"], before["definition"], {"positions": {"status": {"running": {"x": 200, "y": 300}}}})
    assert saved["definition_version"] == before["definition_version"]


def test_system_enter_exit_multi_group_snapshot_and_same_group_first_match(client):
    rules = [reaction(), reaction("communication"), {**reaction(), "id": "loser", "effects": [{"entity_id": "workflow", "from_state": "*", "to_state": "done"}]}]
    rules[1]["conditions"] = [{"entity_id": "workflow", "state_id": "idle"}]
    services, agent, definition, instance = install(client, groups=("workflow", "communication"), rules=rules)
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    assert services.state_machines.get_states(instance["id"]) == {"status": "running", "workflow": "researching", "communication": "researching"}
    result = next(item for item in services.state_machine_runtime.diagnostics(agent["id"])["diagnostics"] if item.get("system_transitions"))
    assert result["rule_ids"] == ["workflow", "communication"]
    assert result["rules"][2]["reason"] == "lower_priority"
    assert result["user_transitions"][0]["triggered_by"] == {"entity_id": "status", "state_id": "running", "event": "state.entered"}
    preview = replay_preview(PreviewRequest(machine=StateMachineConfig.model_validate(definition), events=[{
        "entity_id": "status", "event": "agent.work_started", "event_id": "fact", "time_ms": 0}]))
    assert preview["states"] == result["states"]
    assert preview["steps"][0]["rule_ids"] == result["rule_ids"]
    definition["rules"] = [reaction(phase="exited", target="done")]
    saved = services.state_machines.save(agent["id"], definition)
    services.state_machines.activate(agent["id"], saved["definition_version"])
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_waiting")
    assert services.state_machines.get_states(instance["id"])["workflow"] == "done"


def test_direct_assignment_blocked_at_schema_store_and_low_level_write(client):
    services, agent, definition, instance = install(client)
    definition["rules"][0]["effects"][0]["entity_id"] = "status"
    definition["rules"][0]["effects"][0]["to_state"] = "running"
    with pytest.raises(ValidationError, match="directly assigned"):
        StateMachineConfig.model_validate(definition)
    for write in (lambda: services.state_machines.put_states(instance["id"], {"status": "running"}),
                  lambda: services.state_machines.put_runtime(instance["id"], states={"status": "running", "workflow": "idle"})):
        with pytest.raises(ResourceValidationError, match="directly assigned"): write()
    assert services.state_machines.node_status(agent["id"]) == "idle"


def test_command_request_queues_existing_action_without_faking_state(client):
    command_rule = {"id": "start", "name": "Start work", "trigger": {"entity_id": "workflow", "event": "custom"}, "effects": [],
                    "command": {"entity_id": "status", "state_id": "running", "command_id": "start_work", "arguments": {"prompt": "Research"}},
                    "actions": [{"id": "follow_up", "kind": "run", "arguments": {"prompt": "Follow up"}}]}
    services, agent, definition, instance = install(client, rules=[command_rule])
    sequence, event = emit(services, agent["id"], event="custom")
    result = services.state_machine_runtime.process_event(instance["id"], sequence, event)
    assert result["states"]["status"] == "idle" and len(result["commands"]) == 1
    identity = result["actions"][0]["id"]
    with services.database.locked() as db:
        action = json.loads(db.execute("SELECT intent_json FROM state_machine_actions WHERE id=?", (identity,)).fetchone()[0])["action"]
    assert action["kind"] == "run" and action["operation_id"] == "host:run"
    services.state_machine_runtime._finish(identity, "succeeded", {"run_id": "accepted-only"})
    actions = services.state_machine_runtime.diagnostics(agent["id"])["actions"]
    assert next(item for item in actions if item["id"] == identity)["outcome"] == "accepted"
    assert next(item for item in actions if item["id"] != identity)["kind"] == "action"
    assert services.state_machines.node_status(agent["id"]) == "idle"
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    assert services.state_machines.node_status(agent["id"]) == "running"
    definition["rules"][0]["command"]["command_id"] = "unregistered"
    with pytest.raises(ValidationError, match="registered legal command"):
        StateMachineConfig.model_validate(definition)


def test_disabled_user_automation_keeps_system_operational_truth(client):
    services, agent, _, instance = install(client)
    services.state_machines.put_runtime(instance["id"], enabled=False)
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    assert services.state_machines.get_states(instance["id"]) == {"status": "running", "workflow": "idle"}


def test_primary_display_does_not_replace_agent_operational_status(client):
    services, agent, definition, instance = install(client)
    definition["status_entity_id"] = "workflow"
    saved = services.state_machines.save(agent["id"], definition)
    services.state_machines.activate(agent["id"], saved["definition_version"])
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    card = client.get(f"/api/nodes/{agent['id']}").json()
    assert card["status"] == card["operational_status"] == "running"
    assert card["primary_state"] == "researching" and card["status_label"] == "Researching"
    assert card["state_groups"]["status"]["ownership"] == "system"


def test_plugin_lifecycle_uses_existing_operation_contract(client):
    services = client.app.state.services
    base = services.plugins.node_type("text")
    definition = {"version": 2, "status_entity_id": "runtime", "entities": [{"id": "runtime", "label": "Runtime", "kind": "card",
        "ownership": "system", "owner": "example.lifecycle", "initial_state": "stopped", "states": [{"id": "stopped", "label": "Stopped"}, {"id": "ready", "label": "Ready"}],
        "projection": [{"event": "operation.succeeded", "operation_id": "resource:example.runtime:start", "to_state": "ready"}]}], "rules": []}
    services.plugins.install(PluginDefinition(PluginDescriptor(id="example.lifecycle", version="1.0", plugin_api_version="1.26"),
        lambda registration: registration.register_node_type(replace(base, id="example.runtime", state_machine=definition, lifecycle=None))))
    node = create_node(client, "example.runtime")
    instance = services.state_machines.node_instance(node["id"])[0]
    sequence, event = emit(services, node["id"], event="operation.succeeded", operation_id="resource:example.runtime:start")
    services.state_machine_runtime.process_event(instance["id"], sequence, event)
    assert services.state_machines.node_status(node["id"]) == "ready"
    with pytest.raises(ResourceValidationError): services.state_machines.put_states(instance["id"], {"runtime": "stopped"})


def test_agent_operational_truth_survives_reordering_additional_plugin_system_groups(client):
    services = client.app.state.services
    base = services.plugins.node_type("agent")
    definition = base.state_machine.model_dump(mode="json", exclude_none=True)
    definition["entities"].append({"id": "plugin_runtime", "label": "Plugin runtime", "kind": "group",
        "ownership": "system", "owner": "example.agent", "initial_state": "prepared",
        "states": [{"id": "prepared", "label": "Prepared"}]})
    services.plugins.install(PluginDefinition(PluginDescriptor(id="example.agent", version="1.0", plugin_api_version="1.26"),
        lambda registration: registration.register_node_type(replace(base, id="example.agent", state_machine=definition))))
    node = create_node(client, "example.agent")
    value = services.state_machines.get(node["id"])["definition"]
    value["entities"].reverse()
    value["status_entity_id"] = "plugin_runtime"
    saved = services.state_machines.save(node["id"], value)
    services.state_machines.activate(node["id"], saved["definition_version"])
    services.state_machine_runtime.observe_agent(node["id"], "agent.work_started")
    card = client.get(f"/api/nodes/{node['id']}").json()
    assert card["status"] == card["operational_status"] == "running"
    assert card["primary_state"] == "prepared"


def test_template_omits_system_definitions_resolves_commands_and_preserves_intent(client):
    services, agent, definition, instance = install(client)
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    captured = services.state_machines.capture(agent["id"], {agent["id"]: "worker"})
    assert captured["enabled"] and captured["system_groups"] == ["status"]
    assert all(group["ownership"] == "user" for group in captured["definition"]["entities"])
    assert set(captured) == {"definition", "system_groups", "presentation", "enabled"}
    target = create_node(client, "agent")
    saved = services.state_machines.restore(target["id"], captured, {"worker": target["id"]})
    deployed = services.state_machines.activate(target["id"], saved["definition_version"])
    assert deployed["states"] == {"status": "idle", "workflow": "idle"}
    assert saved["definition"]["rules"][0]["trigger"]["entity_id"] == "status"
    unsupported = create_node(client, "text")
    with pytest.raises(ResourceValidationError, match="incompatibility"):
        services.state_machines.restore(unsupported["id"], captured, {"worker": unsupported["id"]})


def test_stable_reference_survives_apply_and_deletion_is_blocked_before_apply(client):
    services, agent, definition, instance = install(client)
    observer = create_node(client, "text")
    observing = {"version": 2, "entities": [user_group()], "references": [{"entity_id": "member", "card_id": agent["id"], "state_group_id": "workflow", "definition_version": 1}],
                 "rules": [{**reaction(), "trigger": {"entity_id": "member", "event": "state.entered", "state_id": "researching"}}]}
    saved = services.state_machines.save(observer["id"], observing)
    watching = services.state_machines.activate(observer["id"], saved["definition_version"])
    definition["entities"][1]["states"][1]["label"] = "Gathering sources"
    edited = services.state_machines.save(agent["id"], definition)
    services.state_machines.activate(agent["id"], edited["definition_version"])
    hydrated = services.state_machines.get_evaluator_definition(watching["id"])
    assert next(group for group in hydrated.entities if group.id == "member").states[1].label == "Gathering sources"
    definition["entities"][1]["states"].pop(1); definition["rules"] = []
    edited = services.state_machines.save(agent["id"], definition)
    before = services.state_machines.get_instance(instance["id"])
    with pytest.raises(ResourceValidationError, match="break a state reference"):
        services.state_machines.activate(agent["id"], edited["definition_version"])
    assert services.state_machines.get_instance(instance["id"]) == before


def test_atomic_rollback_includes_projection_receipt_user_states_and_outbox(client, monkeypatch):
    services, agent, definition, instance = install(client, groups=("workflow", "communication"))
    definition["rules"][0]["actions"] = [{"id": "run", "kind": "run", "arguments": {"prompt": "Continue"}}]
    saved = services.state_machines.save(agent["id"], definition)
    services.state_machines.activate(agent["id"], saved["definition_version"])
    before = services.state_machines.get_instance(instance["id"])
    original = services.state_machines.put_runtime
    def fail_at_cursor(*args, **kwargs):
        if "cursor" in kwargs: raise RuntimeError("Injected commit failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(services.state_machines, "put_runtime", fail_at_cursor)
    with pytest.raises(RuntimeError, match="Injected"):
        services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    assert services.state_machines.get_instance(instance["id"]) == before
    assert services.state_machine_runtime.diagnostics(agent["id"])["actions"] == []


def test_command_dispatch_uses_runmanager_and_real_aggregate_fact(client):
    from backend.tests.test_runs import RecordingProvider
    services = client.app.state.services
    services.run_manager.install_provider("core.mock", RecordingProvider(mode="block"))
    agent = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    client.portal.call(services.state_machine_runtime.shutdown)
    definition = services.state_machines.get(agent["id"])["definition"]
    definition["entities"].append(user_group())
    definition["rules"] = [{"id": "request", "name": "Request work", "trigger": {"entity_id": "workflow", "event": "custom"},
        "command": {"entity_id": "status", "state_id": "running", "command_id": "start_work", "arguments": {"prompt": "Research"}}}, reaction()]
    saved = services.state_machines.save(agent["id"], definition)
    instance = services.state_machines.activate(agent["id"], saved["definition_version"])
    seq, event = emit(services, agent["id"], event="custom")
    request = services.state_machine_runtime.process_event(instance["id"], seq, event)
    assert request["states"]["status"] == "idle"
    action_id = request["actions"][0]["id"]
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    run = services.run_manager.get_run(action_id)
    assert run.caller_kind == "state_machine"
    assert services.state_machines.get_states(instance["id"]) == {"status": "running", "workflow": "researching"}
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    assert len(services.run_manager.list_runs(agent_id=agent["id"])) == 1
    client.portal.call(services.run_manager.cancel_run, action_id)
    assert services.state_machines.node_status(agent["id"]) == "idle"


def test_legion_observes_member_system_anchor_and_rechecks_member_scope(client):
    from backend.tests.test_runs import RecordingProvider
    services = client.app.state.services
    services.run_manager.install_provider("core.mock", RecordingProvider(mode="block"))
    member = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    group = client.post("/api/legion-groups", json={"name": "Readers", "node_ids": [member["id"]]}).json()[0]
    client.portal.call(services.state_machine_runtime.shutdown)
    definition = services.state_machines.get(group["id"])["definition"]
    definition["references"] = [{"entity_id": "reader", "card_id": member["id"], "state_group_id": "status"}]
    definition["entities"].append(user_group())
    definition["rules"] = [{"id": "request", "name": "Read", "trigger": {"entity_id": "workflow", "event": "custom"},
        "command": {"entity_id": "reader", "state_id": "running", "command_id": "start_work", "arguments": {"prompt": "Read source"}}},
        {**reaction(), "trigger": {"entity_id": "reader", "event": "state.entered", "state_id": "running"}}]
    saved = services.state_machines.save(group["id"], definition)
    instance = services.state_machines.activate(group["id"], saved["definition_version"])
    seq, event = emit(services, group["id"], event="custom")
    action_id = services.state_machine_runtime.process_event(instance["id"], seq, event)["actions"][0]["id"]
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    assert services.run_manager.get_run(action_id).agent_id == member["id"]
    client.portal.call(services.state_machine_runtime.process_pending)
    assert services.state_machines.get_states(instance["id"])["workflow"] == "researching"
    with pytest.raises(ResourceValidationError, match="directly assigned"):
        services.state_machines.put_states(instance["id"], {"reader": "idle"})
    client.portal.call(services.run_manager.cancel_run, action_id)
    seq, event = emit(services, group["id"], event="custom", identity="second")
    pending = services.state_machine_runtime.process_event(instance["id"], seq, event)["actions"][0]["id"]
    assert client.patch(f"/api/nodes/{member['id']}", json={"parent_id": None}).status_code == 200
    client.portal.call(services.state_machine_runtime.dispatch, pending)
    assert services.state_machine_runtime.diagnostics(group["id"])["actions"][0]["status"] == "revoked"
    assert len(services.run_manager.list_runs(agent_id=member["id"])) == 1


def test_broken_user_reference_does_not_freeze_execution_projection(client):
    services, agent, definition, instance = install(client)
    other = create_node(client, "agent")
    definition["references"] = [{"entity_id": "other", "card_id": other["id"], "state_group_id": "status"}]
    saved = services.state_machines.save(agent["id"], definition)
    services.state_machines.activate(agent["id"], saved["definition_version"])
    assert client.delete(f"/api/nodes/{other['id']}").status_code == 200
    services.state_machine_runtime.observe_agent(agent["id"], "agent.work_started")
    assert services.state_machines.node_status(agent["id"]) == "running"
    assert not services.state_machines.get_instance(instance["id"])["enabled"]


def test_template_deployment_preserves_active_and_draft_user_rules(client):
    services, agent, definition, instance = install(client)
    second = create_node(client, "agent")
    draft = services.state_machines.get(second["id"])["definition"]
    draft["entities"].append(user_group())
    draft["rules"] = [reaction()]
    services.state_machines.save(second["id"], draft)
    response = client.post("/api/legions", json={"name": "Reusable", "node_ids": [agent["id"], second["id"]]})
    assert response.status_code == 201, response.text
    deployed = client.post(f"/api/legions/{response.json()['id']}/instances", json={})
    assert deployed.status_code == 201, deployed.text
    nodes = deployed.json()["node_ids"]
    record = services.legions.get(response.json()["id"])
    active_template = next(node for node in record.blueprint.nodes if node.state_machine["enabled"])
    draft_template = next(node for node in record.blueprint.nodes if not node.state_machine["enabled"])
    assert services.state_machines.get(nodes[active_template.key])["enabled"]
    assert not services.state_machines.get(nodes[draft_template.key])["enabled"]
    services.state_machine_runtime.observe_agent(nodes[active_template.key], "agent.work_started")
    assert services.state_machines.state_summary(nodes[active_template.key])["state_groups"]["workflow"]["state_id"] == "researching"


def test_system_contract_update_checks_dependent_stable_states(client):
    services, agent, definition, instance = install(client)
    observer = create_node(client, "text")
    observing = {"version": 2, "entities": [user_group()], "references": [{"entity_id": "member", "card_id": agent["id"], "state_group_id": "status"}],
                 "rules": [{**reaction(), "trigger": {"entity_id": "member", "event": "state.entered", "state_id": "running"}}]}
    saved = services.state_machines.save(observer["id"], observing)
    services.state_machines.activate(observer["id"], saved["definition_version"])
    candidate = deepcopy(definition)
    candidate["entities"][0]["states"] = [state for state in candidate["entities"][0]["states"] if state["id"] != "running"]
    candidate["entities"][0]["projection"] = [projection for projection in candidate["entities"][0]["projection"] if projection["to_state"] != "running"]
    candidate["entities"][0]["commands"] = []
    candidate["rules"] = []
    with pytest.raises(ResourceValidationError, match="break a state reference"):
        services.state_machines.validate_dependents(agent["id"], StateMachineConfig.model_validate(candidate))
    assert services.state_machines.get_instance(instance["id"])["definition_version"] == instance["definition_version"]


def test_active_template_resolves_mutual_user_group_references_after_all_owners_bind(client):
    services, left, definition, _ = install(client)
    right = create_node(client, "agent")
    right_definition = services.state_machines.get(right["id"])["definition"]
    right_definition["entities"].append(user_group())
    saved = services.state_machines.save(right["id"], right_definition)
    services.state_machines.activate(right["id"], saved["definition_version"])
    for owner, other, value in [(left, right, definition), (right, left, right_definition)]:
        value["references"] = [{"entity_id": "peer", "card_id": other["id"], "state_group_id": "workflow"}]
        value["rules"] = [{**reaction(), "trigger": {"entity_id": "peer", "event": "state.entered", "state_id": "done"}}]
        saved = services.state_machines.save(owner["id"], value)
        services.state_machines.activate(owner["id"], saved["definition_version"])
    template = client.post("/api/legions", json={"name": "Mutual references", "node_ids": [left["id"], right["id"]]})
    assert template.status_code == 201, template.text
    deployed = client.post(f"/api/legions/{template.json()['id']}/instances", json={})
    assert deployed.status_code == 201, deployed.text
    identities = set(deployed.json()["node_ids"].values())
    for identity in identities:
        instance = services.state_machines.node_instance(identity)[0]
        assert instance["enabled"] and instance["states"] == {"status": "idle", "workflow": "idle"}
        resolved = services.state_machines.get_evaluator_definition(instance["id"])
        assert next(group.card_id for group in resolved.entities if group.id == "peer") in identities - {identity}


def test_pre_ownership_modern_template_keeps_user_groups_and_activation_intent(client):
    services = client.app.state.services
    target = create_node(client, "agent")
    old_group = user_group("status")
    captured = {"definition": {"version": 2, "status_entity_id": "status", "entities": [old_group], "rules": []},
                "presentation": {}, "enabled": True}
    saved = services.state_machines.restore(target["id"], captured, {})
    assert [group["id"] for group in saved["definition"]["entities"]] == ["status", "legacy_status"]
    services.state_machines.activate_restored([target["id"]])
    assert services.state_machines.get(target["id"])["enabled"]
    services.state_machine_runtime.observe_agent(target["id"], "agent.work_started")
    assert services.state_machines.node_status(target["id"]) == "running"
