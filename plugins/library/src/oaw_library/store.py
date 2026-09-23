"""Paper files: immutable raw PDF plus versioned structured extractions.

Layout inside the node's storage directory (``storage_path``)::

    raw.pdf                     original bytes, never modified
    thumbnail.png               cover preview
    text/pages.json             plain text per page (read_paper)
    extractions/<v>.json        PaperStructure, one file per version
    extractions/<v>/figures/*   figure crops referenced by that version
    manifest.json               written last; the commit point of every change

Structure comes from GROBID (grobid.py), run as a host background job so the
graph is never blocked on it. If GROBID is unreachable or fails, the PDF is still
imported: the manifest records ``extraction_error`` and a person or curating
Agent can re-extract later. Actions and job commits run under the host's node
mutation lock, so manifest updates are serialized.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_agent_world.plugin_api import ConflictError, NodeResourceContext, ResourceValidationError

from . import grobid
from .schema import PaperStructure

MANIFEST = "manifest.json"
RAW = "raw.pdf"
THUMBNAIL = "thumbnail.png"
PAGES = "text/pages.json"
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_READ_CHARS = 60_000


# Files the trusted UI may read through the host's file route (see served_files).
SERVED_FILES = (RAW, THUMBNAIL, "extractions/*.json", "extractions/*/figures/*.png")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PaperFiles:
    """The Paper's files in its host-reserved directory. Keys are this module's own constants."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*key.split("/"))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def get_json(self, key: str):
        return json.loads(self.get(key))

    def put(self, key: str, data: bytes) -> None:
        # Write, fsync, then rename: readers never see a partial file.
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with staging.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
        finally:
            staging.unlink(missing_ok=True)

    def put_json(self, key: str, value) -> None:
        self.put(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def clear(self) -> None:
        # The directory is reserved by the host for this node alone.
        shutil.rmtree(self.root, ignore_errors=True)


def files(context: NodeResourceContext) -> PaperFiles:
    return PaperFiles(context.storage_path)


def load_manifest(context: NodeResourceContext) -> dict | None:
    paper = files(context)
    return paper.get_json(MANIFEST) if paper.exists(MANIFEST) else None


def require_manifest(context: NodeResourceContext) -> dict:
    manifest = load_manifest(context)
    if manifest is None:
        raise ResourceValidationError("No PDF has been imported into this Paper yet")
    return manifest


def load_structure(context: NodeResourceContext, manifest: dict, version: str | None = None) -> tuple[dict, dict]:
    version = version or manifest.get("active")
    if version is None:
        if manifest.get("extracting"):
            raise ResourceValidationError("GROBID is still extracting this Paper; try again in a minute.")
        error = (manifest.get("extraction_error") or {}).get("message", "")
        raise ResourceValidationError(f"This Paper has no structured extraction yet. {error}".strip())
    entry = next((item for item in manifest["extractions"] if item["id"] == version), None)
    if entry is None:
        raise ResourceValidationError(f"Unknown extraction version {version!r}")
    return entry, files(context).get_json(entry["key"])


def store_version(context: NodeResourceContext, manifest: dict, structure: dict, images: dict[str, bytes], *,
                  source: str, based_on: str | None = None, note: str = "") -> dict:
    version = f"v{len(manifest['extractions']) + 1}"
    for figure in structure["figures"]:
        image = figure.get("image")
        if image in images:  # New crops are version-scoped so older versions keep their own files.
            figure["image"] = f"extractions/{version}/{image}"
            files(context).put(figure["image"], images[image])
    structure = PaperStructure.model_validate(structure).model_dump(mode="json")
    key = f"extractions/{version}.json"
    files(context).put_json(key, structure)
    provenance = structure["provenance"]
    manifest["extractions"].append({"id": version, "key": key, "created_at": now(), "source": source,
        "extractor": provenance["extractor"], "actor_id": context.actor_id, "based_on": based_on, "note": note[:500],
        "warnings": len(provenance["warnings"])})
    manifest["active"] = version
    manifest["extraction_error"] = None
    files(context).put_json(MANIFEST, manifest)
    return manifest


# ---- Extraction jobs ------------------------------------------------------------------------
# GROBID can take minutes, so it runs as a host background job (context.background):
# the PDF is committed first, the manifest records the job, and the result lands as a
# new version. Jobs do not survive a restart; a job this process does not know is shown
# as interrupted and can be re-extracted.

_RUNNING: dict[str, str] = {}  # node id -> job id started by this process
_RUNNING_LOCK = threading.Lock()


def _job_expired(job: dict) -> bool:
    # Three GROBID attempts plus slack; past that the commit is lost, not late.
    limit = 3 * float(os.environ.get("OAW_GROBID_TIMEOUT", "180")) + 120
    return (datetime.now(UTC) - datetime.fromisoformat(job["started_at"])).total_seconds() > limit


def view(context: NodeResourceContext, manifest: dict) -> dict:
    """The manifest as people and Agents see it, with ``extracting`` resolved."""
    manifest = dict(manifest)
    job = manifest.get("job")
    with _RUNNING_LOCK:
        live = bool(job) and _RUNNING.get(context.node_id) == job["id"] and not _job_expired(job)
    if job and not live:
        manifest["job"] = None
        manifest["extraction_error"] = {"message": "The extraction was interrupted (the host stopped, timed out or could not save it). "
                                        "Re-extract to try again.", "at": job["started_at"]}
    manifest["extracting"] = live
    return manifest


def _record_error(context: NodeResourceContext, manifest: dict, message: str) -> dict:
    manifest["job"] = None
    manifest["extraction_error"] = {"message": message[:500], "at": now()}
    files(context).put_json(MANIFEST, manifest)
    return manifest


def start_extraction(context: NodeResourceContext, manifest: dict, raw: bytes, text: list[str], *, note: str = "") -> dict:
    """Queue a GROBID version, or record why there is none. Never loses the imported PDF."""
    if sum(len(page.strip()) for page in text) < 80 * len(text):
        return view(context, _record_error(context, manifest,
            "The PDF has little or no text layer (scanned?). Run OCR first; GROBID does not OCR."))
    filename = manifest["filename"]

    def work(cancelled=None):
        import pymupdf
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            return grobid.extract(raw, pdf, filename=filename)

    def forget() -> None:
        with _RUNNING_LOCK:
            if _RUNNING.get(context.node_id) == job["id"]:
                del _RUNNING[context.node_id]

    def commit(target: NodeResourceContext, outcome) -> None:
        forget()
        current = load_manifest(target)
        if current is None or (current.get("job") or {}).get("id") != job["id"]:
            return  # Superseded, or the Paper was emptied meanwhile.
        if isinstance(outcome, grobid.ExtractionError):
            _record_error(target, current, str(outcome))
        elif isinstance(outcome, Exception):
            _record_error(target, current, f"Extraction failed: {type(outcome).__name__}: {outcome}")
        else:
            structure, images = outcome
            current["job"] = None
            try:
                store_version(target, current, structure, images, source="grobid", note=note)
            except (ValidationError, ValueError) as error:  # Never leave the job looking alive.
                _record_error(target, current, f"GROBID output did not match the schema: {error}")

    job = {"id": uuid4().hex, "started_at": now()}
    manifest["job"] = job
    manifest["extraction_error"] = None
    files(context).put_json(MANIFEST, manifest)
    with _RUNNING_LOCK:
        _RUNNING[context.node_id] = job["id"]
    if context.background is None:  # A host without background jobs: extract inline.
        try:
            outcome = work()
        except Exception as error:
            outcome = error
        commit(context, outcome)
        return view(context, load_manifest(context))
    context.background(work, commit, forget)  # A lost commit shows as interrupted.
    return view(context, manifest)


def _store_raw(paper: PaperFiles, raw: bytes, filename: str) -> tuple[dict, list[str]]:
    """Validate and commit the raw layer; returns (manifest, page text)."""
    import pymupdf
    if not raw.startswith(b"%PDF-") or len(raw) > MAX_PDF_BYTES:
        raise ResourceValidationError("Cannot import PDF: upload a PDF up to 25 MiB")
    try:
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if pdf.needs_pass or not len(pdf):
                raise ValueError("PDF must contain readable, unencrypted pages")
            thumbnail = pdf[0].get_pixmap(matrix=pymupdf.Matrix(.35, .35)).tobytes("png")
            text = [page.get_text() for page in pdf]
            pages = len(pdf)
    except (RuntimeError, ValueError) as error:
        raise ResourceValidationError(f"Cannot import PDF: {error}") from error
    paper.put(RAW, raw)
    paper.put(THUMBNAIL, thumbnail)
    paper.put_json(PAGES, text)
    manifest = {"format": 1, "filename": filename[:200], "pages": pages, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "imported_at": now(), "raw": RAW, "thumbnail": THUMBNAIL, "extractions": [], "active": None,
                "extraction_error": None, "job": None}
    paper.put_json(MANIFEST, manifest)  # The PDF is committed before the (slow, fallible) GROBID call.
    return manifest, text


def import_pdf(context: NodeResourceContext, arguments: dict) -> dict:
    if load_manifest(context) is not None:
        raise ConflictError("This Paper already has a PDF. Create a new Paper for another file.")
    try:
        raw = base64.b64decode(arguments.get("pdf", ""), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise ResourceValidationError("Cannot import PDF: invalid base64 data") from None
    manifest, text = _store_raw(files(context), raw, str(arguments.get("filename") or "paper.pdf"))
    return start_extraction(context, manifest, raw, text)


def manifest_action(context: NodeResourceContext, arguments: dict) -> dict:
    manifest = load_manifest(context)
    return view(context, manifest) if manifest else {"extractions": [], "active": None, "extracting": False}


def page_text(context: NodeResourceContext, arguments: dict) -> dict:
    manifest = require_manifest(context)
    page = arguments.get("page", 1)
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= manifest["pages"]:
        raise ResourceValidationError(f"Page must be between 1 and {manifest['pages']}")
    text = files(context).get_json(PAGES)[page - 1]
    # Same fingerprint as the library index: changes when the PDF or the active extraction does.
    return {"filename": manifest["filename"], "pages": manifest["pages"], "page": page, "text": text,
            "fingerprint": f"{manifest['sha256']}:{manifest.get('active') or 'text'}"}


def reextract(context: NodeResourceContext, arguments: dict) -> dict:
    manifest = view(context, require_manifest(context))
    if manifest.pop("extracting"):
        raise ConflictError("An extraction is already running for this Paper")
    return start_extraction(context, manifest, files(context).get(RAW), files(context).get_json(PAGES),
                            note=str(arguments.get("note", "")))


def agent_reextract(context: NodeResourceContext, arguments: dict) -> dict:
    manifest = reextract(context, arguments)
    if manifest["extracting"]:
        return {"extracting": True, "hint": "GROBID is running in the background (usually under a minute). "
                "Call read_paper_structure later; the new version becomes active when it finishes."}
    return {"extracting": False, "error": (manifest["extraction_error"] or {}).get("message")}


def activate(context: NodeResourceContext, arguments: dict) -> dict:
    manifest = require_manifest(context)
    entry, _ = load_structure(context, manifest, str(arguments.get("version", "")))
    manifest["active"] = entry["id"]
    files(context).put_json(MANIFEST, manifest)
    return view(context, manifest)


# ---- Path addressing: "metadata.title", "sections.2.blocks.0.text" ----------------------------

_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")


def parse_path(path: str, *, readable: bool = False) -> list[str | int]:
    parts = path.split(".") if isinstance(path, str) and path else []
    if not parts or len(parts) > 12 or not all(_PATH.fullmatch(part) for part in parts):
        raise ResourceValidationError(f"Invalid path {path!r}; use dotted keys and list indexes like sections.2.heading")
    if parts[0] == "provenance" and not readable:
        raise ResourceValidationError("provenance is maintained by the host")
    return [int(part) if part.isdigit() else part for part in parts]


def resolve(value: Any, parts: list[str | int], path: str) -> Any:
    for part in parts:
        try:
            value = value[part]
        except (KeyError, IndexError, TypeError):
            raise ResourceValidationError(f"Path {path!r} does not exist") from None
    return value


def outline(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: outline(item) if isinstance(item, (dict, list)) else _short(item) for key, item in value.items()}
    if isinstance(value, list):
        return {"list_length": len(value), "first": outline(value[0]) if value else None}
    return _short(value)


def _short(value: Any) -> Any:
    return value[:200] + "…" if isinstance(value, str) and len(value) > 200 else value


def overview(entry: dict, structure: dict) -> dict:
    return {
        "version": entry["id"], "source": entry["source"], "schema_version": structure["schema_version"],
        "metadata": structure["metadata"], "abstract": structure["abstract"]["text"], "highlights": structure["highlights"],
        "sections": [{"path": f"sections.{index}", "id": section["id"], "number": section["number"], "heading": section["heading"],
                      "level": section["level"], "blocks": len(section["blocks"]), "loc": section["loc"]} for index, section in enumerate(structure["sections"])],
        "figures": [{"path": f"figures.{index}", "label": figure["label"], "caption": _short(figure["caption"]),
                     "has_image": bool(figure["image"]), "loc": figure["loc"]} for index, figure in enumerate(structure["figures"])],
        "tables": [{"path": f"tables.{index}", "label": table["label"], "caption": _short(table["caption"]),
                    "rows": len(table["rows"]), "loc": table["loc"]} for index, table in enumerate(structure["tables"])],
        "references": len(structure["references"]),
        "back_matter": sorted(key for key, value in structure["back_matter"].items() if value),
        "domain": sorted(structure["domain"]),
        "extractor": structure["provenance"]["extractor"],
        "warnings": structure["provenance"]["warnings"],
        "hint": "Read a part with path, e.g. 'sections.1', 'references', 'tables.0'.",
    }


def read_structure(context: NodeResourceContext, arguments: dict) -> dict:
    manifest = view(context, require_manifest(context))
    entry, structure = load_structure(context, manifest, arguments.get("version"))
    path = arguments.get("path")
    if not path:
        return {**overview(entry, structure), "extracting": manifest["extracting"]}
    value = resolve(structure, parse_path(path, readable=True), path)
    if len(json.dumps(value, ensure_ascii=False)) > MAX_READ_CHARS:
        return {"version": entry["id"], "path": path, "truncated": True, "outline": outline(value),
                "hint": "The value is large. Request a narrower path, such as a single list index."}
    return {"version": entry["id"], "path": path, "value": value}


def _normal(label: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", label.casefold().replace("figure", "").replace("fig", ""))


def figure_image(context: NodeResourceContext, arguments: dict) -> dict:
    """One figure's crop (base64 PNG) with its caption; select by label (Figure 1), id (f1) or path (figures.0)."""
    manifest = view(context, require_manifest(context))
    entry, structure = load_structure(context, manifest, arguments.get("version"))
    figures, selector = structure["figures"], arguments.get("figure")
    if not isinstance(selector, str) or not selector.strip():
        raise ResourceValidationError("Select a figure by label (e.g. Figure 1), id (e.g. f1) or path (e.g. figures.0)")
    wanted = selector.strip()
    path = re.fullmatch(r"figures\.(\d+)", wanted)
    if path:
        index = int(path.group(1)) if int(path.group(1)) < len(figures) else None
    else:
        index = next((i for i, f in enumerate(figures) if f["id"] == wanted), None)
        if index is None and _normal(wanted):
            index = next((i for i, f in enumerate(figures) if _normal(f["label"]) == _normal(wanted)), None)
    if index is None:
        listing = ", ".join(f"{i}: {f['label'] or f['id']}" for i, f in enumerate(figures)) or "none"
        raise ResourceValidationError(f"No figure {selector!r} in {entry['id']}. Figures: {listing}")
    figure = figures[index]
    result = {"version": entry["id"], "path": f"figures.{index}", "id": figure["id"], "label": figure["label"],
              "caption": figure["caption"], "loc": figure["loc"]}
    if not figure["image"] or not files(context).exists(figure["image"]):
        # GROBID often splits a figure into a captioned entry and an unlabelled one holding the graphic.
        page = (figure["loc"] or {}).get("page")
        nearby = [f"figures.{i}" for i, other in enumerate(figures) if other is not figure and other["image"]
                  and page is not None and (other["loc"] or {}).get("page") == page]
        hint = (f"GROBID cropped no image for this entry, but {', '.join(nearby)} on the same page has one "
                "(GROBID sometimes splits a figure from its caption); view that path." if nearby else
                "GROBID located no image for this figure; read the page text instead.")
        return {**result, "image": None, "hint": hint}
    return {**result, "media_type": "image/png", "image": base64.b64encode(files(context).get(figure["image"])).decode()}


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["set", "remove", "append"]
    path: str = Field(min_length=1, max_length=200)
    value: Any = None


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version: str = Field(min_length=1, max_length=20)
    changes: list[Change] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=500)


def apply_changes(structure: dict, changes: list[Change], *, source: str) -> dict:
    result = copy.deepcopy(structure)
    for change in changes:
        parts = parse_path(change.path)
        parent = resolve(result, parts[:-1], change.path)
        leaf = parts[-1]
        if change.op == "set":
            if isinstance(parent, list) and not (isinstance(leaf, int) and leaf < len(parent)):
                raise ResourceValidationError(f"Index {leaf} is outside {change.path!r}; use op 'append'")
            if isinstance(parent, dict) and leaf not in parent and parts[0] != "domain":
                raise ResourceValidationError(f"Path {change.path!r} does not exist")
            if not isinstance(parent, (list, dict)):
                raise ResourceValidationError(f"Path {change.path!r} does not exist")
            parent[leaf] = change.value
        elif change.op == "remove":
            resolve(parent, [leaf], change.path)
            if not isinstance(parent, (list, dict)):
                raise ResourceValidationError(f"Path {change.path!r} does not exist")
            if isinstance(parent, list):
                parent.pop(leaf)
            elif parts[0] == "domain" and len(parts) > 1:
                parent.pop(leaf)
            else:
                raise ResourceValidationError("Only list items and domain keys can be removed; set a field to null instead")
        else:
            target = resolve(result, parts, change.path)
            if not isinstance(target, list):
                raise ResourceValidationError(f"{change.path!r} is not a list")
            target.append(change.value)
    extractor = result["provenance"]["extractor"]
    if not extractor.endswith(f"+{source}"):
        result["provenance"]["extractor"] = f"{extractor}+{source}"
    try:
        return PaperStructure.model_validate(result).model_dump(mode="json")
    except ValidationError as error:
        first = error.errors(include_url=False)[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ResourceValidationError(f"The revised structure is invalid at {where}: {first['msg']}") from None


def revise(context: NodeResourceContext, arguments: dict) -> dict:
    try:
        request = Revision.model_validate(arguments)
    except ValidationError as error:
        first = error.errors(include_url=False)[0]
        raise ResourceValidationError(f"{'.'.join(map(str, first['loc']))}: {first['msg']}") from None
    if len(json.dumps([change.value for change in request.changes], ensure_ascii=False)) > 256 * 1024:
        raise ResourceValidationError("A revision is limited to 256 KiB of values; split it into smaller revisions")
    manifest = require_manifest(context)
    if request.base_version != manifest["active"]:
        raise ConflictError(f"The active extraction is now {manifest['active']}. Read it again and revise that version.")
    _, structure = load_structure(context, manifest, request.base_version)
    source = "agent" if context.actor_id else "user"
    revised = apply_changes(structure, request.changes, source=source)
    manifest = store_version(context, manifest, revised, {}, source=source, based_on=request.base_version, note=request.note)
    return {"version": manifest["active"], "warnings": revised["provenance"]["warnings"]}


__all__ = ["PaperFiles", "SERVED_FILES", "import_pdf", "manifest_action", "page_text", "reextract", "agent_reextract", "activate", "read_structure",
           "revise", "figure_image"]
