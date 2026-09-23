# Library

Paper cards for reading PDFs and turning them into structured JSON.

## Raw PDF vs. structured content

A Paper keeps two layers apart:

| Layer | Where | Changes |
| --- | --- | --- |
| Raw | Files in the node's `storage_path`: `raw.pdf`, `thumbnail.png`, `text/pages.json` | Never, after import |
| Structured | `extractions/<version>.json` (+ `extractions/<version>/figures/*.png`) | New immutable version per parse or revision |
| Reader | Node document: page, notes, annotations, study layout | Revisioned document actions |

`manifest.json` lists the versions and the active one; it is written last, so a
failed import or revision never leaves a half-visible state. Every file is written
to a temporary name and renamed into place. The UI reads the raw PDF, thumbnail,
extraction JSON and figure crops through the host's `served_files` route (Plugin
API 1.24 in `docs/plugins.md`); the manifest and page text stay private to the
plugin. Deleting a Paper removes its directory. Canvas undo and templates do not
carry the PDF: a Paper restored from a template starts empty.

## Extraction: GROBID

Structure comes from a [GROBID](https://github.com/kermitt2/grobid) service
(`grobid.py`); there are no hand-written layout rules in this plugin. Import and
**Re-extract** send the raw PDF to `/api/processFulltextDocument` (with
`teiCoordinates` and raw citations) and map the TEI onto the schema.

GROBID runs as a host background job: the upload returns as soon as the PDF is
stored, the card shows "extracting" and polls until the new version is active.
The graph stays editable meanwhile. A job does not survive a restart; the card
then reports it as interrupted and **Re-extract** starts it again. The mapped fields:

- header: title, authors (e-mail, ORCID, corresponding), affiliations, journal,
  volume/pages, publisher, ISSN, DOI/arXiv/PMID, publication date, keywords,
  licence, language, abstract (including structured abstracts);
- body: numbered sections with nesting level, paragraphs with citation links to
  reference ids, equations (as text), lists, footnotes;
- figures (with a PNG crop cut from the PDF at GROBID's coordinates) and tables
  (rows of cells; GROBID does not mark header rows);
- back matter: acknowledgements, funders, and annex sections such as competing
  interests, author contributions and data availability; other annexes become
  appendices; references with authors, title, venue, year, pages, DOI and the raw string.

Each element carries `loc` (page + normalized bbox) where GROBID reports coordinates.
`provenance.extractor` is `grobid/<version>`; `provenance.warnings` lists known gaps.

Run GROBID next to OAW and point the host at it:

```sh
docker run --rm -p 8070:8070 grobid/grobid:0.8.2-crf   # CRF image: CPU only, smaller
```

| Variable | Default | |
| --- | --- | --- |
| `OAW_GROBID_URL` | `http://localhost:8070` | Service root |
| `OAW_GROBID_TIMEOUT` | `180` | Seconds per request |
| `OAW_GROBID_CONSOLIDATE` | `0` | `1` lets GROBID look up metadata at CrossRef/biblio-glutton (sends titles and references to that service) |

If GROBID is unreachable or fails, the PDF is still imported and readable; the
manifest records `extraction_error`, the inspector shows it, and **Re-extract**
retries. PDFs without a text layer are not sent to GROBID (it does not OCR).

The schema is `schema.py` (`PaperStructure`, version 1.0). `domain` is free-form
for field-specific facts (e.g. materials, synthesis conditions, properties).

## Literature library

A **Literature library** (`library.collection`) is a card folder for Papers: drag
Paper cards into it, drop PDFs on it or use **Import PDFs**. Its header toggles
between the member cards and its workspace, a catalog of up to 2000 Papers with
full-text search, year filters, **Move out**, and results that open the reader at
the matching page and GROBID box.

Connections to the library mirror the Paper ones and apply to every current member,
including Papers added later; moving a Paper out revokes them on the next call.

| Connection | Tools |
| --- | --- |
| Read library | `list_papers`, `search_library`, and on every member `read_paper`, `read_paper_structure`, `view_paper_figure` |
| Curate library | the above plus `revise_paper_structure` and `reextract_paper` on every member |

`search_library` ranks passages with SQLite FTS5 (BM25, Porter stemming): title and
keywords, abstract, section text in ~1200-character chunks, figure captions and
table text, falling back to page text for Papers without an extraction. Each hit
has the paper id, section heading, page, bbox, extraction path and `cite`
(`<paper id>#p<page>`) for citing. Filters: year range, paper ids, passage kinds.
The index (`index.sqlite` in the library's storage) is derived data. Every library
action reconciles it with the current members and each Paper's PDF hash and active
extraction version, re-indexing only what changed; deleting the library deletes
it. Keyword search does not match synonyms or paraphrases; there is no embedding
search yet. CJK text is tokenized by Unicode word rules, which suits English papers best.

## Agent fallback

| Connection | Tools |
| --- | --- |
| Read paper | `read_paper` (page text + notes), `read_paper_structure`, `view_paper_figure` |
| Curate structure | the above plus `revise_paper_structure` and `reextract_paper` |

`view_paper_figure` returns GROBID's crop of one figure as an image with its caption
(select by label such as `Figure 1`, id `f1` or path `figures.0`).

`read_paper_structure` without `path` returns an overview (outline, extractor,
warnings); dotted paths such as `sections.2` or `references.0` return details, and
large values return an outline instead. `revise_paper_structure` takes the version
it read (`base_version`) and a list of `set`/`append`/`remove` changes. The result
is validated against the schema and saved as a new version with `source: agent`;
a stale `base_version` is rejected. `reextract_paper` reruns GROBID in the
background (the result becomes the new active version). Import and choosing the
active version remain user actions.

A typical use: connect an Agent with **Curate structure**, ask it to check the
GROBID output against `read_paper` for the relevant pages (the warnings point at
known gaps, e.g. table headers), fix what is wrong and fill `domain` with the
facts you need.

## Paper inspector

The inspector shows the active extraction: version picker (GROBID / Agent / manual),
**Re-extract**, **Download JSON**, title/authors/DOI, counts, warnings, the last
extraction error and the section outline. Sections, figures and tables with a
location open the reader at that page and briefly mark GROBID's bounding box.

