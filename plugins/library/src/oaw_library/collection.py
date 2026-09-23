"""Literature library: a container of Papers with a full-text index.

The index is a SQLite FTS5 database in the library's own storage directory
(``index.sqlite``). It is derived data: every action first reconciles it with
the library's current members (``context.members``) and each Paper's manifest,
re-indexing only Papers whose PDF or active extraction changed. There is no
membership event to miss, and a deleted index simply rebuilds.

Chunks come from the active structured extraction (abstract, section text,
figure and table captions) and carry the element's ``loc``, so every hit can
be cited as ``<paper id>#p<page>`` and located in the reader. Papers without an
extraction fall back to plain page text.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from types import SimpleNamespace

from open_agent_world.plugin_api import NodeResourceContext, ResourceValidationError

from . import store

INDEX = "index.sqlite"
INDEX_FORMAT = "2"
PAPER_TYPE = "library.paper"
CHUNK_CHARS = 1200
MAX_TABLE_ROWS = 40
MAX_QUERY_TERMS = 32
_TERM = re.compile(r"\w+", re.UNICODE)

_TABLES = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, name TEXT NOT NULL, title TEXT, authors TEXT NOT NULL,
    year INTEGER, venue TEXT, doi TEXT, pages INTEGER NOT NULL, status TEXT NOT NULL, version TEXT, indexed_at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    text, heading, paper_id UNINDEXED, kind UNINDEXED, page UNINDEXED, bbox UNINDEXED, path UNINDEXED,
    tokenize = 'porter unicode61 remove_diacritics 2');
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _connect(context: NodeResourceContext) -> sqlite3.Connection:
    context.storage_path.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(context.storage_path / INDEX)
    row = None
    try:
        row = connection.execute("SELECT value FROM meta WHERE key = 'format'").fetchone()
    except sqlite3.DatabaseError:
        pass
    if row is None or row[0] != INDEX_FORMAT:  # New, foreign or older index: rebuild from members.
        connection.close()
        (context.storage_path / INDEX).unlink(missing_ok=True)
        connection = sqlite3.connect(context.storage_path / INDEX)
        connection.executescript(_TABLES)
        connection.execute("INSERT INTO meta VALUES ('format', ?)", (INDEX_FORMAT,))
        connection.commit()
    return connection


# ---- Describing and chunking one Paper ---------------------------------------------------

def _year(value) -> int | None:
    match = re.match(r"\s*(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


_ARXIV = re.compile(r"(?<![\d.])(\d{2})(\d{2})\.\d{4,5}(?:v\d+)?(?![\d])")


def _arxiv_year(*values) -> int | None:
    """Preprints often carry no publication date; a new-style arXiv id (YYMM.NNNNN) dates them."""
    for value in values:
        match = _ARXIV.search(str(value or ""))
        if match and 1 <= int(match.group(2)) <= 12 and int(match.group(1)) >= 7:
            return 2000 + int(match.group(1))
    return None


def _describe(member) -> tuple[dict, dict | None, list[str] | None]:
    """Catalog row for a member Paper, plus its structure/page text when there is content."""
    paper = store.PaperFiles(member.storage_path)
    config = member.config
    row = {"paper_id": member.node_id, "name": member.name, "title": None,
           "authors": [a.strip() for a in str(config.get("authors") or "").split(",") if a.strip()],
           "year": _year(config.get("year")), "venue": None, "doi": str(config.get("doi") or "") or None,
           "pages": 0, "status": "empty", "version": None, "fingerprint": "empty"}
    manifest = paper.get_json(store.MANIFEST) if paper.exists(store.MANIFEST) else None
    if manifest is None:
        return row, None, None
    manifest = store.view(SimpleNamespace(node_id=member.node_id), manifest)
    row["pages"] = manifest["pages"]
    row["version"] = manifest.get("active")
    row["fingerprint"] = f"{manifest['sha256']}:{manifest.get('active') or 'text'}"
    structure = None
    if manifest.get("active"):
        entry = next((item for item in manifest["extractions"] if item["id"] == manifest["active"]), None)
        if entry is not None and paper.exists(entry["key"]):
            structure = paper.get_json(entry["key"])
    if structure is not None:
        metadata = structure["metadata"]
        row.update(title=metadata.get("title"), venue=(metadata.get("venue") or {}).get("journal"),
                   authors=[author["name"] for author in metadata.get("authors", [])] or row["authors"],
                   year=_year((metadata.get("dates") or {}).get("published")) or row["year"],
                   doi=(metadata.get("identifiers") or {}).get("doi") or row["doi"], status="structured")
        row["year"] = row["year"] or _arxiv_year((metadata.get("identifiers") or {}).get("arxiv"))
    else:
        row["status"] = "extracting" if manifest["extracting"] else "text"
    row["year"] = row["year"] or _arxiv_year(manifest["filename"], member.name)
    return row, structure, paper.get_json(store.PAGES) if paper.exists(store.PAGES) else None


def _loc(item) -> tuple[int | None, str | None]:
    loc = (item or {}).get("loc") if isinstance(item, dict) else None
    if not loc:
        return None, None
    return loc.get("page"), json.dumps(loc["bbox"]) if loc.get("bbox") else None


def _split(text: str) -> list[str]:
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
        if current and len(current) + len(sentence) > CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
        while len(current) > 2 * CHUNK_CHARS:  # A single overlong "sentence".
            pieces.append(current[:CHUNK_CHARS])
            current = current[CHUNK_CHARS:]
    return [*pieces, current] if current else pieces


def _chunks(row: dict, structure: dict | None, pages: list[str] | None):
    """(text, heading, kind, page, bbox, path) rows for one Paper."""
    head = " ".join(filter(None, [row["title"] or row["name"], ", ".join(row["authors"][:8])]))
    if structure is not None:
        keywords = ", ".join(structure["metadata"].get("keywords", []))
        yield " ".join(filter(None, [head, keywords])), "Title", "metadata", 1, None, "metadata"
        abstract = structure["abstract"]
        if abstract.get("text"):
            page, bbox = _loc(abstract)
            for piece in _split(abstract["text"]):
                yield piece, "Abstract", "abstract", page or 1, bbox, "abstract"
        for index, section in enumerate(structure["sections"]):
            heading = " ".join(filter(None, [section.get("number"), section["heading"]]))
            current, anchor = "", section
            for block in section["blocks"]:
                if current and len(current) + len(block["text"]) > CHUNK_CHARS:
                    for piece in _split(current):
                        yield (piece, heading, "section", *_page_bbox(anchor, section), f"sections.{index}")
                    current, anchor = "", block
                if not current:
                    anchor = block
                current = f"{current}\n{block['text']}".strip()
            for piece in _split(current):
                yield (piece, heading, "section", *_page_bbox(anchor, section), f"sections.{index}")
        for index, figure in enumerate(structure["figures"]):
            text = _labelled(figure["label"], figure["caption"])
            if text:
                yield (text, figure["label"] or "Figure", "figure", *_page_bbox(figure, None), f"figures.{index}")
        for index, table in enumerate(structure["tables"]):
            cells = "\n".join(" | ".join(r) for r in [*table.get("header", []), *table["rows"][:MAX_TABLE_ROWS]])
            text = "\n".join(filter(None, [_labelled(table["label"], table["caption"]), cells]))
            for piece in _split(text):
                yield (piece, table["label"] or "Table", "table", *_page_bbox(table, None), f"tables.{index}")
        if structure["sections"] or abstract.get("text"):
            return
    # No usable structure: plain page text.
    if structure is None:
        yield head, "Title", "metadata", 1, None, None
    for number, text in enumerate(pages or [], start=1):
        for piece in _split(text):
            yield piece, f"Page {number}", "page", number, None, None


def _labelled(label: str, caption: str) -> str:
    # GROBID captions often repeat the label ("Fig. 2 Phonon ..."); do not index it twice.
    key = lambda text: re.sub(r"[^0-9a-z]+", "", text.casefold())
    if label and key(caption[:len(label) + 4]).startswith(key(label)):
        return caption
    return ": ".join(filter(None, [label, caption]))


def _page_bbox(item, fallback) -> tuple[int | None, str | None]:
    page, bbox = _loc(item)
    if page is None and fallback is not None:
        page, bbox = _loc(fallback)
    return page, bbox


def _index_paper(connection: sqlite3.Connection, row: dict, structure, pages) -> None:
    connection.execute("DELETE FROM chunks WHERE paper_id = ?", (row["paper_id"],))
    connection.executemany(
        "INSERT INTO chunks (text, heading, paper_id, kind, page, bbox, path) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(text, heading, row["paper_id"], kind, page, bbox, path)
         for text, heading, kind, page, bbox, path in _chunks(row, structure, pages)])
    _save_row(connection, row)


def _save_row(connection: sqlite3.Connection, row: dict) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO papers VALUES (:paper_id, :fingerprint, :name, :title, :authors, :year, :venue, :doi,"
        " :pages, :status, :version, :indexed_at)",
        {**row, "authors": json.dumps(row["authors"], ensure_ascii=False), "indexed_at": _now()})


def reconcile(context: NodeResourceContext, connection: sqlite3.Connection) -> dict:
    """Bring the index up to date with the current members; returns counts of the work done."""
    members = {member.node_id: member for member in context.members if member.type == PAPER_TYPE}
    known = {paper_id: (fingerprint, name, status) for paper_id, fingerprint, name, status
             in connection.execute("SELECT paper_id, fingerprint, name, status FROM papers")}
    removed = known.keys() - members.keys()
    for paper_id in removed:
        connection.execute("DELETE FROM chunks WHERE paper_id = ?", (paper_id,))
        connection.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
    indexed = 0
    for paper_id, member in members.items():
        if context.cancelled.is_set():
            break
        row, structure, pages = _describe(member)
        previous = known.get(paper_id)
        if previous is None or previous[0] != row["fingerprint"] or previous[1] != row["name"]:
            _index_paper(connection, row, structure, pages)
            indexed += 1
        elif previous[2] != row["status"]:  # e.g. extracting -> interrupted; content unchanged.
            _save_row(connection, row)
    connection.commit()
    return {"indexed": indexed, "removed": len(removed)}


# ---- Resource actions ----------------------------------------------------------------------

def _paper(row: sqlite3.Row | tuple) -> dict:
    paper_id, name, title, authors, year, venue, doi, pages, status, version = row
    return {"paper": paper_id, "name": name, "title": title, "authors": json.loads(authors), "year": year,
            "venue": venue, "doi": doi, "pages": pages, "status": status, "version": version}


def _int(arguments: dict, key: str, default: int | None, low: int, high: int) -> int | None:
    value = arguments.get(key, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ResourceValidationError(f"{key} must be an integer between {low} and {high}")
    return value


def _year_filter(arguments: dict, column: str) -> tuple[str, list]:
    clauses, values = [], []
    for key, operator in (("year_from", ">="), ("year_to", "<=")):
        year = _int(arguments, key, None, 1000, 3000)
        if year is not None:
            clauses.append(f"{column} {operator} ?")
            values.append(year)
    return "".join(f" AND {clause}" for clause in clauses), values


def list_papers(context: NodeResourceContext, arguments: dict) -> dict:
    """Catalog of the library's Papers, optionally filtered by text and publication year."""
    limit = _int(arguments, "limit", 50, 1, 500)
    offset = _int(arguments, "offset", 0, 0, 100_000)
    text = str(arguments.get("text") or "").strip()[:200]
    with closing(_connect(context)) as connection:
        work = reconcile(context, connection)
        where, values = _year_filter(arguments, "year")
        if text:
            where += " AND (name LIKE ? OR title LIKE ? OR authors LIKE ? OR venue LIKE ? OR doi LIKE ?)"
            values += [f"%{text}%"] * 5
        total = connection.execute(f"SELECT count(*) FROM papers WHERE 1 = 1{where}", values).fetchone()[0]
        rows = connection.execute(
            "SELECT paper_id, name, title, authors, year, venue, doi, pages, status, version FROM papers"
            f" WHERE 1 = 1{where} ORDER BY year IS NULL, year DESC, coalesce(title, name) LIMIT ? OFFSET ?",
            [*values, limit, offset]).fetchall()
        counts = dict(connection.execute("SELECT status, count(*) FROM papers GROUP BY status").fetchall())
    return {"total": total, "offset": offset, "papers": [_paper(row) for row in rows], "status_counts": counts, "index": work,
            "hint": "Pass a paper id as the target of read_paper, read_paper_structure or view_paper_figure; "
                    "use search_library to find passages across papers."}


def _match(query: str) -> str:
    terms = list(dict.fromkeys(term.casefold() for term in _TERM.findall(query)))[:MAX_QUERY_TERMS]
    if not terms:
        raise ResourceValidationError("The query needs at least one word")
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def search(context: NodeResourceContext, arguments: dict) -> dict:
    """Ranked passages (BM25) across the library, each with a citation and location."""
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ResourceValidationError("query must be 1 to 1000 characters")
    limit = _int(arguments, "limit", 8, 1, 30)
    per_paper = _int(arguments, "per_paper", 3, 1, 30)
    papers = arguments.get("papers")
    if papers is not None and (not isinstance(papers, list) or len(papers) > 500 or not all(isinstance(p, str) for p in papers)):
        raise ResourceValidationError("papers must be a list of up to 500 paper ids")
    kinds = arguments.get("kinds")
    allowed = {"metadata", "abstract", "section", "figure", "table", "page"}
    if kinds is not None and (not isinstance(kinds, list) or not set(kinds) <= allowed):
        raise ResourceValidationError(f"kinds must be a list drawn from {sorted(allowed)}")
    with closing(_connect(context)) as connection:
        reconcile(context, connection)
        where, values = _year_filter(arguments, "p.year")
        if papers:
            where += f" AND chunks.paper_id IN ({','.join('?' * len(papers))})"
            values += papers
        if kinds:
            where += f" AND chunks.kind IN ({','.join('?' * len(kinds))})"
            values += kinds
        rows = connection.execute(
            "SELECT chunks.paper_id, p.name, p.title, p.year, chunks.kind, chunks.heading, chunks.page, chunks.bbox,"
            " chunks.path, snippet(chunks, 0, '[', ']', '…', 40), bm25(chunks, 1.0, 0.5)"
            " FROM chunks JOIN papers p ON p.paper_id = chunks.paper_id"
            f" WHERE chunks MATCH ?{where} ORDER BY bm25(chunks, 1.0, 0.5) LIMIT ?",
            [_match(query), *values, limit * per_paper * 4]).fetchall()
        indexed = connection.execute("SELECT count(*) FROM papers").fetchone()[0]
    hits, seen = [], {}
    for paper_id, name, title, year, kind, heading, page, bbox, path, snippet, score in rows:
        if seen.get(paper_id, 0) >= per_paper:
            continue
        seen[paper_id] = seen.get(paper_id, 0) + 1
        hits.append({"paper": paper_id, "paper_name": name, "title": title, "year": year, "kind": kind,
                     "heading": heading, "page": page, "bbox": json.loads(bbox) if bbox else None, "path": path,
                     "snippet": snippet, "score": round(-score, 4),
                     "cite": f"{paper_id}#p{page}" if page else paper_id})
        if len(hits) >= limit:
            break
    return {"query": query, "papers_indexed": indexed, "hits": hits,
            "hint": "Cite evidence as [paper#pN] using each hit's cite value. Read the surrounding text with "
                    "read_paper (page) or read_paper_structure (path) before relying on a snippet."}


__all__ = ["INDEX", "list_papers", "search", "reconcile"]
