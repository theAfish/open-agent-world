"""Ontology store: alias normalisation, resolution, the relation citation policy, merge and retraction."""
import time
from threading import Event

import httpx
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import ResourceValidationError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.services import create_services
from backend.tests.test_knowledge_common import page_text
from backend.tests.test_paper_extraction import FakeGrobid, imported
from backend.world.models import CardCreate, EdgeCreate
from backend.plugins.resources import NodeResourceContext
from oaw_knowledge import common, ontology
from oaw_knowledge.ontology import normalize
from oaw_library import grobid

READ_TOOLS = {"resolve_entity", "find_entities", "entity_neighbors", "list_ontology_vocabulary",
              "check_knowledge_provenance", "read_knowledge_log"}
CURATE_TOOLS = {"add_entity", "add_aliases", "relate", "merge_entities", "retract_ontology_record", "cite_ontology_record"}


@pytest.fixture(autouse=True)
def fake_grobid(monkeypatch):
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(FakeGrobid()))


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


class World:
    def __init__(self, services, provider, agent, store, paper, tools):
        self.services, self.provider, self.agent, self.store, self.paper, self.tools = services, provider, agent, store, paper, tools

    async def call(self, tool, **arguments):
        return await self.provider.invoke_tool(self.agent.id, self.tools[tool].capability_id, {"store": self.store.id, **arguments})

    async def user(self, action, **arguments):
        return await invoke_resource_action(self.services, self.store.id, action, ResourceActionRequest(arguments=arguments))

    async def quote(self, page=2, start=0):
        words = (await page_text(self.services, self.agent, self.paper, page))["text"].split()
        return {"paper": self.paper.id, "page": page, "quote": " ".join(words[start:start + 10])}

    async def entity(self, kind, name, **arguments):
        return (await self.call("add_entity", kind=kind, name=name, **arguments))["entity"]["id"]


async def setup(services, relationship="knowledge.ontology.curate"):
    library = await services.create_card(CardCreate(type="library.collection", name="Cathodes"))
    paper = await services.create_card(CardCreate(type="library.paper", name="Layered oxide", parent_id=library.id))
    await imported(services, paper.id)
    store = await services.create_card(CardCreate(type="knowledge.ontology", name="Ontology"))
    agent = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.read"))
    await services.create_edge(EdgeCreate(source=agent.id, target=store.id, relationship=relationship))
    provider = WorldAgentCapabilityProvider(services)
    tools = {tool.name: tool for tool in await provider.list_tools(agent.id)}
    return World(services, provider, agent, store, paper, tools)


@pytest.mark.asyncio
async def test_read_and_curate_tool_sets(services):
    world = await setup(services, "knowledge.ontology.read")
    assert READ_TOOLS <= world.tools.keys() and not CURATE_TOOLS & world.tools.keys()
    curator = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=curator.id, target=world.store.id, relationship="knowledge.ontology.curate"))
    names = {tool.name for tool in await world.provider.list_tools(curator.id)}
    assert READ_TOOLS | CURATE_TOOLS <= names


def test_alias_normalisation():
    assert normalize("LiFePO₄") == normalize("lifepo4") == normalize("Li Fe PO4") == "lifepo4"
    assert normalize("Sol-gel") == normalize("sol gel") == normalize("SOL_GEL") == normalize("sol–gel") == "solgel"
    assert normalize("Li(Ni,Co)O2") != normalize("LiNiCoO2")


@pytest.mark.asyncio
async def test_aliases_conflicts_and_resolution(services):
    world = await setup(services)
    lfp = await world.entity("material", "Lithium iron phosphate", formula="LiFePO₄", aliases=["LFP", "LiFePO4", "lfp"])
    detail = (await world.call("entity_neighbors", entity=lfp))["entity"]
    assert detail["reduced"] == "FeLiO4P" and detail["chemsys"] == "Fe-Li-O-P"
    assert [alias["alias"] for alias in detail["aliases"]] == ["LFP", "LiFePO4"]  # "lfp" collapsed into "LFP"

    # Every spelling lands on one entity, and the match says why.
    for text, by in (("lfp", "alias"), ("LiFePO₄", "alias"), ("lithium-iron phosphate", "name"), ("FeLiPO4", "formula")):
        top = (await world.call("resolve_entity", text=text))["candidates"][0]
        assert top["id"] == lfp and top["matched"][0]["by"] == by, text
    assert (await world.call("resolve_entity", text="lithium iron phosphat"))["candidates"][0]["id"] == lfp
    oxide = await world.entity("material", "Iron phosphate oxide", formula="Fe2P2O9")
    [system] = (await world.call("resolve_entity", text="Li2FeP2O7"))["candidates"]
    assert system["id"] == lfp and system["matched"][0] == {"by": "chemsys", "chemsys": "Fe-Li-O-P"}
    assert [c["id"] for c in (await world.call("find_entities", elements=["Fe", "P"]))["entities"]] == [oxide, lfp]
    assert [c["id"] for c in (await world.call("find_entities", chemsys="Li-Fe-P-O"))["entities"]] == [lfp]

    # A name taken within the kind is refused and names the owner; another kind may reuse it.
    with pytest.raises(ResourceValidationError, match=rf"'L-F-P' already names entity #{lfp} 'Lithium iron phosphate'"):
        await world.entity("material", "Olivine", aliases=["L-F-P"])
    with pytest.raises(ResourceValidationError, match="already has formula FeLiO4P"):
        await world.entity("material", "Maricite", formula="LiFePO4")
    await world.entity("phase", "LFP")
    maricite = await world.entity("material", "Maricite", formula="LiFePO4", allow_same_formula=True)
    with pytest.raises(ResourceValidationError, match=f"already names entity #{lfp}"):
        await world.call("add_aliases", entity=maricite, aliases=["NaFePO4 type", "LFP"])
    assert (await world.call("resolve_entity", text="NaFePO4 type"))["candidates"] == []  # nothing written
    added = await world.call("add_aliases", entity=lfp, aliases=["lfp", "olivine LFP"])
    assert added["added"] == ["olivine LFP"] and added["already_known"] == ["lfp"]


@pytest.mark.asyncio
async def test_relation_citation_policy(services):
    world = await setup(services)
    lfp = await world.entity("material", "LiFePO4", formula="LiFePO4", citations=[await world.quote(1)])
    conductivity = await world.entity("property", "Ionic conductivity")
    cathode = await world.entity("concept", "Polyanion cathode")
    log_size = len((await world.call("read_knowledge_log"))["entries"])

    with pytest.raises(ResourceValidationError, match="has_property relations must cite Paper pages"):
        await world.call("relate", subject=lfp, predicate="has_property", object=conductivity)
    bad = {**await world.quote(2), "quote": "this sentence is certainly not on the page"}
    with pytest.raises(ResourceValidationError, match="quote is not on page 2"):
        await world.call("relate", subject=lfp, predicate="has_property", object=conductivity, citations=[bad])
    assert (await world.call("entity_neighbors", entity=lfp))["relations"] == []
    assert len((await world.call("read_knowledge_log"))["entries"]) == log_size  # nothing written

    cited = (await world.call("relate", subject=lfp, predicate="has_property", object=conductivity,
                              note="room temperature", citations=[await world.quote(2)]))["relation"]
    assert cited["sources"][0]["level"] == "quote" and not cited["unsourced"]
    taxonomy = await world.call("relate", subject=lfp, predicate="is_a", object=cathode)
    assert taxonomy["relation"]["unsourced"] and taxonomy["relation"]["sources"] == []
    hinted = await world.call("relate", subject=conductivity, predicate="used_for", object=cathode, citations=[await world.quote(2, 5)])
    assert "application object" in hinted["warnings"][0]

    with pytest.raises(ResourceValidationError, match="same entity"):
        await world.call("relate", subject=lfp, predicate="is_a", object=lfp)
    with pytest.raises(ResourceValidationError, match=r"relation #\d+ already states"):
        await world.call("relate", subject=lfp, predicate="is_a", object=cathode)
    await world.call("relate", subject=cathode, predicate="related_to", object=conductivity, citations=[await world.quote(2)])
    with pytest.raises(ResourceValidationError, match="already states"):  # symmetric
        await world.call("relate", subject=conductivity, predicate="related_to", object=cathode, citations=[await world.quote(2)])

    vocabulary = await world.call("list_ontology_vocabulary")
    by_name = {item["predicate"]: item for item in vocabulary["predicates"]}
    assert by_name["is_a"]["citations"] == "optional" and by_name["is_a"]["unsourced"] == 1
    assert by_name["has_property"]["relations"] == 1 and vocabulary["totals"]["entities"] == 3


@pytest.mark.asyncio
async def test_merge_moves_aliases_and_collapses_relations(services):
    world = await setup(services)
    keep = await world.entity("material", "LiFePO4", formula="LiFePO4")
    dup = await world.entity("material", "Lithium iron phosphate", aliases=["LFP"], description="Olivine cathode")
    conductivity = await world.entity("property", "Ionic conductivity")
    first = (await world.call("relate", subject=keep, predicate="has_property", object=conductivity,
                              citations=[await world.quote(2)]))["relation"]["id"]
    second = (await world.call("relate", subject=dup, predicate="has_property", object=conductivity, note="slow",
                               citations=[await world.quote(2, 20)]))["relation"]["id"]
    self_relation = (await world.call("relate", subject=dup, predicate="is_a", object=keep))["relation"]["id"]

    with pytest.raises(ResourceValidationError, match="only entities of one kind merge"):
        await world.call("merge_entities", keep=keep, merge=[conductivity])
    report = await world.call("merge_entities", keep=keep, merge=[dup], reason="same compound")
    assert report["merged"] == [dup] and report["aliases_moved"] == 2
    assert report["collapsed"] == [{"relation": second, "into": first}] and report["self_relations_retracted"] == [self_relation]
    assert (await world.call("resolve_entity", text="LFP"))["candidates"][0]["id"] == keep
    assert (await world.call("resolve_entity", text="lithium iron phosphate"))["candidates"][0]["id"] == keep

    view = await world.call("entity_neighbors", entity=dup)
    assert view["redirected_from"] == dup and view["entity"]["id"] == keep
    assert view["entity"]["description"] == "Olivine cathode" and view["entity"]["merged_from"][0]["id"] == dup
    [relation] = view["relations"]
    assert relation["id"] == first and len(relation["sources"]) == 2 and relation["note"] == "slow"
    assert (await world.call("find_entities", kind="material"))["total"] == 1
    entry = (await world.call("read_knowledge_log"))["entries"][0]
    assert entry["op"] == "merge" and f"entity:{dup}" in entry["records"] and entry["note"] == "same compound"
    with pytest.raises(ResourceValidationError, match="merged into entity"):
        await world.call("retract_ontology_record", record=f"entity:{dup}", reason="duplicate")


@pytest.mark.asyncio
async def test_neighbors_are_bounded(services):
    world = await setup(services)
    chain = [await world.entity("concept", f"Level {index}") for index in range(5)]
    for child, parent in zip(chain, chain[1:]):
        await world.call("relate", subject=child, predicate="is_a", object=parent)
    around = await world.call("entity_neighbors", entity=chain[2])
    assert {r["id"] for r in around["relations"]} and all(r["hop"] == 1 for r in around["relations"]) and len(around["relations"]) == 2
    deep = await world.call("entity_neighbors", entity=chain[0], direction="out", depth=2)
    assert [(r["subject"]["id"], r["object"]["id"], r["hop"]) for r in deep["relations"]] == [
        (chain[0], chain[1], 1), (chain[1], chain[2], 2)]
    assert not (await world.call("entity_neighbors", entity=chain[0], direction="in"))["relations"]
    limited = await world.call("entity_neighbors", entity=chain[2], depth=2, limit=3)
    assert len(limited["relations"]) == 3 and limited["truncated"]
    with pytest.raises(ResourceValidationError, match="depth must be an integer between 1 and 2"):
        await world.call("entity_neighbors", entity=chain[0], depth=3)


@pytest.mark.asyncio
async def test_retraction_and_workspace_actions(services):
    world = await setup(services)
    lfp = await world.entity("material", "LiFePO4", formula="LiFePO4", aliases=["LFP"])
    wrong = await world.entity("material", "Lithium ferrite", aliases=["LiFeO2 typo"])
    relation = (await world.call("relate", subject=wrong, predicate="related_to", object=lfp,
                                 citations=[await world.quote(2)]))["relation"]["id"]

    result = await world.call("retract_ontology_record", record=f"entity:{wrong}", reason="not in the paper")
    assert result["retracted"] == [f"entity:{wrong}", f"relation:{relation}"]
    assert (await world.call("resolve_entity", text="lithium ferrite"))["candidates"] == []
    assert (await world.call("entity_neighbors", entity=lfp))["relations"] == []
    hidden = await world.call("find_entities", include_retracted=True)
    assert {e["id"]: e["status"] for e in hidden["entities"]} == {lfp: "active", wrong: "retracted"}
    with pytest.raises(ResourceValidationError, match="was retracted"):
        await world.call("relate", subject=wrong, predicate="is_a", object=lfp)
    await world.entity("material", "Lithium ferrite", formula="LiFeO2")  # the name is free again
    with pytest.raises(ResourceValidationError, match="record must be a record key"):
        await world.call("retract_ontology_record", record="12", reason="bad")

    # The workspace: search, detail, alias and retract as the user; provenance cannot be injected.
    found = await world.user("ui_search", text="lfp")
    assert found["entities"][0]["id"] == lfp
    added = await world.user("ui_add_alias", entity=lfp, alias="olivine", _sources=[{"paper": "x"}])
    assert added["added"] == ["olivine"]
    detail = await world.user("ui_entity", entity=lfp, include_retracted=True)
    assert [a["alias"] for a in detail["aliases"]] == ["LFP", "olivine"] and detail["aliases"][1]["sources"] == []
    assert detail["incoming"][0]["status"] == "retracted"
    alias = detail["aliases"][1]["record"]
    await world.user("ui_retract", record=alias, reason="too vague")
    log = (await world.user("ui_log", limit=2))["entries"]
    assert log[0]["actor"] == "user" and log[0]["op"] == "retract" and log[0]["records"] == [alias]
    assert log[1]["op"] == "add_aliases" and log[1]["actor"] == "user"
    summary = await world.user("ui_vocabulary")
    assert summary["totals"]["entities"] == 2 and summary["totals"]["retracted_entities"] == 1


@pytest.mark.asyncio
async def test_merge_chains_stay_one_hop(services):
    world = await setup(services)
    entities = [await world.entity("concept", f"Idea {index}") for index in range(60)]
    target = await world.entity("concept", "Target")
    # Each entity is merged into the next: without path compression the chain grows to 60 hops.
    for previous, following in zip(entities, entities[1:] + [target]):
        await world.call("merge_entities", keep=following, merge=[previous])
    other = await world.entity("concept", "Other")
    relation = (await world.call("relate", subject=entities[0], predicate="is_a", object=other))["relation"]
    assert relation["subject"]["id"] == target
    view = await world.call("entity_neighbors", entity=entities[0])
    assert view["entity"]["id"] == target and view["redirected_from"] == entities[0]
    resource = NodeResourceContext(world.store.id, services.resources.node_storage_path(world.store.id), Event())
    with common.connect(resource) as connection:
        assert {row[0] for row in connection.execute("SELECT merged_into FROM entities WHERE status = 'merged'")} == {target}
        # Legacy or corrupted chains end in an error, never on a merged entity.
        connection.execute("UPDATE entities SET merged_into = ? WHERE id = ?", (entities[1], entities[0]))
        connection.execute("UPDATE entities SET merged_into = ? WHERE id = ?", (entities[2], entities[1]))
        connection.execute("UPDATE entities SET merged_into = ? WHERE id = ?", (entities[3], entities[2]))
        connection.execute("UPDATE entities SET merged_into = ? WHERE id = ?", (entities[4], entities[3]))
    with pytest.raises(ResourceValidationError, match="is not active"):
        await world.call("relate", subject=entities[0], predicate="is_a", object=other)


@pytest.mark.asyncio
async def test_fuzzy_resolution_is_bounded(services):
    world = await setup(services)
    base = "lithium nickel manganese cobalt oxide cathode material with coating " * 3
    resource = NodeResourceContext(world.store.id, services.resources.node_storage_path(world.store.id), Event())
    with common.connect(resource) as connection:
        connection.executescript(ontology.SCHEMA)
        for index in range(3000):
            name = f"{base[:190]} v{index}"
            entity = connection.execute("INSERT INTO entities (kind, name, created_at, created_by) VALUES ('concept', ?, '', 'test')",
                                        (name,)).lastrowid
            connection.execute("INSERT INTO aliases (entity, alias, normalized, kind, is_name, created_at, created_by)"
                               " VALUES (?, ?, ?, 'concept', 1, '', 'test')", (entity, name, ontology.normalize(name)))
    started = time.monotonic()
    found = await world.call("resolve_entity", text=base[:180] + "x", limit=50)
    assert time.monotonic() - started < 3
    assert found["candidates"] and all(c["matched"][0]["by"] in {"prefix", "fuzzy"} for c in found["candidates"])
    assert (await world.call("resolve_entity", text="lithium nikel manganese cobalt oxide cathode material with coatng",
                             limit=5))["candidates"]


@pytest.mark.asyncio
async def test_out_of_range_ids_and_citing_existing_records(services):
    world = await setup(services)
    lfp = await world.entity("material", "LiFePO4", formula="LiFePO4")
    cathode = await world.entity("concept", "Polyanion cathode")
    for value in (2**63, "99999999999999999999999"):
        with pytest.raises(ResourceValidationError, match="must be an entity id"):
            await world.call("entity_neighbors", entity=value)
    with pytest.raises(ResourceValidationError, match="record must be a record key"):
        await world.call("retract_ontology_record", record="relation:99999999999999999999999", reason="overflow")

    relation = (await world.call("relate", subject=lfp, predicate="is_a", object=cathode))["relation"]
    assert relation["unsourced"]
    bad = {**await world.quote(2), "quote": "this sentence is certainly not on the page"}
    with pytest.raises(ResourceValidationError, match="quote is not on page 2"):
        await world.call("cite_ontology_record", record=relation["record"], citations=[bad])
    with pytest.raises(ResourceValidationError, match="Cite at least one"):
        await world.call("cite_ontology_record", record=relation["record"], citations=[])
    cited = await world.call("cite_ontology_record", record=relation["record"], note="stated in the intro",
                             citations=[await world.quote(2)])
    assert cited["sources"][0]["level"] == "quote"
    [shown] = (await world.call("entity_neighbors", entity=lfp))["relations"]
    assert not shown["unsourced"] and len(shown["sources"]) == 1
    entry = (await world.call("read_knowledge_log"))["entries"][0]
    assert entry["op"] == "cite" and entry["records"] == [relation["record"]] and entry["note"] == "stated in the intro"
    await world.call("retract_ontology_record", record=relation["record"], reason="wrong")
    with pytest.raises(ResourceValidationError, match="not active"):
        await world.call("cite_ontology_record", record=relation["record"], citations=[await world.quote(2)])
