import httpx
import pytest

from backend.tests.conftest import create_node
from backend.node_documents import read_document, write_document
from oaw_literature.search import CrossrefClient


def configure(client, searches=3, papers=10):
    node = create_node(client, "literature.scope")
    document = client.get(f"/api/nodes/{node['id']}/document").json()
    response = client.post(f"/api/nodes/{node['id']}/actions/revise", json={"expected_revision": document["revision"],
        "arguments": {"question": "Local synthetic test query", "budget": {"max_searches": searches, "max_papers": papers}}})
    assert response.status_code == 200, response.text
    return node


def search(client, node, identity="first", **overrides):
    document = client.get(f"/api/literature/scopes/{node['id']}").json()
    return client.post(f"/api/literature/scopes/{node['id']}/search", json={"expected_revision": document["revision"],
        "arguments": {"scope_revision": document["value"]["current_revision"], "request_id": identity,
                      "query": "synthetic fixture", "rows": 1, **overrides}})


def response():
    return httpx.Response(200, json={"status": "ok", "message": {"total-results": 1, "items": [
        {"DOI": "10.1234/local-test", "title": ["Synthetic test fixture"], "URL": "https://doi.org/10.1234/local-test",
         "author": [{"given": "Test", "family": "Author"}], "published": {"date-parts": [[2024]]}}]}})


@pytest.fixture
def transport(client):
    from backend.literature_service import service
    requests = []
    def handle(request):
        requests.append(str(request.url))
        return response()
    service(client.app.state.services).client = CrossrefClient(transport=httpx.MockTransport(handle))
    return requests


def test_search_reserves_budget_creates_paper_and_replays_without_network(client, transport):
    node = configure(client)
    result = search(client, node)
    assert result.status_code == 200, result.text
    run = result.json()["run"]
    assert len(run["paper_ids"]) == 1
    paper_id = run["paper_ids"][0]
    assert run["candidates"][0]["paper_id"] == paper_id
    paper = client.get(f"/api/nodes/{paper_id}/document").json()["value"]
    assert not paper["pdf"] and paper["metadata"]["doi"] == "10.1234/local-test"
    assert search(client, node).json()["replay"] is True
    assert len(transport) == 1
    again = search(client, node, "second")
    assert again.status_code == 200, again.text
    assert again.json()["run"]["paper_ids"] == [paper_id]
    assert len([card for card in client.get("/api/nodes").json() if card["type"] == "library.paper"]) == 1
    scope = client.get(f"/api/literature/scopes/{node['id']}").json()["value"]
    assert len(scope["search_budgets"]["1"]["reservations"]) == 2
    assert all(item["attempts"] == 1 for item in scope["search_budgets"]["1"]["reservations"])


def test_budget_and_request_identity_are_enforced_before_network(client, transport):
    node = configure(client, searches=1)
    assert search(client, node).status_code == 200
    assert search(client, node, query="different request").status_code == 422
    assert search(client, node, "second").status_code == 422
    assert len(transport) == 1


def test_search_intent_change_retains_response_but_does_not_create_papers(client):
    from backend.literature_service import service
    node = configure(client)
    services = client.app.state.services
    def change_while_awaiting(request):
        current = read_document(services, node["id"])
        # Simulate a desktop pause during network I/O.
        write_document(services, node["id"], {**current["value"], "paused": True}, current["revision"])
        return response()
    service(services).client = CrossrefClient(transport=httpx.MockTransport(change_while_awaiting))
    result = search(client, node)
    assert result.status_code == 200, result.text
    assert result.json()["run"]["status"] == "completed_stale"
    assert result.json()["run"]["paper_ids"] == []
    assert result.json()["run"]["candidates"][0]["paper_id"] is None
    assert not [card for card in client.get("/api/nodes").json() if card["type"] == "library.paper"]


def test_failed_search_preserves_spent_budget_and_audit(client):
    from backend.literature_service import service
    node = configure(client, searches=1)
    service(client.app.state.services).client = CrossrefClient(transport=httpx.MockTransport(lambda request:httpx.Response(503)))
    result = search(client, node)
    assert result.status_code == 422, result.text
    scope = client.get(f"/api/literature/scopes/{node['id']}").json()["value"]
    assert scope["search_runs"][0]["status"] == "failed"
    assert scope["search_budgets"]["1"]["reservations"][0]["attempts"] == 1
    assert search(client, node, "second").status_code == 422


def test_scoped_read_cannot_reach_other_papers(client, transport):
    first, second = configure(client), configure(client)
    paper_id = search(client, first).json()["run"]["paper_ids"][0]
    denied = client.post(f"/api/literature/scopes/{second['id']}/paper", json={"arguments":{"paper_id":paper_id,"view":"metadata"}})
    assert denied.status_code == 403
    allowed = client.post(f"/api/literature/scopes/{first['id']}/paper", json={"arguments":{"paper_id":paper_id,"view":"metadata"}})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["value"]["metadata"]["doi"] == "10.1234/local-test"


def test_search_cannot_omit_or_widen_scope_year_boundary(client, transport):
    node = configure(client)
    current = client.get(f"/api/literature/scopes/{node['id']}").json()
    changed = client.post(f"/api/nodes/{node['id']}/actions/revise", json={"expected_revision":current["revision"],
        "arguments":{"question":"Bounded date fixture","start_year":2020,"end_year":2025,
            "budget":{"max_searches":3,"max_papers":10}}})
    assert changed.status_code == 200
    assert search(client,node).status_code == 422
    assert search(client,node,from_year=2019,until_year=2025).status_code == 422
    assert search(client,node,from_year=2020,until_year=2026).status_code == 422
    assert not transport
    assert search(client,node,from_year=2020,until_year=2025).status_code == 200


def test_doi_resolution_excludes_known_year_outside_scope(client):
    from backend.literature_service import service
    node = configure(client)
    current = client.get(f"/api/literature/scopes/{node['id']}").json()
    assert client.post(f"/api/nodes/{node['id']}/actions/revise",json={"expected_revision":current["revision"],
        "arguments":{"question":"Modern papers only","start_year":2020,"end_year":2025,
            "budget":{"max_searches":1,"max_papers":1}}}).status_code == 200
    service(client.app.state.services).client = CrossrefClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(200,json={"status":"ok","message":{"DOI":"10.1234/old","title":["Old fixture"],"published":{"date-parts":[[2000]]}}})))
    doc = client.get(f"/api/literature/scopes/{node['id']}").json()
    result = client.post(f"/api/literature/scopes/{node['id']}/resolve",json={"expected_revision":doc["revision"],
        "arguments":{"scope_revision":2,"request_id":"old-doi","doi":"10.1234/old"}})
    assert result.status_code == 200,result.text
    run = result.json()["run"]
    assert run["provider_run"]["candidate_count"] == 1 and run["admitted_candidate_count"] == 0
    assert run["paper_ids"] == [] and run["filtered_out"][0]["reason"] == "outside_scope_years"
    assert not [card for card in client.get("/api/nodes").json() if card["type"] == "library.paper"]


def test_agent_search_cannot_expand_grant_to_an_existing_foreign_paper(client, transport):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.errors import PermissionDeniedError
    private, target = configure(client), configure(client)
    paper_id = search(client, private).json()["run"]["paper_ids"][0]
    agent = create_node(client,"agent")
    assert client.post("/api/edges",json={"source":agent["id"],"target":target["id"],"relationship":"literature.research"}).status_code == 201
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    current = client.get(f"/api/literature/scopes/{target['id']}").json()
    result = client.portal.call(provider.invoke_tool,agent["id"],"operation:literature_research",{
        "scope":target["id"],"operation":"search","arguments":{"expected_revision":current["revision"],
            "scope_revision":1,"request_id":"agent-public-search","query":"fixture","rows":1}})
    assert result["run"]["paper_ids"] == []
    assert result["run"]["conflicts"][0]["status"] == "existing_identity_requires_desktop_link"
    assert paper_id not in str(result)
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool,agent["id"],"operation:literature_read",{
            "scope":target["id"],"operation":"paper","arguments":{"paper_id":paper_id}})
