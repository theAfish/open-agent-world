"""Vector store (knowledge.vectors): passages an Agent chose, for lexical, semantic and hybrid retrieval.

Unlike a Literature library's own index, nothing here is derived from membership: a
curating Agent ingests Papers it can read (through its own grants, page by page) and
adds its own cited notes. A store may therefore span Papers from several libraries.

Every passage has a BM25 entry (FTS5). Vectors come from an OpenAI-compatible embedding
service (vectors_embed) in a host background job, so ingesting returns at once. Each
vector records its model; only vectors of the configured model are searched, and without
a service the store answers in lexical mode and says so.

Page passages cite their page (level 'quote': the text is the page's own), pinned to the
Paper's fingerprint; check_knowledge_provenance marks them stale when the Paper changes.
Re-ingesting replaces a Paper's page passages; nothing is re-read automatically.
"""
from __future__ import annotations

import asyncio
import heapq
import json
import math
import re
import threading
from array import array
from uuid import uuid4

from open_agent_world.plugin_api import NodeResourceContext, NotFoundError, PermissionDeniedError, ResourceValidationError

from . import vectors_embed as embed
from .common import (PAPER_READ, Tool, add_sources, citations_schema, cited, connect, forward, log, now,
                     register_store, sources_for, trusted_sources)

MAX_PASSAGES = 20_000       # Brute-force cosine stays well under a second at this size.
MAX_INGEST = 50
MAX_PAGES = 1000
CHUNK_MIN, CHUNK_MAX, OVERLAP = 1000, 1500, 150
MAX_RESULTS = 50
CANDIDATES = 200
RRF_K = 60
QUERY_DEADLINE = 3.0        # The workspace embeds its query while holding the node lock; hard bound.
KINDS = ("page", "agent_note")
_TERM = re.compile(r"\w+", re.UNICODE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reused, so a late job cannot attach a vector to a new passage
    text TEXT NOT NULL,
    paper TEXT,                     -- page passages: the Paper; notes: their first citation
    page INTEGER,
    heading TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,             -- 'page' (ingested page text) or 'agent_note'
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS passages_paper ON passages(paper, kind);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(text, heading, content='passages', content_rowid='id',
    tokenize = 'porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS passages_ai AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading); END;
CREATE TRIGGER IF NOT EXISTS passages_ad AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts(passages_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading); END;
CREATE TABLE IF NOT EXISTS embeddings (
    passage INTEGER NOT NULL REFERENCES passages(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,           -- unit-length float32 (array('f'))
    PRIMARY KEY (passage, model)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,            -- running | done | failed | interrupted
    op TEXT NOT NULL,
    papers TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL,
    passages INTEGER NOT NULL,
    embedded INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    started_by TEXT NOT NULL
);
"""


def _db(context: NodeResourceContext):
    return connect(context, SCHEMA)  # Commits and closes on leaving ``with``.


def _key(passage_id: int) -> str:
    return f"passage:{passage_id}"


def _chunked(values: list, size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


# ---- Chunking -----------------------------------------------------------------------------------

def chunk(text: str) -> list[str]:
    """Page text as passages of ~1000-1500 chars, cut at sentence ends, overlapping ~150 chars."""
    text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", text)  # Words hyphenated across lines.
    text = re.sub(r"\s+", " ", text).strip()
    pieces, start = [], 0
    while len(text) - start > CHUNK_MAX:
        window = text[start + CHUNK_MIN:start + CHUNK_MAX]
        ends = [m.end() for m in re.finditer(r"[.!?;:]\s", window)] or [m.end() for m in re.finditer(r"\s", window)]
        cut = start + CHUNK_MIN + (ends[-1] if ends else len(window))
        pieces.append(text[start:cut].strip())
        following = max(cut - OVERLAP, start + 1)
        space = text.find(" ", following, cut)
        start = space + 1 if space >= 0 else following
    if text[start:].strip():
        pieces.append(text[start:].strip())
    return pieces


def _embed_text(heading: str, text: str) -> str:
    return f"{heading}\n{text}" if heading else text


# ---- Embedding jobs -----------------------------------------------------------------------------
# Jobs do not survive a restart. A 'running' job this process did not start (or whose
# commit was lost) is marked interrupted on the next action; reembed_passages resumes it.

_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def _settle_jobs(connection) -> None:
    with _RUNNING_LOCK:
        live = list(_RUNNING)
    connection.execute(
        f"UPDATE jobs SET state = 'interrupted', finished_at = ?, error = ? WHERE state = 'running'"
        f" AND id NOT IN ({','.join('?' * len(live))})",
        (now(), "Interrupted (the host stopped or could not save the vectors). Run reembed_passages to finish.", *live))


def _job_view(row) -> dict:
    return {**dict(row), "papers": json.loads(row["papers"])}


def _start_job(context: NodeResourceContext, op: str, passages: list[tuple[int, str]], papers: list[str]) -> dict | None:
    """Embed ``passages`` (id, text) in the background; commit writes vectors for those still present."""
    model = embed.model()
    if model is None or not passages:
        return None
    job = {"id": uuid4().hex, "state": "running", "op": op, "papers": papers[:MAX_INGEST], "model": model,
           "passages": len(passages), "embedded": 0, "error": None, "started_at": now(), "finished_at": None,
           "started_by": context.actor_id or "user"}
    with _db(context) as connection:
        connection.execute("INSERT INTO jobs VALUES (:id, :state, :op, :papers, :model, :passages, :embedded, :error,"
                           " :started_at, :finished_at, :started_by)", {**job, "papers": json.dumps(job["papers"])})
    with _RUNNING_LOCK:
        _RUNNING.add(job["id"])
    ids, texts = [p[0] for p in passages], [p[1] for p in passages]

    def work(cancelled=None):
        vectors, error = [], None
        for batch in _chunked(texts, embed.batch_size()):
            try:
                vectors.extend(embed.embed(batch, cancelled=cancelled))
            except embed.EmbeddingError as failure:
                error = str(failure)  # Keep what finished; reembed_passages does the rest.
                break
        return vectors, error

    def forget():
        with _RUNNING_LOCK:
            _RUNNING.discard(job["id"])

    def commit(target: NodeResourceContext, outcome):
        forget()
        vectors, error = ([], f"{type(outcome).__name__}: {outcome}") if isinstance(outcome, Exception) else outcome
        with _db(target) as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job["id"],)).fetchone() is None:
                return
            present = set()
            for part in _chunked(ids):
                present.update(r[0] for r in connection.execute(
                    f"SELECT id FROM passages WHERE id IN ({','.join('?' * len(part))})", part))
            rows = [(pid, model, len(vector), vector.tobytes()) for pid, vector in zip(ids, vectors) if pid in present]
            connection.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)", rows)
            connection.execute("UPDATE jobs SET state = ?, embedded = ?, error = ?, finished_at = ? WHERE id = ?",
                                ("failed" if error else "done", len(rows), error, now(), job["id"]))

    if context.background is None:
        # Never call the network under the node lock: keep the passages, record why they have no vectors.
        forget()
        with _db(context) as connection:
            connection.execute("UPDATE jobs SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
                               ("This host cannot run background jobs, so nothing was embedded. Passages are searchable "
                                "lexically; reembed_passages finishes once background jobs are available.", now(), job["id"]))
            return _job_view(connection.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone())
    context.background(work, commit, forget)
    return job


# ---- Search -------------------------------------------------------------------------------------

def _filters(arguments: dict) -> tuple[str, list]:
    clauses, values = [], []
    papers = arguments.get("papers") or []
    if not isinstance(papers, list) or len(papers) > 100 or not all(isinstance(p, str) for p in papers):
        raise ResourceValidationError("papers must be a list of up to 100 Paper ids")
    if papers:
        marks = ",".join("?" * len(papers))
        clauses.append(f"(p.paper IN ({marks}) OR 'passage:' || p.id IN (SELECT record FROM sources WHERE paper IN ({marks})))")
        values += papers * 2
    kind = arguments.get("kind")
    if kind:
        if kind not in KINDS:
            raise ResourceValidationError("kind must be 'page' or 'agent_note'")
        clauses.append("p.kind = ?")
        values.append(kind)
    return (" AND " + " AND ".join(clauses)) if clauses else "", values


def _current_vectors(connection, model: str | None) -> int:
    if model is None:
        return 0
    return connection.execute("SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)).fetchone()[0]


def _lexical(connection, query: str, where: str, values: list) -> list[tuple[int, float]]:
    terms = list(dict.fromkeys(term.casefold() for term in _TERM.findall(query)))[:32]
    if not terms:
        return []
    match = " OR ".join(f'"{term}"' for term in terms)
    rows = connection.execute(
        f"SELECT p.id, bm25(passages_fts) AS score FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid"
        f" WHERE passages_fts MATCH ?{where} ORDER BY score LIMIT {CANDIDATES}", [match, *values])
    return [(row[0], -row[1]) for row in rows]


def _dense(connection, query: array, model: str, where: str, values: list) -> list[tuple[int, float]]:
    rows = connection.execute(
        f"SELECT e.passage, e.vector FROM embeddings e JOIN passages p ON p.id = e.passage"
        f" WHERE e.model = ? AND e.dim = ?{where}", [model, len(query), *values])
    scored = ((pid, math.sumprod(query, array("f", blob))) for pid, blob in rows)
    return heapq.nlargest(CANDIDATES, scored, key=lambda item: item[1])


def _search(connection, arguments: dict) -> dict:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ResourceValidationError("Give a query of 1-2000 characters")
    requested = arguments.get("mode") or "auto"
    if requested not in {"auto", "hybrid", "dense", "lexical"}:
        raise ResourceValidationError("mode must be 'hybrid', 'dense' or 'lexical' (or omit it)")
    limit = min(max(int(arguments.get("limit") or 8), 1), MAX_RESULTS)
    per_paper = int(arguments.get("max_per_paper") or 0)
    where, values = _filters(arguments)
    model = embed.model()
    vectors = _current_vectors(connection, model)
    vector = arguments.get("_query_vector")
    mode, warning = "lexical", None
    if requested != "lexical":
        if model is None:
            warning = "No embedding service is configured, so this is a lexical (BM25) search."
        elif not vectors:
            warning = f"No passage has a vector from {model} yet, so this is a lexical (BM25) search."
        elif not vector or arguments.get("_query_model") != model:
            warning = f"The query could not be embedded ({arguments.get('_query_error') or 'no vector'}); lexical search used."
        elif len(vector) not in (dims := {r[0] for r in connection.execute(
                "SELECT DISTINCT dim FROM embeddings WHERE model = ?", (model,))}):
            warning = (f"The query vector has {len(vector)} dimensions but the stored {model} vectors have "
                       f"{', '.join(map(str, sorted(dims)))}; lexical search used. reembed_passages with "
                       "drop_other_models cannot fix a changed dimension: remove and re-ingest the passages.")
        else:
            mode = "dense" if requested == "dense" else "hybrid"
        if requested == "auto" and model is None:
            warning = None  # Nothing was asked for that could not be given.
    lexical = _lexical(connection, query, where, values) if mode != "dense" else []
    dense = _dense(connection, embed.normalise(vector), model, where, values) if mode != "lexical" else []
    fused: dict[int, dict] = {}
    for rank, (pid, score) in enumerate(lexical, 1):
        fused.setdefault(pid, {"score": 0.0})["score"] += 1 / (RRF_K + rank)
        fused[pid].update(lexical_rank=rank, bm25=round(score, 4))
    for rank, (pid, score) in enumerate(dense, 1):
        fused.setdefault(pid, {"score": 0.0})["score"] += 1 / (RRF_K + rank)
        fused[pid].update(dense_rank=rank, cosine=round(score, 4))
    order = sorted(fused, key=lambda pid: fused[pid]["score"], reverse=True)
    rows = {}
    for part in _chunked(order):
        rows.update((row["id"], row) for row in connection.execute(
            f"SELECT * FROM passages WHERE id IN ({','.join('?' * len(part))})", part))
    chosen, per = [], {}
    for pid in order:
        paper = rows[pid]["paper"]
        if per_paper > 0 and per.get(paper, 0) >= per_paper:
            continue
        per[paper] = per.get(paper, 0) + 1
        chosen.append(pid)
        if len(chosen) == limit:
            break
    sources = sources_for(connection, [_key(pid) for pid in chosen])
    results = []
    for pid in chosen:
        row, scores = rows[pid], fused[pid]
        cites = sources[_key(pid)]
        item = {"passage": pid, "kind": row["kind"], "paper": row["paper"], "page": row["page"],
                "heading": row["heading"], "cite": f"{row['paper']}#p{row['page']}", "text": row["text"][:1600],
                "score": round(scores.pop("score"), 5), "scores": scores,
                "stale": any(source["status"] == "stale" for source in cites)}
        if row["kind"] == "agent_note":
            item["sources"] = cites
        results.append(item)
    total = connection.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    result = {"mode": mode, "model": model if mode != "lexical" else None, "results": results,
              "coverage": {"passages": total, "with_vectors": vectors}}
    if warning:
        result["warning"] = warning
    if not results:
        result["hint"] = ("No matching passages. Try other words, drop filters, or ingest more Papers "
                          "(ingest_papers needs a Curate connection).") if total else "The store is empty; a curating Agent adds Papers with ingest_papers."
    return result


def search_action(context: NodeResourceContext, arguments: dict) -> dict:
    with _db(context) as connection:
        _settle_jobs(connection)
        return _search(connection, arguments)


def ui_search(context: NodeResourceContext, arguments: dict) -> dict:
    arguments = {key: value for key, value in arguments.items() if not key.startswith("_")}
    model = embed.model()
    if model and arguments.get("mode") != "lexical" and isinstance(arguments.get("query"), str) and arguments["query"].strip():
        try:
            arguments["_query_vector"] = list(embed.embed_within([arguments["query"]], QUERY_DEADLINE)[0])
            arguments["_query_model"] = model
        except embed.EmbeddingError as error:
            arguments["_query_error"] = str(error)
    return search_action(context, arguments)


async def _search_tool(context, capability, arguments):
    arguments = {key: value for key, value in arguments.items() if not key.startswith("_")}
    model = embed.model()
    if model and arguments.get("mode") != "lexical" and isinstance(arguments.get("query"), str) and arguments["query"].strip():
        # Embed outside the host's node lock; the action only compares vectors.
        try:
            vector = (await asyncio.to_thread(embed.embed, [arguments["query"]]))[0]
            arguments.update(_query_vector=list(vector), _query_model=model)
        except embed.EmbeddingError as error:
            arguments["_query_error"] = str(error)
    return await context.node_resource_action(capability, "search", arguments)


# ---- Status -------------------------------------------------------------------------------------

def status_action(context: NodeResourceContext, arguments: dict) -> dict:
    model = embed.model()
    with _db(context) as connection:
        _settle_jobs(connection)
        kinds = dict(connection.execute("SELECT kind, COUNT(*) FROM passages GROUP BY kind").fetchall())
        vectors = _current_vectors(connection, model)
        stale = dict(connection.execute(
            "SELECT paper, COUNT(DISTINCT record) FROM sources WHERE status = 'stale' GROUP BY paper").fetchall())
        papers = [{**dict(row), "stale": stale.get(row["paper"], 0)} for row in connection.execute(
            "SELECT p.paper, SUM(p.kind = 'page') AS page_passages, SUM(p.kind = 'agent_note') AS notes,"
            " COUNT(DISTINCT CASE WHEN p.kind = 'page' THEN p.page END) AS pages,"
            " SUM(e.passage IS NOT NULL) AS with_vectors, MAX(p.created_at) AS updated_at"
            " FROM passages p LEFT JOIN embeddings e ON e.passage = p.id AND e.model = ?"
            " GROUP BY p.paper ORDER BY p.paper LIMIT 500", (model or "",))]
        models = [{"model": row[0], "dim": row[1], "vectors": row[2], "searched": row[0] == model}
                  for row in connection.execute("SELECT model, dim, COUNT(*) FROM embeddings GROUP BY model, dim")]
        jobs = [_job_view(row) for row in connection.execute("SELECT * FROM jobs ORDER BY started_at DESC, rowid DESC LIMIT 10")]
    total = sum(kinds.values())
    result = {"mode": "hybrid" if model and vectors else "lexical",
              "embedding": {"configured": model is not None, "model": model, "endpoint": embed.endpoint() if model else None},
              "passages": total, "page_passages": kinds.get("page", 0), "notes": kinds.get("agent_note", 0),
              "with_vectors": vectors, "without_vectors": total - vectors if model else total,
              "max_passages": MAX_PASSAGES, "papers": papers, "models": models, "jobs": jobs}
    hints = []
    if model is None:
        hints.append("No embedding service is configured: search is lexical (BM25) only.")
    elif total - vectors and not any(job["state"] == "running" for job in jobs):
        hints.append(f"{total - vectors} passages have no {model} vector; a curating Agent can run reembed_passages.")
    if any(not m["searched"] for m in models):
        hints.append("Vectors from other models are kept but never searched; reembed_passages can replace them.")
    if stale:
        hints.append("Some passages cite a Paper that changed; re-ingest it (ingest_papers) or remove them.")
    if hints:
        result["hint"] = " ".join(hints)
    return result


# ---- Curate -------------------------------------------------------------------------------------

def _paper_ids(value) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_INGEST or not all(isinstance(p, str) and p for p in value):
        raise ResourceValidationError(f"papers must list 1-{MAX_INGEST} Paper ids (from list_papers or search_library)")
    return list(dict.fromkeys(value))


def _delete(connection, ids: list[int]) -> None:
    for part in _chunked(ids):
        marks = ",".join("?" * len(part))
        connection.execute(f"DELETE FROM sources WHERE record IN ({marks})", [_key(pid) for pid in part])
        connection.execute(f"DELETE FROM passages WHERE id IN ({marks})", part)


def _page_chunks(pages: list[dict]) -> list[dict]:
    return [{"page": page["page"], "text": piece} for page in pages for piece in chunk(page["text"])]


async def _ingest_tool(context, capability, arguments):
    papers = _paper_ids(arguments.get("papers"))
    read, failed = [], []
    for paper in papers:
        try:
            grant = await context.agent_capability(capability, PAPER_READ, paper)
            first = await context.node_resource_action(grant, "page_text", {"page": 1})
            if first["pages"] > MAX_PAGES:
                raise ResourceValidationError(f"The Paper has {first['pages']} pages; at most {MAX_PAGES} can be ingested")
            pages = [first]
            for number in range(2, first["pages"] + 1):
                pages.append(await context.node_resource_action(grant, "page_text", {"page": number}))
        except (PermissionDeniedError, NotFoundError):  # Deleted meanwhile reads the same as not connected.
            failed.append({"paper": paper, "status": "failed", "error": "You cannot read this Paper. Only Papers "
                           "connected to you (directly or through a library you read) can be ingested."})
            continue
        except ResourceValidationError as error:
            failed.append({"paper": paper, "status": "failed", "error": str(error)})
            continue
        if len({page["fingerprint"] for page in pages}) != 1:
            failed.append({"paper": paper, "status": "failed", "error": "The Paper changed while it was read; retry."})
            continue
        chunks = await asyncio.to_thread(_page_chunks, pages)
        if not chunks:
            failed.append({"paper": paper, "status": "failed", "error": "The Paper has no text layer (scanned?); nothing to ingest."})
            continue
        read.append({"paper": paper, "fingerprint": first["fingerprint"], "pages": first["pages"], "chunks": chunks})
    # Page text goes straight into the store; it never passes through the model.
    result = {"papers": [], "job": None}
    if read:
        result = await context.node_resource_action(capability, "ingest", {
            "_papers": read, "force": bool(arguments.get("force")), "note": str(arguments.get("note") or "")})
    result["papers"] = [*result["papers"], *failed]
    return result


def ingest_action(context: NodeResourceContext, arguments: dict) -> dict:
    papers = arguments.get("_papers") or []
    created_at, author = now(), context.actor_id or "user"
    report, new, embed_now = [], [], []
    with _db(context) as connection:
        _settle_jobs(connection)
        total = connection.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        for item in papers:
            old = [r[0] for r in connection.execute("SELECT id FROM passages WHERE paper = ? AND kind = 'page'", (item["paper"],))]
            prints = {r[0] for part in _chunked(old) for r in connection.execute(
                f"SELECT DISTINCT fingerprint FROM sources WHERE record IN ({','.join('?' * len(part))})",
                [_key(pid) for pid in part])}
            entry = {"paper": item["paper"], "pages": item["pages"]}
            if old and prints == {item["fingerprint"]} and len(old) == len(item["chunks"]) and not arguments.get("force"):
                report.append({**entry, "status": "unchanged", "passages": len(old)})
                continue
            if total - len(old) + len(item["chunks"]) > MAX_PASSAGES:
                report.append({**entry, "status": "failed", "error": f"The store is limited to {MAX_PASSAGES} passages. "
                               "Remove passages (remove_passages) or use another vector store."})
                continue
            _delete(connection, old)
            total += len(item["chunks"]) - len(old)
            ids = []
            for piece in item["chunks"]:
                pid = connection.execute(
                    "INSERT INTO passages (text, paper, page, heading, kind, created_at, created_by) VALUES (?, ?, ?, '', 'page', ?, ?)",
                    (piece["text"], item["paper"], piece["page"], created_at, author)).lastrowid
                # The passage is the page's own text: provenance at quote level, pinned to the fingerprint.
                add_sources(connection, context, _key(pid), [{"paper": item["paper"], "page": piece["page"],
                    "quote": piece["text"][:160], "level": "quote", "fingerprint": item["fingerprint"], "verified_at": created_at}])
                ids.append(pid)
                embed_now.append((pid, piece["text"]))
            new += ids
            report.append({**entry, "status": "ingested", "passages": len(ids), "replaced": len(old)})
        ingested = [r["paper"] for r in report if r["status"] == "ingested"]
        if ingested:
            log(connection, context, "ingest", [_key(pid) for pid in new],
                f"{', '.join(ingested)}{': ' + arguments['note'] if arguments.get('note') else ''}")
    job = _start_job(context, "ingest", embed_now, ingested)
    return {"papers": report, "job": job, "mode": "hybrid" if job else _mode(context),
            "hint": ("Vectors are computed in the background; search is lexical for these passages until the job is done "
                     "(vector_store_status shows it).") if job else "Passages are searchable now (lexical mode)."}


def _mode(context) -> str:
    model = embed.model()
    if model is None:
        return "lexical"
    with _db(context) as connection:
        return "hybrid" if _current_vectors(connection, model) else "lexical"


def add_action(context: NodeResourceContext, arguments: dict) -> dict:
    text, title = arguments.get("text"), arguments.get("title") or ""
    if not isinstance(text, str) or not 20 <= len(text.strip()) <= 4000:
        raise ResourceValidationError("text must be 20-4000 characters: your summary or note, in your own words")
    if not isinstance(title, str) or len(title) > 200:
        raise ResourceValidationError("title must be at most 200 characters")
    sources = trusted_sources(arguments)
    if not sources:
        raise ResourceValidationError("Cite at least one Paper page (paper, page and a verbatim quote)")
    with _db(context) as connection:
        _settle_jobs(connection)
        if connection.execute("SELECT COUNT(*) FROM passages").fetchone()[0] >= MAX_PASSAGES:
            raise ResourceValidationError(f"The store is full ({MAX_PASSAGES} passages); remove passages first")
        pid = connection.execute(
            "INSERT INTO passages (text, paper, page, heading, kind, created_at, created_by) VALUES (?, ?, ?, ?, 'agent_note', ?, ?)",
            (text.strip(), sources[0]["paper"], sources[0]["page"], title.strip(), now(), context.actor_id or "user")).lastrowid
        add_sources(connection, context, _key(pid), sources)
        log(connection, context, "add", [_key(pid)], str(arguments.get("note") or title))
        cites = sources_for(connection, [_key(pid)])[_key(pid)]
    job = _start_job(context, "add", [(pid, _embed_text(title.strip(), text.strip()))], [])
    return {"passage": pid, "sources": cites, "job": job}


def remove_action(context: NodeResourceContext, arguments: dict) -> dict:
    ids, papers = arguments.get("passages") or [], arguments.get("papers") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids) or len(ids) > 1000:
        raise ResourceValidationError("passages must be a list of up to 1000 passage ids (numbers from semantic_search)")
    if not isinstance(papers, list) or not all(isinstance(p, str) for p in papers) or len(papers) > 100:
        raise ResourceValidationError("papers must be a list of up to 100 Paper ids")
    if not ids and not papers:
        raise ResourceValidationError("Give passage ids to remove, or Paper ids whose ingested pages to remove")
    with _db(context) as connection:
        _settle_jobs(connection)
        found = set()
        for part in _chunked(ids):
            found.update(r[0] for r in connection.execute(f"SELECT id FROM passages WHERE id IN ({','.join('?' * len(part))})", part))
        if papers:
            found.update(r[0] for r in connection.execute(
                f"SELECT id FROM passages WHERE kind = 'page' AND paper IN ({','.join('?' * len(papers))})", papers))
        removed = sorted(found)
        _delete(connection, removed)
        if removed:
            log(connection, context, "remove", [_key(pid) for pid in removed], str(arguments.get("note") or ""))
    return {"removed": len(removed), "passages": removed[:200], "missing": sorted(set(ids) - found)[:200]}


def reembed_action(context: NodeResourceContext, arguments: dict) -> dict:
    model = embed.model()
    if model is None:
        raise ResourceValidationError("No embedding service is configured (OAW_EMBEDDING_URL and OAW_EMBEDDING_MODEL "
                                      "on the host), so there is nothing to embed with. Lexical search still works.")
    with _db(context) as connection:
        _settle_jobs(connection)
        running = connection.execute("SELECT id FROM jobs WHERE state = 'running' LIMIT 1").fetchone()
        if running:
            raise ResourceValidationError(f"Embedding job {running[0]} is still running; check vector_store_status and retry when it is done")
        dropped = 0
        if arguments.get("drop_other_models"):
            dropped = connection.execute("DELETE FROM embeddings WHERE model != ?", (model,)).rowcount
        missing = [(row[0], _embed_text(row[1], row[2])) for row in connection.execute(
            "SELECT id, heading, text FROM passages WHERE id NOT IN (SELECT passage FROM embeddings WHERE model = ?) ORDER BY id", (model,))]
        if dropped:
            log(connection, context, "drop_vectors", [], f"Dropped {dropped} vectors not from {model}")
    job = _start_job(context, "reembed", missing, [])
    return {"model": model, "missing": len(missing), "dropped": dropped, "job": job,
            **({} if job else {"hint": f"Every passage already has a {model} vector."})}


# ---- Registration -------------------------------------------------------------------------------

PAPERS = {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 100},
          "description": "Only passages from (or citing) these Paper ids"}

SEARCH = ("Search this vector store's passages (ingested Paper pages and Agents' cited notes) for retrieval-augmented "
    "answers. mode 'hybrid' (default when vectors exist) fuses BM25 keyword ranking and embedding similarity; 'dense' "
    "uses embeddings only; 'lexical' uses BM25 only. The result's mode says what was actually used: without an "
    "embedding service it is always lexical. Each result has the passage text, its Paper, page and cite "
    "('<paper>#p<page>'). To cite a result elsewhere, read that page with read_paper (you need your own connection "
    "to the Paper) and quote it; the store does not grant access to Papers.")
STATUS = ("Show this vector store's state: passage counts per Paper, the embedding model and search mode, vectors from "
    "other models (not searched), stale provenance and recent embedding jobs. Use it to see what is in the store before "
    "searching, or to follow an ingest job.")
INGEST = ("Add whole Papers to this vector store. For each Paper id (1-50; ids come from list_papers or search_library) "
    "every page is read through your own connection to it, split into page-tagged passages (~1500 characters) and "
    "stored with the page as provenance, replacing that Paper's earlier page passages (unchanged Papers are skipped "
    "unless force is true). The text goes straight into the store, not through you. Papers you cannot read are "
    "reported per Paper and nothing from them is stored. Embeddings run in the background: the call returns a job; "
    "passages are searchable lexically at once.")
ADD = ("Add your own passage (a summary, synthesis or note) to this vector store so later searches retrieve it. "
    "Write it in your own words and cite the Paper pages it rests on: each citation needs paper and page, and a "
    "verbatim quote from that page is checked against the page text with your own Paper connection.")
REMOVE = ("Remove passages from this vector store: by passage id (from semantic_search), and/or all ingested page "
    "passages of some Papers. Agent notes are removed only by id. Logged with your note.")
REEMBED = ("Compute embeddings for passages that have no vector from the configured embedding model (after a failed or "
    "interrupted job, or a model change). drop_other_models also deletes vectors of other models. Runs in the background.")


def register(registration):
    read_tools = [
        Tool("search", "semantic_search", SEARCH, {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "A question or description of what you look for"},
            "mode": {"type": "string", "enum": ["hybrid", "dense", "lexical"]},
            "papers": PAPERS,
            "kind": {"type": "string", "enum": list(KINDS), "description": "'page' for Paper text, 'agent_note' for Agents' notes"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "description": "Results (default 8)"},
            "max_per_paper": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "description": "At most this many results per Paper"}}},
            _search_tool, {"search": search_action}),
        Tool("status", "vector_store_status", STATUS, {"type": "object", "additionalProperties": False, "properties": {}},
             forward("status"), {"status": status_action}),
    ]
    curate_tools = [
        Tool("ingest", "ingest_papers", INGEST, {"type": "object", "additionalProperties": False, "required": ["papers"], "properties": {
            "papers": {"type": "array", "minItems": 1, "maxItems": MAX_INGEST, "items": {"type": "string", "maxLength": 100}},
            "force": {"type": "boolean", "description": "Re-ingest Papers whose text has not changed"},
            "note": {"type": "string", "maxLength": 500, "description": "Why, for the write log"}}},
            _ingest_tool, {"ingest": ingest_action}),
        Tool("add", "add_passage", ADD, {"type": "object", "additionalProperties": False, "required": ["text", "citations"], "properties": {
            "text": {"type": "string", "minLength": 20, "maxLength": 4000},
            "title": {"type": "string", "maxLength": 200, "description": "Short heading, also searched"},
            "citations": citations_schema(),
            "note": {"type": "string", "maxLength": 500, "description": "Why, for the write log"}}},
            cited("add_passage"), {"add_passage": add_action}),
        Tool("remove", "remove_passages", REMOVE, {"type": "object", "additionalProperties": False, "properties": {
            "passages": {"type": "array", "maxItems": 1000, "items": {"type": "integer"}},
            "papers": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 100}},
            "note": {"type": "string", "maxLength": 500}}},
            forward("remove"), {"remove": remove_action}),
        Tool("reembed", "reembed_passages", REEMBED, {"type": "object", "additionalProperties": False, "properties": {
            "drop_other_models": {"type": "boolean"}}},
            forward("reembed"), {"reembed": reembed_action}),
    ]
    register_store(registration, card="vectors", label="Vector store",
                   description="Passages Agents chose from Papers, with embeddings for semantic and hybrid search",
                   icon="search", color="#5f8fb0", schema=SCHEMA, read_tools=read_tools, curate_tools=curate_tools,
                   user_actions={"ui_status": status_action, "ui_search": ui_search, "ui_remove": remove_action},
                   default_size=(340, 230))
