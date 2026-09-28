"""World identity, topology authorization, provenance and copy boundaries."""
import pytest

from backend.tests.conftest import create_node
from backend.tests.test_literature_service import configure, search, transport
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.node_documents import read_document, write_document


def document(client, node):
    return client.get(f"/api/literature/scopes/{node['id']}").json()


def action(client, node, name="sync", **arguments):
    doc = document(client, node)
    return client.post(f"/api/literature/scopes/{node['id']}/organize", json={
        "expected_revision": doc["revision"], "arguments": {"action": name, **arguments}})


def frontier(client, node, query):
    doc = document(client, node)
    response = client.post(f"/api/literature/scopes/{node['id']}/frontier", json={"expected_revision": doc["revision"],
        "arguments": {"query": query, "missing_evidence": ["Synthetic unsearched route"], "rationale": "User test proposal"}})
    assert response.status_code == 200, response.text
    return response.json()["value"]["frontiers"][-1]["id"]


def seed(client, scope, paper):
    services = client.app.state.services
    doc = read_document(services, scope["id"])
    write_document(services, scope["id"], {**doc["value"], "paper_ids": [*doc["value"]["paper_ids"], paper["id"]]}, doc["revision"])


def test_external_papers_index_and_migration_preserve_identity_and_layout(client, transport):
    from backend.world.models import CardCreate
    camp = client.portal.call(client.app.state.services._create_card, CardCreate(type="legion", position={"x":700,"y":800})).model_dump(mode="json")
    scope = configure(client)
    assert client.patch(f"/api/nodes/{scope['id']}", json={"parent_id": camp["id"]}).status_code == 200
    result = search(client, scope)
    paper_id = result.json()["run"]["paper_ids"][0]
    assert client.get(f"/api/nodes/{paper_id}").json()["parent_id"] is None
    legacy = create_node(client, "library.paper", parent_id=camp["id"], name="Retained local source")
    original = client.get(f"/api/nodes/{legacy['id']}/document").json()["value"]
    seed(client, scope, legacy)
    frontier(client, scope, "An unsearched next direction")
    synchronized = action(client, scope)
    assert synchronized.status_code == 200, synchronized.text
    nodes = client.get("/api/nodes").json()
    indexes = [node for node in nodes if node["type"] == "literature.index"]
    assert len(indexes) == 1 and indexes[0]["parent_id"] == camp["id"]
    assert client.get(f"/api/nodes/{legacy['id']}").json()["parent_id"] is None
    assert client.get(f"/api/nodes/{legacy['id']}/document").json()["value"] == original
    # Unsearched routes must not absorb historical search results.
    edges = client.get("/api/edges").json()
    assert any(edge["source"] == next(item["node_id"] for item in synchronized.json()["value"]["exploration_nodes"] if item["id"] == "trail:origin") and edge["target"] == paper_id for edge in edges)
    assert not any(link["relation"] == "discovers" for link in synchronized.json()["value"]["exploration_links"])
    assert client.patch(f"/api/nodes/{legacy['id']}", json={"position": {"x": 9000, "y": 321}}).status_code == 200
    assert action(client, scope).status_code == 200
    assert len(client.get("/api/nodes").json()) == len(nodes)
    assert client.get(f"/api/nodes/{legacy['id']}").json()["position"] == {"x": 9000, "y": 321}


def test_shared_perspective_connects_two_routes_once_and_core_selection_is_explicit(client):
    scope = configure(client)
    paper = create_node(client, "library.paper")
    seed(client, scope, paper)
    first, second = frontier(client, scope, "Polymer branch A"), frontier(client, scope, "Polymer branch B")
    for branch in (first, second):
        response = action(client, scope, "add", kind="perspective", title="Shared ion transport perspective",
            paper_ids=[paper["id"]], frontier_id=branch, rationale="Same source-backed question from both routes")
        assert response.status_code == 200, response.text
    value = response.json()["value"]
    perspectives = [item for item in value["exploration_nodes"] if item["kind"] == "perspective"]
    assert len(perspectives) == 1
    assert len([item for item in value["exploration_links"] if item["relation"] == "discovers" and item["target"] == perspectives[0]["id"]]) == 2
    core = action(client, scope, "core_collection", title="Core polymer sources", paper_ids=[paper["id"]],
        frontier_id=first, rationale="User-selected methods comparison; not an automatic quality rating")
    assert core.status_code == 200, core.text
    assert len([item for item in core.json()["value"]["exploration_nodes"] if item["kind"] == "collection"]) == 1
    assert client.get(f"/api/nodes/{perspectives[0]['node_id']}").json()["parent_id"] is None


def test_invalid_scope_urls_and_stale_cas_leave_no_partial_nodes(client):
    scope = configure(client)
    other = create_node(client, "library.paper")
    count = len(client.get("/api/nodes").json())
    rejected = action(client, scope, "add", kind="perspective", title="Foreign Paper", paper_ids=[other["id"]], rationale="Not scoped")
    assert rejected.status_code == 403, rejected.text
    assert len(client.get("/api/nodes").json()) == count
    rejected = action(client, scope, "add", kind="web", title="Unsafe", url="javascript:alert(1)", rationale="No")
    assert rejected.status_code == 422
    stale = document(client, scope)["revision"]
    assert action(client, scope).status_code == 200
    response = client.post(f"/api/literature/scopes/{scope['id']}/organize", json={"expected_revision": stale, "arguments": {"action": "sync"}})
    assert response.status_code == 409
    assert action(client, scope, "link", source="foreign", target="foreign2", relation="related", rationale="No").status_code == 403


def test_new_markers_do_not_overlap_old_markers(client):
    scope = configure(client)
    first = frontier(client, scope, "First")
    assert action(client, scope).status_code == 200
    second = frontier(client, scope, "Second")
    assert action(client, scope).status_code == 200
    markers = [node for node in client.get("/api/nodes").json() if node["type"] == "literature.trail"]
    assert len(markers) == 3 and len({tuple(node["position"].values()) for node in markers}) == 3


def test_only_coordinator_organizes_and_task_staging_needs_separate_management(client):
    scope = configure(client)
    agent, worker = create_node(client, "agent"), create_node(client, "agent")
    for node, relation in ((agent, "literature.coordinate"), (worker, "literature.research")):
        assert client.post("/api/edges", json={"source": node["id"], "target": scope["id"], "relationship": relation}).status_code == 201
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    arguments = {"scope": scope["id"], "operation": "organize", "arguments": {
        "action": "sync", "expected_revision": document(client, scope)["revision"]}}
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, worker["id"], "operation:literature_organize", arguments)
    assert [entity["id"] for entity in client.portal.call(provider.invoke_tool, agent["id"], "operation:literature_organize", arguments)["value"]["exploration_nodes"]] == ["trail:origin"]
    board = create_node(client, "matcreator.tasks")
    services = client.app.state.services
    doc = read_document(services, scope["id"])
    write_document(services, scope["id"], {**doc["value"], "task_board_id": board["id"]}, doc["revision"])
    paper = create_node(client, "library.paper")
    seed(client, scope, paper)
    arguments["arguments"] = {"action": "stage_task", "strategy": "close_read", "paper_id": paper["id"],
        "rationale": "Inspect exact transport assumptions", "expected_revision": document(client, scope)["revision"]}
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent["id"], "operation:literature_organize", arguments)
    assert client.post("/api/edges", json={"source": agent["id"], "target": board["id"], "relationship": "matcreator.tasks.manage"}).status_code == 201
    result = client.portal.call(provider.invoke_tool, agent["id"], "operation:literature_organize", arguments)
    assert len(result["value"]["exploration_tasks"]) == 1
    plans = client.get(f"/api/nodes/{board['id']}/document").json()["value"]["plans"]
    assert plans[0]["tasks"][0]["status"] == "pending"
    assert not client.get("/api/runs").json()


def test_copied_scope_archives_topology_and_detaches_stale_marker_references(client):
    from oaw_literature.scope import remap, remap_exploration_config
    scope = configure(client)
    branch = frontier(client, scope, "Original route")
    value = action(client, scope).json()["value"]
    copied = remap(value, {scope["id"]: "copied"})
    assert copied["exploration_nodes"] == [] and copied["exploration_links"] == []
    assert copied["archived_results"][-1]["exploration_nodes"]
    assert remap_exploration_config({"scope_id": scope["id"], "entity_id": "trail:" + branch, "frontier_id": branch},
        {scope["id"]: "copied"}) == {"scope_id": "copied", "entity_id": None, "frontier_id": None}


def test_camp_replacement_updates_only_semantic_edges_and_does_not_grant_execution(client):
    scope = configure(client)
    branch = frontier(client, scope, "A camp-supported route")
    first, second = create_node(client, "oaw.barracks"), create_node(client, "oaw.barracks")
    agent = create_node(client, "agent")
    assert client.post("/api/edges", json={"source": agent["id"], "target": scope["id"], "relationship": "literature.coordinate"}).status_code == 201
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    before = {cap.kind for cap in client.app.state.services.capabilities.derive(agent["id"]).capabilities}
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent["id"], "operation:literature_organize", {"scope": scope["id"], "operation": "organize",
            "arguments": {"expected_revision": document(client, scope)["revision"], "action": "attach_camp", "frontier_id": branch,
                "barracks_id": first["id"], "rationale": "No summon grant"}})
    for camp in (first, second):
        response = action(client, scope, "attach_camp", frontier_id=branch, barracks_id=camp["id"], rationale="Desktop explicitly selects an existing camp")
        assert response.status_code == 200, response.text
    edges = [item for item in client.get("/api/edges").json() if item["relationship"] == "literature.camp"]
    assert len(edges) == 1 and edges[0]["target"] == second["id"]
    after = {cap.kind for cap in client.app.state.services.capabilities.derive(agent["id"]).capabilities}
    assert before == after
    assert not client.get("/api/runs").json()


def test_different_url_same_perspective_title_is_not_silently_merged(client):
    scope = configure(client)
    assert action(client, scope, "add", kind="perspective", title="Same name", url="https://example.org/first", rationale="First source").status_code == 200
    rejected = action(client, scope, "add", kind="perspective", title="Same name", url="https://example.org/second", rationale="Different source")
    assert rejected.status_code == 422
    entities = [entity for entity in document(client, scope)["value"]["exploration_nodes"] if entity["kind"] == "perspective"]
    assert len(entities) == 1 and entities[0]["url"] == "https://example.org/first"
    assert action(client, scope, "add", kind="web", title="Malformed URL", url=42, rationale="Invalid input").status_code == 422


def test_initial_signpost_is_created_once_per_scope_and_keeps_its_position(client):
    first, second = create_node(client, "literature.scope"), create_node(client, "literature.scope")
    def origin(scope):
        return next(entity for entity in document(client, scope)["value"]["exploration_nodes"] if entity["id"] == "trail:origin")
    marker = origin(first)
    assert marker["node_id"] != origin(second)["node_id"]
    node = client.get(f"/api/nodes/{marker['node_id']}").json()
    assert node["type"] == "literature.trail" and node["parent_id"] is None
    assert client.patch(f"/api/nodes/{node['id']}", json={"position":{"x":4321,"y":987}}).status_code == 200
    for _ in range(2): assert action(client, first).status_code == 200
    assert origin(first)["node_id"] == node["id"]
    assert client.get(f"/api/nodes/{node['id']}").json()["position"] == {"x":4321,"y":987}
    assert not document(client, first)["value"]["search_runs"]
