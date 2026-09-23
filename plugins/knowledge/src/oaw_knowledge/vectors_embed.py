"""Embedding client for the vector store: any OpenAI-compatible ``/v1/embeddings`` endpoint.

Configured by environment, read at call time:

* ``OAW_EMBEDDING_URL``: server base URL (``http://host:8000``), its ``/v1`` root, or the full
  endpoint ending in ``/embeddings``; ``/v1/embeddings`` is appended to a bare base URL.
* ``OAW_EMBEDDING_MODEL``: model name sent with every request and recorded with each vector.
* ``OAW_EMBEDDING_API_KEY`` (optional): sent as a Bearer token.
* ``OAW_EMBEDDING_BATCH`` (default 64) and ``OAW_EMBEDDING_TIMEOUT`` seconds (default 60).

Without URL and model the store runs in lexical (BM25) mode only.
"""
from __future__ import annotations

import math
import os
import threading
from array import array

import httpx

_transport: httpx.BaseTransport | None = None  # Tests substitute a mock transport.
MAX_INPUT_CHARS = 8000


class EmbeddingError(Exception):
    pass


def model() -> str | None:
    """The configured model, or None when no embedding service is configured."""
    if not os.environ.get("OAW_EMBEDDING_URL", "").strip():
        return None
    return os.environ.get("OAW_EMBEDDING_MODEL", "").strip() or None


def endpoint() -> str:
    url = os.environ.get("OAW_EMBEDDING_URL", "").strip().rstrip("/")
    if url.endswith("/embeddings"):
        return url
    return f"{url}/embeddings" if url.endswith("/v1") else f"{url}/v1/embeddings"


def batch_size() -> int:
    return min(max(int(os.environ.get("OAW_EMBEDDING_BATCH", "64") or 64), 1), 512)


def timeout() -> float:
    return float(os.environ.get("OAW_EMBEDDING_TIMEOUT", "60") or 60)


def normalise(values) -> array:
    vector = array("f", values)
    norm = math.sqrt(math.sumprod(vector, vector))
    if not norm or not math.isfinite(norm):
        raise EmbeddingError("The embedding service returned a zero or invalid vector")
    return array("f", (value / norm for value in vector))


def embed_within(texts: list[str], deadline: float) -> list[array]:
    """``embed`` with a hard overall deadline (httpx only bounds each phase: connect, read...).

    For callers holding the host's node lock. On timeout the request is abandoned in a daemon
    thread, which its own per-phase timeouts end soon after.
    """
    outcome: list = []
    worker = threading.Thread(target=lambda: outcome.append(_attempt(texts, deadline)), daemon=True)
    worker.start()
    worker.join(deadline)
    if not outcome:
        raise EmbeddingError(f"The embedding service did not answer within {deadline:g} s")
    if isinstance(outcome[0], EmbeddingError):
        raise outcome[0]
    return outcome[0]


def _attempt(texts, deadline):
    try:
        return embed(texts, request_timeout=deadline, connect_timeout=min(1.0, deadline))
    except EmbeddingError as error:
        return error
    except Exception as error:  # Surfaced as a warning rather than a silent wait.
        return EmbeddingError(f"{type(error).__name__}: {error}")


def embed(texts: list[str], *, cancelled=None, request_timeout: float | None = None,
          connect_timeout: float = 5) -> list[array]:
    """Unit-length float32 vectors for ``texts``, in order. Raises EmbeddingError."""
    name = model()
    if name is None:
        raise EmbeddingError("No embedding service is configured (set OAW_EMBEDDING_URL and OAW_EMBEDDING_MODEL)")
    headers = {}
    if key := os.environ.get("OAW_EMBEDDING_API_KEY", "").strip():
        headers["Authorization"] = f"Bearer {key}"
    limit = request_timeout or timeout()
    vectors: list[array] = []
    with httpx.Client(timeout=httpx.Timeout(limit, connect=min(limit, connect_timeout)), transport=_transport, headers=headers) as client:
        for start in range(0, len(texts), batch_size()):
            if cancelled is not None and cancelled.is_set():
                raise EmbeddingError("Cancelled")
            batch = [text[:MAX_INPUT_CHARS] or " " for text in texts[start:start + batch_size()]]
            try:
                response = client.post(endpoint(), json={"model": name, "input": batch})
            except httpx.TimeoutException:
                raise EmbeddingError(f"The embedding service did not answer within {limit:g} s") from None
            except httpx.HTTPError as error:
                raise EmbeddingError(f"Cannot reach the embedding service: {error}") from None
            if response.status_code != 200:
                raise EmbeddingError(f"The embedding service answered {response.status_code}: {response.text[:200]}")
            try:
                data = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
                batch_vectors = [normalise(item["embedding"]) for item in data]
            except (ValueError, KeyError, TypeError) as error:
                raise EmbeddingError(f"Unexpected embedding response: {error}") from None
            if len(batch_vectors) != len(batch) or len({len(v) for v in batch_vectors}) != 1:
                raise EmbeddingError("The embedding service returned the wrong number or size of vectors")
            if vectors and len(batch_vectors[0]) != len(vectors[0]):
                raise EmbeddingError("The embedding service changed vector size between batches")
            vectors.extend(batch_vectors)
    return vectors
