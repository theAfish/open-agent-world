from pathlib import Path
import json

import pytest

from backend.agents import MockAgentRuntime
from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.legions.models import LegionCapture, LegionInstantiate
from backend.legions.runtime import LegionStateWrite, write_shared_state
from backend.services import create_services
from backend.world.models import CardCreate, CardPatch


@pytest.mark.asyncio
async def test_group_mode_has_no_team_context_and_switching_revokes_access(tmp_path: Path):
    services = create_services(Settings.for_data_root(tmp_path), default_runtime_provider_id="core.mock")
    manager = services.run_manager

    class CaptureRuntime(MockAgentRuntime):
        async def execute(self, config, context, runtime_input):
            self.config, self.context = config, context
            async for event in super().execute(config, context, runtime_input):
                yield event

    runtime = CaptureRuntime(manager.capability_provider)
    manager.install_provider("core.mock", runtime)
    try:
        agent = await services.create_card(CardCreate(type="agent", config={"model": "own/model"}))
        group, _ = await services.form_legion_group("Optional team", [agent.id])
        assert group.config["mode"] == "group"
        await services.update_card(group.id, CardPatch(config={
            "instruction": "Private team plan", "model_override": "team/model", "paused": True,
        }))
        write_shared_state(services.world, services.state, group.id, LegionStateWrite(value={"goal": "Team goal"}, expected_revision=0))
        run = await manager.start_run(agent.id, "hello")
        await manager.wait_execution(run.run_id)
        assert runtime.config.model == "own/model"
        assert "Private team plan" not in runtime.config.system_instruction
        assert runtime.context.group_context is None
        assert "legion" not in [scope.scope_kind for scope in runtime.context.state_context.scope_stack]
        assert not any(cap.kind.startswith("legion.") for cap in services.capabilities.derive(agent.id).capabilities)
        await services.update_card(group.id, CardPatch(config={"mode": "team", "paused": False}))
        run = await manager.start_run(agent.id, "hello")
        await manager.wait_execution(run.run_id)
        assert runtime.config.model == "team/model"
        assert "Private team plan" in runtime.config.system_instruction
        assert runtime.context.group_context["shared_state"]["value"] == {"goal": "Team goal"}
        await services.update_card(group.id, CardPatch(config={"mode": "group"}))
        with pytest.raises(PermissionDeniedError):
            await manager.capability_provider.invoke_tool(agent.id, f"legion.state.read:{group.id}", {})
    finally:
        await services.shutdown()


@pytest.mark.asyncio
async def test_saved_layout_and_four_states_survive_restart_and_unwrap(tmp_path: Path):
    settings = Settings.for_data_root(tmp_path)
    services = create_services(settings)
    try:
        agents = [await services.create_card(CardCreate(type="agent", name=f"State {index + 1}",
                  position={"x": 500 + index * 500, "y": 400 + index * 80})) for index in range(4)]
        group, *_ = await services.form_legion_group("Four states", [a.id for a in agents])
        from backend.world.models import EdgeCreate
        await services.create_edge(EdgeCreate(source=agents[0].id, target=agents[1].id, relationship="communicate"))
        presentation = {a.id: {"level": level, "base_level": "node", "workspace_size": {"width": 1200, "height": 800}, "surface_sizes": {"inspector": {"width": 640, "height": 480}}}
                        for a, level in zip(agents, ("node", "preview", "inspector", "workspace"), strict=True)}
        saved = await services.capture_legion(LegionCapture(name="Layout", node_ids=[group.id, *[a.id for a in agents]], presentation=presentation))
    finally:
        await services.shutdown()
    restored = create_services(settings)
    try:
        for unwrap in (False, True):
            copy = await restored.instantiate_legion(saved.id, LegionInstantiate(position={"x": 5000, "y": 6000}, unwrap=unwrap))
            assert len(copy.nodes) == (4 if unwrap else 5)
            assert len(copy.edges) == 1
            members = sorted((n for n in copy.nodes if n.type == "agent"), key=lambda n: n.name)
            assert [copy.presentation[n.id].level for n in members] == ["node", "preview", "inspector", "workspace"]
            assert copy.presentation[members[3].id].workspace_size.width == 1200
            assert copy.presentation[members[2].id].surface_sizes["inspector"].width == 640
            assert [n.position.x - members[0].position.x for n in members] == [0, 500, 1000, 1500]
            assert [n.position.y - members[0].position.y for n in members] == [0, 80, 160, 240]
            assert all(n.status == "idle" for n in members)
            assert all((n.parent_id is None) == unwrap for n in members)
            assert copy.edges[0].source == members[0].id and copy.edges[0].target == members[1].id
    finally:
        await restored.shutdown()


@pytest.mark.parametrize("preset,nodes,edges", [("assistant", 2, 1), ("coding", 3, 2), ("team", 4, 6)])
def test_starter_presets_share_legion_deployment_without_a_wrapper(client, preset, nodes, edges):
    catalog = client.get("/api/legions/presets")
    assert catalog.status_code == 200
    assert all(item["compatible"] for item in catalog.json()), catalog.text
    response = client.post(f"/api/legions/presets/{preset}/instances", json={"unwrap": True})
    assert response.status_code == 201, response.text
    instance = response.json()
    assert len(instance["nodes"]) == nodes
    assert len(instance["edges"]) == edges
    assert all(n["type"] != "legion" and n["parent_id"] is None for n in instance["nodes"])
    assert len(instance["presentation"]) == nodes
    assert all(n["config"]["model"] == "oaw:default" for n in instance["nodes"] if n["type"] == "agent")
    assert client.get("/api/legions").json() == []
    assert client.post(f"/api/legions/presets/{preset}/instances", json={"unwrap": True, "as_group": True}).status_code == 422


def test_unknown_preset_does_not_create_nodes(client):
    assert client.post("/api/legions/presets/missing/instances", json={"unwrap": True}).status_code == 404
    assert client.get("/api/nodes").json() == []


@pytest.mark.asyncio
async def test_legacy_team_template_still_deploys(tmp_path: Path):
    services = create_services(Settings.for_data_root(tmp_path))
    try:
        member = await services.create_card(CardCreate(type="agent"))
        group, _ = await services.form_legion_group("Legacy team", [member.id])
        await services.update_card(group.id, CardPatch(config={"mode": "team", "instruction": "Legacy instructions"}))
        saved = await services.capture_legion(LegionCapture(name="Legacy", node_ids=[group.id, member.id]))
        blueprint = services.legions.get(saved.id).blueprint.model_dump(mode="json")
        for node in blueprint["nodes"]:
            if node["type"] == "legion":
                del node["config"]["mode"]
            del node["presentation"]
        with services.world.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE legions SET blueprint_json = ? WHERE id = ?", (json.dumps(blueprint), saved.id))
        instance = await services.instantiate_legion(saved.id, LegionInstantiate())
        team = next(n for n in instance.nodes if n.type == "legion")
        assert team.config["mode"] == "team"
        assert team.config["instruction"] == "Legacy instructions"
        assert instance.presentation == {}
    finally:
        await services.shutdown()
