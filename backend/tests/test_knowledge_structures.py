"""Structure database: CIF keys, provenance on write, queries by chemistry and symmetry, similarity."""
from itertools import permutations, product

import httpx
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider, _CapabilityContext
from backend.config import Settings
from backend.errors import ResourceValidationError
from backend.services import create_services
from backend.tests.test_paper_extraction import FakeGrobid, imported
from backend.world.models import CardCreate, EdgeCreate
from oaw_knowledge import structures_cif as sc
from oaw_library import grobid


def _cubic_ops(centring=((0, 0, 0),)) -> list[str]:
    """The 48 operations of m-3m (signed permutations), times the lattice centring translations."""
    ops = []
    for shift in centring:
        for order in permutations("xyz"):
            for signs in product("+-", repeat=3):
                ops.append(",".join(f"{'-' if sign == '-' else ''}{axis}{'+1/2' if t else ''}"
                                    for sign, axis, t in zip(signs, order, shift)))
    return ops


FCC = ((0, 0, 0), (0, 1, 1), (1, 0, 1), (1, 1, 0))


def cif(name, a, sites, *, ops=None, symbol=None, number=None, formula=None, angles=True, extra=""):
    lines = [f"data_{name}", f"_cell_length_a {a}", f"_cell_length_b {a}", f"_cell_length_c {a}"]
    if angles:
        lines += ["_cell_angle_alpha 90.0", "_cell_angle_beta 90.00(0)", "_cell_angle_gamma 90"]
    if symbol:
        lines.append(f"_symmetry_space_group_name_H-M '{symbol}'")
    if number:
        lines.append(f"_space_group_IT_number {number}")
    if formula:
        lines.append(f"_chemical_formula_sum '{formula}'")
    lines.append(extra)
    if ops:
        lines += ["loop_", "_space_group_symop_operation_xyz", *(f"'{op}'" for op in ops)]
    lines += ["loop_", "_atom_site_label", "_atom_site_type_symbol", "_atom_site_fract_x", "_atom_site_fract_y",
              "_atom_site_fract_z", "_atom_site_occupancy", *sites]
    return "\n".join(lines) + "\n"


def rocksalt(cation, anion, a, name=None):
    return cif(name or f"{cation}{anion}", a, [f"{cation}1 {cation}1+ 0 0 0 1", f"{anion}1 {anion}1- 0.5 0.5 0.5 1"],
               ops=_cubic_ops(FCC), symbol="F m -3 m", formula=f"{cation} {anion}")


NACL = rocksalt("Na", "Cl", "5.6402(3)")
KCL = rocksalt("K", "Cl", "6.2931(2)")
SRTIO3 = cif("SrTiO3", "3.9050(1)", ["Sr1 Sr2+ 0 0 0 1", "Ti1 Ti4+ 0.5 0.5 0.5 1", "O1 O2- 0.5 0.5 0 1.0"],
             ops=_cubic_ops(), number=221, extra="# comment line\n_journal_name_full\n;\nA text field; with 'quotes'\n;")
SI = cif("Si", "5.4310(2)", ["Si1 Si 0.125 0.125 0.125 ?"], number=227, angles=False)


def test_cif_keys_pure_python(monkeypatch):
    monkeypatch.setattr(sc, "PYMATGEN", False)
    nacl = sc.analyse(NACL)
    assert (nacl["reduced"], nacl["chemsys"], nacl["anonymous"]) == ("ClNa", "Cl-Na", "AB")
    assert (nacl["spacegroup_number"], nacl["spacegroup_symbol"], nacl["crystal_system"]) == (225, "Fm-3m", "cubic")
    assert nacl["a"] == 5.6402 and nacl["nsites"] == 8 and nacl["sites_basis"] == "cell"
    assert nacl["volume"] == pytest.approx(5.6402 ** 3, rel=1e-6) and nacl["volume_per_site"] == pytest.approx(5.6402 ** 3 / 8, rel=1e-4)
    assert nacl["warnings"] == []

    perovskite = sc.analyse(SRTIO3)  # No formula tag: composition from the expanded sites.
    assert perovskite["formula"] == "O3SrTi" and perovskite["reduced"] == "O3SrTi" and perovskite["anonymous"] == "ABC3"
    assert perovskite["nsites"] == 5 and perovskite["spacegroup_symbol"] == "Pm-3m"

    si = sc.analyse(SI)  # No symmetry operations, no angles: asymmetric unit only, angles assumed.
    assert si["nsites"] == 1 and si["sites_basis"] == "asymmetric unit" and si["volume_per_site"] is None
    assert si["alpha"] == 90.0 and si["crystal_system"] == "cubic" and si["spacegroup_symbol"] == "Fd-3m"
    assert any("asymmetric unit" in w for w in si["warnings"]) and any("90 degrees" in w for w in si["warnings"])

    # Disordered site: two species on one position are one site with mixed occupancy.
    alloy = sc.analyse(cif("CuAu", 3.9, ["Cu1 Cu 0 0 0 0.5", "Au1 Au 0 0 0 0.5"], ops=_cubic_ops(FCC), number=225))
    assert alloy["nsites"] == 4 and alloy["reduced"] == "AuCu"

    for symbol, number in (("Fm-3m", 225), ("F m 3 m", 225), ("P 1 21/c 1", 14), ("P2_1/c", 14), ("Pnma", 62),
                           ("Ia3d", 230), ("R -3 m :H", 166), ("Cmca", 64), ("nonsense", None)):
        assert sc.spacegroup_number(symbol) == number
    assert sc.number("5.4310(2)") == 5.431 and sc.number("?") is None and sc.number("1/4") == 0.25
    assert sc.parse_symop("-x+1/2, y-x, 1/2+z") == (((-1.0, 0.0, 0.0), (-1.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.5, 0.0, 0.5))


@pytest.mark.parametrize("text, message", [
    ("", "empty"),
    ("_cell_length_a 5\n", "No data block"),
    (NACL.replace("_cell_length_b 5.6402(3)", ""), "_cell_length_b is missing"),
    (NACL.replace("_cell_angle_gamma 90", "_cell_angle_gamma 190"), "angles between 0 and 180"),
    (NACL.split("loop_\n_atom_site_label")[0], "No atom sites"),
    (NACL.replace("Cl1 Cl1- 0.5 0.5 0.5 1", "Cl1 Cl1- 0.5 0.5 1"), "values for 6 columns"),
    (NACL.replace("Cl1 Cl1-", "Xx1 Xx"), "cannot tell its element"),
    (NACL.replace("'x,y,z'", "'x,y'"), "Cannot read symmetry operation"),
    (NACL.replace("0.5 0.5 0.5 1", "0.5 abc 0.5 1"), "must be numbers"),
    ("data_x\n_cell_length_a 'unterminated\n", "unterminated"),
    ("data_x\n" + "#" * (sc.MAX_CIF_CHARS + 1), "the limit is"),
])
def test_invalid_cifs_are_rejected_with_a_reason(text, message):
    with pytest.raises(sc.CifError, match=message):
        sc.analyse(text)


# ---- Through the Agent tools ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_grobid(monkeypatch):
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(FakeGrobid()))


@pytest.fixture(autouse=True)
def pure_path(monkeypatch):
    monkeypatch.setattr(sc, "PYMATGEN", False)


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


async def setup(services, relationship="knowledge.structures.curate"):
    library = await services.create_card(CardCreate(type="library.collection", name="Oxides"))
    paper = await services.create_card(CardCreate(type="library.paper", name="Layered oxide", parent_id=library.id))
    await imported(services, paper.id)
    store = await services.create_card(CardCreate(type="knowledge.structures", name="Structures"))
    agent = await services.create_card(CardCreate(type="agent"))
    await services.create_edge(EdgeCreate(source=agent.id, target=library.id, relationship="library.collection.read"))
    await services.create_edge(EdgeCreate(source=agent.id, target=store.id, relationship=relationship))
    provider = WorldAgentCapabilityProvider(services)
    tools = {tool.name: tool for tool in await provider.list_tools(agent.id)}

    async def call(tool, /, **arguments):
        return await provider.invoke_tool(agent.id, tools[tool].capability_id, {"store": store.id, **arguments})

    grant = services.capabilities.capability_for_id(agent.id, f"library.read:{paper.id}")
    text = (await _CapabilityContext(services).node_resource_action(grant, "page_text", {"page": 2}))["text"]
    return paper, store, agent, tools, call, " ".join(text.split()[:10])


COD = {"database": "COD", "id": "9008678", "url": "https://www.crystallography.net/cod/9008678.html"}


@pytest.mark.asyncio
async def test_tool_sets_follow_read_and_curate(services):
    *_, tools, _, _ = await setup(services)
    curate = {"find_structures", "get_structure", "find_similar_structures", "add_structure", "annotate_structure",
              "retract_structure", "check_knowledge_provenance", "read_knowledge_log"}
    assert curate <= tools.keys()
    *_, tools, _, _ = await setup(services, "knowledge.structures.read")
    assert tools.keys() & curate == {"find_structures", "get_structure", "find_similar_structures",
                                     "check_knowledge_provenance", "read_knowledge_log"}


@pytest.mark.asyncio
async def test_adding_needs_verified_provenance_and_writes_nothing_on_failure(services):
    paper, store, agent, tools, call, quote = await setup(services)
    with pytest.raises(ResourceValidationError, match="Give provenance"):
        await call("add_structure", cif=NACL)
    with pytest.raises(ResourceValidationError, match="quote is not on page 1"):
        await call("add_structure", cif=NACL, citations=[{"paper": paper.id, "page": 1, "quote": quote}])
    with pytest.raises(ResourceValidationError, match="you cannot read Paper"):
        await call("add_structure", cif=NACL, citations=[{"paper": "elsewhere", "page": 1}])
    with pytest.raises(ResourceValidationError, match="Invalid CIF: .*_cell_length_b"):
        await call("add_structure", cif=NACL.replace("_cell_length_b 5.6402(3)", ""),
                   citations=[{"paper": paper.id, "page": 2, "quote": quote}])
    with pytest.raises(ResourceValidationError, match="external_ref.id is required"):
        await call("add_structure", cif=NACL, external_ref={"database": "COD"})
    with pytest.raises(ResourceValidationError, match="Property name"):
        await call("add_structure", cif=NACL, external_ref=COD, properties={"band gap": 5})
    assert (await call("find_structures"))["total"] == 0
    assert (await call("read_knowledge_log"))["entries"] == []

    cited = await call("add_structure", cif=NACL, name="Halite", note="Room temperature",
                       citations=[{"paper": paper.id, "page": 2, "quote": quote}], properties={"band_gap_eV": 8.5})
    assert cited["record"] == f"structure:{cited['id']}" and cited["warnings"] == [] and cited["possible_duplicates"] == []
    detail = await call("get_structure", id=cited["id"])
    assert detail["sources"][0]["level"] == "quote" and detail["sources"][0]["cite"] == f"{paper.id}#p2"
    assert detail["external_ref"] is None and "cif" not in detail and detail["cif_chars"] == len(NACL)
    assert (await call("get_structure", id=cited["id"], include_cif=True))["cif"] == NACL

    external = await call("add_structure", cif=KCL, external_ref=COD)
    detail = await call("get_structure", id=external["id"])
    assert detail["sources"] == [] and detail["external_ref"] == {**COD, "verified": False}
    assert detail["created_by"] == agent.id and detail["name"] == "KCl Fm-3m"

    log = (await call("read_knowledge_log"))["entries"]
    assert [(e["op"], e["actor"], e["records"]) for e in log] == [
        ("add", agent.id, [external["record"]]), ("add", agent.id, [cited["record"]])]
    assert (await call("check_knowledge_provenance"))["summary"] == {"fresh": 1, "stale": 0, "unavailable": 0}


@pytest.mark.asyncio
async def test_queries_by_chemistry_and_symmetry(services):
    *_, call, _ = await setup(services)
    ids = {}
    for key, text in (("nacl", NACL), ("kcl", KCL), ("srtio3", SRTIO3), ("si", SI)):
        ids[key] = (await call("add_structure", cif=text, external_ref=COD))["id"]
    found = lambda result: {item["id"] for item in result["structures"]}

    assert found(await call("find_structures", formula="Na4Cl4")) == {ids["nacl"]}
    assert found(await call("find_structures", chemsys="Cl-Na")) == {ids["nacl"]}
    assert found(await call("find_structures", chemsys="K-Na-Cl", chemsys_match="within")) == {ids["nacl"], ids["kcl"]}
    assert found(await call("find_structures", chemsys="Na-Cl-O-Sr-Ti", chemsys_match="within")) == {ids["nacl"], ids["srtio3"]}
    assert found(await call("find_structures", elements=["Cl"], exclude_elements=["K"])) == {ids["nacl"]}
    assert found(await call("find_structures", spacegroup="Fm-3m")) == found(await call("find_structures", spacegroup=225)) \
        == {ids["nacl"], ids["kcl"]}
    assert found(await call("find_structures", crystal_system="cubic", nsites_max=5)) == {ids["srtio3"], ids["si"]}
    page = await call("find_structures", limit=3)
    assert page["total"] == 4 and page["next_offset"] == 3 and len((await call("find_structures", offset=3))["structures"]) == 1
    first = page["structures"][0]
    assert "cif" not in first and first["provenance"] == {"citations": 0, "stale": 0, "external_ref": {**COD, "verified": False}}

    for arguments, message in ((dict(formula="LFP"), "not a chemical formula"), (dict(spacegroup="Xyz"), "Unknown space group"),
                               (dict(chemsys="Na-Qq"), "Unknown element"), (dict(crystal_system="cubicle"), "crystal_system"),
                               (dict(limit=1000), "limit must be")):
        with pytest.raises(ResourceValidationError, match=message):
            await call("find_structures", **arguments)


@pytest.mark.asyncio
async def test_similarity_ranking_and_duplicate_warning(services):
    *_, call, _ = await setup(services)
    nacl = (await call("add_structure", cif=NACL, external_ref=COD))["id"]
    kcl = (await call("add_structure", cif=KCL, external_ref=COD))["id"]
    await call("add_structure", cif=SRTIO3, external_ref=COD)
    duplicate = await call("add_structure", cif=rocksalt("Na", "Cl", "5.6300", name="NaCl_cold"), external_ref=COD)
    assert [d["id"] for d in duplicate["possible_duplicates"]] == [nacl]
    assert any("Possible duplicate of structure" in w for w in duplicate["warnings"])
    far = await call("add_structure", cif=rocksalt("Na", "Cl", "7.0", name="NaCl_stretched"), external_ref=COD)
    assert far["possible_duplicates"] == []

    similar = await call("find_similar_structures", id=nacl)
    assert similar["method"].startswith("pure-python")
    order = [(item["id"], item["kind"]) for item in similar["results"]]
    assert order == [(duplicate["id"], "same formula and space group"), (far["id"], "same formula and space group"),
                     (kcl, "same prototype")]
    assert similar["results"][0]["cell_distance"] < 0.05 < similar["results"][1]["cell_distance"]

    by_cif = await call("find_similar_structures", cif=rocksalt("K", "Br", "6.6"), limit=1)
    assert [item["id"] for item in by_cif["results"]] == [kcl]  # The closest rock salt cell.
    with pytest.raises(ResourceValidationError, match="exactly one of id"):
        await call("find_similar_structures")


@pytest.mark.asyncio
async def test_annotate_and_retract(services):
    paper, store, agent, tools, call, quote = await setup(services)
    added = await call("add_structure", cif=SRTIO3, external_ref=COD, properties={"band_gap_eV": 3.2, "phase": "cubic"})
    revised = await call("annotate_structure", id=added["id"], properties={"band_gap_eV": 3.25, "phase": None, "dielectric": 300},
                         note="Gap from optical absorption", citations=[{"paper": paper.id, "page": 2, "quote": quote}])
    structure = revised["structure"]
    assert structure["properties"] == {"band_gap_eV": 3.25, "dielectric": 300} and structure["note"] == "Gap from optical absorption"
    assert len(structure["sources"]) == 1
    annotated = (await call("find_structures", has_property="dielectric"))["structures"]
    assert [item["id"] for item in annotated] == [added["id"]]
    with pytest.raises(ResourceValidationError, match="Nothing to change"):
        await call("annotate_structure", id=added["id"])
    with pytest.raises(ResourceValidationError, match="No structure 99"):
        await call("annotate_structure", id=99, note="x")

    with pytest.raises(ResourceValidationError, match="reason is required"):
        await call("retract_structure", id=added["id"], reason="")
    await call("retract_structure", id=added["id"], reason="Duplicate of the COD entry")
    assert (await call("find_structures"))["total"] == 0
    hidden = (await call("find_structures", include_retracted=True))["structures"][0]
    assert hidden["status"] == "retracted" and hidden["retracted_reason"] == "Duplicate of the COD entry"
    with pytest.raises(ResourceValidationError, match="already retracted"):
        await call("retract_structure", id=added["id"], reason="again")
    with pytest.raises(ResourceValidationError, match="is retracted"):
        await call("annotate_structure", id=added["id"], note="late")
    assert [e["op"] for e in (await call("read_knowledge_log"))["entries"]] == ["retract", "annotate", "add"]


def test_pymatgen_keys_when_installed(monkeypatch):
    pytest.importorskip("pymatgen")
    monkeypatch.setattr(sc, "PYMATGEN", True)
    si = sc.analyse(SI)  # pymatgen expands with its own tables and detects the space group.
    assert si["analysis"] == "pymatgen" and si["nsites"] == 8 and si["spacegroup_number"] == 227
    assert sc.match(NACL, rocksalt("Na", "Cl", "5.63"))["match"] is True
    assert sc.match(NACL, KCL, anonymous_match=True)["match"] is True


# ---- Regressions: bounded work and validation --------------------------------------------------

def _translations_cif(rows: int) -> str:
    ops = [f"x+{k}/384,y,z" for k in range(384)]
    return cif("Slow", 5.0, ["Na1 Na 0.1 0.2 0.3 1"] * rows, ops=ops, number=1)


def test_expansion_work_is_bounded():
    import time
    start = time.perf_counter()
    with pytest.raises(sc.CifError, match="5000 listed sites x 384 symmetry operations"):
        sc.analyse(_translations_cif(5000))
    within = sc.analyse(_translations_cif(260))  # 99,840 images: at the limit, still fast
    assert time.perf_counter() - start < 1.0
    assert within["nsites"] == 384


@pytest.mark.parametrize("old, new, message", [
    ("_cell_length_a 5.6402(3)", "_cell_length_a 1e999", "_cell_length_a is missing or not a number"),
    ("_cell_length_a 5.6402(3)", "_cell_length_a 1e6", "Cell lengths must be between"),
    ("_cell_angle_gamma 90", "_cell_angle_gamma 1e999", "_cell_angle_gamma is missing or not a number"),
    ("Cl1 Cl1- 0.5 0.5 0.5 1", "Cl1 Cl1- 1e999 0.5 0.5 1", "must be numbers"),
    ("Cl1 Cl1- 0.5 0.5 0.5 1", "Cl1 Cl1- nan 0.5 0.5 1", "must be numbers"),
    ("Cl1 Cl1- 0.5 0.5 0.5 1", "Cl1 Cl1- 0.5 0.5 0.5 1e999", "occupancy '1e999' is not a number"),
])
def test_non_finite_numbers_are_rejected(old, new, message):
    with pytest.raises(sc.CifError, match=message):
        sc.analyse(NACL.replace(old, new))


def test_out_of_range_space_group_number_is_ignored():
    for value in ("1e999", "1e300", "231"):
        keys = sc.analyse(NACL.replace("_chemical_formula_sum", f"_space_group_IT_number {value}\n_chemical_formula_sum"))
        assert keys["spacegroup_number"] == 225  # falls back to the symbol


@pytest.mark.asyncio
async def test_bad_query_arguments_are_validation_errors(services):
    *_, call, _ = await setup(services)
    with pytest.raises(ResourceValidationError, match="has_property must be a property name"):
        await call("find_structures", has_property='a"b')
    for tool in ("get_structure", "find_similar_structures"):
        with pytest.raises(ResourceValidationError, match="id must be a structure id"):
            await call(tool, id=2 ** 70)


@pytest.mark.asyncio
async def test_structure_matcher_work_is_capped(services, monkeypatch):
    fits = []
    monkeypatch.setattr(sc, "PYMATGEN", True)
    monkeypatch.setattr(sc, "_pymatgen_keys", lambda cif, result: None)
    monkeypatch.setattr(sc, "match", lambda a, b, anonymous_match=False: fits.append(1) or {"match": False, "rms": None})
    *_, call, _ = await setup(services)
    ids = [(await call("add_structure", cif=rocksalt("Na", "Cl", f"5.{60 + i}"), external_ref=COD))["id"] for i in range(13)]
    fits.clear()
    await call("add_structure", cif=NACL, external_ref=COD)
    assert len(fits) == 10  # duplicate check on the closest candidates only
    fits.clear()
    similar = await call("find_similar_structures", id=ids[0], limit=20)
    assert len(fits) == 10 and similar["method"].startswith("pymatgen StructureMatcher on the 10 closest")

    # Cells over the site limit are ranked by cell distance, without StructureMatcher.
    positions = [f"{i / 211:.5f} {i * 7 % 211 / 211:.5f} {i * 13 % 211 / 211:.5f}" for i in range(211)]
    big = cif("Big", 20.0, [f"Na{i} Na {p} 1" for i, p in enumerate(positions[:105])]
              + [f"Cl{i} Cl {p} 1" for i, p in enumerate(positions[105:210])], number=1)
    first = (await call("add_structure", cif=big, external_ref=COD))["id"]
    await call("add_structure", cif=big.replace("20.0", "20.5"), external_ref=COD)
    fits.clear()
    similar = await call("find_similar_structures", id=first)
    assert "pymatgen not used" in similar["method"] and fits == []
    assert similar["results"][0]["nsites"] == 210 and "structure_match" not in similar["results"][0]
