"""Portable PDF documents and native OAW library containers."""
from open_agent_world.plugin_api import PackDefinition
import base64
import hashlib
from datetime import UTC, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import DocumentVersion, PaperMetadata, Sha256, SourceAnchor, source_anchor_status
from open_agent_world.plugin_api import (
    NodeTypeDefinition, NodeContainerDefinition, NodeDocumentDefinition,
    NodeDocumentAction, NodeDocumentDownload, PluginDescriptor, RelationshipDefinition,
    CapabilityDefinition, CapabilityGrantDefinition, ResourceValidationError,
)

class RegionConfig(BaseModel):
    description: str = "A field of literature, agents and research data."

class PaperConfig(BaseModel):
    authors: str = ""
    year: str = ""
    doi: str = ""


# The browser item order/UTF-16 ranges are a different contract from PyMuPDF's
# page-text extraction. A PDF.js upgrade must explicitly version this mapping.
PDFJS_TEXT_INDEX_VERSION = "pdfjs-text-items-v1"


class PaperEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    claim: str = Field(min_length=1, max_length=20_000)
    kind: Literal["author_statement", "agent_inference", "user_note", "hypothesis"] = "user_note"
    source_anchor_id: str | None = Field(default=None, min_length=1, max_length=128)
    review_status: Literal["unreviewed"] = "unreviewed"

    @model_validator(mode="after")
    def traceable_source(self):
        if not self.claim.strip():
            raise ValueError("Evidence claim cannot be blank")
        if self.kind in {"author_statement", "agent_inference"} and not self.source_anchor_id:
            raise ValueError("Source-derived evidence requires a source anchor")
        return self


def _pdf_hash(encoded):
    raw = base64.b64decode(encoded, validate=True)
    if not raw.startswith(b"%PDF-") or len(raw) > 25 * 1024 * 1024:
        raise ValueError("Upload a PDF up to 25 MiB")
    return hashlib.sha256(raw).hexdigest()


def _validate_anchor_binding(anchor, document, *, page=None, quote=None):
    anchor = SourceAnchor.model_validate(anchor)
    if document is None or source_anchor_status(anchor, document) != "current":
        raise ValueError("Source anchor must identify the current PDF and a valid page")
    if page is not None and anchor.page != page:
        raise ValueError("Source anchor page must match its annotation")
    if quote is not None and anchor.quote != quote:
        raise ValueError("Source anchor quote must match the annotation's exact source text")
    if anchor.text_ranges and anchor.text_parser_version != PDFJS_TEXT_INDEX_VERSION:
        raise ValueError("Source anchor text ranges require the current PDF.js text-item parser")
    return anchor.model_copy(update={"status": "current"})

class PaperAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    paper_id: str = Field(min_length=1, max_length=128)
    kind: Literal["supplement"] = "supplement"
    filename: str = Field(min_length=1, max_length=200)
    sha256: Sha256
    created_at: datetime


class PaperDocument(BaseModel):
    schema_version: Literal[2] = 2
    filename: str = "paper.pdf"
    pdf: str = ""
    thumbnail: str = ""
    pages: int = 0
    text: list[str] = Field(default_factory=list)
    page: int = 1
    notes: str = ""
    annotations: list[dict] = Field(default_factory=list)
    study_layout: bool = False
    study_title: str = "学习画布"
    study_relationships: list[dict] = Field(default_factory=list, max_length=5000)
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
    versions: list[DocumentVersion] = Field(default_factory=list)
    current_document_version_id: Sha256 | None = None
    source_anchors: list[SourceAnchor] = Field(default_factory=list, max_length=5000)
    evidence: list[PaperEvidence] = Field(default_factory=list, max_length=5000)
    reading_status: Literal["unread", "screened", "close_read", "verified"] = "unread"
    attachments: list[PaperAttachment] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def versioned_sources(self):
        identifiers = [version.id for version in self.versions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate PDF version manifests")
        current = None
        if self.pdf:
            digest = _pdf_hash(self.pdf)
            if self.current_document_version_id is not None and self.current_document_version_id != digest:
                raise ValueError("Current PDF version does not match the uploaded bytes")
            current = next((version for version in self.versions if version.id == digest), None)
            if current is None:
                # Additive legacy read projection, not a migration write. The
                # original import timestamp/parser version cannot be recovered.
                current = DocumentVersion(sha256=digest, filename=self.filename, pages=self.pages)
                self.versions = [*self.versions, current]
            elif current.pages != self.pages:
                raise ValueError("Current PDF page count disagrees with its version manifest")
            self.current_document_version_id = digest
        elif self.current_document_version_id is not None:
            raise ValueError("A Paper without PDF bytes cannot have a current document version")
        versions = {version.id: version for version in self.versions}
        anchor_ids = [anchor.id for anchor in self.source_anchors if anchor.id is not None]
        if len(set(anchor_ids)) != len(anchor_ids):
            raise ValueError("Duplicate source anchor IDs")
        normalized_anchors = []
        for anchor in self.source_anchors:
            owner = versions.get(anchor.document_version_id)
            if owner is None or anchor.page > owner.pages:
                raise ValueError("Source anchor refers to an unknown version or page")
            if current and anchor.document_version_id == current.id:
                normalized_anchors.append(_validate_anchor_binding(anchor, current))
            else:
                normalized_anchors.append(anchor.model_copy(update={"status": "needs_relocation"}))
        self.source_anchors = normalized_anchors
        # Old annotations remain byte-for-byte equivalent except for this
        # additive version tag. Do not invent PDF.js text-item selectors.
        normalized_annotations = []
        for original in self.annotations:
            annotation = dict(original)
            if current:
                if annotation.get("document_version_id") not in {None, current.id}:
                    raise ValueError("An active annotation belongs to a different PDF version")
                annotation["document_version_id"] = current.id
            if annotation.get("source_anchor") is not None:
                annotation["source_anchor"] = _validate_anchor_binding(annotation["source_anchor"], current,
                    page=annotation.get("page"), quote=annotation.get("text", "")).model_dump(mode="json")
            normalized_annotations.append(annotation)
        self.annotations = normalized_annotations
        learning_ids = {item["id"] for item in self.annotations if item.get("learning")}
        relationship_keys = set()
        for relationship in self.study_relationships:
            key = (relationship.get("source"),relationship.get("target"),relationship.get("type"))
            if set(relationship) != {"id","source","target","type"} or not isinstance(relationship["id"],str) or not 1 <= len(relationship["id"]) <= 128:
                raise ValueError("Invalid study relationship")
            if key[0] not in learning_ids or key[1] not in learning_ids or key[0] == key[1] or key[2] not in {"supports","contradicts","depends_on","derived_from"}:
                raise ValueError("Study relationships require two distinct current learning excerpts and a semantic type")
            if key in relationship_keys:
                raise ValueError("Duplicate study relationship")
            relationship_keys.add(key)
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Duplicate evidence IDs")
        for item in self.evidence:
            if item.source_anchor_id and item.source_anchor_id not in anchor_ids:
                raise ValueError("Evidence refers to an unknown source anchor")
        return self

def import_pdf(value, arguments):
    import pymupdf
    try:
        previous = PaperDocument.model_validate(value).model_dump(mode="json")
        raw = base64.b64decode(arguments.get("pdf", ""), validate=True)
        if not raw.startswith(b"%PDF-") or len(raw) > 25 * 1024 * 1024:
            raise ValueError("Upload a PDF up to 25 MiB")
        digest = hashlib.sha256(raw).hexdigest()
        if digest == previous["current_document_version_id"]:
            # Filename changes and network retries must not erase reading work.
            return previous
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if pdf.needs_pass or not len(pdf):
                raise ValueError("PDF must contain readable, unencrypted pages")
            thumbnail = pdf[0].get_pixmap(matrix=pymupdf.Matrix(.35, .35)).tobytes("png")
            filename = str(arguments.get("filename", "paper.pdf"))[:200] or "paper.pdf"
            versions = previous["versions"]
            if not any(version["id"] == digest for version in versions):
                versions = [*versions, DocumentVersion(sha256=digest, filename=filename, pages=len(pdf),
                    imported_at=datetime.now(UTC), text_parser_version=f"pymupdf/{pymupdf.VersionBind}:page-text-v1",
                    kind=arguments.get("kind", "main"), version_label=arguments.get("version_label", "")).model_dump(mode="json")]
            return PaperDocument.model_validate({**previous, "filename": filename,
                    "pdf": base64.b64encode(raw).decode(), "pages": len(pdf), "page": 1, "annotations": [], "notes": "", "study_layout": False, "study_relationships": [],
                    "text": [p.get_text() for p in pdf],
                    "thumbnail": "data:image/png;base64," + base64.b64encode(thumbnail).decode(),
                    "versions": versions, "current_document_version_id": digest, "reading_status": "unread"}).model_dump(mode="json")
    except Exception as exc:
        raise ResourceValidationError(f"Cannot import PDF: {exc}") from exc

def annotate(value, args):
    # Pure helper callers may still pass the pre-v2 minimal dict. Full stored
    # documents are normalized by the host and carry their current version.
    if value.get("pdf"):
        value = PaperDocument.model_validate(value).model_dump(mode="json")
    page = int(args.get("page", value["page"]))
    if page < 1 or page > max(1, value["pages"]):
        raise ResourceValidationError("Page is outside the PDF")
    annotations = list(value.get("annotations", []))
    if "annotation" in args:
        from uuid import uuid4
        item = args["annotation"]
        existing = next((a for a in annotations if a["id"] == item.get("id")), None)
        if not existing and len(annotations) >= 2000:
            raise ResourceValidationError("At most 2000 annotations per paper")
        rectangles = item.get("rects", [])
        if len(rectangles) > 500 or any(len(r) != 4 or any(not isinstance(x, (float, int)) or not 0 <= x <= 1 for x in r) for r in rectangles):
            raise ResourceValidationError("Invalid normalized selection rectangles")
        import re
        import math
        color = item.get("color", "#f4d144")
        title_color = item.get("title_color", existing.get("title_color", "#36423b") if existing else "#36423b")
        image = item.get("image", "")
        position = item.get("position", {"x": 0, "y": 0})
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ResourceValidationError("Invalid highlight color")
        if not isinstance(title_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", title_color):
            raise ResourceValidationError("Invalid title color")
        if image and (not image.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")) or len(image) > 4*1024*1024):
            raise ResourceValidationError("Image must be PNG/JPEG, up to 4 MiB encoded")
        if not isinstance(position, dict) or any(not isinstance(position.get(k), (float, int)) or not math.isfinite(position[k]) or abs(position[k]) > 1e7 for k in ("x", "y")):
            raise ResourceValidationError("Invalid canvas position")
        identifier = existing["id"] if existing else str(item.get("id") or uuid4())[:100]
        annotations = [a for a in annotations if a["id"] != identifier]
        updated = {"id": identifier, "page": page, "text": str(item.get("text", ""))[:20000],
            "comment": str(item.get("comment", ""))[:20000], "translation": str(item.get("translation", ""))[:40000], "rects": rectangles,
            "title": str(item.get("title", ""))[:300], "color": color, "image": image,
            "title_color": title_color, "collapsed": bool(item.get("collapsed", existing.get("collapsed", False) if existing else False)),
            "learning": bool(item.get("learning", False)), "position": position}
        current_id = value.get("current_document_version_id")
        supplied_id = item.get("document_version_id", existing.get("document_version_id") if existing else None)
        if supplied_id is not None and supplied_id != current_id:
            raise ResourceValidationError("Annotation belongs to a different PDF version")
        source = item.get("source_anchor", existing.get("source_anchor") if existing else None)
        if current_id:
            current = next(version for version in value["versions"] if version["id"] == current_id)
            updated["document_version_id"] = current_id
            if source is None:
                # Reusing a UI annotation ID after a replacement must never
                # retarget evidence that points to an older PDF's anchor.
                anchor_key = hashlib.sha256(f"{current_id}\0{identifier}".encode()).hexdigest()
                source = {"id": f"annotation:{anchor_key}", "document_version_id": current_id, "document_sha256": current_id,
                          "page": page, "quote": updated["text"], "rects": rectangles}
            try:
                selected = _validate_anchor_binding(source, current, page=page, quote=updated["text"])
            except ValueError as error:
                raise ResourceValidationError(str(error)) from error
            updated["source_anchor"] = selected.model_dump(mode="json")
        elif source is not None:
            raise ResourceValidationError("A source anchor requires an imported PDF")
        annotations.append(updated)
    if "delete_annotation" in args:
        annotations = [a for a in annotations if a["id"] != args["delete_annotation"]]
    if "study_positions" in args:
        import math
        positions = args["study_positions"]
        valid_ids = {a["id"] for a in annotations if a.get("learning")}
        if not isinstance(positions, dict) or set(positions) != valid_ids:
            raise ResourceValidationError("Layout must include all current learning excerpts")
        for position in positions.values():
            if not isinstance(position, dict) or any(not isinstance(position.get(k), (int, float)) or not math.isfinite(position[k]) or abs(position[k]) > 1e7 for k in ("x", "y")):
                raise ResourceValidationError("Invalid layout position")
        annotations = [{**a, "position": positions[a["id"]]} if a["id"] in positions else a for a in annotations]
    anchors = list(value.get("source_anchors", []))
    for annotation in annotations:
        source = annotation.get("source_anchor")
        if source and source.get("id"):
            existing_anchor = next((anchor for anchor in anchors if anchor.get("id") == source["id"]), None)
            if existing_anchor and existing_anchor["document_version_id"] != source["document_version_id"]:
                raise ResourceValidationError("Use a new anchor ID when locating a different PDF version")
            anchors = [anchor for anchor in anchors if anchor.get("id") != source["id"]]
            anchors.append(source)
    result = {**value, "page": page, "notes": str(args.get("notes", value["notes"]))[:100000], "annotations": annotations,
            "study_layout": True if "study_positions" in args else value.get("study_layout", False),
            "study_title": str(args.get("study_title", value.get("study_title", "学习画布"))).strip()[:100] or "学习画布"}
    learning_ids = {item["id"] for item in annotations if item.get("learning")}
    result["study_relationships"] = args.get("study_relationships", [item for item in value.get("study_relationships", [])
        if item["source"] in learning_ids and item["target"] in learning_ids])
    if anchors or "source_anchors" in value:
        result["source_anchors"] = anchors
    return result


def metadata_view(value):
    document = PaperDocument.model_validate(value).model_dump(mode="json")
    return {key: document[key] for key in ("metadata", "versions", "current_document_version_id", "reading_status")} | {
        "has_pdf": bool(document["pdf"]), "pages": document["pages"],
        "source_anchor_count": len(document["source_anchors"]), "evidence_count": len(document["evidence"])}


def update_metadata(value, args):
    if set(args) - {"metadata", "reading_status"} or not isinstance(args.get("metadata", {}), dict):
        raise ResourceValidationError("Supply a metadata patch and optional reading_status")
    document = PaperDocument.model_validate(value).model_dump(mode="json")
    document["metadata"] = PaperMetadata.model_validate({**document["metadata"], **args.get("metadata", {})}).model_dump(mode="json")
    if "reading_status" in args:
        document["reading_status"] = args["reading_status"]
    return PaperDocument.model_validate(document).model_dump(mode="json")


def save_source_anchor(value, args):
    if set(args) != {"anchor"}:
        raise ResourceValidationError("Supply one source anchor")
    document = PaperDocument.model_validate(value).model_dump(mode="json")
    current = next((version for version in document["versions"] if version["id"] == document["current_document_version_id"]), None)
    selected = _validate_anchor_binding(args["anchor"], current)
    if not selected.id:
        raise ResourceValidationError("Supply a stable source anchor ID")
    existing = next((anchor for anchor in document["source_anchors"] if anchor.get("id") == selected.id), None)
    if existing and existing["document_version_id"] != selected.document_version_id:
        raise ResourceValidationError("Use a new anchor ID when locating a different PDF version")
    document["source_anchors"] = [anchor for anchor in document["source_anchors"] if anchor.get("id") != selected.id] + [selected.model_dump(mode="json")]
    return PaperDocument.model_validate(document).model_dump(mode="json")


def save_evidence(value, args):
    if set(args) != {"evidence"}:
        raise ResourceValidationError("Supply one evidence record")
    document = PaperDocument.model_validate(value).model_dump(mode="json")
    record = PaperEvidence.model_validate(args["evidence"]).model_dump(mode="json")
    document["evidence"] = [item for item in document["evidence"] if item["id"] != record["id"]] + [record]
    return PaperDocument.model_validate(document).model_dump(mode="json")

def read(value, args):
    if args.get("view") == "metadata":
        return metadata_view(value)
    if not value.get("pdf"):
        raise ResourceValidationError("This Paper has no PDF; use view='metadata' to read its available metadata")
    page = int(args.get("page", 1))
    if page < 1 or page > len(value["text"]):
        raise ResourceValidationError("Page is outside the PDF")
    return {"filename": value["filename"], "pages": value["pages"], "page": page,
            "text": value["text"][page-1], "notes": value["notes"],
            "metadata": value.get("metadata", {}), "document_version_id": value.get("current_document_version_id")}


class PaperWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["metadata", "source_anchor", "evidence"]
    expected_revision: int = Field(ge=0, strict=True)
    metadata: PaperMetadata | None = None
    reading_status: Literal["unread", "screened", "close_read", "verified"] | None = None
    anchor: SourceAnchor | None = None
    evidence: PaperEvidence | None = None

class LibraryPlugin:
    descriptor = PluginDescriptor(id="research.library", version="0.2.0",
        plugin_api_version="1.14", name="Library", description="PDF reading and native research spaces")

    async def read_paper(self, context, capability, arguments):
        document = await context.node_document_action(capability, "read", arguments)
        result = read(document["value"], arguments)
        if "revision" in document:
            result["revision"] = document["revision"]
        return result

    async def update_paper(self, context, capability, arguments):
        request = PaperWriteRequest.model_validate(arguments)
        if request.anchor and request.anchor.paper_id not in {None, capability.target_id}:
            raise ResourceValidationError("Source anchor must belong to this authorized Paper")
        payload = request.model_dump(mode="json", exclude_unset=True,
                                     exclude={"operation", "expected_revision"})
        document = await context.node_document_action(capability, request.operation, payload,
                                                      expected_revision=request.expected_revision)
        return {"revision": document["revision"], **metadata_view(document["value"])}

    def register(self, registration):
        common = dict(color="#70a79a", deck_id="objects", deck_label="Objects", deck_icon="boxes",
                      default_status="available", statuses=frozenset({"available"}))
        registration.register_node_type(NodeTypeDefinition(id="library.region", label="Library",
            description="Literature field with native movable members", icon="library", default_name="New Library",
            default_size=(1100, 800), config_model=RegionConfig, container=NodeContainerDefinition(content_inset=(24,150,24,32)),
            frontend={"body":"region"}, user_creatable=False, templateable=True, **common))
        registration.register_node_type(NodeTypeDefinition(id="library.paper", label="Paper", description="PDF and reading notes",
            icon="file-text", default_name="Paper", default_size=(300,210), config_model=PaperConfig,
            traits=frozenset({"library.readable"}), templateable=True, deck_revision=2,
            frontend={"preview":"thumbnail", "body":"reader", "workspace":"reader"},
            surfaces={"preview":True,"inspector":True,"workspace":True},
            document=NodeDocumentDefinition(model=PaperDocument, max_size_bytes=48*1024*1024,
                binary_history=("pdf",),
                binary_restore_fields={"pdf": ("page", "notes", "annotations", "study_layout", "study_title", "study_relationships", "reading_status")},
                actions={"import":NodeDocumentAction(import_pdf), "annotate":NodeDocumentAction(annotate),
                         "metadata":NodeDocumentAction(update_metadata, capability_kind="library.write"),
                         "source_anchor":NodeDocumentAction(save_source_anchor, capability_kind="library.write"),
                         "evidence":NodeDocumentAction(save_evidence, capability_kind="library.write"),
                         "read":NodeDocumentAction(read, read_only=True, capability_kind="library.read")},
                downloads={"pdf":lambda v: NodeDocumentDownload(v["filename"],base64.b64decode(v["pdf"]),"application/pdf")}), **common))
        registration.register_capability(CapabilityDefinition(kind="library.read", tool_name="read_paper",
            description="Read a Paper's metadata (view=metadata), or extracted text and notes from one PDF page. Scanned pages may have no text. Returns document revision for authorized updates.",
            input_schema={"type":"object","properties":{"page":{"type":"integer","minimum":1},"view":{"type":"string","enum":["page","metadata"]}},"additionalProperties":False}), self.read_paper)
        registration.register_capability(CapabilityDefinition(kind="library.write", tool_name="update_paper",
            description="Update metadata, save a source anchor tied to the current PDF, or record unreviewed evidence on this Paper. Read first and supply expected_revision. Does not import/replace PDF bytes or grant scientific verification.",
            input_schema=PaperWriteRequest.model_json_schema()), self.update_paper)
        registration.register_relationship(RelationshipDefinition(id="library.read",label="Read paper",short_label="read",
            description="Read this paper's text",source_traits=frozenset({"core.agent"}),target_traits=frozenset({"library.readable"}),
            capabilities=(CapabilityGrantDefinition(kind="library.read"),),templateable=True))
        registration.register_relationship(RelationshipDefinition(id="library.write",label="Curate paper",short_label="curate",
            description="Read and update this Paper's metadata and source-linked research records; cannot replace its PDF.",
            source_traits=frozenset({"core.agent"}),target_types=frozenset({"library.paper"}),
            capabilities=(CapabilityGrantDefinition(kind="library.read"),CapabilityGrantDefinition(kind="library.write")),templateable=True))
        registration.register_relationship(RelationshipDefinition(id="library.research",label="Research",short_label="research",
            description="Associate an Agent with a library; membership alone grants no paper access.",
            source_traits=frozenset({"core.agent"}),target_types=frozenset({"library.region"}),templateable=True))
        registration.register_pack(PackDefinition(id='research.library.default', name='Research Library',
            description='Papers and reading spaces.', cards=tuple(registration.nodes)))

def create_plugin():
    return LibraryPlugin()
