"""PDF -> PaperStructure through a GROBID service.

GROBID (https://github.com/kermitt2/grobid) does the parsing; this module only
calls ``/api/processFulltextDocument`` and maps its TEI onto the schema. Figure
crops are cut from the PDF with PyMuPDF using GROBID's coordinates.

Configuration is per host, not per Paper:

    OAW_GROBID_URL          service root, default http://localhost:8070
    OAW_GROBID_TIMEOUT      seconds per request, default 180
    OAW_GROBID_CONSOLIDATE  1 lets GROBID look up header and citation metadata
                            (CrossRef / biblio-glutton); off by default
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET

import httpx
import pymupdf

from .schema import SCHEMA_VERSION, PaperStructure

TEI = "{http://www.tei-c.org/ns/1.0}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
COORDINATES = ("head", "p", "figure", "formula", "biblStruct", "note")
_transport: httpx.BaseTransport | None = None  # Tests substitute a mock transport.


class ExtractionError(Exception):
    """GROBID could not produce a structure; the message is shown to people and Agents."""


def service_url() -> str:
    return os.environ.get("OAW_GROBID_URL", "http://localhost:8070").rstrip("/")


def _client() -> httpx.Client:
    timeout = float(os.environ.get("OAW_GROBID_TIMEOUT", "180"))
    return httpx.Client(base_url=service_url(), timeout=httpx.Timeout(timeout, connect=5), transport=_transport)


def request_tei(raw: bytes, filename: str = "paper.pdf") -> tuple[str, str]:
    """Return (TEI XML, GROBID version)."""
    consolidate = "1" if os.environ.get("OAW_GROBID_CONSOLIDATE", "0") == "1" else "0"
    data = {"consolidateHeader": consolidate, "consolidateCitations": consolidate, "consolidateFunders": consolidate,
            "includeRawCitations": "1", "segmentSentences": "0", "teiCoordinates": list(COORDINATES)}
    try:
        with _client() as client:
            for attempt in range(3):
                response = client.post("/api/processFulltextDocument", data=data,
                                       files={"input": (filename, raw, "application/pdf")})
                if response.status_code != 503:  # 503: every GROBID worker is busy.
                    break
                time.sleep(2 * (attempt + 1))
            if response.status_code == 204:
                raise ExtractionError("GROBID found no content in this PDF")
            if response.status_code != 200:
                raise ExtractionError(f"GROBID returned HTTP {response.status_code}: {response.text[:300]}")
            tei = response.text
            try:
                version = client.get("/api/version").text.strip()
                version = re.sub(r"[^0-9A-Za-z.+-]", "", version)[:40] or "unknown"
            except httpx.HTTPError:
                version = "unknown"
    except httpx.TimeoutException:
        raise ExtractionError(f"GROBID at {service_url()} did not answer in time") from None
    except httpx.HTTPError as error:
        raise ExtractionError(f"Cannot reach GROBID at {service_url()} ({type(error).__name__}). "
                              "Start it, e.g. docker run -p 8070:8070 grobid/grobid:0.8.2-crf, "
                              "or set OAW_GROBID_URL.") from None
    return tei, version


def extract(raw: bytes, document, *, filename: str = "paper.pdf") -> tuple[dict, dict[str, bytes]]:
    """Return (PaperStructure JSON, {object key: PNG bytes} for figure crops)."""
    tei, version = request_tei(raw, filename)
    return from_tei(tei, document, extractor=f"grobid/{version}")


# ---- TEI -> PaperStructure ----------------------------------------------------------------------

def _text(element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip() if element is not None else ""


def _opt(element) -> str | None:
    return _text(element) or None


def _find(element, path: str):
    return element.find(path.replace("tei:", TEI)) if element is not None else None


def _findall(element, path: str) -> list:
    return element.findall(path.replace("tei:", TEI)) if element is not None else []


def _boxes(element) -> list[tuple[int, float, float, float, float]]:
    """GROBID coords: 'page,x,y,w,h;...' in PDF points from the top-left corner."""
    result = []
    for part in (element.get("coords") or "").split(";"):
        try:
            page, x, y, w, h = part.split(",")
            result.append((int(page), float(x), float(y), float(w), float(h)))
        except ValueError:
            continue
    return result


class _Pages:
    def __init__(self, document):
        self.document = document
        self.sizes = [(page.rect.width, page.rect.height) for page in document]

    def rect(self, element) -> tuple[int, pymupdf.Rect] | None:
        boxes = _boxes(element)
        if not boxes or not 1 <= boxes[0][0] <= len(self.sizes):
            return None
        page = boxes[0][0]
        rects = [pymupdf.Rect(x, y, x + w, y + h) for number, x, y, w, h in boxes if number == page]
        rect = rects[0]
        for other in rects[1:]:
            rect |= other
        return page, rect

    def loc(self, element) -> dict | None:
        found = self.rect(element)
        if found is None:
            return None
        page, rect = found
        width, height = self.sizes[page - 1]
        clamp = lambda value: round(min(1.0, max(0.0, value)), 4)
        return {"page": page, "bbox": [clamp(rect.x0 / width), clamp(rect.y0 / height), clamp(rect.x1 / width), clamp(rect.y1 / height)]}

    def crop(self, element) -> bytes | None:
        found = self.rect(element)
        if found is None:
            return None
        page, rect = found
        rect &= self.document[page - 1].rect
        if rect.is_empty or rect.width < 20 or rect.height < 20:
            return None
        return self.document[page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=rect).tobytes("png")


def _person(element) -> str:
    pers = _find(element, "tei:persName") if element.tag != f"{TEI}persName" else element
    if pers is None:
        return ""
    names = [_text(item) for item in _findall(pers, "tei:forename")] + [_text(item) for item in _findall(pers, "tei:surname")]
    return " ".join(name for name in names if name) or _text(pers)


def _reference_id(xml_id: str | None) -> str | None:
    match = re.fullmatch(r"b(\d+)", xml_id or "")
    return f"r{int(match.group(1)) + 1}" if match else None


def _citations(element) -> list[str]:
    ids = []
    for ref in element.iter(f"{TEI}ref"):
        if ref.get("type") == "bibr":
            for target in (ref.get("target") or "").split():
                if (rid := _reference_id(target.lstrip("#"))) and rid not in ids:
                    ids.append(rid)
    return ids


def _blocks(div, pages: _Pages) -> list[dict]:
    blocks = []
    for child in div:
        tag = child.tag.removeprefix(TEI)
        if tag == "p":
            blocks.append({"type": "paragraph", "text": _text(child), "citations": _citations(child), "loc": pages.loc(child)})
        elif tag == "formula":
            label = _find(child, "tei:label")
            number = _text(label).strip("()") or None
            text = re.sub(r"\s+", " ", (child.text or "") + "".join(
                (item.text or "") + (item.tail or "") if item is not label else (item.tail or "") for item in child)).strip()
            blocks.append({"type": "equation", "text": text or _text(child), "number": number, "loc": pages.loc(child)})
        elif tag == "list":
            items = [_text(item) for item in _findall(child, "tei:item")]
            blocks.append({"type": "list", "text": "\n".join(f"• {item}" for item in items if item), "loc": pages.loc(child)})
    return [block for block in blocks if block["text"]]


def _section(div, pages: _Pages, index: int) -> dict:
    head = _find(div, "tei:head")
    number = (head.get("n") or "").rstrip(".") or None if head is not None else None
    level = min(6, number.count(".") + 1) if number and re.fullmatch(r"\d+(\.\d+)*", number) else 1
    return {"id": f"s{index}", "level": level, "number": number, "heading": _text(head),
            "blocks": _blocks(div, pages), "loc": pages.loc(head) if head is not None else None}


# Back-matter headings GROBID leaves in annex divs -> schema fields. Anything else is an appendix.
_BACK = (
    (r"acknowledg", "acknowledgments"), (r"fund|financial support", "funding_statement"),
    (r"contribution", "author_contributions"), (r"conflict|competing|declaration of interest|disclosure", "conflicts"),
    (r"data availab|availability of data", "data_availability"), (r"code availab", "code_availability"),
    (r"ethic", "ethics"), (r"abbreviation|nomenclature", "abbreviations"),
)
_BACK_TYPES = {"acknowledgement": "acknowledgments", "funding": "funding_statement", "availability": "data_availability"}


def _reference(struct, pages: _Pages, rid: str) -> dict:
    analytic, monogr = _find(struct, "tei:analytic"), _find(struct, "tei:monogr")
    authors = [_person(author) for author in _findall(analytic, "tei:author") or _findall(monogr, "tei:author")]
    title = _find(analytic, "tei:title") if analytic is not None else None
    if title is None:
        title = _find(monogr, "tei:title[@level='m']")
    venue = None
    if analytic is not None:  # Elements without children are falsy; never chain them with ``or``.
        venue = _find(monogr, "tei:title[@level='j']")
        if venue is None:
            venue = _find(monogr, "tei:title[@level='m']")
    imprint = _find(monogr, "tei:imprint")
    year = None
    date = _find(imprint, "tei:date")
    if date is not None and (match := re.match(r"(\d{4})", date.get("when") or _text(date))):
        year = int(match.group(1))
    scope = lambda unit: _find(imprint, f"tei:biblScope[@unit='{unit}']")
    pages_scope = scope("page")
    page_range = None
    if pages_scope is not None:
        page_range = "-".join(value for value in (pages_scope.get("from"), pages_scope.get("to")) if value) or _opt(pages_scope)
    doi = next((_text(idno) for idno in struct.iter(f"{TEI}idno") if (idno.get("type") or "").upper() == "DOI"), None)
    raw = _find(struct, "tei:note[@type='raw_reference']")
    return {"id": rid, "raw": _text(raw) or _text(struct), "authors": [name for name in authors if name],
            "title": _opt(title), "venue": _opt(venue), "year": year, "volume": _opt(scope("volume")),
            "pages": page_range, "doi": doi, "loc": pages.loc(struct)}


def from_tei(tei: str, document, *, extractor: str) -> tuple[dict, dict[str, bytes]]:
    try:
        root = ET.fromstring(tei.encode())
    except ET.ParseError as error:
        raise ExtractionError(f"GROBID returned invalid TEI: {error}") from None
    pages = _Pages(document)
    header = _find(root, "tei:teiHeader")
    source = _find(header, "tei:fileDesc/tei:sourceDesc/tei:biblStruct")
    analytic, monogr = _find(source, "tei:analytic"), _find(source, "tei:monogr")
    publication = _find(header, "tei:fileDesc/tei:publicationStmt")
    warnings: list[str] = []

    # Metadata.
    affiliations: dict[str, dict] = {}
    authors = []
    for author in _findall(analytic, "tei:author"):
        name = _person(author)
        if not name:
            continue  # GROBID emits affiliation-only author entries for unmatched affiliations.
        ids = []
        for affiliation in _findall(author, "tei:affiliation"):
            key = affiliation.get("key") or f"aff{len(affiliations)}"
            if key not in affiliations:
                orgs = [_text(org) for org in _findall(affiliation, "tei:orgName")]
                raw = _find(affiliation, "tei:note[@type='raw_affiliation']")
                affiliations[key] = {"id": key, "text": _text(raw) or ", ".join(org for org in orgs if org),
                                     "country": _opt(_find(affiliation, "tei:address/tei:country"))}
            ids.append(key)
        orcid = next((_text(idno) for idno in _findall(author, "tei:idno") if (idno.get("type") or "").upper() == "ORCID"), None)
        authors.append({"name": name, "orcid": orcid, "affiliation_ids": ids,
                        "corresponding": author.get("role") == "corresp", "email": _opt(_find(author, "tei:email"))})
    identifiers = {"doi": None, "arxiv": None, "pmid": None}
    for idno in _findall(source, "tei:idno") + _findall(analytic, "tei:idno") + _findall(monogr, "tei:idno"):
        kind = (idno.get("type") or "").lower()
        if kind in identifiers and identifiers[kind] is None:
            identifiers[kind] = _text(idno)
    imprint = _find(monogr, "tei:imprint")
    scope = lambda unit: _find(imprint, f"tei:biblScope[@unit='{unit}']")
    page_scope = scope("page")
    pages_text = None
    if page_scope is not None:
        pages_text = "-".join(value for value in (page_scope.get("from"), page_scope.get("to")) if value) or _opt(page_scope)
    published = _find(publication, "tei:date[@type='published']")
    if published is None:
        published = _find(imprint, "tei:date[@type='published']")
    issn = next((_text(idno) for idno in _findall(monogr, "tei:idno") if (idno.get("type") or "").lower() in {"issn", "eissn"}), None)
    main_title = _find(header, "tei:fileDesc/tei:titleStmt/tei:title[@type='main']")
    if main_title is None:
        main_title = _find(header, "tei:fileDesc/tei:titleStmt/tei:title")
    metadata = {
        "title": _opt(main_title), "authors": authors, "affiliations": list(affiliations.values()),
        "venue": {"journal": _opt(_find(monogr, "tei:title[@level='j']")), "volume": _opt(scope("volume")),
                  "issue": _opt(scope("issue")), "pages": pages_text, "publisher": _opt(_find(publication, "tei:publisher")), "issn": issn},
        "identifiers": identifiers,
        "dates": {"published": (published.get("when") or _text(published)) if published is not None else None},
        "keywords": [_text(term) for term in _findall(header, "tei:profileDesc/tei:textClass/tei:keywords/tei:term") if _text(term)],
        "license": _opt(_find(publication, "tei:availability/tei:licence")),
        "language": header.get(XML_LANG) if header is not None else None,
    }

    # Abstract: plain paragraphs, or labelled parts for structured abstracts.
    abstract_element = _find(header, "tei:profileDesc/tei:abstract")
    labelled = [{"heading": _text(_find(div, "tei:head")), "text": " ".join(_text(p) for p in _findall(div, "tei:p"))}
                for div in _findall(abstract_element, ".//tei:div") if _find(div, "tei:head") is not None]
    paragraphs = [p for p in abstract_element.iter(f"{TEI}p")] if abstract_element is not None else []
    abstract = {"text": "\n\n".join(_text(p) for p in paragraphs if _text(p)), "sections": [item for item in labelled if item["text"]],
                "loc": pages.loc(paragraphs[0]) if paragraphs else None}

    # Body.
    text = _find(root, "tei:text")
    body, back = _find(text, "tei:body"), _find(text, "tei:back")
    sections = [_section(div, pages, index) for index, div in enumerate(_findall(body, "tei:div"))]
    images: dict[str, bytes] = {}
    figures, tables = [], []
    for element in text.iter(f"{TEI}figure") if text is not None else ():
        head, label = _find(element, "tei:head"), _find(element, "tei:label")
        caption = _text(_find(element, "tei:figDesc"))
        if element.get("type") == "table":
            rows = [[_text(cell) for cell in _findall(row, "tei:cell")] for row in _findall(element, "tei:table/tei:row")]
            tables.append({"id": f"t{len(tables) + 1}", "label": _text(head) or (f"Table {_text(label)}" if _text(label) else ""),
                           "caption": caption, "rows": [row for row in rows if any(row)],
                           "footnotes": [_text(note) for note in _findall(element, "tei:note") if _text(note)], "loc": pages.loc(element)})
        else:
            key = f"figures/f{len(figures) + 1}.png"
            graphic = _find(element, "tei:graphic")
            crop = pages.crop(graphic if graphic is not None and graphic.get("coords") else element)
            if crop:
                images[key] = crop
            figures.append({"id": f"f{len(figures) + 1}", "label": _text(head) or (f"Figure {_text(label)}" if _text(label) else ""),
                            "caption": caption, "image": key if crop else None, "loc": pages.loc(element)})

    # Back matter and references.
    back_matter: dict = {"funding": [], "appendices": []}
    divs = []
    for div in _findall(back, "tei:div"):
        kind = div.get("type")
        if kind == "references":
            continue
        divs.extend((kind, item) for item in _findall(div, "tei:div") or [div])
    for kind, div in divs:
        heading = _text(_find(div, "tei:head"))
        field = _BACK_TYPES.get(kind) or next((name for pattern, name in _BACK if re.search(pattern, heading, re.I)), None)
        content = "\n\n".join(_text(p) for p in _findall(div, "tei:p") if _text(p))
        if field and content:
            back_matter[field] = "\n\n".join(filter(None, [back_matter.get(field), content]))
        elif content or heading:
            back_matter["appendices"].append(_section(div, pages, len(back_matter["appendices"])))
    for funder in _findall(header, "tei:fileDesc/tei:titleStmt/tei:funder"):
        agency = _text(_find(funder, "tei:orgName[@type='full']")) or _text(funder)
        if agency:
            back_matter["funding"].append({"agency": agency})
    references = []
    for index, struct in enumerate(_findall(back, ".//tei:listBibl/tei:biblStruct")):
        references.append(_reference(struct, pages, _reference_id(struct.get(XML_ID)) or f"r{index + 1}"))
    footnotes = [_text(note) for note in (text.iter(f"{TEI}note") if text is not None else ())
                 if note.get("place") == "foot" and _text(note)]

    if not sections:
        warnings.append("GROBID found no body sections.")
    if any(block["type"] == "equation" for section in sections for block in section["blocks"]):
        warnings.append("Equations are kept as text; LaTeX is not reconstructed.")
    if tables:
        warnings.append("GROBID does not mark table header rows; every row is in rows.")
    missing = [f for f, crop in ((figure["label"] or figure["id"], figure["image"]) for figure in figures) if not crop]
    if missing:
        warnings.append(f"No image crop for {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}.")
    structure = {"schema_version": SCHEMA_VERSION, "metadata": metadata, "abstract": abstract, "sections": sections,
                 "figures": figures, "tables": tables, "back_matter": back_matter, "references": references,
                 "footnotes": footnotes, "domain": {}, "provenance": {"extractor": extractor, "warnings": warnings}}
    return PaperStructure.model_validate(structure).model_dump(mode="json"), images


__all__ = ["ExtractionError", "extract", "from_tei", "request_tei", "service_url"]
