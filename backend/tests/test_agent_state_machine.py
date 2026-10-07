from copy import deepcopy

import pytest

from backend.errors import GraphValidationError, NotFoundError, ResourceValidationError, RuntimeUnavailableError
from backend.runs import RunStatus
from backend.tests.test_runs import RecordingProvider, _services
from backend.world.models import CardCreate, CardPatch


@pytest.mark.asyncio
async def test_workflow_presentation_preserves_agent_execution_authority(tmp_path):
    services = _services(tmp_path, RecordingProvider(mode="block"))
    manager, store = services.run_manager, services.state_machines
    try:
        agent = await services.create_card(CardCreate(type="agent"))
        saved = store.get(agent.id)
        assert saved["enabled"] and agent.status == "idle"
        assert "status" not in agent.config
        definition = deepcopy(saved["definition"])
        definition["entities"].append({"id": "workflow", "label": "Workflow", "kind": "group", "initial_state": "idle", "states": [{"id": "idle", "label": "Idle"}, {"id": "researching", "label": "Collecting evidence"}]})
        definition["status_entity_id"] = "workflow"
        definition["rules"] = [{"id": "research", "name": "Research", "trigger": {"entity_id": "status", "event": "state.entered", "state_id": "running"}, "effects": [{"entity_id": "workflow", "from_state": "*", "to_state": "researching"}]}]
        edited = store.save(agent.id, definition, expected_revision=saved["revision"])
        # Saving does not replace the running rules.
        assert store.node_instance(agent.id)[0]["definition_version"] == saved["definition_version"]
        instance = store.activate(agent.id, edited["definition_version"])
        run = await manager.start_run(agent.id, "Research")
        card = services.get_card(agent.id)
        assert card.status == (await manager.get_agent(agent.id)).status == "running"
        assert card.primary_state == "researching"
        assert card.status_label == "Collecting evidence"
        assert card.active_run_count == card.occupied_run_count == 1
        assert store.get_states(instance["id"])["workflow"] == "researching"
        # A custom display state cannot bypass execution capacity.
        with pytest.raises(RuntimeUnavailableError):
            await manager.start_run(agent.id, "Another task")
        with pytest.raises(GraphValidationError, match="state machine"):
            services.world.update_card(agent.id, CardPatch(status="idle"))
        with pytest.raises(GraphValidationError, match="state machine"):
            services.world.update_card(agent.id, CardPatch(config={"status": "idle"}))
        await services.update_card(agent.id, CardPatch(name="Renamed while researching"))
        assert services.get_card(agent.id).status == "running"
        await manager.cancel_run(run.run_id)
        # Only Execution follows Run completion; Workflow remains user-owned.
        assert services.get_card(agent.id).status == "idle"
        assert services.get_card(agent.id).active_run_count == 0
    finally:
        await manager.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_aggregate_run_events_drive_default_waiting_running_and_idle(tmp_path):
    services = _services(tmp_path, RecordingProvider(mode="block"))
    manager = services.run_manager
    try:
        agent = await services.create_card(CardCreate(type="agent", config={"max_concurrent_runs": 2}))
        first = await manager.start_run(agent.id, "one")
        second = await manager.start_run(agent.id, "two")
        await manager.suspend_run(first.run_id, reason="approval", release_agent_slot=True)
        assert services.get_card(agent.id).status == "running"
        await manager.cancel_run(second.run_id)
        card = services.get_card(agent.id)
        assert card.status == "waiting"
        assert card.active_run_count == 1 and card.occupied_run_count == 0
        await manager.transition_run(first.run_id, RunStatus.RUNNING)
        assert services.get_card(agent.id).status == "running"
        await manager.cancel_run(first.run_id)
        assert services.get_card(agent.id).status == "idle"
    finally:
        await manager.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_primary_activation_preserves_valid_state_and_is_atomic(tmp_path):
    services = _services(tmp_path, RecordingProvider(mode="block"))
    store = services.state_machines
    try:
        agent = await services.create_card(CardCreate(type="agent"))
        services.state_machine_runtime.observe_agent(agent.id, "agent.work_started")
        before = store.node_instance(agent.id)[0]
        definition = store.get(agent.id)["definition"]
        definition["entities"].append({"id": "workflow", "label": "Workflow", "kind": "group", "initial_state": "new", "states": [{"id": "new", "label": "New"}]})
        saved = store.save(agent.id, definition)
        with pytest.raises(ResourceValidationError, match="one instance"):
            store.activate(agent.id, saved["definition_version"], scope_key="second")
        changed = store.activate(agent.id, saved["definition_version"])
        assert changed["id"] == before["id"] and changed["states"]["status"] == "running"
        assert len(store.list_instances(False)) == 1
        broken = deepcopy(definition)
        broken["references"] = [{"entity_id": "absent", "card_id": "missing-node"}]
        invalid = store.save(agent.id, broken)
        with pytest.raises(NotFoundError):
            store.activate(agent.id, invalid["definition_version"])
        assert store.node_instance(agent.id)[0] == changed
        store.clear(agent.id)
        assert store.get(agent.id)["definition"]["status_entity_id"] == "status"
        assert services.get_card(agent.id).status == "running"
    finally:
        services.close()


@pytest.mark.asyncio
async def test_error_and_recovery_are_canonical_runtime_projections(tmp_path):
    provider = RecordingProvider(mode="failure")
    services = _services(tmp_path, provider)
    manager = services.run_manager
    try:
        agent = await services.create_card(CardCreate(type="agent"))
        failed = await manager.start_run(agent.id, "Fail")
        await manager.wait_execution(failed.run_id)
        assert services.get_card(agent.id).status == "error"
        provider.mode = "success"
        recovered = await manager.start_run(agent.id, "Recover")
        assert services.get_card(agent.id).status == "running"
        await manager.wait_execution(recovered.run_id)
        assert services.get_card(agent.id).status == "idle"
    finally:
        await manager.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_restart_preserves_applied_custom_state_without_reinstalling_defaults(tmp_path):
    services = _services(tmp_path, RecordingProvider())
    agent = await services.create_card(CardCreate(type="agent"))
    definition = {"version": 2, "status_entity_id": "workflow", "entities": [{
        "id": "workflow", "label": "Review", "kind": "card", "initial_state": "review",
        "states": [{"id": "review", "label": "Review required"}]}], "rules": []}
    definition["entities"].extend(services.state_machines.get(agent.id)["definition"]["entities"])
    saved = services.state_machines.save(agent.id, definition)
    services.state_machines.activate(agent.id, saved["definition_version"])
    services.close()
    restarted = _services(tmp_path, RecordingProvider())
    try:
        await restarted.run_manager.register_agent(restarted.world.get_card(agent.id))
        assert restarted.get_card(agent.id).status == "idle"
        assert restarted.get_card(agent.id).primary_state == "review"
        assert len(restarted.state_machines.list_instances(False)) == 1
        assert restarted.state_machines.get(agent.id)["definition_version"] == saved["definition_version"]
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_legacy_draft_migration_keeps_authored_work_without_enabling_actions(tmp_path):
    from backend.tests.test_state_machine_runtime import machine
    services = _services(tmp_path, RecordingProvider())
    store = services.state_machines
    try:
        agent = await services.create_card(CardCreate(type="agent"))
        legacy = machine(agent.id)
        legacy["entities"] = legacy["entities"][:1]; legacy.pop("status_entity_id")
        store.save(agent.id, legacy, legacy=True)
        with services.database.transaction(immediate=True) as db:
            db.execute("DELETE FROM state_machine_instances WHERE card_id=?", (agent.id,))
        store.initialize_node(agent.id)
        saved = store.get(agent.id)
        assert saved["definition"]["status_entity_id"] == "status"
        assert saved["definition"]["entities"][1]["id"] == "self"
        assert saved["definition"]["rules"][-1]["enabled"] is False
        assert saved["enabled"] is False
        assert services.get_card(agent.id).status == "idle"
        active = store.node_instance(agent.id)[0]
        store.initialize_node(agent.id)
        assert store.get(agent.id) == saved
        assert store.node_instance(agent.id)[0] == active
    finally:
        services.close()
