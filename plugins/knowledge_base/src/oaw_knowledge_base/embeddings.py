"""Optional semantic channel for graph search: an OpenAI-compatible embedding model.

The card's ``embedding_model`` setting is an OAW model reference, resolved through the
host like the vision model is; empty means off and search stays lexical. Vectors for
entities and relations live in one plugin-owned table in the card's SQLite file and are
brought up to date lazily by :func:`sync` — only elements whose text or model changed
are embedded, and rows of elements that left the graph are dropped, so no publish,
unpublish or review path needs a hook. The graph is small enough that a brute-force
cosine over the rows is fine; there is no vector index or native dependency.
"""
from __future__ import annotations

import hashlib
import math
import struct
import time

from .errors import KnowledgeError

TABLE = "oaw_kb_vectors"
# DashScope's OpenAI-compatible endpoint accepts at most 10 inputs per request.
BATCH = 10
MAX_TEXT_CHARS = 1000
SYNC_CHUNK = 50
RETRIES = 3


def ensure_schema(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {TABLE} (element_id TEXT PRIMARY KEY, model TEXT NOT NULL, "
            "text_hash TEXT NOT NULL, vector BLOB NOT NULL)")


def connection_for(model, resolve_model):
    """``(base_url, api_key, model_id)`` for the configured embedding model."""
    reference = (model or "").strip()
    if not reference:
        return None
    if resolve_model is None:
        raise KnowledgeError("This card cannot resolve an OAW model connection for embeddings")
    try:
        connection = resolve_model(reference)
    except Exception as error:  # the host raises its own validation errors
        raise KnowledgeError(f"Embedding model {reference!r}: {error}") from error
    if connection.adapter not in {"openai", "legacy"}:
        raise KnowledgeError("Choose an OpenAI-compatible model connection for embeddings")
    return (connection.base_url or "https://api.openai.com/v1", connection.api_key,
            connection.model_id.removeprefix("openai/"))


def embed(texts, connection, *, timeout=60):
    """One normalized vector per text, from the connection's ``/embeddings`` endpoint."""
    import httpx

    base_url, api_key, model = connection
    headers = {"Content-Type": "application/json"}
    if api_key and api_key != "oaw-no-auth":
        headers["Authorization"] = "Bear" + "er " + api_key
    vectors = []
    with httpx.Client(timeout=timeout) as client:
        for start in range(0, len(texts), BATCH):
            payload = {"model": model, "input": texts[start:start + BATCH]}
            for attempt in range(1, RETRIES + 1):
                try:
                    response = client.post(f"{base_url.rstrip('/')}/embeddings", headers=headers, json=payload)
                    break
                except httpx.TransportError:
                    # The provider drops connections now and then; anything else is the caller's.
                    if attempt == RETRIES:
                        raise
                    time.sleep(0.5 * attempt)
            if response.status_code != 200:
                raise KnowledgeError(f"Embedding model request failed (HTTP {response.status_code}"
                                     f"{_provider_message(response)}); is {model!r} an embedding model?")
            try:
                rows = sorted(response.json()["data"], key=lambda row: row["index"])
                vectors += [_normalize(row["embedding"]) for row in rows]
            except (KeyError, TypeError, ValueError):
                raise KnowledgeError("Embedding model returned an unexpected response shape") from None
    if len(vectors) != len(texts):
        raise KnowledgeError("Embedding model returned the wrong number of vectors")
    return vectors


def _provider_message(response):
    """``: <the provider's own error text>`` when it sent one, else nothing."""
    try:
        error = response.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except (ValueError, AttributeError):
        message = None
    return f": {str(message)[:200]}" if message else ""


def _normalize(vector):
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _pack(vector):
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob):
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def entity_text(entity):
    properties = entity.properties or {}
    aliases = [a for a in properties.get("aliases") or [] if isinstance(a, str)]
    description = properties.get("description")
    parts = [entity.name, *aliases, description if isinstance(description, str) else ""]
    return "\n".join(part for part in parts if part)[:MAX_TEXT_CHARS]


def relation_text(relation, names):
    properties = relation.properties or {}
    claim = properties.get("description") or properties.get("snippet")
    head = f"{names.get(relation.source_id, '')} {relation.type.replace('_', ' ')} {names.get(relation.target_id, '')}"
    return (head + (f": {claim}" if isinstance(claim, str) else ""))[:MAX_TEXT_CHARS]


def _hashes(texts):
    return {identifier: hashlib.sha1(text.encode()).hexdigest() for identifier, text in texts.items()}


def _rows(engine):
    ensure_schema(engine)
    with engine.begin() as db:
        # Rows of elements that have left the graph are dropped on every pass.
        db.exec_driver_sql(
            f"DELETE FROM {TABLE} WHERE element_id NOT IN "
            "(SELECT id FROM oaw_kg_entity UNION SELECT id FROM oaw_kg_relation)")
        return {row[0]: row[1:] for row in db.exec_driver_sql(
            f"SELECT element_id, model, text_hash, vector FROM {TABLE}").fetchall()}


def _stale(texts, rows, model, hashes):
    return [identifier for identifier in texts
            if identifier not in rows or rows[identifier][:2] != (model, hashes[identifier])]


def status(engine, texts, model):
    """How much of ``{element_id: text}`` is embedded with ``model`` — no API call.
    ``stale`` counts elements embedded with another model or an older text."""
    rows = _rows(engine)
    stale = _stale(texts, rows, model, _hashes(texts))
    return {"total": len(texts), "embedded": len(texts) - len(stale),
            "pending": sum(1 for identifier in stale if identifier not in rows),
            "stale": sum(1 for identifier in stale if identifier in rows)}


def graph_texts(entities, relations):
    """``{str(element_id): text}`` for a whole graph — the one shape ``sync``/``status`` take."""
    names = {entity.id: entity.name for entity in entities}
    texts = {str(entity.id): entity_text(entity) for entity in entities}
    texts.update({str(r.id): relation_text(r, names) for r in relations})
    return texts


def sync(engine, texts, connection, *, embedder=None):
    """Bring the vector table up to date for ``{element_id: text}``; return the vectors.

    Rows are keyed by element id and tagged with the model and a hash of the text, so
    editing a statement or switching model re-embeds exactly what changed.
    """
    model = connection[2]
    rows = _rows(engine)
    hashes = _hashes(texts)
    stale = _stale(texts, rows, model, hashes)
    for start in range(0, len(stale), SYNC_CHUNK):
        # Saved chunk by chunk, so a failure late in a first sync keeps what was embedded.
        chunk = stale[start:start + SYNC_CHUNK]
        fresh = (embedder or embed)([texts[identifier] for identifier in chunk], connection)
        with engine.begin() as db:
            for identifier, vector in zip(chunk, fresh):
                db.exec_driver_sql(
                    f"INSERT OR REPLACE INTO {TABLE} (element_id, model, text_hash, vector) VALUES (?, ?, ?, ?)",
                    (identifier, model, hashes[identifier], _pack(vector)))
                rows[identifier] = (model, hashes[identifier], _pack(vector))
    return {identifier: _unpack(rows[identifier][2]) for identifier in texts}


# Fewer elements than this give no meaningful spread to judge "unusually similar" by.
MIN_POPULATION = 8
# The top this-many similarities are left out of the baseline (see ``scores``).
TRIM = 5
TOP = 30
# Below this cosine nothing is a hit however much it stands out from the rest: with a
# tight, uniformly low population "two standard deviations above the mean" can still
# be noise. Tuned on qwen3 text-embedding (true hits 0.56–0.86, noise ≤ 0.44); another
# model may want another value.
ABS_FLOOR = 0.45


def scores(query_vector, vectors, *, floor=ABS_FLOOR):
    """``{element_id: cosine}`` for the elements that stand out from the rest.

    Absolute cosine values depend on the model, so relevance is judged against the
    population: an element counts only when it is more than two standard deviations
    above the mean similarity — and above ``floor``. The mean and spread come from the
    population without its top few, so in a small graph where several elements are
    about the question the hits do not raise the bar against themselves. An unrelated
    question therefore matches nothing instead of returning the least-bad ten.
    """
    if len(vectors) < MIN_POPULATION:
        return {}
    similarity = {identifier: sum(a * b for a, b in zip(query_vector, vector))
                  for identifier, vector in vectors.items()}
    ranked = sorted(similarity.values(), reverse=True)
    baseline = ranked[TRIM:]
    mean = sum(baseline) / len(baseline)
    spread = math.sqrt(sum((value - mean) ** 2 for value in baseline) / len(baseline))
    cutoff = max(mean + 2 * spread, floor)
    best = sorted((item for item in similarity.items() if item[1] > cutoff), key=lambda item: -item[1])
    return dict(best[:TOP])
