from copy import deepcopy
import base64
from urllib.parse import unquote

import httpx
import pymupdf

from backend.literature_service import service
from backend.node_documents import read_document, write_document
from backend.tests.conftest import create_node
from backend.tests.test_literature_service import configure, search
from oaw_literature.search import CrossrefClient


PARENT = {"DOI": "10.1234/article", "type": "journal-article", "title": ["The actual research article"],
    "author": [{"given": "Ada", "family": "Researcher"}], "published": {"date-parts": [[2023]]}}


def component(suffix="s001", parent="10.1234/article"):
    return {"DOI": f"10.1234/article.{suffix}", "type": "component", "title": [f"Supporting information {suffix}"],
        "relation": {"is-component-of": [{"id-type": "doi", "id": parent}]}}


def provider(client, items):
    requests = []
    records = {record["DOI"]: record for record in [PARENT, *items]}
    def handle(request):
        requests.append(str(request.url))
        doi = unquote(request.url.path.removeprefix("/works/"))
        message = {"total-results": len(items), "items": items} if request.url.path == "/works" else records.get(doi)
        return httpx.Response(200 if message else 404, json={"status": "ok", "message": message})
    service(client.app.state.services).client = CrossrefClient(transport=httpx.MockTransport(handle))
    return requests


def post(client, scope, operation, arguments, revision=None):
    if revision is None:
        revision = client.get(f"/api/literature/scopes/{scope['id']}/intake").json()["revision"]
    return client.post(f"/api/literature/scopes/{scope['id']}/{operation}", json={"expected_revision": revision, "arguments": arguments})


def test_components_resolve_one_canonical_article_with_original_provenance(client):
    node = configure(client, searches=3)
    requests = provider(client, [component(), component("s002")])
    result = search(client, node, rows=2)
    assert result.status_code == 200, result.text
    run = result.json()["run"]
    assert len(run["paper_ids"]) == 1 and len(requests) == 2
    paper = read_document(client.app.state.services, run["paper_ids"][0])["value"]
    assert paper["metadata"]["doi"] == PARENT["DOI"]
    assert paper["metadata"]["authors"] == ["Ada Researcher"] and paper["metadata"]["year"] == 2023
    assert run["candidates"][0]["metadata"]["doi"].endswith(".s001")
    assert run["candidates"][0]["record_type"] == "component"
    intake = client.get(f"/api/literature/scopes/{node['id']}/intake").json()
    assert len(intake["papers"][0]["components"]) == 2
    assert len(intake["search_budgets"]["1"]["reservations"]) == 2
    assert intake["papers"][0]["screening_status"] == "pending"
    assert search(client, node, rows=2).json()["replay"]
    assert len(requests) == 2


def test_component_budget_exhaustion_keeps_candidate_without_fake_main_paper(client):
    node = configure(client, searches=1)
    requests = provider(client, [component()])
    result = search(client, node)
    assert result.status_code == 200, result.text
    run = result.json()["run"]
    assert run["paper_ids"] == [] and len(requests) == 1
    assert run["candidates"][0]["intake_status"] == "needs_parent_resolution"
    assert run["candidates"][0]["parent_resolution_status"] == "budget_exhausted"


def test_suffix_alone_never_causes_parent_merge(client):
    node = configure(client)
    suffix_article = {**deepcopy(PARENT), "DOI": "10.1234/article.s001"}
    requests = provider(client, [suffix_article])
    result = search(client, node)
    assert result.status_code == 200, result.text
    paper = read_document(client.app.state.services, result.json()["run"]["paper_ids"][0])["value"]
    assert paper["metadata"]["doi"] == suffix_article["DOI"] and len(requests) == 1


def test_unresolved_component_is_not_normal_article(client):
    node = configure(client)
    item = component()
    item["relation"] = {"is-component-of": None}
    requests = provider(client, [item])
    result = search(client, node)
    assert result.status_code == 200, result.text
    assert result.json()["run"]["paper_ids"] == [] and len(requests) == 1
    assert result.json()["run"]["candidates"][0]["parent_resolution_status"] == "missing_or_ambiguous_parent"


def legacy_scope(client):
    node = configure(client, searches=8)
    services = client.app.state.services
    papers = []
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "Synthetic supporting information fixture")
        encoded_pdf = base64.b64encode(pdf.tobytes()).decode()
    for item in [component(), component("s002")]:
        card = create_node(client, "library.paper", name=item["title"][0], config={"doi": item["DOI"]})
        doc = read_document(services, card["id"])
        write_document(services, card["id"], {**doc["value"], "pdf": encoded_pdf, "pages": 1,
            "notes": "Preserve my scientific notes", "metadata": {"doi": item["DOI"], "title": item["title"][0]}}, doc["revision"])
        papers.append(card["id"])
    doc = read_document(services, node["id"])
    write_document(services, node["id"], {**doc["value"], "paper_ids": papers}, doc["revision"])
    return node, papers


def test_repair_preview_and_apply_group_without_mutating_any_paper(client):
    node, papers = legacy_scope(client)
    provider(client, [component(), component("s002")])
    services = client.app.state.services
    originals = {key: deepcopy(read_document(services, key)) for key in papers}
    result = post(client, node, "repair_preview", {"paper_ids": papers})
    assert result.status_code == 200, result.text
    preview = result.json()["preview"]
    assert all(item["canonical_doi"] == PARENT["DOI"] for item in preview["proposals"])
    assert not read_document(services, node["id"])["value"]["intake_records"]
    applied = post(client, node, "repair_apply", {"preview_id": preview["id"]})
    assert applied.status_code == 200, applied.text
    assert read_document(services, node["id"])["value"]["paper_ids"] == papers
    for key in papers:
        assert read_document(services, key) == originals[key]
    intake = client.get(f"/api/literature/scopes/{node['id']}/intake").json()
    assert {item["canonical_metadata"]["doi"] for item in intake["papers"]} == {PARENT["DOI"]}
    assert post(client, node, "repair_apply", {"preview_id": preview["id"]}).json()["replay"]


def test_repair_rejects_changed_paper_and_stale_scope(client):
    node, papers = legacy_scope(client)
    provider(client, [component(), component("s002")])
    result = post(client, node, "repair_preview", {"paper_ids": papers}).json()
    paper = read_document(client.app.state.services, papers[0])
    write_document(client.app.state.services, papers[0], {**paper["value"], "notes": "New notes"}, paper["revision"])
    assert post(client, node, "repair_apply", {"preview_id": result["preview"]["id"]}).status_code == 409
    assert post(client, node, "repair_apply", {"preview_id": result["preview"]["id"]}, revision=0).status_code == 409


def test_review_requires_reason_current_revisions_and_fulltext_for_close_read(client):
    node = configure(client)
    provider(client, [PARENT])
    assert search(client, node).status_code == 200
    intake = client.get(f"/api/literature/scopes/{node['id']}/intake").json()
    paper = intake["papers"][0]
    args = {"paper_id": paper["paper_id"], "item_revision": paper["revision"], "paper_revision": paper["paper_revision"],
        "screening_status": "included", "screening_reason": "Matches scope composition", "reading_status": "screened"}
    assert post(client, node, "review_paper", {**args, "screening_reason": ""}).status_code == 422
    assert post(client, node, "review_paper", {**args, "reading_status": "verified"}).status_code == 422
    assert post(client, node, "review_paper", {**args, "reading_status": "close_read"}).status_code == 422
    saved = post(client, node, "review_paper", args)
    assert saved.status_code == 200, saved.text
    assert saved.json()["papers"][0]["reading_status"] == "screened"
    assert saved.json()["papers"][0]["screening_status"] == "included"
    assert post(client, node, "review_paper", args).status_code == 409


def test_parent_resolution_respects_route_capacity(client):
    from backend.tests.test_literature_frontiers_service import action, route
    node = configure(client, searches=5, papers=20)
    requests = provider(client, [component()])
    item = route(client, node)
    result = action(client, node, "explore", frontier_id=item["id"], request_id="component-route")
    assert result.status_code == 200, result.text
    assert len(requests) == 1
    assert result.json()["run"]["candidates"][0]["parent_resolution_status"] == "route_budget_exhausted"
    assert result.json()["run"]["paper_ids"] == []


def test_parent_resolution_is_included_in_route_usage(client):
    from backend.tests.test_literature_frontiers_service import action, route
    node = configure(client, searches=5, papers=20)
    requests = provider(client, [component()])
    item = route(client, node)
    services = client.app.state.services
    current = read_document(services, node["id"])
    value = deepcopy(current["value"])
    value["frontiers"][0]["budget"].update(max_searches=2, max_papers=6)
    write_document(services, node["id"], value, current["revision"])
    result = action(client, node, "explore", frontier_id=item["id"], request_id="component-route-allowed")
    assert result.status_code == 200, result.text
    assert len(requests) == 2 and len(result.json()["run"]["paper_ids"]) == 1
    scope = client.get(f"/api/literature/scopes/{node['id']}").json()["value"]
    assert scope["frontiers"][0]["budget_used"] == {"searches": 2, "papers": 6}


def test_repair_preview_obeys_scope_concurrency_before_network(client):
    node, papers = legacy_scope(client)
    requests = provider(client, [component()])
    host = service(client.app.state.services)
    host.running[(node["id"], "other-live-search")] = None
    result = post(client, node, "repair_preview", {"paper_ids": papers})
    assert result.status_code == 422 and not requests
    host.running.clear()
