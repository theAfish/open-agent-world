"""Full-text search over converted markdown, alongside MKB's own tables.

Mirrors ``graph_store.py``'s pattern: a plugin-owned SQL structure in the same SQLite
file as the rest of the card, populated as documents are converted and queried
directly by the ``search`` resource action. This is OAW-owned code — mat-know-base has
no search concept of its own to extend, so nothing here depends on it beyond the
engine the card already opens.

SQLite's FTS5 module is the whole engine: no embeddings, no external index, no model
call. It ranks with ``bm25()``, which is good enough for keyword and short-phrase
recall; if that turns out to be insufficient once there is real usage, an embedding
based ranker can be added later without changing the row shape here.
"""
from __future__ import annotations

import re

TABLE = "oaw_kb_chunks_fts"
# A chunk this size keeps citations focused on one passage without cutting an
# argument in half; there is no overlap between adjacent chunks in this first pass.
CHUNK_TARGET_CHARS = 1000
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def ensure_schema(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5("
            "text, heading_path UNINDEXED, record_id UNINDEXED, source_id UNINDEXED, "
            "group_id UNINDEXED, chunk_index UNINDEXED)")


def _sections(text):
    """Split markdown into ``(heading_path, lines)`` sections along heading lines.

    The heading line itself opens the next section (not the one it closes), so the
    heading text stays with the content it introduces.
    """
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    path = ""
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current:
                yield path, current
            level = len(match.group(1))
            stack = [item for item in stack if item[0] < level]
            stack.append((level, match.group(2).strip()))
            path = " > ".join(title for _, title in stack)
            current = []
        current.append(line)
    if current:
        yield path, current


def chunk_markdown(text):
    """Yield ``(chunk_index, heading_path, chunk_text)`` for one document.

    Paragraphs (blank-line separated) accumulate into a chunk up to roughly
    ``CHUNK_TARGET_CHARS`` before starting the next one, so a chunk never splits a
    paragraph in half and rarely splits a short argument across two chunks.
    """
    index = 0
    for path, lines in _sections(text):
        buffer: list[str] = []
        buffer_len = 0
        for paragraph in "\n".join(lines).split("\n\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if buffer and buffer_len + len(paragraph) > CHUNK_TARGET_CHARS:
                yield index, path, "\n\n".join(buffer)
                index += 1
                buffer, buffer_len = [], 0
            buffer.append(paragraph)
            buffer_len += len(paragraph)
        if buffer:
            yield index, path, "\n\n".join(buffer)
            index += 1


def index_record(engine, *, record_id, source_id, group_id, text):
    """Replace one record's chunks. Safe to call again after a re-conversion."""
    ensure_schema(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"DELETE FROM {TABLE} WHERE record_id = ?", (str(record_id),))
        rows = [(chunk, heading_path, str(record_id), str(source_id),
                 str(group_id) if group_id else None, index)
                for index, heading_path, chunk in chunk_markdown(text) if chunk.strip()]
        if rows:
            connection.exec_driver_sql(
                f"INSERT INTO {TABLE} "
                "(text, heading_path, record_id, source_id, group_id, chunk_index) "
                "VALUES (?, ?, ?, ?, ?, ?)", rows)


def has_chunks(engine, record_id):
    """Whether a record already has at least one chunk indexed."""
    ensure_schema(engine)
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            f"SELECT 1 FROM {TABLE} WHERE record_id = ? LIMIT 1", (str(record_id),)).fetchone()
    return row is not None


def _match_expression(query):
    """A defensive FTS5 MATCH string: quote every token and OR them together, so
    punctuation in the raw query (``"``, ``*``, ``:``, ``-``...) can never be read as
    an FTS5 operator, and a document matching any one meaningful word still ranks
    (an implicit AND between bare tokens would otherwise require every word present)."""
    tokens = _TOKEN_RE.findall(query or "")
    if not tokens:
        return None
    return " OR ".join('"{}"'.format(token.replace('"', '""')) for token in tokens)


def search(engine, query, *, limit=8, group_id=None):
    """Ranked chunks matching ``query``, newest-ranked first. ``[]`` for an empty query."""
    ensure_schema(engine)
    match = _match_expression(query)
    if not match:
        return []
    sql = (f"SELECT text, heading_path, record_id, source_id, group_id, chunk_index, "
           f"bm25({TABLE}) AS rank FROM {TABLE} WHERE {TABLE} MATCH ?")
    params: list = [match]
    if group_id:
        sql += " AND group_id = ?"
        params.append(str(group_id))
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(1, min(int(limit), 50)))
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(sql, tuple(params)).mappings().all()
    return [dict(row) for row in rows]
