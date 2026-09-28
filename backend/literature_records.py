"""Resolve literature evidence against authorized, versioned local source bytes."""
import base64
import hashlib
import json
import re
from datetime import UTC, datetime

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document


def allowed_papers(scope):
    seeds = scope["revisions"][-1]["seed_paper_ids"] if scope["revisions"] else []
    return set(scope["paper_ids"]) | set(seeds)


def paragraph_sources(services, paper_id, page_number):
    import pymupdf
    from oaw_library.contracts import quote_sha256
    from oaw_literature.evidence import EvidenceSource
    if services.world.get_card(paper_id).type != "library.paper":
        raise ResourceValidationError("Source is not a Paper")
    document = read_document(services, paper_id)["value"]
    if type(page_number) is not int or not 1 <= page_number <= document["pages"]:
        raise ResourceValidationError("Choose a page with local PDF text")
    digest = document["current_document_version_id"]
    sources = []
    with pymupdf.open(stream=base64.b64decode(document["pdf"]), filetype="pdf") as pdf:
        page = pdf[page_number-1]
        width, height = page.rect.width, page.rect.height
        for block in page.get_text("blocks"):
            if block[6] != 0 or not block[4].strip():
                continue
            x0,y0,x1,y1,text = block[:5]
            # PyMuPDF text blocks are unrotated; PDF.js displays the page with
            # its intrinsic rotation. Store normalized displayed coordinates.
            box = pymupdf.Rect(x0,y0,x1,y1) * page.rotation_matrix
            x0,y0,x1,y1 = box
            rect = [max(0,x0/width),max(0,y0/height),min(1,x1/width)-max(0,x0/width),min(1,y1/height)-max(0,y0/height)]
            if rect[2] <= 0 or rect[3] <= 0:
                continue
            identity = hashlib.sha256(json.dumps([digest,page_number,rect,text],ensure_ascii=False).encode()).hexdigest()
            sources.append(EvidenceSource(id=identity,paper_id=paper_id,document_version_id=digest,document_sha256=digest,
                page=page_number,text_parser_version=f"pymupdf/{pymupdf.VersionBind}:blocks-v1",quote=text,
                quote_sha256=quote_sha256(text),rects=[rect],page_rotation=page.rotation).model_dump(mode="json"))
    return {"paper_id":paper_id,"page":page_number,"document_version_id":digest,"sources":sources,
            "coverage":"text_blocks" if sources else "no_extractable_text", "note":"Location fidelity is not scientific verification."}


def source_validator(services, scope):
    members = allowed_papers(scope)
    cache = {}
    def validate(source):
        if source.paper_id not in members:
            raise PermissionDeniedError("Evidence references a Paper outside this research scope")
        key = (source.paper_id,source.page)
        if key not in cache:
            try:
                cache[key] = paragraph_sources(services,*key)
            except Exception as exc:
                from backend.errors import NotFoundError
                if isinstance(exc,(NotFoundError,ResourceValidationError)):
                    return "missing"
                raise
        resolved = cache[key]
        if resolved["document_version_id"] != source.document_version_id:
            return "needs_relocation"
        # Exact host-generated paragraph identity proves both text and geometry.
        paragraph = next((item for item in resolved["sources"] if item["id"] == source.id), None)
        submitted = source.model_dump(mode="json")
        if paragraph and all(paragraph[field] == submitted[field] for field in
                            ("quote","quote_sha256","document_sha256","rects","text_parser_version","page_rotation","text_ranges")):
            return "current"
        # Reader selections must be relocated to a host source anchor before
        # publication; browser textItem indices cannot be proved by PyMuPDF.
        return "needs_relocation"
    return validate


def relocate_selection(services, paper_id, arguments):
    """Suggest host paragraphs; reader geometry never becomes an evidence anchor.

    Matching is intentionally advisory. The reader must inspect and explicitly
    choose a returned paragraph; record() independently validates that paragraph.
    """
    import unicodedata
    selected = arguments.get("selected_text")
    if not isinstance(selected, str) or not selected.strip() or len(selected) > 100_000:
        raise ResourceValidationError("Choose a nonblank text selection of at most 100000 characters")
    resolved = paragraph_sources(services, paper_id, arguments.get("page", 1))
    if arguments.get("document_version_id") != resolved["document_version_id"]:
        raise RevisionConflictError("The Paper changed; reopen it before relocating this selection")

    def normalized(text):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).replace("\u00ad", "")).strip().casefold()

    needle = normalized(selected)
    # A normalized text match ranks candidates only; the original host quote,
    # hash, paragraph identity and geometry stay byte-for-byte unchanged.
    matching = [source["id"] for source in resolved["sources"] if needle and needle in normalized(source["quote"])]
    return {**resolved, "matching_source_ids": matching,
            "match_status": "unique_text_match" if len(matching) == 1 else "ambiguous_text_match" if matching else "manual_selection_required",
            "confirmation_required": True}


def review_evidence(services, scope_id, arguments, capability=None):
    """An explicit desktop review, separate from extraction and source freshness."""
    from uuid import uuid4
    from oaw_literature.evidence import Evidence, evidence_sha256, record_scientific_review, validate_evidence_sources
    if capability is not None:
        raise PermissionDeniedError("Scientific review requires the explicit desktop review action")
    if set(arguments) - {"expected_revision", "evidence_id", "item_revision", "decision", "rationale"}:
        raise ResourceValidationError("Reviewer identity, time and evidence binding are assigned by the host")
    current = read_document(services, scope_id)
    if arguments.get("expected_revision") != current["revision"]:
        raise RevisionConflictError("Reload the research scope before reviewing evidence")
    value = current["value"]
    existing = next((item for item in value["evidence"] if item["id"] == arguments.get("evidence_id")), None)
    if existing is None:
        raise ResourceValidationError("Unknown evidence")
    if type(arguments.get("item_revision")) is not int or arguments["item_revision"] != existing["revision"]:
        raise RevisionConflictError("The evidence changed; inspect its current revision before reviewing")
    try:
        evidence = validate_evidence_sources(Evidence.model_validate(existing), source_validator(services, value))
        reviewed = record_scientific_review(evidence, {"id": str(uuid4()), "reviewer": "desktop",
            "reviewed_at": datetime.now(UTC), "evidence_sha256": evidence_sha256(evidence),
            "decision": arguments.get("decision"), "rationale": arguments.get("rationale")})
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc
    item = reviewed.model_dump(mode="json")
    result = write_document(services, scope_id,
        {**value, "evidence": [item if other["id"] == item["id"] else other for other in value["evidence"]]},
        current["revision"], actor_id="desktop")
    return {"revision": result["revision"], "kind": "evidence_review", "item": item}


def record(services, scope_id, arguments, capability=None):
    if arguments.get("kind") == "micro_skill":
        from backend.literature_micro import record_micro_skill
        return record_micro_skill(services, scope_id, arguments, capability)
    from oaw_literature.evidence import Evidence, validate_evidence_sources, revise_evidence
    from oaw_literature.methods import MethodSpec, method_to_skill_package
    current = read_document(services,scope_id)
    if arguments.get("expected_revision") != current["revision"]:
        raise RevisionConflictError("Reload the research scope before adding evidence")
    value = current["value"]
    kind = arguments.get("kind")
    payload = arguments.get("value")
    validator = source_validator(services,value)
    actor = capability.agent_id if capability else "desktop"
    try:
        if kind == "evidence":
            payload = dict(payload)
            if payload.get("scientific_reviews"):
                raise ValueError("Scientific reviews must be submitted separately with the actual reviewer identity")
            payload["extracted_by"] = actor
            proposed = Evidence.model_validate(payload)
            existing = next((item for item in value["evidence"] if item["id"] == proposed.id), None)
            if existing:
                changes = proposed.model_dump(mode="json",exclude={"id","revision","scientific_reviews","scientific_verification","schema_version"})
                proposed = revise_evidence(existing, changes, expected_revision=arguments.get("item_revision"))
            elif proposed.revision != 1:
                raise ValueError("New evidence begins at revision 1")
            item = validate_evidence_sources(proposed,validator).model_dump(mode="json")
            field = "evidence"
        elif kind == "method":
            method = MethodSpec.model_validate(payload)
            existing = next((item for item in value["methods"] if item["id"] == method.id), None)
            if method.revision != (existing["revision"]+1 if existing else 1):
                raise ValueError("Method revision must follow its last retained revision")
            package = method_to_skill_package(method,source_validator=validator)
            # No caller-authored execution receipt can promote a method.
            item = {**method.model_dump(mode="json"),"status":"draft","recorded_by":actor,
                    "package":package.model_dump(mode="json"),"previous":existing.get("previous",[])+[
                        {key:item for key,item in existing.items() if key!="previous"}] if existing else []}
            field = "methods"
        else:
            raise ValueError("Choose evidence, method or micro_skill as the record kind")
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc
    updated = {**value,field:[other for other in value[field] if other["id"]!=item["id"]]+[item]}
    result = write_document(services,scope_id,updated,current["revision"],actor_id=actor)
    return {"revision":result["revision"],"kind":kind,"item":item}


def method_package(services, scope_id, method_id):
    from oaw_literature.methods import MethodSpec, method_to_skill_package
    value = read_document(services,scope_id)["value"]
    item = next((item for item in value["methods"] if item["id"]==method_id),None)
    if item is None:
        raise ResourceValidationError("Unknown method")
    spec = {key:item[key] for key in MethodSpec.model_fields if key in item}
    try:
        return method_to_skill_package(spec,source_validator=source_validator(services,value))
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc


def assimilate_method(services, scope_id, method_id, arguments, capability=None):
    scope = read_document(services,scope_id)
    if arguments.get("expected_revision") != scope["revision"]:
        raise RevisionConflictError("Research scope changed")
    knowledge_id = arguments.get("knowledge_id") or scope["value"].get("knowledge_id")
    if not knowledge_id or services.world.get_card(knowledge_id).type != "matcreator.kdg":
        raise ResourceValidationError("Select an existing Know-Do Graph")
    if capability is not None:
        raise PermissionDeniedError("Import the generated method package through the Know-Do Graph's explicit curation interface")
    from oaw_matcreator.knowledge import assimilate
    package = method_package(services,scope_id,method_id)
    current = read_document(services,knowledge_id)
    if arguments.get("knowledge_revision") != current["revision"]:
        raise RevisionConflictError("Read the Know-Do Graph and supply its current revision")
    value = assimilate(current["value"],package.model_dump(mode="json"),
        {"node_id":scope_id,"source_type":"literature.scope","method_id":method_id,"source_revision":scope["revision"]})
    return write_document(services,knowledge_id,value,current["revision"],actor_id="desktop")
