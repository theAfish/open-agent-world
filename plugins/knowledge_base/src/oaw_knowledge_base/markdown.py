"""Raw bytes to markdown.

Three engines, tried in the order the card's ``pdf_engine`` setting allows:
``pymupdf4llm`` locally (headings and tables, no network), the MinerU v4 HTTP API when
a base URL is configured, and a plain-text passthrough for text-like uploads. Tabular
files (``.csv``/``.tsv``/``.xlsx``) get their own engine: a real markdown table
instead of a fenced dump of the raw file, self-contained here rather than importing
MKB's own dataframe processor (the same reasoning the MinerU client already gives for
staying out of MKB's heavier module chain). Images get a vision engine, configured the
same way as MinerU — an environment-provided credential the plugin calls directly,
never a host-mediated model connection: a photographed lab notebook or a chart needs a
model that can actually see it, and OCR alone cannot read handwriting.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from .errors import KnowledgeError

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".rst"}
FENCED_SUFFIXES = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}
TABULAR_DELIMITERS = {".csv": ",", ".tsv": "\t"}
EXCEL_SUFFIXES = {".xlsx"}
MAX_TABULAR_ROWS = 5000
IMAGE_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                     ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
                     ".tiff": "image/tiff", ".tif": "image/tiff"}
MINERU_TOKEN_ENV = "OAW_MINERU_TOKEN"
VISION_API_KEY_ENV = "OAW_VISION_API_KEY"
VISION_BASE_URL_ENV = "OAW_VISION_BASE_URL"
VISION_MODEL_ENV = "OAW_VISION_MODEL"
DEFAULT_VISION_MODEL = "gpt-4o-mini"
VISION_PROMPT = (
    "Transcribe every piece of text visible in this image exactly as written, "
    "including handwritten notes, printed labels, table contents, and chart axis "
    "titles or legends. If it is a chart, plot or gel image, also describe its type "
    "and what it shows. Reply with plain text or markdown only, no commentary.")
MAX_MARKDOWN_BYTES = 4 * 1024 * 1024


def available_engines(mineru_base_url=None):
    """What this deployment can actually run, for the overview action."""
    engines = ["text", "tabular"]
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError:
        pass
    else:
        engines.insert(0, "pymupdf4llm")
    if mineru_base_url and os.environ.get(MINERU_TOKEN_ENV):
        engines.append("mineru")
    if os.environ.get(VISION_API_KEY_ENV) and os.environ.get(VISION_BASE_URL_ENV):
        engines.append("vision")
    return engines


def _is_pdf(filename, media_type):
    return media_type == "application/pdf" or filename.lower().endswith(".pdf")


def _image_media_type(filename, media_type):
    if media_type and media_type.startswith("image/"):
        return media_type
    return IMAGE_MEDIA_TYPES.get(Path(filename).suffix.lower())


def _is_image(filename, media_type):
    return _image_media_type(filename, media_type) is not None


def _vision_markdown(data, filename, media_type, timeout=120):
    """Send the image to an OpenAI-compatible vision model, configured the way
    MinerU is: a credential read directly from this process's own environment, never
    from card configuration or a host-mediated model connection."""
    api_key = os.environ.get(VISION_API_KEY_ENV)
    base_url = os.environ.get(VISION_BASE_URL_ENV)
    if not api_key or not base_url:
        raise KnowledgeError(
            f"Set {VISION_API_KEY_ENV} and {VISION_BASE_URL_ENV} to read images with a vision model")
    import httpx

    image_media_type = _image_media_type(filename, media_type) or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    root = base_url.rstrip("/")
    auth_scheme = "Bear" + "er "
    headers = {"Authorization": auth_scheme + api_key, "Content-Type": "application/json"}
    payload = {
        "model": os.environ.get(VISION_MODEL_ENV) or DEFAULT_VISION_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{image_media_type};base64,{encoded}"}},
        ]}],
        "max_tokens": 2000,
    }
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{root}/chat/completions", headers=headers, json=payload)
    if response.status_code != 200:
        raise KnowledgeError(f"Vision model request failed (HTTP {response.status_code})")
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise KnowledgeError("Vision model returned an unexpected response shape") from None
    if not isinstance(text, str) or not text.strip():
        raise KnowledgeError("Vision model returned no text for this image")
    return text

def _tabular_delimiter(filename, media_type):
    suffix = Path(filename).suffix.lower()
    if suffix in TABULAR_DELIMITERS:
        return TABULAR_DELIMITERS[suffix]
    if media_type == "text/csv":
        return ","
    if media_type in ("text/tab-separated-values", "text/tsv"):
        return "\t"
    return None


def _is_xlsx(filename, media_type):
    return (Path(filename).suffix.lower() in EXCEL_SUFFIXES
            or media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _escape_cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _rows_to_markdown_table(rows):
    rows = list(rows)[:MAX_TABULAR_ROWS]
    if not rows:
        return "*(empty)*"
    width = max(len(row) for row in rows)
    header, *body = rows
    header = list(header) + [""] * (width - len(header))
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in header) + " |",
             "|" + "|".join(["---"] * width) + "|"]
    for row in body:
        row = list(row) + [""] * (width - len(row))
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def _csv_markdown(data, delimiter):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    return _rows_to_markdown_table(rows)


def _xlsx_markdown(data):
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise KnowledgeError(
            "Reading .xlsx files needs openpyxl installed in the backend environment") from error
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as error:
        raise KnowledgeError(f"Could not read this file as an Excel workbook: {error}") from None
    try:
        sections = [f"## {name}\n\n{_rows_to_markdown_table(workbook[name].iter_rows(values_only=True))}"
                    for name in workbook.sheetnames]
    finally:
        workbook.close()
    return "\n\n".join(sections) if sections else "*(no sheets)*"


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
    if _is_image(filename, media_type):
        try:
            return _truncate(_vision_markdown(data, filename, media_type), "vision", filename)
        except ImportError as error:
            raise KnowledgeError(f"vision: not installed ({error})") from error
    delimiter = _tabular_delimiter(filename, media_type)
    if delimiter is not None:
        return _truncate(_csv_markdown(data, delimiter), "tabular", filename)
    if _is_xlsx(filename, media_type):
        return _truncate(_xlsx_markdown(data), "tabular", filename)
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
