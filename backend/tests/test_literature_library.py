"""Literature library: member-scoped grants, the full-text index and library tools (GROBID mocked)."""
import httpx
import pytest

from backend.agents.media import VisualToolResult
from backend.capabilities.projection import MAX_ENUM_TARGETS, selector_schema
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.services import create_services
from backend.tests.test_paper_extraction import FakeGrobid, imported
from backend.world.models import CardCreate, CardPatch, EdgeCreate
from oaw_library import grobid


@pytest.fixture(autouse=True)
def fake_grobid(monkeypatch):
    fake = FakeGrobid()
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


async def tools(provider, agent_id):
    return {tool.name: tool for tool in await provider.list_tools(agent_id)}


async def library_with_paper(services):
    library = await services.create_card(CardCreate(type="library.collection", name="Cathodes"))
    paper = await services.create_card(CardCreate(type="library.paper", name="Layered oxide", parent_id=library.id))
    await imported(services, paper.id)
    return library, paper


@pytest.mark.asyncio
async def test_library_connection_reaches_current_members_only(services):
    library, paper = await library_with_paper(services)
    outside = await services.create_card(CardCreate(type="library.paper", name="Elsewhere"))
    await imported(services, outside.id)
    agent = await services.create_card(CardCreate(type="agent"))
    provider = WorldAgentCapabilityProvider(services)
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.read"))

    available = await tools(provider, agent.id)
    assert {"list_papers", "search_library", "read_paper", "read_paper_structure", "view_paper_figure"} <= available.keys()
    assert not {"revise_paper_structure", "reextract_paper"} & available.keys()
    assert available["list_papers"].input_schema["properties"]["library"]["enum"] == ["cathodes"]
    page = await provider.invoke_tool(agent.id, available["read_paper"].capability_id, {"target": paper.id, "page": 2})
    assert "Results and discussion" in page["text"]
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, available["read_paper"].capability_id, {"target": outside.id, "page": 1})

    # Membership is read live: moving a Paper in grants it, moving it out revokes it.
    await services.update_card(outside.id, CardPatch(parent_id=library.id))
    assert (await provider.invoke_tool(agent.id, available["read_paper"].capability_id, {"target": outside.id, "page": 1}))["page"] == 1
    await services.update_card(paper.id, CardPatch(parent_id=None))
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, available["read_paper"].capability_id, {"target": paper.id, "page": 1})
    listed = await provider.invoke_tool(agent.id, available["list_papers"].capability_id, {"library": library.id})
    assert [item["paper"] for item in listed["papers"]] == [outside.id]


@pytest.mark.asyncio
async def test_curate_library_revises_member_structure(services):
    library, paper = await library_with_paper(services)
    agent = await services.create_card(CardCreate(type="agent"))
    provider = WorldAgentCapabilityProvider(services)
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.curate"))
    revise = (await tools(provider, agent.id))["revise_paper_structure"]
    result = await provider.invoke_tool(agent.id, revise.capability_id, {"target": paper.id, "base_version": "v1",
        "changes": [{"op": "set", "path": "metadata.title", "value": "Sodium layered oxide cathodes"}]})
    assert result["version"] == "v2"
    # A direct edge and the library grant the same kinds; they merge into one tool.
    await services.create_edge(EdgeCreate(source=agent.id, target=paper.id, relationship="library.read"))
    available = [tool.name for tool in await provider.list_tools(agent.id)]
    assert available.count("read_paper") == 1
    listed = await provider.invoke_tool(agent.id, (await tools(provider, agent.id))["list_papers"].capability_id,
                                        {"library": library.id})
    assert listed["papers"][0]["title"] == "Sodium layered oxide cathodes"
    assert listed["papers"][0]["version"] == "v2"


@pytest.mark.asyncio
async def test_search_ranks_cited_passages_and_follows_changes(services):
    library, paper = await library_with_paper(services)
    blank = await services.create_card(CardCreate(type="library.paper", name="Not imported", parent_id=library.id))
    search = lambda **arguments: invoke_resource_action(services, library.id, "search", ResourceActionRequest(arguments=arguments))

    found = await search(query="capacity cathode")
    assert found["papers_indexed"] == 2 and found["hits"]
    hit = found["hits"][0]
    assert hit["paper"] == paper.id and hit["cite"] == f"{paper.id}#p{hit['page']}" and "[" in hit["snippet"]
    assert {h["kind"] for h in (await search(query="Electrochemical data", kinds=["table"]))["hits"]} == {"table"}
    assert (await search(query="capacity", year_from=2025))["hits"] == []
    assert (await search(query="capacity", year_to=2024))["hits"]
    with pytest.raises(ResourceValidationError):
        await search(query="  ... ")

    catalog = await invoke_resource_action(services, library.id, "catalog", ResourceActionRequest())
    assert catalog["total"] == 2 and catalog["status_counts"] == {"empty": 1, "structured": 1}
    assert catalog["papers"][0]["year"] == 2024 and catalog["papers"][0]["authors"][0] == "Alice Zhang"

    # Moved-out Papers leave the index on the next read; renames are picked up.
    await services.update_card(blank.id, CardPatch(parent_id=None))
    await services.update_card(paper.id, CardPatch(name="Renamed oxide"))
    again = await invoke_resource_action(services, library.id, "catalog", ResourceActionRequest())
    assert again["total"] == 1 and again["index"] == {"indexed": 1, "removed": 1}
    assert again["papers"][0]["name"] == "Renamed oxide"
    unchanged = await invoke_resource_action(services, library.id, "catalog", ResourceActionRequest())
    assert unchanged["index"] == {"indexed": 0, "removed": 0}


@pytest.mark.asyncio
async def test_papers_without_structure_are_searchable_by_page(services, fake_grobid):
    fake_grobid.down = True
    library, paper = await library_with_paper(services)
    found = await invoke_resource_action(services, library.id, "search", ResourceActionRequest(arguments={"query": "synthesized"}))
    assert found["hits"][0]["kind"] == "page" and found["hits"][0]["paper"] == paper.id
    # Once GROBID succeeds the Paper is re-indexed from its structure.
    fake_grobid.down = False
    await invoke_resource_action(services, paper.id, "extract", ResourceActionRequest())
    await services.resource_jobs.wait()
    found = await invoke_resource_action(services, library.id, "search", ResourceActionRequest(arguments={"query": "synthesized"}))
    assert found["hits"][0]["kind"] == "section" and found["hits"][0]["path"].startswith("sections.")


@pytest.mark.asyncio
async def test_figure_tool_returns_the_crop(services):
    library, paper = await library_with_paper(services)
    agent = await services.create_card(CardCreate(type="agent"))
    provider = WorldAgentCapabilityProvider(services)
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.read"))
    figure = (await tools(provider, agent.id))["view_paper_figure"]
    result = await provider.invoke_tool(agent.id, figure.capability_id, {"target": paper.id, "figure": "1"})
    assert isinstance(result, VisualToolResult)
    assert result.metadata["id"] == "f1" and result.images[0].media_type == "image/png"
    missing = await provider.invoke_tool(agent.id, figure.capability_id, {"target": paper.id, "figure": "f2"})
    assert "image" not in missing and "no image" in missing["hint"]
    with pytest.raises(ResourceValidationError):
        await provider.invoke_tool(agent.id, figure.capability_id, {"target": paper.id, "figure": "Figure 9"})


def test_large_target_sets_are_not_enumerated():
    class Node:
        def __init__(self, index):
            self.id, self.name = f"id-{index}", f"Paper {index}"
    few = {f"id-{i}": Node(i) for i in range(3)}
    many = {f"id-{i}": Node(i) for i in range(MAX_ENUM_TARGETS + 1)}
    assert len(selector_schema(few)["enum"]) == 3
    assert "enum" not in selector_schema(many) and str(MAX_ENUM_TARGETS + 1) in selector_schema(many)["description"]


def test_preprints_are_dated_by_arxiv_id_and_labels_are_not_repeated():
    from oaw_library.collection import _arxiv_year, _labelled
    assert _arxiv_year("2108.11442.pdf") == 2021 and _arxiv_year("arXiv:2404.09457v3") == 2024
    assert _arxiv_year("10.1016/j.jechem.2024.04.016") is None and _arxiv_year("Paper 2023") is None
    assert _labelled("Fig. 2", "Fig. 2 Phonon dispersion") == "Fig. 2 Phonon dispersion"
    assert _labelled("Figure 1", "Structure of the cathode") == "Figure 1: Structure of the cathode"
