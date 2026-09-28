"""Real persisted road editing remains distinct from source provenance."""
from copy import deepcopy

from backend.tests.conftest import create_node
from backend.tests.test_literature_exploration import action, document, frontier, seed
from backend.tests.test_literature_service import configure, transport
from backend.literature_roads import road_connections
from backend.node_documents import read_document, write_document


def prepared(client, count=5):
    scope = configure(client)
    papers = [create_node(client, "library.paper", name=f"Initial source {i}",
        position={"x": -900 - i * 200, "y": 360 + i * 120}) for i in range(count)]
    for paper in papers:
        seed(client, scope, paper)
    return scope, papers


def test_legacy_five_papers_migrate_to_ordered_trunk_without_inventing_route_sources(client):
    scope, papers = prepared(client)
    branch = frontier(client, scope, "Unsearched single-ion route")
    # Simulate the old star's physical edges before deliberate migration.
    index = next(node for node in client.get("/api/nodes").json() if node["type"] == "literature.index")
    for paper in papers[1:]:
        assert client.post("/api/edges", json={"source":index["id"], "target":paper["id"], "relationship":"literature.contains"}).status_code == 201
    result = action(client, scope, "migrate_roads", layout=True)
    assert result.status_code == 200, result.text
    value = result.json()["value"]
    roads = {road["id"]: road for road in value["exploration_roads"]}
    ids = ["paper:" + paper["id"] for paper in papers]
    assert roads["trunk"]["member_ids"] == ids
    assert roads["route:" + branch]["member_ids"] == []
    assert not value["search_runs"] and not value["exploration_links"]
    edges = client.get("/api/edges").json()
    assert not any(edge["source"] == index["id"] and edge["relationship"] == "literature.contains" for edge in edges)
    assert [(source,target) for source,target,road in road_connections(value) if road == "trunk"] == [(None, "trail:origin"), ("trail:origin", ids[0]), *zip(ids,ids[1:])]
    positioned = [client.get(f"/api/nodes/{paper['id']}").json()["position"] for paper in papers]
    assert len({position["y"] for position in positioned}) == 1
    assert all(left["x"] < right["x"] for left,right in zip(positioned,positioned[1:]))


def test_reorder_reparent_and_mode_are_persisted_navigation_only(client):
    scope, papers = prepared(client, 3)
    first, second = frontier(client, scope, "First branch"), frontier(client, scope, "Second branch")
    before = document(client, scope)["value"]
    positions = {paper["id"]: client.get(f"/api/nodes/{paper['id']}").json()["position"] for paper in papers}
    reverse_ids = ["paper:" + paper["id"] for paper in reversed(papers)]
    reordered = action(client, scope, "reorder", road_id="trunk", member_ids=reverse_ids)
    assert reordered.status_code == 200, reordered.text
    reparented = action(client, scope, "reparent", road_id="route:"+second, parent_id="route:"+first)
    assert reparented.status_code == 200, reparented.text
    assert action(client, scope, "route_mode", road_id="route:"+second, mode="chain").status_code == 200
    value = document(client, scope)["value"]
    assert value["search_runs"] == before["search_runs"]
    assert value["exploration_links"] == before["exploration_links"]
    assert value["frontiers"] == before["frontiers"]
    roads = {road["id"]:road for road in value["exploration_roads"]}
    assert roads["trunk"]["member_ids"] == reverse_ids
    assert roads["route:"+second]["parent_id"] == "route:"+first
    assert all(client.get(f"/api/nodes/{paper_id}").json()["position"] == position for paper_id,position in positions.items())


def test_branch_papers_follow_actual_search_and_no_physical_discovery_star(client, transport):
    scope, initial = prepared(client, 2)
    branch = frontier(client, scope, "Actual bounded query")
    response = client.post(f"/api/literature/scopes/{scope['id']}/explore", json={
        "expected_revision":document(client,scope)["revision"], "arguments":{"frontier_id":branch,"request_id":"road-search"}})
    assert response.status_code == 200, response.text
    paper_id = response.json()["run"]["paper_ids"][0]
    value = document(client, scope)["value"]
    roads = {road["id"]:road for road in value["exploration_roads"]}
    assert roads["trunk"]["member_ids"] == ["paper:"+paper["id"] for paper in initial]
    assert roads["route:"+branch]["member_ids"] == ["paper:"+paper_id]
    assert any(link["source"] == "trail:"+branch and link["target"] == "paper:"+paper_id and link["relation"] == "discovers" for link in value["exploration_links"])
    assert not any(edge["relationship"] == "literature.discovers" for edge in client.get("/api/edges").json())
    assert any(edge["relationship"] == "literature.road" and edge["target"] == paper_id for edge in client.get("/api/edges").json())


def test_invalid_cycle_foreign_members_and_stale_revision_roll_back_all_changes(client):
    scope, papers = prepared(client, 2)
    first, second = frontier(client, scope, "First"), frontier(client, scope, "Second")
    assert action(client, scope, "reparent", road_id="route:"+second, parent_id="route:"+first).status_code == 200
    before = document(client,scope)
    edges = client.get("/api/edges").json()
    assert action(client, scope, "reparent", road_id="route:"+first, parent_id="route:"+second).status_code == 422
    assert action(client, scope, "reparent", road_id="route:"+first, parent_id="missing").status_code == 422
    assert action(client, scope, "reorder", road_id="trunk", member_ids=["paper:"+papers[0]["id"]]*2).status_code == 422
    assert action(client, scope, "move_member", road_id="trunk", entity_id="paper:foreign").status_code == 403
    assert document(client,scope) == before
    assert client.get("/api/edges").json() == edges
    assert action(client, scope, "route_mode", road_id="route:"+first, mode="branch").status_code == 200
    response = client.post(f"/api/literature/scopes/{scope['id']}/organize", json={"expected_revision":before["revision"],
        "arguments":{"action":"reorder","road_id":"trunk","member_ids":["paper:"+paper["id"] for paper in reversed(papers)]}})
    assert response.status_code == 409


def test_manual_move_to_unsearched_branch_is_navigation_not_discovery(client):
    scope, papers = prepared(client, 2)
    branch = frontier(client,scope,"Unsearched")
    response = action(client,scope,"move_member",road_id="route:"+branch,entity_id="paper:"+papers[0]["id"],index=0)
    assert response.status_code == 200,response.text
    value = response.json()["value"]
    roads = {road["id"]:road for road in value["exploration_roads"]}
    assert roads["trunk"]["member_ids"] == ["paper:"+papers[1]["id"]]
    assert roads["route:"+branch]["member_ids"] == ["paper:"+papers[0]["id"]]
    assert not value["search_runs"] and not value["exploration_links"]
    assert document(client,scope)["value"]["frontiers"][0]["discovery_state"] == "unsearched"
    assert action(client,scope).json()["value"]["exploration_roads"] == value["exploration_roads"]


def test_only_explicit_layout_moves_existing_cards_and_only_selected_subtree(client):
    scope,papers = prepared(client,2)
    first,second = frontier(client,scope,"First"),frontier(client,scope,"Second")
    assert action(client,scope,"move_member",road_id="route:"+first,entity_id="paper:"+papers[0]["id"]).status_code == 200
    assert action(client,scope,"move_member",road_id="route:"+second,entity_id="paper:"+papers[1]["id"]).status_code == 200
    before = [client.get(f"/api/nodes/{paper['id']}").json()["position"] for paper in papers]
    assert action(client,scope).status_code == 200
    assert [client.get(f"/api/nodes/{paper['id']}").json()["position"] for paper in papers] == before
    assert action(client,scope,"layout",road_id="route:"+first).status_code == 200
    after = [client.get(f"/api/nodes/{paper['id']}").json()["position"] for paper in papers]
    assert after[0] != before[0] and after[1] == before[1]


def test_branch_attachment_point_and_copy_archive(client):
    from oaw_literature.scope import remap
    scope,papers=prepared(client,2)
    branch=frontier(client,scope,"Specific branch point")
    assert action(client,scope,"route_mode",road_id="route:"+branch,mode="branch").status_code == 200
    result=action(client,scope,"reparent",road_id="route:"+branch,parent_id="trunk",attach_after="paper:"+papers[0]["id"])
    assert result.status_code == 200,result.text
    value=result.json()["value"]
    assert ("paper:"+papers[0]["id"],"trail:"+branch,"route:"+branch) in road_connections(value)
    copied=remap(value,{scope["id"]:"copied-scope"})
    assert copied["exploration_roads"] == []
    assert copied["archived_results"][-1]["exploration_roads"] == value["exploration_roads"]


def test_source_method_stays_beside_paper_not_on_main_road(client):
    from backend.tests.test_literature_records_service import setup_scope, anchors, method, invoke
    scope_id, paper_id, _, _ = setup_scope(client)
    scope = {"id":scope_id}
    assert action(client,scope,"migrate_roads",layout=True).status_code == 200
    source = anchors(client,scope_id,paper_id).json()["sources"][0]
    result = invoke(client,scope_id,"record",{"kind":"method","value":method(source)})
    assert result.status_code == 200,result.text
    value = document(client,scope)["value"]
    assert all("method:local-method" not in road["member_ids"] for road in value["exploration_roads"])
    entity = next(entity for entity in value["exploration_nodes"] if entity["kind"] == "method")
    finding = client.get(f"/api/nodes/{entity['node_id']}").json()
    paper = client.get(f"/api/nodes/{paper_id}").json()
    assert finding["position"]["x"] == paper["position"]["x"]
    assert finding["position"]["y"] > paper["position"]["y"] + paper["size"]["height"]
    assert any(edge["relationship"] == "literature.method" and edge["target"] == finding["id"] for edge in client.get("/api/edges").json())
    assert action(client,scope,"move_member",road_id="trunk",entity_id=entity["id"]).status_code == 200
    assert entity["id"] in document(client,scope)["value"]["exploration_roads"][0]["member_ids"]


def test_scope_has_independent_trunks_and_crosslinks_without_reparenting(client):
    scope, papers = prepared(client, 2)
    first, second = frontier(client, scope, "Independent A"), frontier(client, scope, "Independent B")
    value = action(client, scope).json()["value"]
    roads = {road["id"]: road for road in value["exploration_roads"]}
    for route_id in (first, second):
        assert roads["route:" + route_id]["parent_id"] is None
        assert ("trail:origin", "trail:" + route_id, "route:" + route_id) in road_connections(value)
    result = action(client, scope, "link", source="trail:" + first, target="trail:" + second,
        relation="related", rationale="The two directions meet at a shared research question")
    assert result.status_code == 200, result.text
    assert result.json()["value"]["exploration_roads"] == value["exploration_roads"]
    assert any(edge["relationship"] == "literature.related" for edge in client.get("/api/edges").json())
    assert action(client, scope, "layout").status_code == 200
    assert not document(client, scope)["value"]["search_runs"]


def test_core_collection_stays_in_hub_until_explicit_road_mount(client):
    scope, papers = prepared(client, 2)
    branch = frontier(client, scope, "Core selection route")
    result = action(client, scope, "core_collection", frontier_id=branch, title="Selected sources",
        paper_ids=[paper["id"] for paper in papers], rationale="Sources selected for independent methodological coverage")
    assert result.status_code == 200, result.text
    value = result.json()["value"]
    core = next(entity for entity in value["exploration_nodes"] if entity["kind"] == "collection")
    assert core["node_id"] is None
    assert all(core["id"] not in road["member_ids"] for road in value["exploration_roads"])
    mounted = action(client, scope, "move_member", road_id="route:" + branch, entity_id=core["id"])
    assert mounted.status_code == 200, mounted.text
    core = next(entity for entity in mounted.json()["value"]["exploration_nodes"] if entity["kind"] == "collection")
    assert core["node_id"]
    assert client.get(f"/api/nodes/{core['node_id']}").json()["type"] == "literature.finding"
