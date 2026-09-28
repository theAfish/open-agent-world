"""Metadata-only Micro-Skills remain source-bound research strategy drafts."""
from copy import deepcopy

import pytest

from backend.node_documents import read_document, write_document
from backend.tests.conftest import create_node
from backend.tests.test_literature_records_service import document, invoke
from oaw_literature.evidence import canonical_sha256
from oaw_literature.search import CrossrefClient


@pytest.fixture(autouse=True)
def no_metadata_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Micro-Skills use only held metadata; no index or paid provider call")
    monkeypatch.setattr(CrossrefClient, "search", forbidden)
    monkeypatch.setattr(CrossrefClient, "resolve", forbidden)


def fixture(client, abstract=""):
    paper = create_node(client, "library.paper", name="Held metadata source")["id"]
    services = client.app.state.services
    current = read_document(services, paper)
    metadata = {"title": "Controlled literature metadata fixture", "doi": "10.1234/controlled",
        "authors": ["Fixture Author"], "source_url": "https://doi.org/10.1234/controlled", "source_abstract": abstract}
    write_document(services, paper, {**current["value"], "metadata": metadata}, current["revision"])
    scope = create_node(client, "literature.scope")["id"]
    response = client.post(f"/api/nodes/{scope}/actions/revise", json={"expected_revision": document(client, scope)["revision"],
        "arguments": {"question": "A bounded metadata research strategy", "seed_paper_ids": [paper],
            "budget": {"max_searches": 0, "max_papers": 1}}})
    assert response.status_code == 200, response.text
    return scope, paper


def payload(client, paper, **changes):
    return {"id": "micro-controlled", "name": "Check assumptions before designing experiments", "paper_id": paper,
        "paper_revision": document(client, paper)["revision"], "basis": "metadata", "purpose": "Plan a bounded follow-up search from the held title.",
        "steps": ["Locate an author-held abstract and record the reported scope.", "Compare reported assumptions before drafting experimental work."],
        "missing": ["Full text and experimental parameters are unavailable."], **changes}


def record(client, scope, value, **changes):
    return invoke(client, scope, "record", {"kind": "micro_skill", "value": value, **changes})


def test_no_pdf_metadata_strategy_records_exact_host_snapshot_and_draft(client):
    scope, paper = fixture(client)
    before = document(client, paper)
    assert before["value"]["pdf"] == "" and before["value"]["pages"] == 0
    response = record(client, scope, payload(client, paper))
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["status"] == "draft" and item["kind"] == "micro_skill" and item["skill_kind"] == "research_strategy"
    assert item["recorded_by"] == "desktop" and item["revision"] == 1
    assert "not a reconstructed experimental method" in item["claim_scope"]
    assert item["metadata_snapshot"] == before["value"]["metadata"]
    assert item["metadata_sha256"] == canonical_sha256(before["value"]["metadata"])
    source = item["sources"][0]
    assert source["paper_id"] == paper and source["paper_revision"] == before["revision"]
    assert source["basis"] == "metadata" and source["quote"] == "" and source["doi"] == "10.1234/controlled"
    assert not set(source) & {"page", "rects", "document_version_id", "text_ranges", "quote_sha256"}
    assert all(step["origin"] == "research_strategy" for step in item["steps"])
    stored = document(client, scope)["value"]
    assert stored["micro_skills"] == [item] and stored["methods"] == [] and stored["evidence"] == []
    assert document(client, paper) == before
    contracts = invoke(client, scope, "contracts").json()
    assert contracts["MicroSkill"]["additionalProperties"] is False


def test_abstract_basis_needs_real_stored_abstract_and_preserves_it_exactly(client):
    scope, paper = fixture(client)
    before = document(client, scope)
    assert record(client, scope, payload(client, paper, basis="abstract")).status_code == 422
    assert document(client, scope) == before
    scope, paper = fixture(client, "  This controlled abstract reports a comparison, not an experimental protocol.\n")
    response = record(client, scope, payload(client, paper, basis="abstract"))
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["sources"][0]["quote"] == document(client, paper)["value"]["metadata"]["source_abstract"]
    assert item["basis"] == "abstract" and item["status"] == "draft"


@pytest.mark.parametrize("changes", [{"status": "validated"}, {"scientific_reviews": []}, {"sources": [{"page": 1, "quote": "invented"}]},
    {"recorded_by": "invented"}, {"source_abstract": "invented"}, {"revision": 2}, {"missing": []}, {"steps": [" "]}, {"id": "method-id"}])
def test_client_cannot_forge_status_provenance_or_anchors(client, changes):
    scope, paper = fixture(client)
    before = document(client, scope)
    response = record(client, scope, payload(client, paper, **changes))
    assert response.status_code == 422, response.text
    assert document(client, scope) == before


def test_stale_paper_outside_scope_and_stale_item_cannot_mutate(client):
    scope, paper = fixture(client)
    foreign_scope, foreign = fixture(client)
    assert record(client, scope, payload(client, foreign)).status_code == 403
    stale = payload(client, paper)
    services = client.app.state.services
    current = read_document(services, paper)
    write_document(services, paper, {**current["value"], "notes": "Paper revision changed"}, current["revision"])
    assert record(client, scope, stale).status_code == 409
    value = payload(client, paper)
    initial = record(client, scope, value)
    assert initial.status_code == 200, initial.text
    before = document(client, scope)
    assert record(client, scope, value).status_code == 409
    assert record(client, scope, value, item_revision=0).status_code == 409
    assert document(client, scope) == before
    revised = record(client, scope, {**value, "purpose": "Refine the bounded strategy"}, item_revision=1)
    assert revised.status_code == 200, revised.text
    assert revised.json()["item"]["revision"] == 2
    assert revised.json()["item"]["previous"] == [{key: value for key, value in initial.json()["item"].items() if key != "previous"}]


def test_agent_abstract_is_not_accepted_as_a_stored_source_abstract(client):
    scope, paper = fixture(client)
    services = client.app.state.services
    current = read_document(services, paper)
    write_document(services, paper, {**current["value"], "metadata": {**current["value"]["metadata"],
        "agent_abstract": "An agent-generated interpretation, not source prose."}}, current["revision"])
    assert record(client, scope, payload(client, paper, basis="abstract")).status_code == 422


def test_scoped_agent_records_real_actor_and_stale_scope_does_not_write(client):
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    scope, paper = fixture(client)
    agent = create_node(client, "agent")["id"]
    edge = client.post("/api/edges", json={"source": agent, "target": scope, "relationship": "literature.research"})
    assert edge.status_code == 201, edge.text
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    current = document(client, scope)
    arguments = {"kind": "micro_skill", "expected_revision": current["revision"], "value": payload(client, paper)}
    result = client.portal.call(provider.invoke_tool, agent, "operation:literature_research",
        {"scope": scope, "operation": "record", "arguments": arguments})
    # The scope record is authoritative regardless of provider envelope formatting.
    item = document(client, scope)["value"]["micro_skills"][0]
    assert item["recorded_by"] == agent
    before = document(client, scope)
    stale = invoke(client, scope, "record", {"kind": "micro_skill", "value": payload(client, paper, id="micro-another")}, revision=current["revision"])
    assert stale.status_code == 409
    assert document(client, scope) == before


def test_copy_archives_micro_skills_and_never_rebinds_historical_paper_identity(client):
    from oaw_literature.scope import remap
    scope, paper = fixture(client)
    assert record(client, scope, payload(client, paper)).status_code == 200
    before = document(client, scope)["value"]
    copied = remap(deepcopy(before), {scope: "copy-scope", paper: "copy-paper"})
    assert copied["micro_skills"] == []
    assert copied["archived_results"][-1]["micro_skills"] == before["micro_skills"]
    assert copied["archived_results"][-1]["micro_skills"][0]["sources"][0]["paper_id"] == paper
