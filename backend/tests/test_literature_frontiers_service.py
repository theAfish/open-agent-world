import httpx
from backend.tests.test_literature_service import configure, response, transport
from backend.literature_service import service
from oaw_literature.search import CrossrefClient


def action(client,node,operation,**arguments):
    document=client.get(f"/api/literature/scopes/{node['id']}").json()
    return client.post(f"/api/literature/scopes/{node['id']}/{operation}",json={"expected_revision":document['revision'],"arguments":arguments})


def route(client,node):
    result=action(client,node,"frontier",query="Local synthetic test query",missing_evidence=["A located source"],rationale="A bounded synthetic route")
    assert result.status_code==200,result.text
    return result.json()["value"]["frontiers"][-1]


def test_frontier_budget_receipt_replay_and_truthful_projection(client,transport):
    node=configure(client)
    item=route(client,node)
    assert item["discovery_state"]=="unsearched"
    before=client.get(f"/api/literature/scopes/{node['id']}").json()
    assert before["value"]["frontiers"][0]["budget_used"]=={"searches":0,"papers":0}
    result=action(client,node,"explore",frontier_id=item["id"],request_id="route-first")
    assert result.status_code==200,result.text
    assert result.json()["run"]["frontier_id"]==item["id"]
    assert action(client,node,"explore",frontier_id=item["id"],request_id="route-first").json()["replay"]
    assert action(client,node,"explore",frontier_id=item["id"],request_id="route-second").status_code==422
    document=client.get(f"/api/literature/scopes/{node['id']}").json()
    projected=document["value"]["frontiers"][0]
    assert projected["discovery_state"]=="found" and projected["evidence_state"]=="none"
    assert projected["budget_used"]=={"searches":1,"papers":5}
    assert len(transport)==1


def test_empty_route_is_not_unsearched_and_failed_budget_is_retained(client):
    node=configure(client)
    service(client.app.state.services).client=CrossrefClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"status":"ok","message":{"items":[],"total-results":0}})))
    item=route(client,node)
    assert action(client,node,"explore",frontier_id=item["id"],request_id="empty").status_code==200
    doc=client.get(f"/api/literature/scopes/{node['id']}").json()
    assert doc["value"]["frontiers"][0]["discovery_state"]=="no_results"
    other=route(client,node)
    service(client.app.state.services).client=CrossrefClient(transport=httpx.MockTransport(lambda request:httpx.Response(503)))
    assert action(client,node,"explore",frontier_id=other["id"],request_id="failed").status_code==422
    doc=client.get(f"/api/literature/scopes/{node['id']}").json()
    assert doc["value"]["frontiers"][1]["discovery_state"]=="failed"
    assert doc["value"]["frontiers"][1]["budget_used"]["searches"]==1


def test_frontier_cannot_reuse_other_route_id_or_change_intent(client,transport):
    node=configure(client)
    first,second=route(client,node),route(client,node)
    assert action(client,node,"explore",frontier_id=first["id"],request_id="same").status_code==200
    assert action(client,node,"explore",frontier_id=second["id"],request_id="same").status_code==422
    doc=client.get(f"/api/literature/scopes/{node['id']}").json()
    revised=client.post(f"/api/nodes/{node['id']}/actions/revise",json={"expected_revision":doc['revision'],"arguments":{"question":"Changed intent","budget":{"max_searches":10,"max_papers":20}}})
    assert revised.status_code==200
    assert action(client,node,"explore",frontier_id=second["id"],request_id="new").status_code==422
    assert len(transport)==1


def test_evaluation_is_local_audited_rule_and_does_not_spend_search(client,transport):
    node=configure(client)
    item=route(client,node)
    result=action(client,node,"evaluate_frontier",frontier_id=item["id"])
    assert result.status_code==200,result.text
    evaluation=result.json()["evaluation"]
    assert evaluation["status"]=="unavailable" and evaluation["jev_score"] is None
    assert evaluation["rule_priority"]["interpretation"]=="deterministic_policy_score_not_probability"
    assert not transport


def test_proposed_discovery_cannot_forge_completed_search(client):
    node=configure(client)
    item=route(client,node)
    forged={**item,"id":"forged","discovery_state":"found","evidence_state":"reviewed_support","proposed_by":"Fake Reviewer"}
    result=action(client,node,"frontier",value=forged)
    assert result.status_code==200,result.text
    retained=result.json()["value"]["frontiers"][-1]
    assert retained["proposed_by"]=="desktop"
    assert retained["discovery_state"]=="unsearched" and retained["evidence_state"]=="none"


def test_changed_source_invalidates_priority_and_stale_snapshot_is_excluded(client, transport):
    from backend.tests.test_literature_service import search
    from backend.tests.test_literature_snapshot_service import make, update_paper
    node=configure(client)
    paper_id=search(client,node).json()["run"]["paper_ids"][0]
    make(client,node["id"])
    item=route(client,node)
    first=action(client,node,"evaluate_frontier",frontier_id=item["id"])
    assert first.status_code==200,first.text
    assert first.json()["evaluation"]["snapshot_context"]["version"]==1
    update_paper(client,paper_id,"metadata",{"metadata":{"title":"Changed source metadata"}})
    scope=client.get(f"/api/literature/scopes/{node['id']}").json()
    assert scope["value"]["frontiers"][0]["evaluation"]["stale"] is True
    new=action(client,node,"evaluate_frontier",frontier_id=item["id"])
    assert new.status_code==200,new.text
    assert new.json()["evaluation"]["snapshot_context"]["version"] is None
    assert new.json()["evaluation"]["snapshot_context"]["excluded_reasons"]
    assert new.json()["item"]["evaluation"]["stale"] is False
