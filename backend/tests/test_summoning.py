"""Callable subgraphs: restoration, scoped tools, recursion, and retained instances."""
import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.agents import AgentEvent, AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import ConflictError, PermissionDeniedError
from backend.main import create_app
from backend.node_documents import read_document, write_document
from backend.plugins.loader import load_plugin_registry
from backend.plugins.summoning import SummoningAction
from backend.runs.models import RunStatus
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_runs import RecordingProvider
from backend.tests.test_skill_packages import edit
from backend.world.models import CardCreate, CardPatch, EdgeCreate


def equip(client, resource, owner):
    response = client.patch(f"/api/nodes/{resource['id']}", json={"parent_id": None,
        "equipment": {"owner_id": owner["id"], "relationship": None}})
    assert response.status_code == 200, response.text
    return response.json()


def stock(client, box, agent):
    response = client.patch(f"/api/nodes/{agent['id']}", json={"parent_id": box["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def invoke(client, box, **args):
    response = client.post(f"/api/nodes/{box['id']}/summoning/actions", json=args)
    assert response.status_code == 200, response.text
    return response.json()


def settle(client, box, instance):
    client.portal.call(client.app.state.services.run_manager.wait_execution, instance["run_id"])
    return invoke(client, box, action="inspect", instance_id=instance["id"])


def test_equipped_agent_snapshot_shared_library_fresh_sandbox_and_restart(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path / "world"), agent_runtime="core.mock", sandbox_runtime="auto")
    with TestClient(create_app(settings)) as client:
        box = create_node(client, "oaw.barracks")
        entry = create_node(client, "agent", name="Researcher", config={"system_instruction": "Original instructions"})
        sandbox = create_node(client, "sandbox")
        skills = create_node(client, "oaw.skills")
        edit(client, skills, "upsert", {"name": "Read sources", "instructions": "Cite primary sources."})
        for target, relationship in [(sandbox, "execute"), (skills, "oaw.skills.use")]:
            assert client.post("/api/edges", json={"source": entry["id"], "target": target["id"], "relationship": relationship}).status_code == 201
        original_workspace = client.get(f"/api/sandboxes/{sandbox['id']}").json()["workspace"]
        from pathlib import Path
        Path(original_workspace).mkdir(parents=True, exist_ok=True)
        Path(original_workspace, "original.txt").write_text("Private work", encoding="utf-8")
        template = stock(client, box, entry)
        equip(client, sandbox, entry)
        assert client.get(f"/api/nodes/{template['id']}").json()["parent_id"] == box["id"]
        client.patch(f"/api/nodes/{entry['id']}", json={"config": {"system_instruction": "Changed later"}})
        instance = settle(client, box, invoke(client, box, action="summon", agent_id=template["id"], prompt="Inspect sources"))
        assert instance["status"] == "succeeded"
        assert instance["result"] == "Mock response: Inspect sources"
        nodes = [client.get(f"/api/nodes/{key}").json() for key in instance["node_ids"]]
        assert len(nodes) == 2
        workspace = client.get(f"/api/nodes/{instance['workspace_id']}").json()
        assert workspace["type"] == "core.virtual-workspace"
        assert next(n for n in nodes if n["type"] == "agent")["parent_id"] == workspace["id"]
        generated = [e for e in client.app.state.services.world.list_edges() if e.relationship == "core.generated"]
        assert [(e.source, e.target) for e in generated] == [(box["id"], workspace["id"])]
        assert next(n for n in nodes if n["type"] == "agent")["config"]["system_instruction"] == "Changed later"
        fresh_sandbox = next(n for n in nodes if n["type"] == "sandbox")
        fresh_path = client.get(f"/api/sandboxes/{fresh_sandbox['id']}").json()["workspace"]
        assert fresh_path != original_workspace and not Path(fresh_path, "original.txt").exists()
        caps = client.app.state.services.capabilities.derive(instance["entry_agent_id"]).capabilities
        assert {c.target_id for c in caps} == {fresh_sandbox["id"], skills["id"]}
        followup = settle(client, box, invoke(client, box, action="message", instance_id=instance["id"], prompt="Continue"))
        assert followup["node_ids"] == instance["node_ids"] and followup["run_id"] != instance["run_id"]
    with TestClient(create_app(settings)) as client:
        saved = invoke(client, box, action="inspect", instance_id=instance["id"])
        assert saved["result"] == "Mock response: Continue"
        assert client.get(f"/api/nodes/{saved['workspace_id']}").status_code == 200
        visitor = create_node(client, "text", parent_id=saved["workspace_id"])
        reclaimed = invoke(client, box, action="reclaim", instance_id=instance["id"])
        assert reclaimed["status"] == "reclaimed" and reclaimed["result"] == saved["result"]
        assert not Path(fresh_path).exists()
        assert client.get(f"/api/nodes/{saved['workspace_id']}").status_code == 404
        assert client.get(f"/api/nodes/{visitor['id']}").json()["parent_id"] is None
        assert not any(e.relationship == "core.generated" for e in client.app.state.services.world.list_edges())
        assert client.get(f"/api/nodes/{skills['id']}").status_code == 200


def test_membership_equipment_and_live_authorization(client):
    box = create_node(client, "oaw.barracks")
    worker = create_node(client, "agent")
    resource = create_node(client, "text", content="private notes")
    caller = create_node(client, "agent")
    summoner = create_node(client, "oaw.barracks.summoner")
    stock(client, box, worker)
    equip(client, resource, worker)
    equip(client, summoner, caller)
    services = client.app.state.services
    assert services.capabilities.read_text(worker["id"], resource["id"]).content == "private notes"
    edge = client.post("/api/edges", json={"source": summoner["id"], "target": box["id"], "relationship": "oaw.barracks.summon"})
    assert edge.status_code == 201, edge.text
    caps = services.capabilities.derive(caller["id"]).capabilities
    assert len(caps) == 1 and caps[0].target_id == box["id"]
    assert client.patch(f"/api/nodes/{summoner['id']}", json={"equipment": None}).status_code == 200
    with pytest.raises(PermissionDeniedError):
        services.capabilities.capability_for_id(caller["id"], caps[0].id)
    linked = client.post('/api/edges', json={'source': caller['id'], 'target': summoner['id'], 'relationship': 'oaw.barracks.use'})
    assert linked.status_code == 201, linked.text
    assert services.capabilities.derive(caller['id']).capabilities == caps
    assert client.delete(f"/api/edges/{linked.json()['id']}").status_code == 200
    assert services.capabilities.derive(caller['id']).capabilities == []
    assert client.patch(f"/api/nodes/{resource['id']}", json={"equipment": None}).status_code == 200
    assert services.capabilities.derive(worker["id"]).capabilities == []
    assert client.post("/api/edges", json={"source": worker["id"], "target": resource["id"], "relationship": "read"}).status_code == 201
    assert services.capabilities.read_text(worker["id"], resource["id"]).content == "private notes"
    assert client.patch(f"/api/nodes/{worker['id']}", json={"parent_id": None}).status_code == 200
    assert services.summoning.snapshot(box["id"])["agents"] == []
    assert client.post(f"/api/nodes/{box['id']}/summoning/capture", json={}).status_code == 404
    catalog = client.get("/api/catalog").json()["node_types"]
    assert all(t["id"] != "oaw.barracks.template" for t in catalog)
    skill = next(t for t in catalog if t["id"] == "oaw.barracks.summoner")
    assert skill["label"] == "Summoning"
    assert (skill["deck_id"], skill["deck_revision"]) == ("tools", 2)


class Summoner(RecordingProvider):
    def __init__(self, capability_provider):
        super().__init__()
        self.tools = capability_provider
        self.blocked = asyncio.Event()
        self.limit_errors = []
        self.results = []

    async def execute(self, config, context, runtime_input):
        self.contexts.append(context)
        prompt = runtime_input.prompt
        if prompt == "failure":
            raise RuntimeError("Research provider failed")
        if prompt == "hold":
            self.blocked.set()
            await asyncio.Event().wait()
        count = int(prompt.split(":")[1]) if ":" in prompt else 0
        if count:
            tool = next(t for t in await self.tools.list_tools(context.agent_id) if t.name.startswith("summon_agents"))
            listing = await self.tools.invoke_tool(context.agent_id, tool.capability_id, {})
            for _ in range(2 if prompt.startswith("twice") else 1):
                try:
                    result = await self.tools.invoke_tool(context.agent_id, tool.capability_id, {
                        "action": "summon", "agent_id": listing["agents"][0]["id"],
                        "prompt": "failure" if prompt.startswith("failchain") else (f"holdchain:{count - 1}" if count > 1 else "hold") if prompt.startswith("holdchain") else f"chain:{count - 1}"})
                    self.results.append(result)
                except ConflictError as error:
                    self.limit_errors.append(str(error))
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {"text": "Finished " + prompt}, run_status=RunStatus.SUCCEEDED)


async def recursive_world(tmp_path, policy):
    registry = load_plugin_registry()
    install_test_plugin(registry, "test.summoner", lambda r: r.register_runtime_provider("test.summoner", lambda capabilities: Summoner(capabilities)))
    services = create_services(replace(Settings.for_data_root(tmp_path), agent_runtime="test.summoner"), plugins=registry)
    box = await services.create_card(CardCreate(type="oaw.barracks"))
    entry = await services.create_card(CardCreate(type="agent"))
    caller = await services.create_card(CardCreate(type="agent"))
    await services.update_card(entry.id, CardPatch(parent_id=box.id))
    for node in [entry, caller]:
        adapter = await services.create_card(CardCreate(type="oaw.barracks.summoner", equipment={"owner_id": node.id}))
        await services.create_edge(EdgeCreate(source=adapter.id, target=box.id, relationship="oaw.barracks.summon"))
    current = read_document(services, box.id)
    write_document(services, box.id, {**current["value"], "policy": policy}, current["revision"])
    return services, box, caller, services.run_manager.default_provider()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,prompt,expected,limit", [
    ({"max_depth": 2, "max_concurrent": 4, "max_instances": 10}, "chain:3", 2, "depth"),
    ({"max_depth": 4, "max_concurrent": 1, "max_instances": 10}, "chain:3", 1, "concurrent"),
    ({"max_depth": 4, "max_concurrent": 4, "max_instances": 1}, "twice:1", 1, "total"),
])
async def test_recursive_summons_share_root_budget(tmp_path, policy, prompt, expected, limit):
    services, box, caller, runtime = await recursive_world(tmp_path, policy)
    try:
        root = await services.run_manager.start_run(caller.id, prompt)
        await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), timeout=10)
        records = services.summoning.snapshot(box.id)["instances"]
        assert len(records) == expected
        assert all(r["created_root_id"] == root.run_id for r in records)
        assert all(r["status"] == "succeeded" for r in records)
        for record in records:
            link = next(e for e in services.world.list_edges() if e.target == record["workspace_id"])
            assert link.relationship == "core.generated"
            assert services.world.get_card(link.source).equipment.owner_id == record["caller_agent_id"]
        assert any(limit in error for error in runtime.limit_errors)
        assert all(r["result"].startswith("Finished") for r in runtime.results)
        children = services.run_manager.list_child_runs(root.run_id)
        assert children and children[0].caller_id == caller.id
    finally:
        services.close()


@pytest.mark.asyncio
async def test_parent_cancel_settles_recursive_runs_and_reclaim_keeps_shared_library(tmp_path):
    services, box, caller, runtime = await recursive_world(tmp_path, {"max_depth": 4, "max_concurrent": 4, "max_instances": 10})
    try:
        root = await services.run_manager.start_run(caller.id, "holdchain:2")
        await asyncio.wait_for(runtime.blocked.wait(), timeout=10)
        await asyncio.wait_for(services.run_manager.cancel_run(root.run_id), timeout=10)
        instances = services.summoning.snapshot(box.id)["instances"]
        assert len(instances) == 2 and all(i["status"] == "cancelled" for i in instances)
        await services.summoning.action(box.id, SummoningAction(action="reclaim", instance_id=instances[0]["id"]))
        assert all(i["reclaimed"] for i in services.summoning.snapshot(box.id)["instances"])
        assert services.world.get_card(box.id)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_failed_child_returns_result_handle_only_its_caller_can_manage(tmp_path):
    services, box, caller, runtime = await recursive_world(tmp_path, {})
    try:
        root = await services.run_manager.start_run(caller.id, "failchain:1")
        await asyncio.wait_for(services.run_manager.wait_execution(root.run_id), timeout=10)
        result = runtime.results[0]
        assert result["status"] == "failed" and "Research provider failed" in result["error"]
        assert services.world.get_card(result["entry_agent_id"])
        tool = next(t for t in await runtime.tools.list_tools(caller.id) if t.name.startswith("summon_agents"))
        assert (await runtime.tools.invoke_tool(caller.id, tool.capability_id, {"action": "inspect", "instance_id": result["id"]}))["status"] == "failed"
        child = result["entry_agent_id"]
        child_tool = next(t for t in await runtime.tools.list_tools(child) if t.name.startswith("summon_agents"))
        assert (await runtime.tools.invoke_tool(child, child_tool.capability_id, {}))["instances"] == []
        with pytest.raises(PermissionDeniedError):
            await runtime.tools.invoke_tool(child, child_tool.capability_id, {"action": "inspect", "instance_id": result["id"]})
    finally:
        services.close()


def test_removing_a_shared_dependency_changes_the_live_blueprint(client):
    box = create_node(client, "oaw.barracks")
    entry = stock(client, box, create_node(client, "agent"))
    reference = create_node(client, "text")
    client.post("/api/edges", json={"source": entry["id"], "target": reference["id"], "relationship": "read"})
    client.delete(f"/api/nodes/{reference['id']}")
    instance = invoke(client, box, action="summon", agent_id=entry["id"], prompt="Work")
    assert client.app.state.services.capabilities.derive(instance["entry_agent_id"]).capabilities == []


def test_summoning_avoids_other_cards_and_empty_management_prompts(client):
    from backend.world.layout import WorldLayout
    box = create_node(client, "oaw.barracks", size={"width": 96, "height": 96})
    assert box["size"] == {"width": 800, "height": 500}
    # Older palette-created containers may still have compact persisted sizes.
    with client.app.state.services.database.transaction() as connection:
        connection.execute("UPDATE cards SET width=96,height=96 WHERE id=?", (box["id"],))
    agent = stock(client, box, create_node(client, "agent", config={"runtime_provider_id": "core.mock"}))
    create_node(client, "text", position={"x": box["size"]["width"] + 200, "y": 150})
    assert invoke(client, box, action="list", prompt="")["agents"]
    for action in ("summon", "message"):
        response = client.post(f"/api/nodes/{box['id']}/summoning/actions", json={
            "action": action, "agent_id": agent["id"], "prompt": ""})
        assert response.status_code == 422 and "Supply a task prompt" in response.text
    for _ in range(2):
        obstacles = WorldLayout.capture(client.app.state.services.world).footprints.values()
        instance = settle(client, box, invoke(client, box, action="summon", agent_id=agent["id"], prompt="Work"))
        region = client.app.state.services.world.get_card(instance["workspace_id"])
        assert (region.position.x >= box["position"]["x"] + 800
                or region.position.x + region.size.width <= box["position"]["x"]
                or region.position.y >= box["position"]["y"] + 500
                or region.position.y + region.size.height <= box["position"]["y"])
        assert all(region.position.x + region.size.width <= rect.x or rect.x + rect.width <= region.position.x
                   or region.position.y + region.size.height <= rect.y or rect.y + rect.height <= region.position.y
                   for rect in obstacles)
        assert invoke(client, box, action="inspect", instance_id=instance["id"], prompt="")["id"] == instance["id"]
    assert invoke(client, box, action="stop", instance_id=instance["id"], prompt="")["id"] == instance["id"]
    assert invoke(client, box, action="reclaim", instance_id=instance["id"], prompt="")["reclaimed"]
