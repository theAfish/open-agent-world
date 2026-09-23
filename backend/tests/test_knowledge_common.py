"""Knowledge stores' shared core: verified provenance, staleness checks, the write log and formula keys."""
from threading import Event

import httpx
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider, _CapabilityContext
from backend.config import Settings
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins.resources import NodeResourceContext
from backend.services import create_services
from backend.tests.test_paper_extraction import FakeGrobid, imported
from backend.world.models import CardCreate, EdgeCreate
from oaw_knowledge import chem, common
from oaw_library import grobid


@pytest.fixture(autouse=True)
def fake_grobid(monkeypatch):
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(FakeGrobid()))


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


async def setup(services, relationship="library.collection.read"):
    library = await services.create_card(CardCreate(type="library.collection", name="Cathodes"))
    paper = await services.create_card(CardCreate(type="library.paper", name="Layered oxide", parent_id=library.id))
    await imported(services, paper.id)
    store = await services.create_card(CardCreate(type="knowledge.facts", name="Facts"))
    agent = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship=relationship))
    await services.create_edge(EdgeCreate(source=agent.id, target=store.id, relationship="knowledge.facts.read"))
    capability = services.capabilities.capability_for_id(agent.id, f"knowledge.facts.log:{store.id}")
    return library, paper, store, agent, capability


async def page_text(services, agent, paper, page=1):
    grant = services.capabilities.capability_for_id(agent.id, f"library.read:{paper.id}")
    return await _CapabilityContext(services).node_resource_action(grant, "page_text", {"page": page})


@pytest.mark.asyncio
async def test_citations_are_checked_with_the_agents_own_grant(services):
    library, paper, store, agent, capability = await setup(services)
    context = _CapabilityContext(services)
    text = (await page_text(services, agent, paper, 2))["text"]
    quote = " ".join(text.split()[:12])

    verified = await common.verify_citations(context, capability, [{"paper": paper.id, "page": 2, "quote": quote.upper()}])
    assert verified[0]["level"] == "quote" and verified[0]["fingerprint"].endswith(":v1")
    assert (await common.verify_citations(context, capability, [{"paper": paper.id, "page": 2}]))[0]["level"] == "page"

    with pytest.raises(ResourceValidationError, match="quote is not on page 1"):
        await common.verify_citations(context, capability, [{"paper": paper.id, "page": 1, "quote": quote}])
    with pytest.raises(ResourceValidationError, match="Cite at least one"):
        await common.verify_citations(context, capability, [])
    assert await common.verify_citations(context, capability, [], required=False) == []
    with pytest.raises(ResourceValidationError, match="Page must be between"):
        await common.verify_citations(context, capability, [{"paper": paper.id, "page": 99}])

    # A Paper outside the Agent's grants, and one that does not exist, fail alike.
    outside = await services.create_card(CardCreate(type="library.paper", name="Elsewhere"))
    await imported(services, outside.id)
    for target in (outside.id, "no-such-paper"):
        with pytest.raises(ResourceValidationError, match="you cannot read Paper"):
            await common.verify_citations(context, capability, [{"paper": target, "page": 1}])
    with pytest.raises(PermissionDeniedError):
        await context.agent_capability(capability, "library.read", outside.id)


@pytest.mark.asyncio
async def test_provenance_check_marks_changed_sources_stale(services):
    library, paper, store, agent, capability = await setup(services, "library.collection.curate")
    context = _CapabilityContext(services)
    sources = await common.verify_citations(context, capability, [{"paper": paper.id, "page": 1}])
    resource = NodeResourceContext(store.id, services.resources.node_storage_path(store.id), Event(), actor_id=agent.id)
    with common.connect(resource) as connection:
        common.add_sources(connection, resource, "fact:1", sources)
        common.log(connection, resource, "assert", ["fact:1"], "test")

    provider = WorldAgentCapabilityProvider(services)
    tools = {tool.name: tool for tool in await provider.list_tools(agent.id)}
    assert {"check_knowledge_provenance", "read_knowledge_log"} <= tools.keys()
    check = lambda: provider.invoke_tool(agent.id, tools["check_knowledge_provenance"].capability_id, {"store": store.id})
    assert (await check())["summary"] == {"fresh": 1, "stale": 0, "unavailable": 0}

    revise = tools["revise_paper_structure"]
    await provider.invoke_tool(agent.id, revise.capability_id, {"target": paper.id, "base_version": "v1",
        "changes": [{"op": "set", "path": "metadata.title", "value": "Revised"}]})
    report = await check()
    assert report["summary"] == {"fresh": 0, "stale": 1, "unavailable": 0} and report["papers"][0]["status"] == "stale"
    with common.connect(resource) as connection:
        assert common.sources_for(connection, ["fact:1"])["fact:1"][0]["status"] == "stale"

    log = await provider.invoke_tool(agent.id, tools["read_knowledge_log"].capability_id, {"store": store.id})
    assert log["entries"][0]["actor"] == agent.id and log["entries"][0]["records"] == ["fact:1"]


def test_formula_keys():
    assert chem.keys("LiFePO₄") == chem.keys("LiFePO4") == {
        "reduced": "FeLiO4P", "chemsys": "Fe-Li-O-P", "elements": ["Fe", "Li", "O", "P"]}
    assert chem.keys("Mg3(PO4)2")["reduced"] == "Mg3O8P2"
    assert chem.keys("CuSO4·5H2O")["reduced"] == "CuH10O9S"
    assert chem.keys("C6H12O6")["reduced"] == "CH2O"
    assert chem.keys("LiNi0.8Mn0.1Co0.1O2")["reduced"] == "Co0.1LiMn0.1Ni0.8O2"
    assert chem.chemsys_of("O, Li ,Fe") == "Fe-Li-O"
    for text in ("LFP", "lithium iron phosphate", "Ca(OH", "2Li"):
        assert chem.try_keys(text) is None


def test_user_calls_cannot_store_provenance(tmp_path):
    # The desktop reaches resource actions without a capability; forged _sources must not persist.
    fake = {"paper": "p", "page": 1, "quote": None, "level": "page", "fingerprint": "x", "verified_at": common.now()}
    user = NodeResourceContext("store", tmp_path, Event())
    agent = NodeResourceContext("store", tmp_path, Event(), actor_id="agent-1")
    with common.connect(user) as connection:
        common.add_sources(connection, user, "fact:1", common.trusted_sources({"_sources": [fake]}))
        common.add_sources(connection, agent, "fact:2", [fake])
        found = common.sources_for(connection, ["fact:1", "fact:2"])
    assert found["fact:1"] == [] and len(found["fact:2"]) == 1
