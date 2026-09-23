"""Fact table: cited writes, composition and unit-aware queries, revision, retraction and the workspace actions."""
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
from oaw_knowledge import facts_units
from oaw_library import grobid

READ_TOOLS = {"query_facts", "list_fact_vocabulary", "check_knowledge_provenance", "read_knowledge_log"}
CURATE_TOOLS = {"record_facts", "revise_fact", "retract_facts"}


@pytest.fixture(autouse=True)
def fake_grobid(monkeypatch):
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(FakeGrobid()))


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


class World:
    def __init__(self, services, provider, store, agent, paper, quote):
        self.services, self.provider, self.store, self.agent, self.paper, self.quote = services, provider, store, agent, paper, quote

    async def tools(self, agent=None):
        return {tool.name: tool for tool in await self.provider.list_tools((agent or self.agent).id)}

    async def call(self, name, arguments, agent=None):
        tool = (await self.tools(agent))[name]
        return await self.provider.invoke_tool((agent or self.agent).id, tool.capability_id, {"store": self.store.id, **arguments})

    async def user(self, action, arguments):
        return await invoke_resource_action(self.services, self.store.id, action, ResourceActionRequest(arguments=arguments))

    def cite(self, page=2, quote=None):
        return [{"paper": self.paper.id, "page": page, "quote": quote or self.quote}]


async def world(services, relationship="knowledge.facts.curate") -> World:
    library = await services.create_card(CardCreate(type="library.collection", name="Cathodes"))
    paper = await services.create_card(CardCreate(type="library.paper", name="Layered oxide", parent_id=library.id))
    await imported(services, paper.id)
    store = await services.create_card(CardCreate(type="knowledge.facts", name="Facts"))
    agent = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.read"))
    await services.create_edge(EdgeCreate(source=agent.id, target=store.id, relationship=relationship))
    quote = " ".join((await page_text(services, agent, paper, 2))["text"].split()[:12])
    return World(services, WorldAgentCapabilityProvider(services), store, agent, paper, quote)


def fact(material, prop, value=None, unit="", **extra):
    return {"material": material, "property": prop, **({"value": value} if value is not None else {}), "unit": unit, **extra}


@pytest.mark.asyncio
async def test_read_and_curate_tool_sets(services):
    w = await world(services)
    assert READ_TOOLS | CURATE_TOOLS <= (await w.tools()).keys()
    reader = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=reader.id, target=w.store.id, relationship="knowledge.facts.read"))
    names = (await w.tools(reader)).keys()
    assert READ_TOOLS <= names and not CURATE_TOOLS & names


@pytest.mark.asyncio
async def test_citations_are_verified_per_fact_and_failures_write_nothing(services):
    w = await world(services)
    good = fact("LiCoO2", "specific_capacity", 140, "mAh/g", citations=w.cite())
    with pytest.raises(ResourceValidationError, match=r"facts\.1\.citations\.0: the quote is not on page 1.*Nothing was written"):
        await w.call("record_facts", {"facts": [good, fact("LiCoO2", "band_gap", 2.7, "eV", citations=w.cite(page=1))]})
    with pytest.raises(ResourceValidationError, match=r"facts\.0: Cite at least one"):
        await w.call("record_facts", {"facts": [fact("LiCoO2", "band_gap", 2.7, "eV", citations=[])]})

    # A Paper the Agent is not connected to cannot be cited.
    outside = await services.create_card(CardCreate(type="library.paper", name="Elsewhere"))
    await imported(services, outside.id)
    with pytest.raises(ResourceValidationError, match=r"facts\.1\.citations\.0: you cannot read Paper"):
        await w.call("record_facts", {"facts": [good, fact("LiCoO2", "band_gap", 2.7, "eV",
                                                             citations=[{"paper": outside.id, "page": 1}])]})
    # Shape errors are reported before any Paper is read; injected provenance is ignored.
    with pytest.raises(ResourceValidationError, match=r"facts\.0\.value_max \(1\.0\) is below value"):
        await w.call("record_facts", {"facts": [fact("LiCoO2", "band_gap", 2.7, "eV", value_max=1, citations=w.cite())]})
    forged = {**fact("LiCoO2", "band_gap", 2.7, "eV"), "_sources": [{"paper": "x", "page": 1, "level": "quote",
              "fingerprint": "f", "verified_at": "now"}]}
    with pytest.raises(ResourceValidationError, match="Cite at least one"):
        await w.call("record_facts", {"facts": [forged]})

    assert (await w.call("query_facts", {}))["total"] == 0
    assert (await w.call("read_knowledge_log", {}))["entries"] == []

    result = await w.call("record_facts", {"facts": [good], "note": "from the abstract"})
    record = (await w.call("query_facts", {}))["facts"][0]
    assert result["recorded"][0]["id"] == record["id"] and record["created_by"] == w.agent.id
    assert record["sources"][0]["cite"] == f"{w.paper.id}#p2" and record["sources"][0]["level"] == "quote"
    entry = (await w.call("read_knowledge_log", {}))["entries"][0]
    assert entry["op"] == "record" and entry["records"] == [record["key"]] and entry["note"] == "from the abstract"

    # Recording the same measurement from the same Paper again is flagged, not refused.
    again = await w.call("record_facts", {"facts": [fact("LiCoO₂", "specific_capacity", 0.14, "Ah/g", citations=w.cite())]})
    assert again["possible_duplicates"][0]["same_as"] == [record["id"]]


@pytest.mark.asyncio
async def test_composition_keys_and_element_filters(services):
    w = await world(services)
    cite = w.cite()
    result = await w.call("record_facts", {"facts": [
        fact("LiFePO₄", "specific_capacity", 160, "mAh/g", citations=cite),
        fact("LFP", "specific_capacity", 150, "mAh/g", formula="LiFePO4", citations=cite),
        fact("LFP nanoplates", "specific_capacity", 155, "mAh/g", citations=cite),
        fact("Li2O", "band_gap", 7.8, "eV", citations=cite),
        fact("NaCoO2", "band_gap", 1.2, "eV", citations=cite),
    ]})
    assert any("'LFP nanoplates' is not a parseable formula" in note for note in result["warnings"])
    ids = [item["id"] for item in result["recorded"]]
    found = lambda r: sorted(f["id"] for f in r["facts"])

    lfp = await w.call("query_facts", {"formula": "FePO4Li"})
    assert found(lfp) == ids[:2] and lfp["facts"][0]["reduced"] == "FeLiO4P" and lfp["facts"][0]["chemsys"] == "Fe-Li-O-P"
    assert found(await w.call("query_facts", {"material": "lfp"})) == ids[1:3]
    assert found(await w.call("query_facts", {"chemsys": "O-P-Li-Fe"})) == ids[:2]
    assert found(await w.call("query_facts", {"chemsys": "Li,Fe,P,O", "chemsys_mode": "within"})) == [ids[0], ids[1], ids[3]]
    assert found(await w.call("query_facts", {"elements": ["O", "Co"]})) == [ids[4]]
    with pytest.raises(ResourceValidationError, match="not a chemical formula.*use material"):
        await w.call("query_facts", {"formula": "LFP"})
    with pytest.raises(ResourceValidationError, match="Unknown element 'Xx'"):
        await w.call("query_facts", {"chemsys": "Li-Xx"})
    with pytest.raises(ResourceValidationError, match="formula 'lithium' is not a chemical formula"):
        await w.call("record_facts", {"facts": [fact("LFP", "band_gap", 3.5, "eV", formula="lithium", citations=cite)]})

    vocabulary = await w.call("list_fact_vocabulary", {})
    assert vocabulary["totals"] == {"facts": 5, "materials": 4, "properties": 2, "retracted": 0}
    assert vocabulary["properties"][0] == {"property": "specific_capacity", "count": 3, "units": {"mAh/g": 3}}


@pytest.mark.asyncio
async def test_value_ranges_compare_within_a_dimension_only(services):
    w = await world(services)
    cite = w.cite()
    result = await w.call("record_facts", {"facts": [
        fact("TiO2", "band_gap", 3.2, "eV", citations=cite),
        fact("ZnO", "band_gap", 3300, "meV", citations=cite),
        fact("Si", "band_gap", 1.1, "eV", citations=cite),
        fact("GaN", "band gap", 3.3, "kJ/mol", citations=cite),          # wrong dimension: warned, never compared
        fact("CdS", "band_gap", 2.4, "furlongs", citations=cite),        # unknown unit
        fact("Li3PS4", "ionic_conductivity", 0.16, "mS cm⁻¹", citations=cite),
        fact("Li7La3Zr2O12", "ionic_conductivity", 0.03, "S/m", citations=cite),
        fact("Li2S", "ionic_conductivity", 1e-9, "S/cm", citations=cite),
        fact("LiPON", "ionic_conductivity", 2.0, "uS/cm", value_max=3.0, conditions={"Temperature": "25 °C"}, citations=cite),
    ]})
    ids = [item["id"] for item in result["recorded"]]
    assert any("band_gap is usually a energy" in note for note in result["warnings"])
    assert any("'furlongs' is not in the normalisation table" in note for note in result["warnings"])

    gaps = await w.call("query_facts", {"property": "band_gap", "min_value": 3000, "max_value": 3500, "unit": "meV"})
    assert [f["id"] for f in gaps["facts"]] == [ids[0], ids[1]]
    assert gaps["facts"][1]["normalized"] == {"value": 3.3, "value_max": None, "unit": "eV", "dimension": "energy"}
    assert "2 matching fact(s) were not compared" in gaps["notes"][0] and "kJ/mol: 1" in gaps["notes"][0]
    unknown = await w.call("query_facts", {"property": "band_gap", "min_value": 2, "unit": "furlongs"})
    assert [f["id"] for f in unknown["facts"]] == [ids[4]] and "not in the normalisation table" in unknown["notes"][0]
    with pytest.raises(ResourceValidationError, match="A value range needs unit"):
        await w.call("query_facts", {"property": "band_gap", "min_value": 1})

    # 1e-4 S/cm = 0.1 mS/cm; a range overlaps [2, 3] uS/cm.
    conductive = await w.call("query_facts", {"property": "ionic_conductivity", "min_value": 1e-4, "unit": "S cm-1"})
    assert sorted(f["id"] for f in conductive["facts"]) == [ids[5], ids[6]]
    low = await w.call("query_facts", {"property": "ionic_conductivity", "min_value": 2.5, "max_value": 10, "unit": "µS/cm"})
    assert [f["id"] for f in low["facts"]] == [ids[8]]
    assert [f["id"] for f in (await w.call("query_facts", {"conditions": {"temperature": "25 °c"}}))["facts"]] == [ids[8]]
    assert facts_units.resolve("℃").to_base(25) == pytest.approx(298.15)


@pytest.mark.asyncio
async def test_revise_is_logged_and_retraction_hides(services):
    w = await world(services)
    first = (await w.call("record_facts", {"facts": [fact("LiCoO2", "band_gap", 2.7, "eV", citations=w.cite())]}))["recorded"][0]["id"]
    with pytest.raises(ResourceValidationError, match="note is required"):
        await w.call("revise_fact", {"fact": first, "changes": {"value": 2.1}})
    with pytest.raises(ResourceValidationError, match="the quote is not on page 1"):
        await w.call("revise_fact", {"fact": first, "changes": {"value": 2.1}, "note": "table 2", "citations": w.cite(page=1)})
    assert (await w.call("query_facts", {}))["facts"][0]["value"] == 2.7

    revised = await w.call("revise_fact", {"fact": first, "changes": {"value": 2100, "unit": "meV"}, "note": "misread table 2",
                                           "citations": [{"paper": w.paper.id, "page": 1}], "replace_citations": True})
    assert revised["changed"] == ["value", "unit"] and revised["fact"]["normalized"]["value"] == pytest.approx(2.1)
    assert [s["level"] for s in revised["fact"]["sources"]] == ["page"] and revised["fact"]["updated_by"] == w.agent.id
    entry = (await w.call("read_knowledge_log", {}))["entries"][0]
    assert entry["op"] == "revise" and entry["records"] == [f"fact:{first}"]
    assert entry["note"] == f"misread table 2; changed value, unit; replaced 1 citation(s); removed citation(s) {w.paper.id}#p2"

    with pytest.raises(ResourceValidationError, match="Give a reason"):
        await w.call("retract_facts", {"facts": [first], "reason": ""})
    with pytest.raises(ResourceValidationError, match="There is no fact 99"):
        await w.call("retract_facts", {"facts": [first, 99], "reason": "duplicate"})
    assert (await w.call("retract_facts", {"facts": [first], "reason": "duplicate of fact:0"}))["retracted"] == [first]
    assert (await w.call("query_facts", {}))["total"] == 0
    hidden = (await w.call("query_facts", {"include_retracted": True}))["facts"][0]
    assert hidden["status"] == "retracted" and hidden["retract_reason"] == "duplicate of fact:0"
    assert (await w.call("list_fact_vocabulary", {}))["totals"]["retracted"] == 1
    with pytest.raises(ResourceValidationError, match="retracted"):
        await w.call("revise_fact", {"fact": first, "changes": {"value": 2.0}, "note": "again"})
    assert (await w.call("read_knowledge_log", {}))["entries"][0]["op"] == "retract"


@pytest.mark.asyncio
async def test_user_entries_have_no_sources(services):
    w = await world(services)
    added = await w.user("ui_add", {"fact": {**fact("NaCl", "melting_point", 801, "°C"), "_sources": [
        {"paper": w.paper.id, "page": 1, "level": "page", "fingerprint": "forged", "verified_at": "now"}]}})
    assert added["fact"]["sources"] == [] and added["fact"]["created_by"] == "user"
    assert added["fact"]["normalized"]["value"] == pytest.approx(1074.15)
    listed = await w.user("ui_query", {"property": "melting_point", "min_value": 1000, "unit": "K"})
    assert [f["id"] for f in listed["facts"]] == [added["fact"]["id"]]
    revised = await w.user("ui_revise", {"fact": added["fact"]["id"], "changes": {"value": 800.7}, "note": "handbook value",
                                         "_sources": [{"paper": "x", "page": 1}]})
    assert revised["fact"]["sources"] == [] and revised["fact"]["updated_by"] == "user"
    await w.user("ui_retract", {"facts": [added["fact"]["id"]], "reason": "entered twice"})
    entries = (await w.user("ui_log", {}))["entries"]
    assert [(e["op"], e["actor"]) for e in entries] == [("retract", "user"), ("revise", "user"), ("record", "user")]
    assert (await w.user("ui_vocabulary", {}))["totals"]["facts"] == 0


def test_unit_canonical_spellings():
    same = lambda *units: len({facts_units.resolve(u).key for u in units}) == 1
    assert same("mAh/g", "mA h g⁻¹", "mAh g-1", "mA·h/g")
    assert same("S/cm", "S cm-1", "S·cm⁻¹")
    assert facts_units.resolve("W/(m K)").dimension == "thermal_conductivity"
    assert facts_units.resolve("nm").to_base(0.5) == pytest.approx(5.0)
    assert facts_units.resolve("bogus").dimension is None


def test_unit_prefix_case_is_significant():
    # A case-insensitive fallback once read Mbar as mbar and MeV as meV (off by 1e9).
    assert facts_units.resolve("Mbar").to_base(1) == pytest.approx(100)          # GPa
    assert facts_units.resolve("mbar").to_base(1) == pytest.approx(1e-7)
    assert facts_units.resolve("MeV").to_base(1) == pytest.approx(1e6)           # eV
    assert facts_units.resolve("meV").to_base(1) == pytest.approx(1e-3)
    assert facts_units.resolve("mPa").to_base(1) == pytest.approx(1e-12)
    assert facts_units.resolve("MV").to_base(1) == pytest.approx(1e6)
    assert facts_units.resolve("mpa").dimension is None and facts_units.resolve("ev").dimension is None


@pytest.mark.asyncio
async def test_invalid_numbers_conditions_and_ids_are_validation_errors(services):
    w = await world(services)
    with pytest.raises(ResourceValidationError, match=r"facts\.0\.value is too large to convert from 'THz'"):
        await w.call("record_facts", {"facts": [fact("Si", "phonon_frequency", 1e300, "THz", citations=w.cite())]})
    with pytest.raises(ResourceValidationError, match="too large to convert"):
        await w.user("ui_add", {"fact": fact("Si", "phonon_frequency", 1e300, "THz")})
    with pytest.raises(ResourceValidationError, match="value must be a finite number"):
        await w.user("ui_add", {"fact": fact("Si", "band_gap", 10**400, "eV")})
    with pytest.raises(ResourceValidationError, match=r"conditions\.temperature must be a short string.*not an object"):
        await w.call("record_facts", {"facts": [fact("Si", "band_gap", 1.1, "eV", conditions={"temperature": {"K": 300}},
                                                     citations=w.cite())]})
    with pytest.raises(ResourceValidationError, match=r"conditions\.doping must be"):
        await w.user("ui_add", {"fact": fact("Si", "band_gap", 1.1, "eV", conditions={"doping": ["B"]})})
    assert (await w.user("ui_query", {}))["total"] == 0

    added = (await w.user("ui_add", {"fact": fact("Si", "band_gap", 1.1, "eV", conditions={"doping": None, "cycle": 2})}))["fact"]
    assert [f["id"] for f in (await w.call("query_facts", {"conditions": {"doping": None, "cycle": 2}}))["facts"]] == [added["id"]]
    with pytest.raises(ResourceValidationError, match=r"conditions\.temperature must be"):
        await w.call("query_facts", {"conditions": {"temperature": {"K": 300}}})
    with pytest.raises(ResourceValidationError, match="positive fact id"):
        await w.call("query_facts", {"ids": [2**64]})
    with pytest.raises(ResourceValidationError, match="positive fact id"):
        await w.call("retract_facts", {"facts": [2**63], "reason": "overflow"})
    with pytest.raises(ResourceValidationError, match="positive fact id"):
        await w.call("revise_fact", {"fact": 2**70, "changes": {"value": 1}, "note": "overflow"})
    assert (await w.call("query_facts", {"offset": 10**30}))["facts"] == []


@pytest.mark.asyncio
async def test_revised_content_never_keeps_old_citations(services):
    w = await world(services)
    first = (await w.call("record_facts", {"facts": [fact("LiCoO2", "band_gap", 2.7, "eV", citations=w.cite())]}))["recorded"][0]["id"]
    with pytest.raises(ResourceValidationError, match="Changing value needs citations"):
        await w.call("revise_fact", {"fact": first, "changes": {"value": 2.1}, "note": "misread"})
    current = (await w.call("query_facts", {}))["facts"][0]
    assert current["value"] == 2.7 and current["sources"][0]["level"] == "quote"

    # A caveat is not a claim: note-only revisions keep the citations.
    kept = await w.call("revise_fact", {"fact": first, "changes": {"note": "thin film"}, "note": "add sample detail"})
    assert kept["fact"]["sources"] == current["sources"]

    revised = await w.call("revise_fact", {"fact": first, "changes": {"method": "UV-vis"}, "note": "method from page 1",
                                           "citations": [{"paper": w.paper.id, "page": 1}]})
    assert [(s["page"], s["level"]) for s in revised["fact"]["sources"]] == [(1, "page")]
    entry = (await w.call("read_knowledge_log", {}))["entries"][0]
    assert entry["note"] == f"method from page 1; changed method; replaced 1 citation(s); removed citation(s) {w.paper.id}#p2"

    # The user has no citations: a content change drops the Agent's, and the fact shows as user-entered.
    by_user = await w.user("ui_revise", {"fact": first, "changes": {"value": 2.2}, "note": "handbook"})
    assert by_user["fact"]["sources"] == [] and by_user["fact"]["updated_by"] == "user"
    assert (await w.user("ui_log", {}))["entries"][0]["note"] == f"handbook; changed value; removed citation(s) {w.paper.id}#p1"
