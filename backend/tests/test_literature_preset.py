"""Real isolated deployment, KDG resources, scope permissions and template copies."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError
from backend.main import create_app
from backend.plugins.loader import load_plugin_registry
from backend.services import create_services
from backend.tests.conftest import create_node

ROOT = Path(__file__).resolve().parents[2]
for folder in ("plugins/library/src", "plugins/matcreator", "plugins/literature/src"):
    sys.path.insert(0, str(ROOT / folder))
from oaw_literature.preset import COORDINATOR_NAME, MINISTER_NAME, PRESET_ID, WORKER_ROLES, definition, initial_knowledge  # noqa: E402
from oaw_literature.search import CrossrefClient  # noqa: E402
from oaw_literature.skills import PACKAGE_ID, SKILL_IDS, literature_skill_package  # noqa: E402
from oaw_matcreator.preset import INSTRUCTION as MATCREATOR_INSTRUCTION  # noqa: E402


@pytest.fixture
def literature_workspace(tmp_path, monkeypatch):
    # Exercise the actual Literature plugin registration and published plugins.
    async def forbidden_network(*args, **kwargs):
        pytest.fail("Creating/copying/reading a preset must not perform a Crossref request")

    monkeypatch.setattr(CrossrefClient, "search", forbidden_network)
    monkeypatch.setattr(CrossrefClient, "resolve", forbidden_network)
    settings = replace(Settings.for_data_root(tmp_path / "isolated-literature-preset"), agent_runtime="core.mock")
    services = create_services(settings, plugins=load_plugin_registry())
    try:
        with TestClient(create_app(settings, services=services)) as client:
            yield client
    finally:
        services.close()


def deploy(client):
    response = client.post(f"/api/legions/presets/{PRESET_ID}/instances", json={})
    assert response.status_code == 201, response.text
    return response.json()


def document(client, node_id):
    response = client.get(f"/api/nodes/{node_id}/document")
    assert response.status_code == 200, response.text
    return response.json()


def edit(client, node_id, action, arguments):
    return client.post(f"/api/nodes/{node_id}/actions/{action}", json={
        "arguments": arguments, "expected_revision": document(client, node_id)["revision"]})


def call(client, agent_id, name, arguments):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    return client.portal.call(provider.invoke_tool, agent_id, "operation:" + name, arguments)


def test_preset_uses_real_assimilation_and_never_forges_minister_or_execution():
    graph = initial_knowledge()
    assert len(graph["snapshots"]) == 1 and len(graph["skills"]) == 6
    digest, snapshot = next(iter(graph["snapshots"].items()))
    assert digest == hashlib.sha256(json.dumps(snapshot["package"], sort_keys=True).encode()).hexdigest()
    assert snapshot["package"] == literature_skill_package().model_dump(mode="json")
    assert tuple(skill["id"] for skill in snapshot["package"]["skills"]) == SKILL_IDS
    assert snapshot["provenance"]["node_id"] is None
    assert snapshot["package"]["package_id"] == PACKAGE_ID
    assert all(entry["provenance"]["snapshot"] == digest and entry["verification"] == "unverified" for entry in graph["entries"])
    assert all(entry["resources"] for entry in graph["entries"])
    assert all(skill["node_id"] is None for skill in graph["skills"])
    graph["skills"][0]["instructions"] = "local mutation"
    assert initial_knowledge()["skills"][0]["instructions"] != "local mutation"

    preset = definition()
    nodes = {node.key: node for node in preset.nodes}
    assert nodes["agent"].type == "agent" and nodes["agent"].name == COORDINATOR_NAME
    assert "待任命" in nodes["agent"].name and preset.revision >= 3
    instruction = nodes["agent"].config["system_instruction"]
    assert MATCREATOR_INSTRUCTION.partition("\n")[2] in instruction
    assert "You are MatCreator" not in instruction
    assert "canvas_inspect" in instruction and "canvas_update" in instruction
    assert "Scope-bound road organization" in instruction and "reorder (road_id, member_ids" in instruction
    assert nodes["group"].config["mode"] == "group"
    assert "用户授权" in nodes["group"].config["description"]
    assert nodes["scope"].initial_document["current_revision"] == 0
    assert nodes["scope"].initial_document["revisions"] == [] and nodes["scope"].initial_document["paused"]
    assert all("minister" not in node.config for node in preset.nodes)
    assert all(node.type not in {"sandbox", "core.minister-role"} for node in preset.nodes)
    assert not any(edge.relationship == "execute" for edge in preset.edges)
    assert {nodes[key].parent_key for key, *_ in WORKER_ROLES} == {"barracks"}
    assert nodes["barracks"].initial_document["policy"] == {"max_depth": 1, "max_concurrent": 2, "max_instances": 12}


def test_real_deployment_and_copy_have_fresh_resources_empty_scope_and_no_auto_run(literature_workspace):
    client = literature_workspace
    catalog = client.get("/api/legions/presets").json()
    assert next(item for item in catalog if item["id"] == PRESET_ID)["compatible"]
    first, second = deploy(client), deploy(client)
    a, b = first["node_ids"], second["node_ids"]
    assert set(a.values()).isdisjoint(b.values())
    nodes = {node["id"]: node for node in first["nodes"]}
    assert len(a) == 15
    # Deployment must preserve the authored compact basecamp even when host
    # defaults expand Conversation into a full workspace.
    for key, node_id in a.items():
        expected = "preview" if key == "group" else "node"
        assert first["presentation"][node_id]["level"] == expected
        assert not nodes[node_id]["expanded"]
    assert nodes[a["summoning"]]["equipment"]["owner_id"] == a["agent"]
    assert nodes[a["agent"]]["config"]["model"] == "oaw:default"
    assert all(nodes[a[key]]["minister"] is None for key in ["agent", *(role[0] for role in WORKER_ROLES)])
    for ids in (a, b):
        scope = document(client, ids["scope"])["value"]
        assert scope["id"] == ids["scope"] and scope["task_board_id"] == ids["tasks"] and scope["knowledge_id"] == ids["knowledge"]
        assert scope["current_revision"] == 0 and scope["paused"] and scope["search_runs"] == []
        assert scope["paper_ids"] == [] and scope["search_budgets"] == {}
        assert document(client, ids["tasks"])["value"]["plans"] == []
    graph = document(client, a["knowledge"])["value"]
    assert len(graph["skills"]) == 6 and len(graph["snapshots"]) == 1
    assert {s["node_id"] for s in graph["skills"]}.isdisjoint(s["node_id"] for s in document(client, b["knowledge"])["value"]["skills"])
    for skill in graph["skills"]:
        assert client.get(f"/api/nodes/{skill['node_id']}").json()["parent_id"] == a["knowledge"]
    assert edit(client, a["knowledge"], "replace", {**graph, "snapshots": {}}).status_code == 422

    created = edit(client, a["tasks"], "create_plan", {"title": "Retained test plan", "session_id": "old-session", "tasks": [
        {"id": "read", "title": "Read source", "status": "done", "result": "Synthetic fixture only", "outputs": ["fixture.json"]}]})
    assert created.status_code == 200, created.text
    saved = client.post("/api/legions", json={"name": "Reusable literature workspace", "node_ids": list(a.values())})
    assert saved.status_code == 201, saved.text
    copied = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
    assert copied.status_code == 201, copied.text
    copy_nodes = copied.json()["nodes"]
    copied_graph_node = next(node for node in copy_nodes if node["type"] == "matcreator.kdg")
    copied_graph = document(client, copied_graph_node["id"])["value"]
    assert copied_graph["snapshots"] == graph["snapshots"]
    assert len(copied_graph["skills"]) == 6
    assert {s["node_id"] for s in copied_graph["skills"]}.isdisjoint(s["node_id"] for s in graph["skills"])
    copied_board = next(node for node in copy_nodes if node["type"] == "matcreator.tasks")
    plan = document(client, copied_board["id"])["value"]["plans"][0]
    assert plan["session_id"] == "" and plan["tasks"][0]["status"] == "pending"
    assert plan["tasks"][0]["result"] == "" and plan["tasks"][0]["outputs"] == []
    copied_scope = document(client, next(node for node in copy_nodes if node["type"] == "literature.scope")["id"])["value"]
    assert copied_scope["task_board_id"] == copied_board["id"] and copied_scope["knowledge_id"] == copied_graph_node["id"]
    assert copied_scope["paused"] and not copied_scope["search_runs"]
    group = next(node for node in copy_nodes if node["type"] == "legion")
    assert not any(original in json.dumps(group["config"]["workspace_layout"]) for original in a.values())


def test_real_worker_scope_kdg_grants_are_narrow_and_revocable(literature_workspace):
    client = literature_workspace
    instance = deploy(client)
    ids, services = instance["node_ids"], client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    for key, _, _, _, relation in WORKER_ROLES:
        caps = services.capabilities.derive(ids[key]).capabilities
        assert {cap.target_id for cap in caps} <= {ids["scope"], ids["knowledge"]}
        assert not any(cap.kind.startswith(("minister.", "matcreator.tasks.", "oaw.barracks.")) for cap in caps)
        assert ("literature.research" in {cap.kind for cap in caps}) == (relation == "literature.research")
    found = call(client, ids["verify"], "knowledge_search", {"knowledge": ids["knowledge"], "query": "evidence-verify", "limit": 1})
    entry = found["value"]["nodes"][0]
    inspected = call(client, ids["verify"], "knowledge_inspect", {"knowledge": ids["knowledge"], "entry_id": entry["id"]})
    skill_id = inspected["value"]["resources"][0]["skill_node_id"]
    assert skill_id and "## Contract and access" in inspected["value"]["entry"]["content"]
    asset = call(client, ids["verify"], "knowledge_inspect", {"knowledge": ids["knowledge"],
        "entry_id": entry["id"], "resource_path": "schemas/output.schema.json"})
    assert json.loads(asset["value"]["content"])["additionalProperties"] is False
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "read_skill", {"skill": skill_id})
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "knowledge_save_memory", {"knowledge": ids["knowledge"], "title": "No write grant", "content": "No"})
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "literature_research", {"scope": ids["scope"], "operation": "search", "arguments": {}})
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "task_board_read", {"board": ids["tasks"]})

    memory = call(client, ids["agent"], "knowledge_save_memory", {"knowledge": ids["knowledge"],
        "title": "Fixture observation", "content": "Synthetic pending memory; no scientific validation.",
        "session_id": "isolated-session", "source_ids": [entry["id"]],
        "expected_revision": document(client, ids["knowledge"])["revision"]})
    assert memory["summary"]["entries"] == 7
    stored = document(client, ids["knowledge"])["value"]["entries"][-1]
    assert stored["type"] == "memory" and stored["refinement"] == "pending" and stored["verification"] == "unverified"
    with pytest.raises(PermissionDeniedError):
        call(client, ids["agent"], "knowledge_distill", {"knowledge": ids["knowledge"], "memory_ids": [stored["id"]],
            "title": "No curate grant", "content": "No", "evidence": "None", "expected_revision": memory["revision"]})

    seed, outside = create_node(client, "library.paper"), create_node(client, "library.paper")
    revised = edit(client, ids["scope"], "revise", {"question": "A synthetic scoped reading test", "seed_paper_ids": [seed["id"]],
        "budget": {"max_searches": 0, "max_papers": 1, "max_duration_seconds": 60}})
    assert revised.status_code == 200, revised.text
    scoped = call(client, ids["verify"], "literature_read", {"scope": ids["scope"], "operation": "paper", "arguments": {"paper_id": seed["id"], "view": "metadata"}})
    assert scoped["paper_id"] == seed["id"]
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "literature_read", {"scope": ids["scope"], "operation": "paper", "arguments": {"paper_id": outside["id"]}})
    assert any(t.name == "literature_read" for t in client.portal.call(provider.list_tools, ids["verify"]))
    for target in ("scope", "knowledge"):
        edge = next(edge for edge in instance["edges"] if edge["source"] == ids["verify"] and edge["target"] == ids[target])
        assert client.delete(f"/api/edges/{edge['id']}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "literature_read", {"scope": ids["scope"], "operation": "scope"})
    with pytest.raises(PermissionDeniedError):
        call(client, ids["verify"], "knowledge_inspect", {"knowledge": ids["knowledge"],
            "entry_id": entry["id"], "resource_path": "schemas/output.schema.json"})


def test_minister_is_explicit_existing_user_grant_and_dependencies_disable_preset(literature_workspace):
    client = literature_workspace
    ids = deploy(client)["node_ids"]
    services = client.app.state.services
    original = client.get(f"/api/nodes/{ids['agent']}").json()
    runtime = services.run_manager._agent_config(services.world.get_card(ids["agent"]))
    assert original["minister"] is None
    assert not any(cap.kind.startswith("minister.") for cap in services.capabilities.derive(ids["agent"]).capabilities)
    role = create_node(client, "core.minister-role")
    promoted = client.post(f"/api/ministers/{ids['agent']}/appoint", json={"source_id": role["id"],
        "source_revision": role["revision"], "expected_revision": original["revision"]})
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["id"] == original["id"] and promoted.json()["config"] == original["config"]
    after = services.run_manager._agent_config(services.world.get_card(ids["agent"]))
    assert after.model == runtime.model and after.runtime_provider_id == runtime.runtime_provider_id
    assert any(cap.kind.startswith("minister.") for cap in services.capabilities.derive(ids["agent"]).capabilities)
    assert client.get(f"/api/nodes/{role['id']}").status_code == 404
    assert client.patch(f"/api/nodes/{ids['agent']}", json={"minister": None}).status_code == 200
    assert not any(cap.kind.startswith("minister.") for cap in services.capabilities.derive(ids["agent"]).capabilities)
    services.plugins.set_enabled("matcreator", False)
    try:
        assert PRESET_ID not in {item["id"] for item in client.get("/api/legions/presets").json()}
        assert client.post(f"/api/legions/presets/{PRESET_ID}/instances", json={}).status_code == 404
    finally:
        services.plugins.set_enabled("matcreator", True)


def test_desktop_appointment_preserves_research_identity_and_enables_bounded_road_layout(literature_workspace):
    client = literature_workspace
    ids = deploy(client)["node_ids"]
    services = client.app.state.services
    agent_id = ids["agent"]
    planned = edit(client, ids["tasks"], "create_plan", {"title": "Preserved research plan", "tasks": [
        {"id": "read", "title": "Read exact source", "acceptance": "Keep the observed source version"}]})
    assert planned.status_code == 200, planned.text
    before = client.get(f"/api/nodes/{agent_id}").json()
    documents = {key: document(client, ids[key]) for key in ("tasks", "knowledge", "scope")}
    edges = client.get("/api/edges").json()
    caps = services.capabilities.derive(agent_id).capabilities
    normal = {cap.id for cap in caps}
    assert {"literature.read", "literature.research", "literature.organize"} <= {cap.kind for cap in caps}
    runtime = services.run_manager._agent_config(services.world.get_card(agent_id))
    all_ids = {node["id"] for node in client.get("/api/nodes").json()}

    # This is the existing desktop Roles appointment, not a config/name shortcut.
    response = client.post(f"/api/ministers/{agent_id}/appoint", json={"expected_revision": before["revision"]})
    assert response.status_code == 200, response.text
    appointed = response.json()
    assert appointed["minister"] == {"control_radius": 1200, "allow_canvas_edits": True}
    assert {node["id"] for node in client.get("/api/nodes").json()} == all_ids
    assert all(appointed[key] == before[key] for key in ("id", "type", "config", "parent_id", "equipment"))
    renamed = client.patch(f"/api/nodes/{agent_id}", json={"name": MINISTER_NAME, "expected_revision": appointed["revision"]})
    assert renamed.status_code == 200, renamed.text
    assert normal < {cap.id for cap in services.capabilities.derive(agent_id).capabilities}
    after_runtime = services.run_manager._agent_config(services.world.get_card(agent_id))
    assert (after_runtime.model, after_runtime.runtime_provider_id) == (runtime.model, runtime.runtime_provider_id)
    assert before["config"]["system_instruction"] in after_runtime.system_instruction
    assert client.get("/api/edges").json() == edges
    assert {key: document(client, ids[key]) for key in documents} == documents

    # Ordinary canvas layout is exercised through the actual Minister broker.
    # These isolated marker fixtures make no discovery or scientific claim.
    x, y = before["position"]["x"], before["position"]["y"]
    first = create_node(client, "literature.trail", name="Synthetic local route", position={"x": x + 380, "y": y},
        config={"scope_id": ids["scope"], "entity_id": "trail:synthetic"})
    second = create_node(client, "literature.finding", name="Synthetic local finding", position={"x": x + 380, "y": y + 350},
        config={"scope_id": ids["scope"], "entity_id": "perspective:synthetic"})
    def inspect():
        return call(client, agent_id, "canvas_inspect", {"minister": agent_id})
    moved = call(client, agent_id, "canvas_update", {"minister": agent_id, "versions": inspect()["versions"], "updates": [
        {"node_id": first["id"], "patch": {"position": second["position"]}},
        {"node_id": second["id"], "patch": {"position": first["position"]}},
    ]})
    assert len(moved) == 2
    assert client.get(f"/api/nodes/{first['id']}").json()["position"] == second["position"]
    assert client.get(f"/api/nodes/{second['id']}").json()["position"] == first["position"]
    with pytest.raises(PermissionDeniedError):
        call(client, agent_id, "canvas_move", {"minister": agent_id, "node_id": first["id"],
            "position": {"x": x + 5000, "y": y}, "versions": inspect()["versions"]})
    with pytest.raises(PermissionDeniedError):
        call(client, ids["frontier"], "canvas_inspect", {"minister": agent_id})

    current = client.get(f"/api/nodes/{agent_id}").json()
    assert client.patch(f"/api/nodes/{agent_id}", json={"minister": None, "expected_revision": current["revision"]}).status_code == 200
    assert {cap.id for cap in services.capabilities.derive(agent_id).capabilities} == normal
    assert {key: document(client, ids[key]) for key in documents} == documents
    assert client.get("/api/edges").json() == edges
