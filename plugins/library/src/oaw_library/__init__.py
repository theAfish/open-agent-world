"""Portable PDF documents and native OAW library containers."""
import base64
from pydantic import BaseModel, Field
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

class PaperDocument(BaseModel):
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

def import_pdf(value, arguments):
    import pymupdf
    try:
        raw = base64.b64decode(arguments.get("pdf", ""), validate=True)
        if not raw.startswith(b"%PDF-") or len(raw) > 25 * 1024 * 1024:
            raise ValueError("Upload a PDF up to 25 MiB")
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if pdf.needs_pass or not len(pdf):
                raise ValueError("PDF must contain readable, unencrypted pages")
            thumbnail = pdf[0].get_pixmap(matrix=pymupdf.Matrix(.35, .35)).tobytes("png")
            return {**value, "filename": str(arguments.get("filename", "paper.pdf"))[:200],
                    "pdf": base64.b64encode(raw).decode(), "pages": len(pdf), "page": 1, "annotations": [], "notes": "", "study_layout": False,
                    "text": [p.get_text() for p in pdf],
                    "thumbnail": "data:image/png;base64," + base64.b64encode(thumbnail).decode()}
    except Exception as exc:
        raise ResourceValidationError(f"Cannot import PDF: {exc}") from exc

def annotate(value, args):
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
        annotations.append({"id": identifier, "page": page, "text": str(item.get("text", ""))[:20000],
            "comment": str(item.get("comment", ""))[:20000], "translation": str(item.get("translation", ""))[:40000], "rects": rectangles,
            "title": str(item.get("title", ""))[:300], "color": color, "image": image,
            "title_color": title_color, "collapsed": bool(item.get("collapsed", existing.get("collapsed", False) if existing else False)),
            "learning": bool(item.get("learning", False)), "position": position})
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
    return {**value, "page": page, "notes": str(args.get("notes", value["notes"]))[:100000], "annotations": annotations,
            "study_layout": True if "study_positions" in args else value.get("study_layout", False),
            "study_title": str(args.get("study_title", value.get("study_title", "学习画布"))).strip()[:100] or "学习画布"}

def read(value, args):
    page = int(args.get("page", 1))
    if page < 1 or page > len(value["text"]):
        raise ResourceValidationError("Page is outside the PDF")
    return {"filename": value["filename"], "pages": value["pages"], "page": page,
            "text": value["text"][page-1], "notes": value["notes"]}

class LibraryPlugin:
    descriptor = PluginDescriptor(id="research.library", version="0.2.0",
        plugin_api_version="1.10", name="Library", description="PDF reading and native research spaces")

    async def read_paper(self, context, capability, arguments):
        document = await context.node_document_action(capability, "read", arguments)
        return read(document["value"], arguments)

    def register(self, registration):
        common = dict(color="#70a79a", deck_id="objects", deck_label="Objects", deck_icon="boxes",
                      default_status="available", statuses=frozenset({"available"}))
        registration.register_node_type(NodeTypeDefinition(id="library.region", label="Library",
            description="Literature field with native movable members", icon="library", default_name="New Library",
            default_size=(1100, 800), config_model=RegionConfig, container=NodeContainerDefinition(content_inset=(24,150,24,32)),
            frontend={"body":"region"}, user_creatable=False, templateable=True, **common))
        registration.register_node_type(NodeTypeDefinition(id="library.paper", label="Paper", description="PDF and reading notes",
            icon="book-open", default_name="Paper", default_size=(300,210), config_model=PaperConfig,
            traits=frozenset({"library.readable"}), templateable=True, deck_revision=2,
            frontend={"preview":"thumbnail", "body":"reader", "workspace":"reader"},
            surfaces={"preview":True,"inspector":True,"workspace":True},
            document=NodeDocumentDefinition(model=PaperDocument, max_size_bytes=48*1024*1024,
                actions={"import":NodeDocumentAction(import_pdf), "annotate":NodeDocumentAction(annotate),
                         "read":NodeDocumentAction(read, read_only=True, capability_kind="library.read")},
                downloads={"pdf":lambda v: NodeDocumentDownload(v["filename"],base64.b64decode(v["pdf"]),"application/pdf")}), **common))
        registration.register_capability(CapabilityDefinition(kind="library.read", tool_name="read_paper",
            description="Read extracted text and notes from a PDF page. Scanned pages may have no text.",
            input_schema={"type":"object","properties":{"page":{"type":"integer","minimum":1}},"additionalProperties":False}), self.read_paper)
        registration.register_relationship(RelationshipDefinition(id="library.read",label="Read paper",short_label="read",
            description="Read this paper's text",source_traits=frozenset({"core.agent"}),target_traits=frozenset({"library.readable"}),
            capabilities=(CapabilityGrantDefinition(kind="library.read"),),templateable=True))
        registration.register_relationship(RelationshipDefinition(id="library.research",label="Research",short_label="research",
            description="Associate an Agent with a library; membership alone grants no paper access.",
            source_traits=frozenset({"core.agent"}),target_types=frozenset({"library.region"}),templateable=True))

def create_plugin():
    return LibraryPlugin()
