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
from backend.plugins.summoning import SummoningAction, SummoningCapture
from backend.runs.models import RunStatus
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_runs import RecordingProvider
from backend.tests.test_skill_packages import edit
from backend.world.models import CardCreate, EdgeCreate


def capture(client, box, nodes, entry, shared=(), name="Researcher"):
    url = f"/api/nodes/{box['id']}"
    revision = client.get(url + "/document").json()["revision"]
    result = client.post(url + "/summoning/capture", json={"name": name, "description": "Research a specific topic",
        "node_ids": [n["id"] for n in nodes], "entry_agent_id": entry["id"],
        "shared_node_ids": [n["id"] for n in shared], "expected_revision": revision})
    assert result.status_code == 200, result.text
    return result.json()["templates"][-1]


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
        template = capture(client, box, [entry, sandbox], entry, [skills])
        assert client.get(f"/api/nodes/{template['id']}").json()["parent_id"] == box["id"]
        client.patch(f"/api/nodes/{entry['id']}", json={"config": {"system_instruction": "Changed later"}})
        instance = settle(client, box, invoke(client, box, action="summon", template_id=template["id"], prompt="Inspect sources"))
        assert instance["status"] == "succeeded"
        assert instance["result"] == "Mock response: Inspect sources"
        nodes = [client.get(f"/api/nodes/{key}").json() for key in instance["node_ids"]]
        assert len(nodes) == 2
        assert next(n for n in nodes if n["type"] == "agent")["config"]["system_instruction"] == "Original instructions"
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
        reclaimed = invoke(client, box, action="reclaim", instance_id=instance["id"])
        assert reclaimed["status"] == "reclaimed" and reclaimed["result"] == saved["result"]
        assert not Path(fresh_path).exists()
        assert client.get(f"/api/nodes/{skills['id']}").status_code == 200


def test_legion_and_single_template_use_same_scope_and_restore_contract(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path / "world"), agent_runtime="core.mock", sandbox_runtime="auto")
    with TestClient(create_app(settings)) as client:
        box = create_node(client, "oaw.barracks")
        lead = create_node(client, "agent", name="Lead")
        peer = create_node(client, "agent", name="Peer")
        group = client.post("/api/legion-groups", json={"name": "Team", "node_ids": [lead["id"], peer["id"]]}).json()[0]
        client.put(f"/api/legion-groups/{group['id']}/state", json={"value": {"brief": "Saved team"}, "expected_revision": 0})
        team = capture(client, box, [group], lead, name="Team template")
        solo = capture(client, box, [lead], lead, name="Solo template")
        caller = create_node(client, "agent")
        edge = client.post("/api/edges", json={"source": caller["id"], "target": solo["id"], "relationship": "oaw.barracks.summon"}).json()
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        tool = client.portal.call(provider.list_tools, caller["id"])[0]
        listing = client.portal.call(provider.invoke_tool, caller["id"], tool.capability_id, {})
        assert [t["id"] for t in listing["templates"]] == [solo["id"]]
        instance = settle(client, box, invoke(client, box, action="summon", template_id=team["id"], prompt="Work as a team"))
        new_lead = client.get(f"/api/nodes/{instance['entry_agent_id']}").json()
        assert new_lead["parent_id"] != group["id"]
        assert client.get(f"/api/legion-groups/{new_lead['parent_id']}/state").json()["value"] == {"brief": "Saved team"}
        solo_instance = settle(client, box, invoke(client, box, action="summon", template_id=solo["id"], prompt="Work alone"))
        assert client.get(f"/api/nodes/{solo_instance['entry_agent_id']}").json()["parent_id"] is None
        personal_note = create_node(client, "text", name="Keep my note", parent_id=new_lead["parent_id"])
        invoke(client, box, action="reclaim", instance_id=instance["id"])
        kept = client.get(f"/api/nodes/{personal_note['id']}").json()
        assert kept["name"] == "Keep my note" and kept["parent_id"] is None
        client.delete(f"/api/edges/{edge['id']}")
        with pytest.raises(PermissionDeniedError):
            client.portal.call(provider.invoke_tool, caller["id"], tool.capability_id, {})


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
                        "action": "summon", "template_id": listing["templates"][0]["id"],
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
    for node in [entry, caller]:
        await services.create_edge(EdgeCreate(source=node.id, target=box.id, relationship="oaw.barracks.summon"))
    current = read_document(services, box.id)
    write_document(services, box.id, {**current["value"], "policy": policy}, current["revision"])
    await services.summoning.capture(box.id, SummoningCapture(name="Recursive worker", node_ids=[entry.id], entry_agent_id=entry.id,
        shared_node_ids=[box.id], expected_revision=read_document(services, box.id)["revision"]))
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


def test_removed_shared_binding_rolls_back_instantiation(tmp_path):
    settings = replace(Settings.for_data_root(tmp_path / "world"), agent_runtime="core.mock")
    with TestClient(create_app(settings)) as client:
        box = create_node(client, "oaw.barracks")
        entry = create_node(client, "agent")
        reference = create_node(client, "text")
        client.post("/api/edges", json={"source": entry["id"], "target": reference["id"], "relationship": "read"})
        template = capture(client, box, [entry], entry, [reference])
        client.delete(f"/api/nodes/{reference['id']}")
        before = {node.id for node in client.app.state.services.world.list_cards()}
        response = client.post(f"/api/nodes/{box['id']}/summoning/actions", json={"action": "summon", "template_id": template["id"], "prompt": "Read the reference"})
        assert response.status_code == 404
        assert {node.id for node in client.app.state.services.world.list_cards()} == before
        assert client.app.state.services.summoning.records() == []
