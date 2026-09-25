"""Raw bytes to markdown.

Three engines, tried in the order the card's ``pdf_engine`` setting allows:
``pymupdf4llm`` locally (headings and tables, no network), the MinerU v4 HTTP API when
a base URL is configured, and a plain-text passthrough for text-like uploads.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from .errors import KnowledgeError

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".rst"}
FENCED_SUFFIXES = {".json": "json", ".csv": "csv", ".tsv": "tsv", ".yaml": "yaml", ".yml": "yaml"}
MINERU_TOKEN_ENV = "OAW_MINERU_TOKEN"
MAX_MARKDOWN_BYTES = 4 * 1024 * 1024


def available_engines(mineru_base_url=None):
    """What this deployment can actually run, for the overview action."""
    engines = ["text"]
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError:
        pass
    else:
        engines.insert(0, "pymupdf4llm")
    if mineru_base_url and os.environ.get(MINERU_TOKEN_ENV):
        engines.append("mineru")
    return engines


def _is_pdf(filename, media_type):
    return media_type == "application/pdf" or filename.lower().endswith(".pdf")


def _text_markdown(data, filename):
    suffix = Path(filename).suffix.lower()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    if suffix in {".md", ".markdown"}:
        return text
    language = FENCED_SUFFIXES.get(suffix)
    if language is None:
        return text
    if language == "json":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    return f"```{language}\n{text}\n```"


def _pymupdf_markdown(data):
    import pymupdf4llm

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.pdf"
        path.write_bytes(data)
        return pymupdf4llm.to_markdown(str(path))


def _mineru_markdown(data, filename, base_url, timeout=600):
    """MinerU v4 batch upload.

    MKB's own ``PDFMineruAPIProcessor`` is not importable here: its module chain pulls in
    the heavy materials stack. Try it anyway, then fall back to this self-contained client.
    """
    token = os.environ.get(MINERU_TOKEN_ENV)
    if not token:
        raise KnowledgeError(f"Set {MINERU_TOKEN_ENV} to use the MinerU engine")
    import httpx

    root = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        created = client.post(
            f"{root}/api/v4/file-urls/batch", headers=headers,
            json={"enable_formula": True, "enable_table": True,
                  "files": [{"name": filename, "is_ocr": True}]})
        if created.status_code != 200:
            raise KnowledgeError(f"MinerU rejected the upload (HTTP {created.status_code})")
        payload = created.json().get("data") or {}
        batch_id, urls = payload.get("batch_id"), payload.get("file_urls") or []
        if not batch_id or not urls:
            raise KnowledgeError("MinerU did not return an upload URL")
        uploaded = client.put(urls[0], content=data)
        if uploaded.status_code not in (200, 201, 204):
            raise KnowledgeError(f"MinerU upload failed (HTTP {uploaded.status_code})")

        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(3)
            polled = client.get(f"{root}/api/v4/extract-results/batch/{batch_id}", headers=headers)
            if polled.status_code != 200:
                continue
            results = (polled.json().get("data") or {}).get("extract_result") or []
            for result in results:
                state = result.get("state")
                if state == "done":
                    return _read_zip(client, result.get("full_zip_url"))
                if state == "failed":
                    raise KnowledgeError(
                        f"MinerU extraction failed: {result.get('err_msg') or 'unknown error'}")
    raise KnowledgeError("MinerU extraction timed out")


def _read_zip(client, url):
    if not url:
        raise KnowledgeError("MinerU finished without a result archive")
    archive = client.get(url)
    if archive.status_code != 200:
        raise KnowledgeError(f"MinerU result download failed (HTTP {archive.status_code})")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        names = [name for name in bundle.namelist() if name.endswith(".md")]
        if not names:
            raise KnowledgeError("MinerU result archive contained no markdown")
        # "full.md" is the whole document; anything else is a per-section fragment.
        name = next((item for item in names if item.endswith("full.md")), sorted(names)[0])
        return bundle.read(name).decode("utf-8", errors="replace")


def to_markdown(data, filename, media_type, *, engine="auto", mineru_base_url=None):
    """Return ``(text, engine_used, metadata)`` or raise ``KnowledgeError``."""
    requested = engine or "auto"
    if _is_pdf(filename, media_type):
        order = ["pymupdf4llm", "mineru"] if requested == "auto" else [requested]
        errors = []
        for candidate in order:
            try:
                if candidate == "pymupdf4llm":
                    text = _pymupdf_markdown(data)
                elif candidate == "mineru":
                    if not mineru_base_url:
                        raise KnowledgeError("No MinerU base URL is configured")
                    text = _mineru_markdown(data, filename, mineru_base_url)
                elif candidate == "text":
                    text = _text_markdown(data, filename)
                else:
                    raise KnowledgeError(f"Unknown PDF engine {candidate!r}")
            except ImportError:
                errors.append(f"{candidate}: not installed")
            except KnowledgeError as error:
                errors.append(f"{candidate}: {error}")
            else:
                return _truncate(text, candidate, filename)
        raise KnowledgeError(
            "No PDF engine could convert this file (" + "; ".join(errors) + "). "
            "Install pymupdf4llm into the backend environment or configure MinerU.")

    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES or (media_type or "").startswith("text/"):
        return _truncate(_text_markdown(data, filename), "text", filename)
    raise KnowledgeError(
        f"No markdown engine handles {media_type or suffix or 'this file'}; "
        "upload a PDF or a text-like file.")


def _truncate(text, engine, filename):
    encoded = text.encode("utf-8")
    truncated = len(encoded) > MAX_MARKDOWN_BYTES
    if truncated:
        text = encoded[:MAX_MARKDOWN_BYTES].decode("utf-8", errors="ignore")
        text += "\n\n*[Markdown truncated by the knowledge base.]*"
    return text, engine, {"engine": engine, "filename": filename, "truncated": truncated,
                          "characters": len(text)}
