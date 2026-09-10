from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.agents import MockAgentRuntime
from backend.config import Settings
from backend.errors import ConflictError, PermissionDeniedError, RevisionConflictError, RuntimeUnavailableError
from backend.legions.runtime import LegionStateWrite, read_shared_state, write_shared_state
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.world.models import CardCreate, CardPatch


def test_legion_is_managed_and_cannot_be_created_directly(client):
    legion_type = next(
        item for item in client.get("/api/catalog").json()["node_types"]
        if item["id"] == "legion"
    )
    assert legion_type["user_creatable"] is False

    response = client.post("/api/nodes", json={"type": "legion"})
    assert response.status_code == 422
    assert "cannot be created directly" in response.text

    member = create_node(client, "agent")
    formed = client.post(
        "/api/legion-groups", json={"name": "Managed", "node_ids": [member["id"]]}
    )
    assert formed.status_code == 200, formed.text
    assert formed.json()[0]["type"] == "legion"


def test_group_membership_move_external_edges_and_reload(client):
    first = create_node(client, "agent", position={"x": 400, "y": 200})
    second = create_node(client, "text", position={"x": 800, "y": 400})
    outside = create_node(client, "agent")
    edge = client.post("/api/edges", json={"source": first["id"], "target": outside["id"], "relationship": "communicate"}).json()
    response = client.post("/api/legion-groups", json={"name": "Team", "node_ids": [first["id"], second["id"]]})
    assert response.status_code == 200, response.text
    group, *members = response.json()
    assert all(m["parent_id"] == group["id"] for m in members)
    moved = client.patch(f"/api/nodes/{group['id']}", json={"position": {"x": group["position"]["x"] + 5000, "y": group["position"]["y"]}})
    assert moved.status_code == 200, moved.text
    snapshot = client.get("/api/world?chunks=2:0").json()
    by_id = {n["id"]: n for n in snapshot["nodes"]}
    assert by_id[first["id"]]["position"]["x"] == 5400
    assert by_id[second["id"]]["parent_id"] == group["id"]
    assert any(e["id"] == edge["id"] for e in snapshot["edges"])
    # No membership edge grants extra resource/agent permissions.
    assert client.delete(f"/api/nodes/{group['id']}").status_code == 422
    for member in members:
        assert client.patch(f"/api/nodes/{member['id']}", json={"parent_id": None}).status_code == 200
    assert client.delete(f"/api/nodes/{group['id']}").status_code == 200


def test_templates_remap_membership_and_copy_independent_shared_variables(client):
    member = create_node(client, "agent")
    group = client.post("/api/legion-groups", json={"name": "Reusable", "node_ids": [member["id"]]}).json()[0]
    client.patch(f"/api/nodes/{group['id']}", json={"config": {"instruction": "Review before execution", "model_override": "test/model"}})
    state_url = f"/api/legion-groups/{group['id']}/state"
    assert client.put(state_url, json={"value": {"progress": "original"}, "expected_revision": 0}).status_code == 200
    template = client.post("/api/legions", json={"name": "Reusable", "node_ids": [group["id"], member["id"]]}).json()
    response = client.post(f"/api/legions/{template['id']}/instances", json={"as_group": True})
    assert response.status_code == 201, response.text
    nodes = response.json()["nodes"]
    copy = next(n for n in nodes if n["type"] == "legion")
    worker = next(n for n in nodes if n["type"] == "agent")
    assert worker["parent_id"] == copy["id"] != group["id"]
    assert copy["config"]["instruction"] == "Review before execution"
    copy_state_url = f"/api/legion-groups/{copy['id']}/state"
    assert client.get(copy_state_url).json() == {"value": {"progress": "original"}, "revision": 1}
    assert client.put(copy_state_url, json={"value": {"progress": "copy changed"}, "expected_revision": 1}).status_code == 200
    assert client.get(state_url).json()["value"] == {"progress": "original"}
    assert client.put(state_url, json={"value": {"progress": "source changed"}, "expected_revision": 1}).status_code == 200
    another = client.post(f"/api/legions/{template['id']}/instances", json={"as_group": True}).json()
    another_group = next(n for n in another["nodes"] if n["type"] == "legion")
    assert client.get(f"/api/legion-groups/{another_group['id']}/state").json()["value"] == {"progress": "original"}
    assert client.put(state_url, json={"value": {}, "expected_revision": 0}).status_code == 409
    assert client.post("/api/nodes", json={"type": "legion", "parent_id": group["id"]}).status_code == 422
    assert client.post("/api/nodes", json={"type": "text", "parent_id": member["id"]}).status_code == 422


class CaptureRuntime(MockAgentRuntime):
    block = False

    async def execute(self, config, context, runtime_input):
        self.captured_config = config
        self.captured_context = context
        if self.block:
            await asyncio.Event().wait()
        async for event in super().execute(config, context, runtime_input):
            yield event


@pytest.mark.asyncio
@pytest.mark.parametrize("team_model,own_model", [("team/model", "own/model"), ("oaw:model:team", "oaw:model:own")])
async def test_runtime_inherits_context_and_revokes_tools(tmp_path: Path, team_model: str, own_model: str):
    services = create_services(Settings.for_data_root(tmp_path / "world"), default_runtime_provider_id="core.mock")
    manager = services.run_manager
    assert manager is not None
    runtime = CaptureRuntime(manager.capability_provider)
    manager.install_provider("core.mock", runtime)
    try:
        group = await services.restore_card(CardCreate(id="runtime-team", type="legion", config={"instruction": "Coordinate through the board", "model_override": team_model}))
        member = await services.create_card(CardCreate(type="agent", parent_id=group.id, config={"model": own_model, "legion_role": "Planner"}))
        outsider = await services.create_card(CardCreate(type="agent"))
        write_shared_state(services.world, services.state, group.id, LegionStateWrite(value={"goal": "Deliver a report"}, expected_revision=0))
        run = await manager.start_run(member.id, "hello")
        await manager.wait_execution(run.run_id)
        assert runtime.captured_config.model == team_model
        assert "Coordinate through the board" in runtime.captured_config.system_instruction
        assert "Deliver a report" in runtime.captured_config.system_instruction
        assert runtime.captured_context.group_context["role"] == "Planner"
        assert [s.scope_kind for s in runtime.captured_context.state_context.scope_stack] == ["world", "legion", "agent", "run"]
        # Live tool checks re-derive membership and access mode for every invocation.
        provider = manager.capability_provider
        patch_id = f"legion.state.patch:{group.id}"
        result = await provider.invoke_tool(member.id, patch_id, {"value": {"plan": ["a", "b"]}, "expected_revision": 1})
        assert result["revision"] == 2 and "goal" in result["value"]
        with pytest.raises(RevisionConflictError):
            await provider.invoke_tool(member.id, patch_id, {"value": {"plan": []}, "expected_revision": 1})
        with pytest.raises(PermissionDeniedError):
            await provider.invoke_tool(outsider.id, patch_id, {"value": {}, "expected_revision": 2})
        await services.update_card(group.id, CardPatch(config={"shared_state_access": "read_only", "paused": True}))
        with pytest.raises(PermissionDeniedError):
            await provider.invoke_tool(member.id, patch_id, {"value": {}, "expected_revision": 2})
        with pytest.raises(RuntimeUnavailableError, match="paused"):
            await manager.start_run(member.id, "not admitted")
        await services.update_card(group.id, CardPatch(config={"paused": False}))
        await services.update_card(member.id, CardPatch(config={"inherit_legion_model": False}))
        runtime.block = True
        run = await manager.start_run(member.id, "wait")
        await asyncio.sleep(0.01)
        assert runtime.captured_config.model == own_model
        with pytest.raises(ConflictError):
            await services.update_card(member.id, CardPatch(parent_id=None))
        await manager.cancel_run(run.run_id)
        await services.update_card(member.id, CardPatch(parent_id=None))
        with pytest.raises(PermissionDeniedError):
            await provider.invoke_tool(member.id, f"legion.state.read:{group.id}", {})
        assert read_shared_state(services.world, services.state, group.id)["value"]["plan"] == ["a", "b"]
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_group_state_and_membership_survive_backend_restart(tmp_path: Path):
    settings = Settings.for_data_root(tmp_path / "world")
    services = create_services(settings)
    group = await services.restore_card(CardCreate(id="persistent-team", type="legion"))
    member = await services.create_card(CardCreate(type="text", parent_id=group.id))
    write_shared_state(services.world, services.state, group.id, LegionStateWrite(value={"step": 2}, expected_revision=0))
    await services.shutdown()
    restored = create_services(settings)
    try:
        assert restored.world.get_card(member.id).parent_id == group.id
        assert read_shared_state(restored.world, restored.state, group.id) == {"value": {"step": 2}, "revision": 1}
    finally:
        await restored.shutdown()


@pytest.mark.asyncio
async def test_failed_formation_rolls_back_container_and_membership(tmp_path: Path, monkeypatch):
    services = create_services(Settings.for_data_root(tmp_path / "world"))
    try:
        member = await services.create_card(CardCreate(type="text"))
        def fail(*args, **kwargs):
            raise RuntimeError("simulated membership commit failure")
        monkeypatch.setattr(services.world, "update_cards", fail)
        with pytest.raises(RuntimeError, match="simulated"):
            await services.form_legion_group("Atomic team", [member.id])
        assert services.world.list_legion_groups() == []
        assert services.world.get_card(member.id).parent_id is None
    finally:
        await services.shutdown()
