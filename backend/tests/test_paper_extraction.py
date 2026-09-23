"""Served node files and Library structured extraction (GROBID, mocked)."""
import base64
import time
from threading import Event
from urllib.parse import parse_qs

import httpx
import pymupdf
import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError
from backend.node_resources import ResourceActionRequest, invoke_resource_action
from backend.plugins.resources import served_file, served_file_pattern_valid
from backend.services import create_services
from backend.tests.conftest import create_node
from backend.world.models import CardCreate, EdgeCreate
from open_agent_world.plugin_api import NodeResourceContext
from oaw_library import grobid, store

LOREM = ("Layered oxides were synthesized by a solid state route and characterized by diffraction. "
         "The measured capacity exceeded previous reports [1] and agrees with calculations [2,3]. ") * 3


def research_pdf() -> bytes:
    """Two-column article with running headers, a figure, a ruled table and numbered references."""
    doc = pymupdf.open()
    box = lambda page, rect, text, size=9, bold=False: page.insert_textbox(
        pymupdf.Rect(*rect), text, fontsize=size, fontname="hebo" if bold else "helv")
    for number in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 30), "Journal of Test Materials 12 (2024) 100-110", fontsize=8)
        page.insert_text((290, 820), str(number + 1), fontsize=8)
        if number == 0:
            box(page, (50, 60, 545, 122), "High capacity layered oxide cathodes for sodium batteries", 18, True)
            box(page, (50, 125, 545, 154), "Alice Zhang, Bob Smith and Carol Lee", 11)
            box(page, (50, 148, 545, 184), "Department of Materials Science, Test University, Beijing, China. alice@test.edu", 8)
            box(page, (50, 172, 545, 200), "Received 3 March 2024; Accepted 5 May 2024. https://doi.org/10.1016/j.test.2024.01.002", 8)
            box(page, (50, 195, 545, 224), "Abstract", 10, True)
            box(page, (50, 212, 545, 314), "We report a layered oxide cathode with high capacity. " * 6)
            box(page, (50, 305, 545, 334), "Keywords: sodium battery; cathode; layered oxide")
            box(page, (50, 330, 290, 359), "1. Introduction", 10, True)
            box(page, (50, 348, 290, 614), LOREM)
            box(page, (305, 330, 545, 359), "2. Methods", 10, True)
            box(page, (305, 348, 545, 614), LOREM)
        elif number == 1:
            page.draw_rect(pymupdf.Rect(60, 70, 280, 250), color=(0, 0, 1), fill=(0.8, 0.8, 1))
            page.draw_line((70, 240), (270, 90))
            box(page, (50, 255, 290, 304), "Figure 1. (a) Structure and (b) capacity of the cathode.", 8)
            box(page, (50, 300, 290, 329), "3. Results and discussion", 10, True)
            box(page, (50, 318, 290, 614), LOREM)
            box(page, (305, 60, 545, 94), "Table 1. Electrochemical data.", 8)
            y = 90
            for row in (("Sample", "Capacity"), ("A", "150"), ("B", "170")):
                page.draw_rect(pymupdf.Rect(305, y, 545, y + 20))
                page.draw_line((425, y), (425, y + 20))
                page.insert_text((310, y + 14), row[0], fontsize=9)
                page.insert_text((430, y + 14), row[1], fontsize=9)
                y += 20
            box(page, (305, 170, 545, 199), "4. Conclusions", 10, True)
            box(page, (305, 188, 545, 314), "The cathode is promising for storage. " * 4)
        else:
            box(page, (50, 60, 545, 89), "Acknowledgments", 10, True)
            box(page, (50, 78, 545, 114), "This work was supported by the NSFC (grant 12345).")
            box(page, (50, 110, 545, 139), "References", 10, True)
            box(page, (50, 128, 545, 414),
                "[1] A. Author, B. Writer, Layered cathodes, J. Power Sources 10 (2019) 1-10. https://doi.org/10.1016/j.jps.2019.01.001\n"
                "[2] C. Author, Sodium storage, Nat. Energy 3 (2020) 55.\n[3] D. Author, Calculations of oxides, Phys. Rev. B 99 (2021) 12.", 8)
    return doc.tobytes()


def scanned_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(50, 50, 500, 700), fill=(0.9, 0.9, 0.9))
    return doc.tobytes()


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode()


def settle(client, paper_id: str) -> dict:
    """GROBID runs as a background job; wait for its manifest to settle."""
    for _ in range(200):
        manifest = client.post(f"/api/nodes/{paper_id}/resource/manifest", json={"arguments": {}}).json()
        if not manifest["extracting"]:
            return manifest
        time.sleep(0.05)
    raise AssertionError("extraction did not finish")


async def imported(services, paper_id: str, pdf: bytes | None = None) -> dict:
    await invoke_resource_action(services, paper_id, "import",
                                 ResourceActionRequest(arguments={"pdf": encoded(pdf or research_pdf())}))
    await services.resource_jobs.wait()
    return await invoke_resource_action(services, paper_id, "manifest", ResourceActionRequest())


# What GROBID 0.8 returns for research_pdf() with teiCoordinates and includeRawCitations.
TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:space="preserve">
<teiHeader xml:lang="en">
  <fileDesc>
    <titleStmt>
      <title level="a" type="main">High capacity layered oxide cathodes for sodium batteries</title>
      <funder ref="#_nsfc"><orgName type="full">National Natural Science Foundation of China</orgName></funder>
    </titleStmt>
    <publicationStmt><publisher>Elsevier BV</publisher><availability status="unknown"><licence/></availability>
      <date type="published" when="2024-05-20">20 May 2024</date></publicationStmt>
    <sourceDesc><biblStruct>
      <analytic>
        <author role="corresp"><persName><forename type="first">Alice</forename><surname>Zhang</surname></persName>
          <email>alice@test.edu</email><idno type="ORCID">0000-0002-1825-0097</idno>
          <affiliation key="aff0"><note type="raw_affiliation">Department of Materials Science, Test University, Beijing, China</note>
            <orgName type="department">Department of Materials Science</orgName><orgName type="institution">Test University</orgName>
            <address><settlement>Beijing</settlement><country key="CN">China</country></address></affiliation></author>
        <author><persName><forename type="first">Bob</forename><surname>Smith</surname></persName>
          <affiliation key="aff0"><orgName type="institution">Test University</orgName></affiliation></author>
        <author><persName><forename type="first">Carol</forename><surname>Lee</surname></persName></author>
        <author><affiliation key="aff1"><orgName>Unmatched Lab</orgName></affiliation></author>
      </analytic>
      <monogr><title level="j" type="main">Journal of Test Materials</title><idno type="ISSN">1234-5678</idno>
        <imprint><biblScope unit="volume">12</biblScope><biblScope unit="page" from="100" to="110"/>
          <date type="published" when="2024-05-20"/></imprint></monogr>
      <idno type="DOI">10.1016/j.test.2024.01.002</idno>
    </biblStruct></sourceDesc>
  </fileDesc>
  <profileDesc>
    <textClass><keywords><term>sodium battery</term><term>cathode</term><term>layered oxide</term></keywords></textClass>
    <abstract><div><p coords="1,50,212,495,100">We report a layered oxide cathode with high capacity.</p></div></abstract>
  </profileDesc>
</teiHeader>
<text xml:lang="en">
  <body>
    <div><head n="1." coords="1,50,330,100,14">Introduction</head>
      <p coords="1,50,348,240,260">Layered oxides were synthesized by a solid state route <ref type="bibr" target="#b0">[1]</ref>
        and agree with calculations <ref type="bibr" target="#b1 #b2">[2,3]</ref>.</p></div>
    <div><head n="2.">Methods</head><p>Samples were annealed.</p>
      <formula xml:id="formula_0" coords="1,305,400,200,20">E = mc 2 <label>(1)</label></formula></div>
    <div><head n="2.1.">Diffraction</head><p>XRD was collected.</p></div>
    <div><head n="3.">Results and discussion</head><p>The capacity is high.</p></div>
    <figure xml:id="fig_0" coords="2,60,70,220,180"><head>Figure 1 .</head><label>1</label>
      <figDesc>(a) Structure and (b) capacity of the cathode.</figDesc><graphic coords="2,60,70,220,180" type="bitmap"/></figure>
    <figure xml:id="fig_1"><head>Figure 2</head><figDesc>Not located.</figDesc></figure>
    <figure type="table" xml:id="tab_0" coords="2,305,60,240,90"><head>Table 1</head><label>1</label>
      <figDesc>Electrochemical data.</figDesc>
      <table><row><cell>Sample</cell><cell>Capacity</cell></row><row><cell>A</cell><cell>150</cell></row><row><cell>B</cell><cell>170</cell></row></table>
      <note>Measured at 0.1 C.</note></figure>
    <note place="foot" n="1">Equal contribution.</note>
  </body>
  <back>
    <div type="acknowledgement"><div><head>Acknowledgments</head><p>This work was supported by the NSFC (grant 12345).</p></div></div>
    <div type="annex">
      <div><head>Declaration of competing interest</head><p>The authors declare no competing interests.</p></div>
      <div><head>Appendix A. Extra data</head><p>More numbers.</p></div>
    </div>
    <div type="references"><listBibl>
      <biblStruct xml:id="b0" coords="3,50,128,495,20">
        <analytic><title level="a" type="main">Layered cathodes</title>
          <author><persName><forename type="first">A</forename><surname>Author</surname></persName></author>
          <author><persName><forename type="first">B</forename><surname>Writer</surname></persName></author>
          <idno type="DOI">10.1016/j.jps.2019.01.001</idno></analytic>
        <monogr><title level="j">J. Power Sources</title><imprint><biblScope unit="volume">10</biblScope>
          <biblScope unit="page" from="1" to="10"/><date type="published" when="2019"/></imprint></monogr>
        <note type="raw_reference">A. Author, B. Writer, Layered cathodes, J. Power Sources 10 (2019) 1-10.</note>
      </biblStruct>
      <biblStruct xml:id="b1"><analytic><title level="a">Sodium storage</title></analytic>
        <monogr><title level="j">Nat. Energy</title><imprint><date type="published" when="2020"/></imprint></monogr>
        <note type="raw_reference">C. Author, Sodium storage, Nat. Energy 3 (2020) 55.</note></biblStruct>
      <biblStruct xml:id="b2"><monogr><title level="m">Calculations of oxides</title><imprint><date>2021</date></imprint></monogr>
        <note type="raw_reference">D. Author, Calculations of oxides (2021).</note></biblStruct>
    </listBibl></div>
  </back>
</text>
</TEI>"""


class FakeGrobid:
    """Stands in for the GROBID HTTP service; ``down`` simulates an unreachable server."""

    def __init__(self):
        self.down = False
        self.requests: list[httpx.Request] = []
        self.gate = Event()  # Cleared by a test to hold GROBID "busy".
        self.gate.set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/version":
            return httpx.Response(200, text="0.8.2")
        assert request.url.path == "/api/processFulltextDocument" and request.method == "POST"
        assert self.gate.wait(10)
        return httpx.Response(200, text=TEI, headers={"content-type": "application/xml"})


@pytest.fixture(autouse=True)
def fake_grobid(monkeypatch):
    fake = FakeGrobid()
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(fake))
    return fake


def parse_research_pdf() -> tuple[dict, dict]:
    with pymupdf.open(stream=research_pdf(), filetype="pdf") as pdf:
        return grobid.from_tei(TEI, pdf, extractor="grobid/0.8.2")


@pytest.fixture
def services(tmp_path):
    instance = create_services(Settings.for_data_root(tmp_path / "profile"))
    yield instance
    instance.close()


# ---- Served node files and Paper files ------------------------------------------------------

def paper_files(services, node_id):
    return store.PaperFiles(services.resources.node_storage_path(node_id))


def test_served_files_match_whole_keys_only(tmp_path):
    for key in ("raw.pdf", "manifest.json", "extractions/v1.json", "extractions/v1/figures/f1.png", "text/pages.json"):
        store.PaperFiles(tmp_path).put(key, b"x")
    served = lambda key: served_file(tmp_path, key, store.SERVED_FILES)
    assert served("raw.pdf") == tmp_path / "raw.pdf"
    assert served("extractions/v1.json") and served("extractions/v1/figures/f1.png")
    # Unlisted files stay private, and '*' never crosses a '/'.
    for key in ("manifest.json", "text/pages.json", "extractions/v1/figures", "missing.pdf",
                "../raw.pdf", "a//b", "", "/raw.pdf", ".hidden", "x" * 200):
        assert served(key) is None
    assert served_file(tmp_path, "extractions/v1/figures/f1.png", ("extractions/*.png",)) is None
    assert all(map(served_file_pattern_valid, store.SERVED_FILES))
    assert not any(map(served_file_pattern_valid, ("../x", "a//b", "/abs", ".x")))


def test_paper_files_replace_atomically_and_clear(tmp_path):
    paper = store.PaperFiles(tmp_path / "paper")
    paper.put_json("extractions/v1.json", {"a": 1})
    paper.put_json("extractions/v1.json", {"a": 2})
    assert paper.get_json("extractions/v1.json") == {"a": 2}
    assert [path.name for path in (tmp_path / "paper" / "extractions").iterdir()] == ["v1.json"]
    paper.clear()
    assert not (tmp_path / "paper").exists() and not paper.exists("extractions/v1.json")


# ---- GROBID ---------------------------------------------------------------------------------

def test_grobid_tei_maps_onto_the_schema():
    structure, images = parse_research_pdf()
    metadata = structure["metadata"]
    assert metadata["title"] == "High capacity layered oxide cathodes for sodium batteries"
    # Affiliation-only <author> entries are not people.
    assert [author["name"] for author in metadata["authors"]] == ["Alice Zhang", "Bob Smith", "Carol Lee"]
    alice = metadata["authors"][0]
    assert (alice["email"], alice["orcid"], alice["corresponding"], alice["affiliation_ids"]) == (
        "alice@test.edu", "0000-0002-1825-0097", True, ["aff0"])
    assert metadata["affiliations"] == [{"id": "aff0", "text": "Department of Materials Science, Test University, Beijing, China", "country": "China"}]
    assert metadata["identifiers"]["doi"] == "10.1016/j.test.2024.01.002"
    assert metadata["venue"] == {"journal": "Journal of Test Materials", "volume": "12", "issue": None, "pages": "100-110",
                                 "publisher": "Elsevier BV", "issn": "1234-5678"}
    assert metadata["dates"]["published"] == "2024-05-20" and metadata["language"] == "en" and metadata["license"] is None
    assert metadata["keywords"] == ["sodium battery", "cathode", "layered oxide"]
    assert structure["abstract"]["text"] == "We report a layered oxide cathode with high capacity."
    assert structure["abstract"]["loc"]["page"] == 1
    assert [(section["number"], section["heading"], section["level"]) for section in structure["sections"]] == [
        ("1", "Introduction", 1), ("2", "Methods", 1), ("2.1", "Diffraction", 2), ("3", "Results and discussion", 1)]
    intro = structure["sections"][0]["blocks"][0]
    assert intro["citations"] == ["r1", "r2", "r3"] and intro["loc"]["page"] == 1
    assert 0 <= intro["loc"]["bbox"][0] < intro["loc"]["bbox"][2] <= 1
    equation = structure["sections"][1]["blocks"][1]
    assert (equation["type"], equation["text"], equation["number"]) == ("equation", "E = mc 2", "1")
    first, second = structure["figures"]
    assert (first["label"], first["caption"], first["image"], first["loc"]["page"]) == (
        "Figure 1 .", "(a) Structure and (b) capacity of the cathode.", "figures/f1.png", 2)
    assert images["figures/f1.png"].startswith(b"\x89PNG") and second["image"] is None
    table = structure["tables"][0]
    assert (table["label"], table["caption"], table["rows"], table["footnotes"]) == (
        "Table 1", "Electrochemical data.", [["Sample", "Capacity"], ["A", "150"], ["B", "170"]], ["Measured at 0.1 C."])
    back = structure["back_matter"]
    assert back["acknowledgments"].startswith("This work was supported")
    assert back["conflicts"] == "The authors declare no competing interests."
    assert [section["heading"] for section in back["appendices"]] == ["Appendix A. Extra data"]
    assert back["funding"] == [{"agency": "National Natural Science Foundation of China", "grant": None}]
    assert [(ref["id"], ref["title"], ref["venue"], ref["year"], ref["pages"], ref["doi"]) for ref in structure["references"]] == [
        ("r1", "Layered cathodes", "J. Power Sources", 2019, "1-10", "10.1016/j.jps.2019.01.001"),
        ("r2", "Sodium storage", "Nat. Energy", 2020, None, None),
        ("r3", "Calculations of oxides", None, 2021, None, None)]
    assert structure["references"][0]["raw"].startswith("A. Author, B. Writer")
    assert structure["references"][0]["authors"] == ["A Author", "B Writer"]
    assert structure["footnotes"] == ["Equal contribution."]
    provenance = structure["provenance"]
    assert provenance["extractor"] == "grobid/0.8.2"
    assert any("Figure 2" in warning for warning in provenance["warnings"])


def test_grobid_request_and_failures(fake_grobid, monkeypatch):
    with pymupdf.open(stream=research_pdf(), filetype="pdf") as pdf:
        structure, _ = grobid.extract(b"%PDF-1.7", pdf, filename="a.pdf")
    assert structure["provenance"]["extractor"] == "grobid/0.8.2"
    request = fake_grobid.requests[0]
    assert str(request.url) == "http://localhost:8070/api/processFulltextDocument"
    body = request.content.decode(errors="replace")
    assert 'name="input"; filename="a.pdf"' in body and 'name="includeRawCitations"' in body
    assert body.count('name="teiCoordinates"') == len(grobid.COORDINATES)
    # Consolidation would send metadata to external services; it is opt-in.
    assert 'name="consolidateHeader"\r\n\r\n0' in body

    fake_grobid.down = True
    monkeypatch.setenv("OAW_GROBID_URL", "http://grobid.internal:8070/")
    with pytest.raises(grobid.ExtractionError, match="Cannot reach GROBID at http://grobid.internal:8070"):
        grobid.request_tei(b"%PDF")
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(lambda request: httpx.Response(500, text="boom")))
    with pytest.raises(grobid.ExtractionError, match="HTTP 500: boom"):
        grobid.request_tei(b"%PDF")
    monkeypatch.setattr(grobid, "_transport", httpx.MockTransport(lambda request: httpx.Response(204)))
    with pytest.raises(grobid.ExtractionError, match="no content"):
        grobid.request_tei(b"%PDF")
    with pytest.raises(grobid.ExtractionError, match="invalid TEI"):
        grobid.from_tei("<TEI", None, extractor="grobid/x")


def test_revisions_validate_and_are_attributed():
    structure, _ = parse_research_pdf()
    change = lambda **kwargs: store.Change(**kwargs)
    revised = store.apply_changes(structure, [
        change(op="set", path="metadata.authors.1.name", value="Robert Smith"),
        change(op="set", path="domain.materials", value=[{"formula": "NaNi0.5Mn0.5O2"}]),
        change(op="append", path="metadata.keywords", value="XRD"),
    ], source="agent")
    assert revised["metadata"]["authors"][1]["name"] == "Robert Smith"
    assert revised["domain"]["materials"][0]["formula"] == "NaNi0.5Mn0.5O2"
    assert revised["metadata"]["keywords"][-1] == "XRD"
    assert revised["provenance"]["extractor"] == "grobid/0.8.2+agent"
    assert structure["metadata"]["authors"][1]["name"] == "Bob Smith"  # The base version is untouched.
    for bad in (change(op="set", path="metadata.nope", value=1), change(op="set", path="provenance.warnings", value=[]),
                change(op="set", path="sections.99.heading", value="x"), change(op="remove", path="metadata.title"),
                change(op="set", path="metadata.authors", value="not a list"),
                change(op="set", path="metadata.title.text", value="x"), change(op="set", path="sections.0.number.x", value=1),
                change(op="remove", path="domain.note.0")):
        with pytest.raises(ResourceValidationError):
            store.apply_changes(dict(structure, domain={"note": "text"}), [bad], source="agent")
    with pytest.raises(ValueError):
        store.Change(op="resolve", path="metadata.title")


# ---- Paper card: HTTP, agents and lifecycle -------------------------------------------------

def test_import_serves_objects_and_versions_extractions(client):
    paper = create_node(client, "library.paper")
    url = f"/api/nodes/{paper['id']}"
    pdf = research_pdf()
    response = client.post(url + "/resource/import", json={"arguments": {"filename": "cathode.pdf", "pdf": encoded(pdf)}})
    assert response.status_code == 200, response.text
    # The request returns once the PDF is stored; GROBID finishes in the background.
    assert response.json()["extracting"] is True and response.json()["active"] is None
    manifest = settle(client, paper["id"])
    assert (manifest["filename"], manifest["pages"], manifest["active"], manifest["extraction_error"]) == ("cathode.pdf", 3, "v1", None)
    assert client.post(url + "/resource/import", json={"arguments": {"pdf": encoded(research_pdf())}}).status_code == 409
    # The reader document stays small: no PDF bytes, only the reading layer.
    assert set(client.get(url + "/document").json()["value"]) == {"page", "notes", "annotations", "study_layout", "study_title"}
    raw = client.get(url + "/files/raw.pdf")
    assert raw.status_code == 200 and raw.content == pdf and raw.headers["content-type"] == "application/pdf"
    assert client.get(url + "/files/raw.pdf", headers={"If-None-Match": raw.headers["etag"]}).status_code == 304
    assert client.get(url + "/files/missing.json").status_code == 404
    assert client.get(url + "/files/manifest.json").status_code == 404  # Present, but not served.
    assert client.get(url + "/files/..%2Fescape").status_code == 404
    preview = client.get(f"/api/library/papers/{paper['id']}/preview").json()["value"]
    assert preview["pages"] == 3 and preview["extraction"]["id"] == "v1"
    assert client.get(preview["thumbnail"]).content.startswith(b"\x89PNG")
    overview = client.post(url + "/resource/structure", json={"arguments": {}}).json()
    assert overview["metadata"]["title"].startswith("High capacity") and len(overview["sections"]) == 4
    assert overview["extracting"] is False and any(section["loc"] for section in overview["sections"])
    figure = client.post(url + "/resource/structure", json={"arguments": {"path": "figures.0"}}).json()["value"]
    assert figure["image"] == "extractions/v1/figures/f1.png"
    assert client.get(f"{url}/files/{figure['image']}").content.startswith(b"\x89PNG")
    download = client.get(url + "/files/extractions/v1.json?download=cathode.json")
    assert "cathode.json" in download.headers["content-disposition"] and download.json()["schema_version"] == "1.0"
    # A person's revision, re-extraction and activation all create or select immutable versions.
    revised = client.post(url + "/resource/revise", json={"arguments": {"base_version": "v1",
        "changes": [{"op": "set", "path": "metadata.venue.journal", "value": "Journal of Test Materials"}]}})
    assert revised.status_code == 200, revised.text
    assert client.post(url + "/resource/revise", json={"arguments": {"base_version": "v1",
        "changes": [{"op": "append", "path": "metadata.keywords", "value": "x"}]}}).status_code == 409
    assert client.post(url + "/resource/extract", json={"arguments": {}}).json()["extracting"] is True
    again = settle(client, paper["id"])
    assert [item["source"] for item in again["extractions"]] == ["grobid", "user", "grobid"] and again["active"] == "v3"
    assert client.post(url + "/resource/activate", json={"arguments": {"version": "v2"}}).json()["active"] == "v2"
    journal = client.post(url + "/resource/structure", json={"arguments": {"path": "metadata.venue.journal"}}).json()
    assert journal == {"version": "v2", "path": "metadata.venue.journal", "value": "Journal of Test Materials"}


def test_import_rejects_invalid_and_keeps_pdfs_grobid_cannot_parse(client, fake_grobid):
    paper = create_node(client, "library.paper")
    url = f"/api/nodes/{paper['id']}/resource/"
    assert client.post(url + "import", json={"arguments": {"pdf": encoded(b"not a PDF")}}).status_code == 422
    assert client.post(url + "import", json={"arguments": {"pdf": "%%%"}}).status_code == 422
    assert client.post(url + "structure", json={"arguments": {}}).status_code == 422
    # Scanned: imported and readable, but GROBID is not asked to guess without a text layer.
    scanned = client.post(url + "import", json={"arguments": {"pdf": encoded(scanned_pdf())}})
    assert scanned.status_code == 200 and scanned.json()["active"] is None
    assert "text layer" in scanned.json()["extraction_error"]["message"] and fake_grobid.requests == []

    # GROBID down: the PDF is still imported; the structure can be extracted once it is back.
    fake_grobid.down = True
    other = create_node(client, "library.paper")
    url = f"/api/nodes/{other['id']}/"
    assert client.post(url + "resource/import", json={"arguments": {"pdf": encoded(research_pdf())}}).status_code == 200
    down = settle(client, other["id"])
    assert down["active"] is None and "Cannot reach GROBID" in down["extraction_error"]["message"]
    assert client.get(url + "files/raw.pdf").status_code == 200
    preview = client.get(f"/api/library/papers/{other['id']}/preview").json()["value"]
    assert preview["pages"] == 3 and preview["extraction"] is None
    missing = client.post(url + "resource/structure", json={"arguments": {}})
    assert missing.status_code == 422 and "Cannot reach GROBID" in missing.text
    fake_grobid.down = False
    client.post(url + "resource/extract", json={"arguments": {}})
    again = settle(client, other["id"])
    assert again["active"] == "v1" and again["extraction_error"] is None


@pytest.mark.asyncio
async def test_agent_tools_follow_live_relationships(services, fake_grobid):
    paper = await services.create_card(CardCreate(type="library.paper"))
    await imported(services, paper.id)
    agent = await services.create_card(CardCreate(type="agent"))
    provider = WorldAgentCapabilityProvider(services)
    edge = await services.create_edge(EdgeCreate(source=agent.id, target=paper.id, relationship="library.read"))
    tools = {tool.name: tool for tool in await provider.list_tools(agent.id)}
    assert {"read_paper", "read_paper_structure"} <= tools.keys() and not {"revise_paper_structure", "reextract_paper"} & tools.keys()
    page = await provider.invoke_tool(agent.id, tools["read_paper"].capability_id, {"target": paper.id, "page": 2})
    assert "Results and discussion" in page["text"] and page["notes"] == ""
    overview = await provider.invoke_tool(agent.id, tools["read_paper_structure"].capability_id, {"target": paper.id})
    assert overview["version"] == "v1" and overview["extractor"] == "grobid/0.8.2"
    title = {"op": "set", "path": "metadata.subtitle", "value": "A study"}
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, f"library.curate:{paper.id}", {"base_version": "v1", "changes": [title]})

    await services.delete_edge(edge.id)
    edge = await services.create_edge(EdgeCreate(source=agent.id, target=paper.id, relationship="library.curate"))
    revise = next(tool for tool in await provider.list_tools(agent.id) if tool.name == "revise_paper_structure")
    assert "$ref" not in str(revise.input_schema)
    result = await provider.invoke_tool(agent.id, revise.capability_id, {"target": paper.id, "base_version": "v1", "note": "Checked authors",
        "changes": [title, {"op": "set", "path": "metadata.article_type", "value": "research"}]})
    assert result["version"] == "v2"
    manifest = await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest())
    assert manifest["extractions"][-1] | {"created_at": None} == manifest["extractions"][-1] | {
        "created_at": None, "source": "agent", "actor_id": agent.id, "based_on": "v1", "note": "Checked authors"}
    with pytest.raises(ConflictError):
        await provider.invoke_tool(agent.id, revise.capability_id, {"target": paper.id, "base_version": "v1", "changes": [title]})
    # Curating Agents may also rerun GROBID; the new version becomes active in the background.
    rerun = next(tool for tool in await provider.list_tools(agent.id) if tool.name == "reextract_paper")
    fake_grobid.gate.clear()
    assert (await provider.invoke_tool(agent.id, rerun.capability_id, {"target": paper.id}))["extracting"] is True
    with pytest.raises(ConflictError):
        await provider.invoke_tool(agent.id, rerun.capability_id, {"target": paper.id})
    fake_grobid.gate.set()
    await services.resource_jobs.wait()
    manifest = await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest())
    assert (manifest["active"], manifest["extracting"], manifest["extractions"][-1]["source"]) == ("v3", False, "grobid")
    # Import and activation stay with people; the people's re-extract action is not an Agent tool.
    capability = services.capabilities.capability_for_id(agent.id, f"library.curate:{paper.id}")
    for action in ("import", "extract", "activate"):
        with pytest.raises(PermissionDeniedError):
            await invoke_resource_action(services, paper.id, action, ResourceActionRequest(), capability=capability)
    await services.delete_edge(edge.id)
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, revise.capability_id, {"target": paper.id, "base_version": "v2", "changes": [title]})


@pytest.mark.asyncio
async def test_deleting_a_paper_removes_its_objects(services):
    paper = await services.create_card(CardCreate(type="library.paper"))
    other = await services.create_card(CardCreate(type="library.paper"))
    for node in (paper, other):
        await imported(services, node.id)
    assert paper_files(services, paper.id).exists("raw.pdf")
    await services.delete_card(paper.id)
    assert not paper_files(services, paper.id).root.exists()
    assert paper_files(services, other.id).exists("raw.pdf")


@pytest.mark.asyncio
async def test_extraction_runs_outside_the_graph_lock(services, fake_grobid):
    paper = await services.create_card(CardCreate(type="library.paper"))
    fake_grobid.gate.clear()  # GROBID is "busy" until released.
    started = await invoke_resource_action(services, paper.id, "import",
                                           ResourceActionRequest(arguments={"pdf": encoded(research_pdf())}))
    assert started["extracting"] is True and started["pages"] == 3
    # Other graph mutations and reads proceed while GROBID works.
    other = await services.create_card(CardCreate(type="library.paper"))
    page = await invoke_resource_action(services, paper.id, "page_text", ResourceActionRequest(arguments={"page": 1}))
    assert page["text"]
    with pytest.raises(ResourceValidationError, match="still extracting"):
        await invoke_resource_action(services, paper.id, "structure", ResourceActionRequest())
    with pytest.raises(ConflictError):
        await invoke_resource_action(services, paper.id, "extract", ResourceActionRequest())
    fake_grobid.gate.set()
    await services.resource_jobs.wait()
    manifest = await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest())
    assert (manifest["active"], manifest["extracting"], manifest["job"]) == ("v1", False, None)

    # A Paper deleted mid-extraction: the late result is dropped, not written into cleared storage.
    fake_grobid.gate.clear()
    await invoke_resource_action(services, other.id, "import", ResourceActionRequest(arguments={"pdf": encoded(research_pdf())}))
    await services.delete_card(other.id)
    fake_grobid.gate.set()
    await services.resource_jobs.wait()
    assert not paper_files(services, other.id).root.exists()


@pytest.mark.asyncio
async def test_unknown_jobs_show_as_interrupted(services):
    paper = await services.create_card(CardCreate(type="library.paper"))
    await imported(services, paper.id)
    # As after a restart: the manifest names a job this process never started.
    paper_ = paper_files(services, paper.id)
    manifest = paper_.get_json("manifest.json")
    paper_.put_json("manifest.json", {**manifest, "job": {"id": "lost", "started_at": store.now()}})
    shown = await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest())
    assert shown["extracting"] is False and "interrupted" in shown["extraction_error"]["message"]
    assert (await invoke_resource_action(services, paper.id, "extract", ResourceActionRequest()))["extracting"] is True
    await services.resource_jobs.wait()
    assert (await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest()))["active"] == "v2"


@pytest.mark.asyncio
async def test_a_lost_commit_does_not_leave_the_job_running(services, fake_grobid, monkeypatch):
    paper = await services.create_card(CardCreate(type="library.paper"))
    fake_grobid.gate.clear()
    await invoke_resource_action(services, paper.id, "import", ResourceActionRequest(arguments={"pdf": encoded(research_pdf())}))
    # A pending deletion elsewhere blocks world writes when GROBID finishes.
    def blocked(self):
        raise ConflictError("world mutations are blocked")
    with monkeypatch.context() as patch:
        patch.setattr(type(services), "_assert_no_live_pending_deletions", blocked)
        fake_grobid.gate.set()
        await services.resource_jobs.wait()
    shown = await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest())
    assert shown["extracting"] is False and "interrupted" in shown["extraction_error"]["message"]
    assert (await invoke_resource_action(services, paper.id, "extract", ResourceActionRequest()))["extracting"] is True
    await services.resource_jobs.wait()
    assert (await invoke_resource_action(services, paper.id, "manifest", ResourceActionRequest()))["active"] == "v1"


def test_store_works_on_a_bare_resource_context(tmp_path):
    context = NodeResourceContext("paper", tmp_path / "paper", Event())
    manifest = store.import_pdf(context, {"filename": "a.pdf", "pdf": encoded(research_pdf())})
    assert store.page_text(context, {"page": 1})["text"].startswith("Journal of Test Materials")
    with pytest.raises(ResourceValidationError):
        store.page_text(context, {"page": manifest["pages"] + 1})
