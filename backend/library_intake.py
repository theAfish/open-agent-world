"""Desktop Paper association and local, source-bound citation intake.

The router owns the node mutation lock. No title matching or network lookup is
performed here; a bibliographic citation is not a scientific support claim.
"""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4
import re
import unicodedata

from backend.errors import ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document
from backend.state import StateContext
from backend.world.models import CardCreate


def intake_options(services, doi=None):
    from oaw_library.contracts import normalize_doi
    try:
        canonical = normalize_doi(doi)
    except ValueError as error:
        raise ResourceValidationError(str(error)) from error
    papers, scopes = [], []
    for node in services.world.list_cards():
        if node.type not in {"library.paper", "literature.scope"}:
            continue
        raw = services.state.resolve(StateContext((services.card_state.scope(node.id),)), "document").value or {}
        if node.type == "literature.scope":
            revisions = raw.get("revisions", [])
            members = list(dict.fromkeys([*raw.get("paper_ids", []), *(revisions[-1].get("seed_paper_ids", []) if revisions else [])]))
            scopes.append({"id": node.id, "title": node.name, "paper_ids": members, "configured": bool(revisions)})
            continue
        metadata = raw.get("metadata") or {}
        try:
            identifier = normalize_doi(metadata.get("doi") or node.config.get("doi"))
        except ValueError:
            identifier = None
        if canonical and identifier != canonical:
            continue
        papers.append({"id": node.id, "title": metadata.get("title") or node.name,
                       "doi": identifier, "has_pdf": bool(raw.get("pdf")), "parent_id": node.parent_id})
    return {"papers": papers, "scopes": scopes}


def paper_document(services, paper_id, expected_revision=None):
    node = services.world.get_card(paper_id)
    if node.type != "library.paper":
        raise ResourceValidationError("Choose a Paper")
    current = read_document(services, paper_id)
    if expected_revision is not None and current["revision"] != expected_revision:
        raise RevisionConflictError("Paper changed; reload before attaching the PDF")
    return node, current


async def attach_pdf(services, paper_id, arguments):
    """Keep supplements in independent Papers, never in the main version slot."""
    from oaw_library import PaperDocument, import_pdf
    from backend.events import EventType
    from backend.literature_exploration import publish
    services.node_execution.assert_editable(paper_id, allow_delegated=True)
    if arguments.get("expected_revision") is None:
        raise ResourceValidationError("Read the Paper before attaching a PDF")
    owner, current = paper_document(services, paper_id, arguments["expected_revision"])
    kind = arguments.get("kind", "main")
    if kind not in {"main", "supplement"}:
        raise ResourceValidationError("Choose main or supplement")
    if kind == "main":
        updated = import_pdf(current["value"], {**arguments, "kind": "main"})
        result = write_document(services, paper_id, updated, current["revision"])
        publish(services, EventType.CARD_UPDATED, services.world.get_card(paper_id))
        return {"paper_id": paper_id, "document": result, "kind": kind}
    # Validate the entire PDF before allocating any node.
    supplement = import_pdf(PaperDocument().model_dump(mode="json"), {**arguments, "kind": "supplement"})
    digest = supplement["current_document_version_id"]
    existing = next((item for item in current["value"].get("attachments", []) if item.get("sha256") == digest), None)
    if existing:
        paper_document(services, existing["paper_id"])
        return {"paper_id": paper_id, "attachment": existing, "replay": True, "kind": kind}
    if len(current["value"].get("attachments", [])) >= 200:
        raise ResourceValidationError("Paper attachment limit reached")
    with services.database.transaction(immediate=True):
        child = await services._create_card(CardCreate(type="library.paper", name=("SI · " + supplement["filename"])[:200],
            parent_id=owner.parent_id, position={"x": owner.position.x + owner.size.width + 80, "y": owner.position.y + 80}), _publish_event=False)
        child_document = read_document(services, child.id)
        write_document(services, child.id, supplement, child_document["revision"])
        attachment = {"id": uuid4().hex, "paper_id": child.id, "kind": "supplement", "filename": supplement["filename"],
                      "sha256": digest, "created_at": datetime.now(UTC).isoformat()}
        updated = {**current["value"], "attachments": [*current["value"].get("attachments", []), attachment]}
        result = write_document(services, paper_id, updated, current["revision"])
    publish(services, EventType.CARD_CREATED, services.world.get_card(child.id))
    publish(services, EventType.CARD_UPDATED, services.world.get_card(paper_id))
    return {"paper_id": paper_id, "attachment": attachment, "document": result, "kind": kind}


def normalized_reference(text):
    """Normalize typography and PDF line breaks, never infer missing words."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "−": "-", "\u00ad": "", "\u200b": "", "\ufeff": ""}))
    return re.sub(r"\s+", "", text).casefold()


def located_reference(document, page_number, quote, doi):
    """Prove the submitted bibliography text against the current PDF bytes."""
    import base64
    import pymupdf
    with pymupdf.open(stream=base64.b64decode(document["pdf"]), filetype="pdf") as pdf:
        page = pdf[page_number - 1]
        page_texts = [page.get_text(), page.get_text(sort=True)]
    normalized_quote = normalized_reference(quote)
    if normalized_reference(doi) not in normalized_quote:
        raise ResourceValidationError("The DOI is absent from the supplied bibliography text")
    matches = []
    for text in page_texts:
        matches.append(normalized_reference(text))
        # Browsers sometimes join a word split at a line-end hyphen. Keep both
        # readings so a genuine hyphenated title also remains matchable.
        matches.append(normalized_reference(re.sub(r"(?<=\w)[-\u00ad][ \t]*\r?\n[ \t]*(?=\w)", "", text)))
    if not normalized_quote or not any(normalized_quote in text for text in matches):
        raise ResourceValidationError("The complete bibliography text could not be located on this PDF page; reselect the original reference")
    return {"text_match": "normalized_page_text", "reference_text_sha256": sha256(quote.encode()).hexdigest(),
        "text_parser_version": f"pymupdf/{pymupdf.VersionBind}:citation-page-v1"}


def reference_road(value, source_id, requested=None):
    roads = value.get("exploration_roads", [])
    default = next((road["id"] for road in roads if "paper:" + source_id in road["member_ids"]), "trunk")
    selected = requested if requested is not None else default
    if not isinstance(selected, str):
        raise ResourceValidationError("Choose a road from this research scope")
    road = next((road for road in roads if road["id"] == selected), None)
    if road is None and selected != "trunk":
        raise ResourceValidationError("The selected road is outside this scope or no longer exists")
    return road or {"id": "trunk", "frontier_id": None}


async def attach_reference_road(services, scope_id, projected, target_id, selected_road_id, previous_road_id, citation):
    """New navigation follows the chosen road; existing branch identity wins."""
    from backend.literature_roads import edit_road
    from backend.literature_service import service
    from backend.literature_exploration import project_results
    value = deepcopy(projected["value"])
    if previous_road_id is None:
        edit_road(value, "move_member", {"road_id": selected_road_id, "entity_id": "paper:" + target_id})
    actual = next(road for road in value["exploration_roads"] if "paper:" + target_id in road["member_ids"])
    citation.update(road_id=actual["id"], frontier_id=actual.get("frontier_id"), requested_road_id=selected_road_id,
        navigation="retained_existing_road" if previous_road_id and previous_road_id != selected_road_id else "joined_selected_road")
    value["exploration_links"].append({"id": uuid4().hex, "source": "paper:" + citation["source_paper_id"],
        "target": "paper:" + target_id, "relation": "related", "rationale": "参考文献引用（未核验）", "citation": citation})
    write_document(services, scope_id, value, projected["revision"])
    return await project_results(service(services), scope_id)


async def collect_reference(services, scope_id, arguments):
    from oaw_library import PaperDocument
    from oaw_library.contracts import normalize_doi
    from backend.literature_service import service
    from backend.literature_exploration import project_results, paper_position, publish
    from backend.events import EventType
    scope = services.world.get_card(scope_id)
    if scope.type != "literature.scope":
        raise ResourceValidationError("Choose a research scope")
    current = read_document(services, scope_id)
    if arguments.get("expected_revision") != current["revision"]:
        raise RevisionConflictError("Research scope changed; reload before collecting the citation")
    if not current["value"].get("revisions"):
        raise ResourceValidationError("Configure the research question first")
    try:
        doi = normalize_doi(arguments.get("doi"))
    except ValueError as error:
        raise ResourceValidationError(str(error)) from error
    if not doi:
        raise ResourceValidationError("A DOI is required for unambiguous citation intake")
    source_id = arguments.get("source_paper_id")
    _, source = paper_document(services, source_id)
    version = arguments.get("source_document_version_id")
    page, quote = arguments.get("reference_page"), arguments.get("reference_text")
    if version != source["value"].get("current_document_version_id") or not version:
        raise ResourceValidationError("Citation belongs to an older PDF; reopen that version before collecting")
    if type(page) is not int or not 1 <= page <= source["value"]["pages"] or not isinstance(quote, str) or not 1 <= len(quote) <= 10000:
        raise ResourceValidationError("Provide the reference page and original bibliography text")
    location = located_reference(source["value"], page, quote, doi)
    chosen_road = reference_road(current["value"], source_id, arguments.get("road_id"))
    matches = intake_options(services, doi)["papers"]
    selected = arguments.get("target_paper_id")
    if selected and selected not in {item["id"] for item in matches}:
        raise ResourceValidationError("The selected Paper does not have this DOI")
    if len(matches) > 1 and not selected:
        raise ResourceValidationError("Multiple Papers have this DOI; choose the existing Paper explicitly")
    target_id = selected or (matches[0]["id"] if matches else None)
    if target_id == source_id:
        raise ResourceValidationError("A Paper cannot be its own cited Paper")
    value = deepcopy(current["value"])
    citation_key = sha256(f"{source_id}\0{version}\0{page}\0{doi}".encode()).hexdigest()
    existing = next((item for item in value["exploration_links"] if item.get("citation", {}).get("id") == citation_key), None)
    if existing:
        return {"paper_id": existing["citation"]["target_paper_id"], "replay": True, "revision": current["revision"],
            "citation": existing["citation"]}
    previous_road_id = next((road["id"] for road in value["exploration_roads"] if "paper:" + str(target_id) in road["member_ids"]), None)
    created = None
    with services.database.transaction(immediate=True):
        if target_id is None:
            created = await services._create_card(CardCreate(type="library.paper", name=quote[:200], parent_id=None,
                position=paper_position(services, scope, len(value["paper_ids"]), chosen_road.get("frontier_id"))), _publish_event=False)
            target_id = created.id
            target = read_document(services, target_id)
            metadata = {"title": quote[:2000], "doi": doi, "source_url": "https://doi.org/" + doi}
            write_document(services, target_id, PaperDocument(metadata=metadata).model_dump(mode="json"), target["revision"])
        value["paper_ids"] = list(dict.fromkeys([*value["paper_ids"], source_id, target_id]))
        citation = {"id": citation_key, "source_paper_id": source_id, "target_paper_id": target_id,
            "source_document_version_id": version, "reference_page": page, "reference_text": quote, "doi": doi,
            "collected_by": "desktop", "collected_at": datetime.now(UTC).isoformat(), "verification": "unreviewed", **location}
        write_document(services, scope_id, value, current["revision"])
        projected = await project_results(service(services), scope_id)
        projected = await attach_reference_road(services, scope_id, projected, target_id, chosen_road["id"], previous_road_id, citation)
    if created:
        publish(services, EventType.CARD_CREATED, services.world.get_card(created.id))
    return {"paper_id": target_id, "created": bool(created), "revision": projected["revision"], "citation": citation}


async def link_paper(services, scope_id, arguments):
    """Explicit desktop association; filenames and similar titles are not identity."""
    from backend.literature_service import service
    from backend.literature_exploration import project_results
    paper_id = arguments.get("paper_id")
    paper_document(services, paper_id)
    current = read_document(services, scope_id)
    if arguments.get("expected_revision") != current["revision"]:
        raise RevisionConflictError("Research scope changed; reload before linking the Paper")
    if not current["value"].get("revisions"):
        raise ResourceValidationError("Configure the research question first")
    if paper_id in current["value"]["paper_ids"]:
        return current
    with services.database.transaction(immediate=True):
        write_document(services, scope_id, {**current["value"], "paper_ids": [*current["value"]["paper_ids"], paper_id]}, current["revision"])
        return await project_results(service(services), scope_id)
