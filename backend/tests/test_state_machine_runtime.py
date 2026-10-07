import asyncio

import pytest

from backend.events.models import EventType, RuntimeEvent
from backend.state_machine_preview import PreviewRequest, replay_preview
from backend.state_machine_runtime import StateMachineRuntime
from backend.tests.conftest import create_node
from backend.tests.test_automatic_events import register_unknown_tool


def machine(card_id, *, action=None, trigger="capability.succeeded"):
    from backend.agent_state_machine import agent_state_machine
    value = {"version": 2, "status_entity_id": "status", "entities": [{"id": "self", "label": "Work", "kind": "card", "card_id": card_id,
        "initial_state": "planning", "states": [{"id": "planning", "label": "Planning"},
                                                 {"id": "review", "label": "Awaiting review"}]}],
        "rules": [{"id": "ready", "name": "Review", "trigger": {"entity_id": "self", "event": trigger},
                   "effects": [{"entity_id": "self", "from_state": "planning", "to_state": "review"}],
                   "actions": [action] if action else []}]}
    value["entities"].extend(agent_state_machine()["entities"])
    return value


def setup(client, definition):
    services = client.app.state.services
    client.portal.call(services.state_machine_runtime.shutdown)
    card_id = definition["entities"][0]["card_id"]
    declared = services.world.card_definition(services.world.get_card(card_id))
    if not declared.state_machine or not any(group.ownership == "system" for group in declared.state_machine.entities):
        definition["entities"] = [group for group in definition["entities"] if group.get("ownership") != "system"]
        definition.pop("status_entity_id", None)
    saved = services.state_machines.save(card_id, definition)
    instance = services.state_machines.activate(card_id, saved["definition_version"])
    return services, instance


def emit(services, card_id, *, identity="invocation-1", event="capability.succeeded", associations=None, **payload):
    observation = RuntimeEvent(id=identity + ":" + event, type=EventType.CAPABILITY_SUCCEEDED,
        agent_id=card_id, node_id=card_id, payload={"event": event, "invocation_id": identity,
        "associations": associations or [], **payload})
    sequence = services.operation_events.record(observation)
    return sequence, observation


def action(target=None):
    return {"id": "polish", "kind": "capability", "capability": "example.autoevents.polish",
            "target": target or {"kind": "current"}, "arguments": {}}


def test_durable_duplicate_event_transitions_once_and_shares_preview_semantics(client):
    agent = create_node(client, "agent")
    definition = machine(agent["id"])
    services, instance = setup(client, definition)
    seq, event = emit(services, agent["id"])
    runtime = services.state_machine_runtime
    actual = runtime.process_event(instance["id"], seq, event)
    before = services.state_machines.get_instance(instance["id"])
    assert services.operation_events.record(event) == seq
    assert runtime.process_event(instance["id"], seq, event) is None
    assert services.state_machines.get_instance(instance["id"])["revision"] == before["revision"]
    preview = replay_preview(PreviewRequest.model_validate({"machine": definition, "events": [{
        "entity_id": "self", "event": "capability.succeeded", "event_id": event.id, "time_ms": event.timestamp.timestamp() * 1000}]}))
    assert actual["states"] == preview["states"] == {"self": "review", "status": "idle"}
    assert actual["rules"] == preview["steps"][0]["rules"]
    assert actual["rule_id"] == preview["last_rule_id"]


def test_mutation_and_durable_event_rollback_together(client):
    services = client.app.state.services
    scope = services.state.ensure_scope("agent", create_node(client, "agent")["id"], schema_id="core.agent")
    before = services.operation_events.max_sequence()
    with pytest.raises(RuntimeError), services.database.transaction(immediate=True):
        services.state.set(scope, "memory", {"entered": "transient"})
        assert services.operation_events.max_sequence() > before
        raise RuntimeError("rollback business mutation")
    assert services.operation_events.max_sequence() == before


def test_pending_intent_survives_processor_restart_and_invokes_existing_capability(client):
    calls = []
    async def handler(context, capability, arguments):
        calls.append(capability.target_id)
        return {"ok": True}
    agent, target, _ = register_unknown_tool(client, handler)
    services, instance = setup(client, machine(agent["id"], action=action({"kind": "specific", "card_id": target["id"]})))
    seq, event = emit(services, agent["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    action_id = result["actions"][0]["id"]
    restarted = StateMachineRuntime(services)
    client.portal.call(restarted.dispatch, action_id)
    client.portal.call(restarted.dispatch, action_id)
    assert calls == [target["id"]]
    assert restarted.diagnostics(agent["id"])["actions"][0]["status"] == "succeeded"


def test_capability_revoked_while_action_pending_blocks_dispatch(client):
    calls = []
    async def handler(*args):
        calls.append(True)
    agent, target, edge = register_unknown_tool(client, handler)
    services, instance = setup(client, machine(agent["id"], action=action({"kind": "specific", "card_id": target["id"]})))
    seq, event = emit(services, agent["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    client.delete(f"/api/edges/{edge['id']}")
    client.portal.call(services.state_machine_runtime.dispatch, result["actions"][0]["id"])
    assert not calls
    assert services.state_machine_runtime.diagnostics(agent["id"])["actions"][0]["status"] == "revoked"


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
def test_side_effect_failure_never_blindly_replayed(client, failure):
    calls = []
    async def handler(*args):
        calls.append("external effect already happened")
        raise failure()
    agent, target, _ = register_unknown_tool(client, handler)
    services, instance = setup(client, machine(agent["id"], action=action({"kind": "specific", "card_id": target["id"]})))
    seq, event = emit(services, agent["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    action_id = result["actions"][0]["id"]
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    client.portal.call(StateMachineRuntime(services).dispatch, action_id)
    assert len(calls) == 1
    assert services.state_machine_runtime.diagnostics(agent["id"])["actions"][0]["status"] == "uncertain"


def test_concurrent_produced_objects_bind_by_invocation_not_type(client):
    agent = create_node(client, "agent")
    targets = [create_node(client, "agent") for _ in range(2)]
    definition = machine(agent["id"], action=action({"kind": "produced"}))
    definition["rules"][0]["effects"][0]["from_state"] = "*"
    services, instance = setup(client, definition)
    for index, target in enumerate(targets):
        seq, event = emit(services, agent["id"], identity=f"invocation-{index}",
                          associations=[{"kind": "object", "id": target["id"], "produced": True}])
        services.state_machine_runtime.process_event(instance["id"], seq, event)
    with services.database.locked() as db:
        import json
        intents = [json.loads(row[0]) for row in db.execute("SELECT intent_json FROM state_machine_actions ORDER BY rowid")]
    assert [item["target_id"] for item in intents] == [target["id"] for target in targets]


def test_invocation_return_is_not_work_completion(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"], trigger="execution.succeeded"))
    seq, event = emit(services, agent["id"])
    services.state_machine_runtime.process_event(instance["id"], seq, event)
    assert services.state_machines.get_states(instance["id"]) == {"self": "planning", "status": "idle"}
    seq, event = emit(services, agent["id"], event="execution.succeeded")
    services.state_machine_runtime.process_event(instance["id"], seq, event)
    assert services.state_machines.get_states(instance["id"]) == {"self": "review", "status": "idle"}


def test_legacy_continuation_cannot_be_dispatched_again(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"], action={"id": "continue", "kind": "run", "arguments": {"prompt": "Continue"}}))
    seq, event = emit(services, agent["id"], continuation_owner="legacy")
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    client.portal.call(services.state_machine_runtime.dispatch, result["actions"][0]["id"])
    diagnostic = services.state_machine_runtime.diagnostics(agent["id"])["actions"][0]
    assert diagnostic["error"] == "continuation_owned_by_legacy"
    assert services.run_manager.list_runs(agent_id=agent["id"]) == []


def test_host_worker_processes_without_editor_or_live_turn(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"]))
    async def observe():
        await services.state_machine_runtime.startup()
        emit(services, agent["id"])
        for _ in range(50):
            if services.state_machines.get_states(instance["id"]) == {"self": "review", "status": "idle"}:
                break
            await asyncio.sleep(.01)
        await services.state_machine_runtime.shutdown()
    client.portal.call(observe)
    assert services.state_machines.get_states(instance["id"]) == {"self": "review", "status": "idle"}
    assert services.run_manager.list_runs(agent_id=agent["id"]) == []


def test_new_backend_reads_unprocessed_events_and_pinned_instance_from_disk(client):
    from backend.services import create_services
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"]))
    emit(services, agent["id"])
    reopened = create_services(services.settings)
    try:
        client.portal.call(reopened.state_machine_runtime.process_pending)
        assert reopened.state_machines.get_states(instance["id"]) == {"self": "review", "status": "idle"}
        assert reopened.state_machines.get_instance(instance["id"])["definition_version"] == instance["definition_version"]
    finally:
        reopened.database.close()


def test_crash_during_dispatch_becomes_uncertain_without_resubmission(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"], action=action()))
    seq, event = emit(services, agent["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    action_id = result["actions"][0]["id"]
    services.state_machine_runtime._finish(action_id, "executing")
    async def recover():
        runtime = StateMachineRuntime(services)
        await runtime.startup()
        await runtime.shutdown()
    client.portal.call(recover)
    assert services.state_machine_runtime.diagnostics(agent["id"])["actions"][0]["status"] == "uncertain"


def test_atomic_cross_object_states_share_member_authority(client):
    agent, member = create_node(client, "agent"), create_node(client, "agent")
    services = client.app.state.services
    services.state_machines.save(member["id"], machine(member["id"]))
    services.state_machines.activate(member["id"])
    definition = machine(agent["id"])
    definition["references"] = [{"entity_id": "member", "card_id": member["id"]}]
    definition["rules"][0]["effects"].append({"entity_id": "member", "from_state": "planning", "to_state": "review"})
    services, instance = setup(client, definition)
    seq, event = emit(services, agent["id"])
    services.state_machine_runtime.process_event(instance["id"], seq, event)
    member_instance = next(item for item in services.state_machines.list_instances(False) if item["card_id"] == member["id"])
    assert member_instance["states"] == {"self": "review", "status": "idle"}
    assert services.state_machines.get_states(instance["id"]) == {"self": "review", "status": "idle", "member": "review"}
    assert member_instance["enabled"]


def test_entered_event_filters_authoritative_group_and_transient_state(client):
    from copy import deepcopy
    agent = create_node(client, "agent")
    definition = machine(agent["id"], trigger="state.entered")
    group = deepcopy(definition["entities"][0])
    group["id"] = "other"
    definition["entities"].append(group)
    definition["rules"][0]["trigger"].update(entity_id="other", state_id="review")
    services, instance = setup(client, definition)
    seq, event = emit(services, agent["id"], event="state.entered", entity_id="self", state_id="review", state_instance_id=instance["id"])
    assert services.state_machine_runtime.process_event(instance["id"], seq, event)["rule_id"] is None
    seq, event = emit(services, agent["id"], identity="other-entry", event="state.entered", entity_id="other", state_id="planning", state_instance_id=instance["id"])
    assert services.state_machine_runtime.process_event(instance["id"], seq, event)["rule_id"] is None
    # A historical entry remains distinguishable even if the current group is
    # already elsewhere; its durable event carries the entered state identity.
    seq, event = emit(services, agent["id"], identity="transient-entry", event="state.entered", entity_id="other", state_id="review", state_instance_id=instance["id"])
    assert services.state_machine_runtime.process_event(instance["id"], seq, event)["rule_id"] == "ready"


def test_rule_priority_is_not_changed_by_object_projection_order(client):
    from copy import deepcopy
    agent = create_node(client, "agent")
    definition = machine(agent["id"])
    group = deepcopy(definition["entities"][0])
    group["id"] = "other"
    definition["entities"].append(group)
    preferred = deepcopy(definition["rules"][0])
    preferred["id"] = "preferred"
    preferred["trigger"]["entity_id"] = "other"
    definition["rules"].insert(0, preferred)
    services, instance = setup(client, definition)
    seq, event = emit(services, agent["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    assert result["rule_id"] == "preferred"
    assert result["rules"][1]["reason"] == "lower_priority"


def test_deleted_reference_does_not_starve_another_instance(client):
    first, member, second = [create_node(client, "agent") for _ in range(3)]
    services = client.app.state.services
    services.state_machines.save(member["id"], machine(member["id"]))
    services.state_machines.activate(member["id"])
    definition = machine(first["id"])
    definition["references"] = [{"entity_id": "member", "card_id": member["id"]}]
    services, broken = setup(client, definition)
    services.state_machines.save(second["id"], machine(second["id"]))
    healthy = services.state_machines.activate(second["id"])
    assert client.delete(f"/api/nodes/{member['id']}").status_code == 200
    emit(services, first["id"])
    emit(services, second["id"], identity="healthy")
    client.portal.call(services.state_machine_runtime.process_pending)
    assert not services.state_machines.get_instance(broken["id"])["enabled"]
    assert services.state_machines.get_states(healthy["id"]) == {"self": "review", "status": "idle"}


def test_stable_event_id_cannot_hide_different_operation(client):
    from backend.errors import ConflictError
    services = client.app.state.services
    agent = create_node(client, "agent")
    _, event = emit(services, agent["id"])
    with pytest.raises(ConflictError):
        services.operation_events.record(event.model_copy(update={"payload": {**event.payload, "target_card_id": "different"}}))


def test_interrupted_run_is_preserved_and_continuation_uses_new_host_run(client):
    from backend.tests.test_runs import RecordingProvider
    from backend.runs.models import RunStatus
    services = client.app.state.services
    services.run_manager.install_provider("core.mock", RecordingProvider())
    agent = create_node(client, "agent", config={"runtime_provider_id": "core.mock"})
    services, instance = setup(client, machine(agent["id"], action={"id": "continue", "kind": "run", "arguments": {"prompt": "Continue review"}}))
    previous = services.run_manager.store.create(agent_id=agent["id"], runtime_provider_id="core.mock", caller_kind="user", context_id="original-context")
    services.run_manager.store.update_status(previous.run_id, RunStatus.INTERRUPTED)
    event = RuntimeEvent(type=EventType.CAPABILITY_SUCCEEDED, node_id=agent["id"], run_id=previous.run_id,
                         payload={"event": "capability.succeeded", "continuation_owner": "state_machine"})
    seq = services.operation_events.record(event)
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    action_id = result["actions"][0]["id"]
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    continued = services.run_manager.get_run(action_id)
    assert services.run_manager.get_run(previous.run_id).status is RunStatus.INTERRUPTED
    assert continued.parent_run_id is None
    assert continued.context_id == "original-context"
    assert continued.lifecycle["continued_from_run"] == previous.run_id
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    assert len(services.run_manager.list_runs(agent_id=agent["id"])) == 2


def test_run_admission_rechecks_connection_after_action_is_claimed(client, monkeypatch):
    from backend.tests.test_runs import RecordingProvider
    import backend.state_machine_runtime as runtime_module
    services = client.app.state.services
    services.run_manager.install_provider("core.mock", RecordingProvider())
    caller, target = [create_node(client, "agent", config={"runtime_provider_id": "core.mock"}) for _ in range(2)]
    edge = client.post("/api/edges", json={"source": caller["id"], "target": target["id"], "relationship": "communicate"}).json()
    continuation = {"id": "continue", "kind": "run", "target": {"kind": "specific", "card_id": target["id"]}, "arguments": {"prompt": "Continue"}}
    services, instance = setup(client, machine(caller["id"], action=continuation))
    seq, event = emit(services, caller["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    original = runtime_module.execute_tool
    async def revoke_then_invoke(operation):
        await services.delete_edge(edge["id"])
        return await original(operation)
    monkeypatch.setattr(runtime_module, "execute_tool", revoke_then_invoke)
    client.portal.call(services.state_machine_runtime.dispatch, result["actions"][0]["id"])
    assert services.run_manager.list_runs(agent_id=target["id"]) == []
    assert services.state_machine_runtime.diagnostics(caller["id"])["actions"][0]["status"] == "revoked"


def test_capacity_wait_does_not_block_fresh_actions(client):
    calls = []
    async def handler(*args):
        calls.append("dispatched")
        return {"ok": True}
    agent, target, _ = register_unknown_tool(client, handler)
    services, instance = setup(client, machine(agent["id"], action=action({"kind": "specific", "card_id": target["id"]})))
    seq, event = emit(services, agent["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    action_id = result["actions"][0]["id"]
    with services.database.transaction(immediate=True) as db:
        for index in range(60):
            db.execute("""INSERT INTO state_machine_actions(id,instance_id,event_id,rule_id,status,intent_json,updated_at)
                SELECT ?,instance_id,event_id,rule_id,'waiting_capacity',
                    json_set(intent_json,'$.action.kind','run','$.action.arguments.prompt','Capacity check'),
                    '2000-01-01 00:00:00' FROM state_machine_actions WHERE id=?""",
                (f"capacity-{index}", action_id))
    client.portal.call(services.state_machine_runtime.process_pending)
    assert calls == ["dispatched"]
    with services.database.locked() as db:
        assert db.execute("SELECT status FROM state_machine_actions WHERE id=?", (action_id,)).fetchone()[0] == "succeeded"


def test_chained_entered_state_preserves_conversation_context(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"]))
    event = RuntimeEvent(type=EventType.CAPABILITY_SUCCEEDED, node_id=agent["id"],
                         conversation_id="conversation", session_id="session", run_id="run",
                         payload={"event": "capability.succeeded"})
    seq = services.operation_events.record(event)
    services.state_machine_runtime.process_event(instance["id"], seq, event)
    entered = next(item for _, item in services.operation_events.after(seq) if item.payload.get("event") == "state.entered")
    assert (entered.conversation_id, entered.session_id, entered.run_id) == ("conversation", "session", "run")
    assert services.state_machine_runtime._conversation({"event": entered.model_dump(), "context": {}}) == ("conversation", "session")


def test_entered_event_uses_authoritative_instance_for_scopes_and_references(client):
    agent, observer = create_node(client, "text"), create_node(client, "text")
    services, first = setup(client, machine(agent["id"]))
    edited_value = machine(agent["id"], trigger="state.entered")
    edited_value["entities"] = edited_value["entities"][:1]; edited_value.pop("status_entity_id")
    edited = services.state_machines.save(agent["id"], edited_value)
    second = services.state_machines.activate(agent["id"], edited["definition_version"], scope_key="edited")
    watching = machine(observer["id"], trigger="state.entered")
    watching["entities"] = watching["entities"][:1]; watching.pop("status_entity_id")
    watching["references"] = [{"entity_id": "member", "card_id": agent["id"], "definition_version": first["definition_version"]}]
    watching["rules"][0]["trigger"]["entity_id"] = "member"
    services.state_machines.save(observer["id"], watching)
    reference = services.state_machines.activate(observer["id"])
    seq, event = emit(services, agent["id"])
    services.state_machine_runtime.process_event(first["id"], seq, event)
    entry_seq, entered = next((n, item) for n, item in services.operation_events.after(seq) if item.payload.get("event") == "state.entered")
    assert entered.payload["state_instance_id"] == first["id"]
    assert services.state_machine_runtime.process_event(second["id"], entry_seq, entered) is None
    assert services.state_machines.get_states(second["id"]) == {"self": "planning"}
    assert services.state_machine_runtime.process_event(reference["id"], entry_seq, entered)["rule_id"] == "ready"


def test_shared_work_payload_context_reaches_action_invocation(client):
    from backend.operation_associations import active_operation
    from backend.card_state import active_state_session
    calls = []
    async def handler(*args):
        calls.append((active_operation.get().payload(), active_state_session.get()))
        return {"ok": True}
    agent, target, _ = register_unknown_tool(client, handler)
    services, instance = setup(client, machine(agent["id"], trigger="execution.succeeded", action=action({"kind": "specific", "card_id": target["id"]})))
    seq, event = emit(services, agent["id"], event="execution.succeeded", conversation_id="conversation", context_id="session")
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    client.portal.call(services.state_machine_runtime.dispatch, result["actions"][0]["id"])
    assert len(calls) == 1
    assert calls[0][0]["conversation_id"] == "conversation"
    assert calls[0][0]["context_id"] == calls[0][1] == "session"


def test_transition_evaluation_waits_for_host_mutation_boundary(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"]))
    emit(services, agent["id"])
    async def observe():
        async with services._node_mutation():
            task = asyncio.create_task(services.state_machine_runtime.process_pending())
            await asyncio.sleep(0)
            assert not task.done()
            assert services.state_machines.get_states(instance["id"]) == {"self": "planning", "status": "idle"}
        await asyncio.wait_for(task, timeout=2)
    client.portal.call(observe)
    assert services.state_machines.get_states(instance["id"]) == {"self": "review", "status": "idle"}


def test_cyclic_state_entry_stops_with_durable_diagnostic(client):
    agent = create_node(client, "agent")
    services, instance = setup(client, machine(agent["id"], trigger="state.entered"))
    seq, event = emit(services, agent["id"], event="state.entered", entity_id="self", state_id="planning",
                      state_instance_id=instance["id"], cascade_depth=32)
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    assert result["reason"] == "cascade_limit"
    assert services.state_machines.get_states(instance["id"]) == {"self": "planning", "status": "idle"}
    assert services.state_machine_runtime.diagnostics(agent["id"])["diagnostics"][0]["reason"] == "cascade_limit"


def test_registered_node_action_retains_durable_action_identity(client):
    from backend.node_documents import read_document
    from backend.tests.test_document_action_prepare import install_prepared_node
    async def prepare(value, arguments):
        return {"content": "completed by the registered action"}
    node, _ = install_prepared_node(client, prepare)
    definition = machine(node["id"], action={"id": "load", "kind": "node_action", "action": "load",
        "operation_id": "document:example.prepared:load", "arguments": {}})
    services, instance = setup(client, definition)
    seq, event = emit(services, node["id"])
    result = services.state_machine_runtime.process_event(instance["id"], seq, event)
    action_id = result["actions"][0]["id"]
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    client.portal.call(services.state_machine_runtime.dispatch, action_id)
    assert read_document(services, node["id"])["revision"] == 1
    events = [item for _, item in services.operation_events.after(seq) if item.type in {EventType.OPERATION_STARTED, EventType.OPERATION_SUCCEEDED}]
    assert [item.payload["invocation_id"] for item in events] == [action_id, action_id]
    assert services.state_machine_runtime.diagnostics(node["id"])["actions"][0]["status"] == "succeeded"
