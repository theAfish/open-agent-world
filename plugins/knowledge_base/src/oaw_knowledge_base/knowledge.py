"""Knowledge: the general concept graph a knowledge base extracts from its literature.

The literature counterpart of ``insights.py``, and the same shape: a caller picks
converted sources and opens a **knowledge draft** over them; ``knowledge_extract_prompt``
hands a model (or a connected Lab assistant) one source's markdown with a fixed
instruction and a closed vocabulary (``knowledge_prompt.py``); ``knowledge_save`` folds
what came back into the draft, merging by content-keyed id so the same concept named
by two papers is one node with both as evidence; a person edits the draft and
publishes it. Publishing is the only write to the ``literature`` layer of the graph,
and unpublishing removes exactly what no other published draft also asserts.

``knowledge_draft_open`` lets an authorized agent prepare a draft. Destructive
management and ``knowledge_review`` remain human-only: a person decides what the
base asserts.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .actions import (
    MAX_MARKDOWN_CHARS, Request, _actor, _base, _bounded, _client, _markdown_record, _scope, _uuid,
)
from . import knowledge_audit
from .errors import KnowledgeError
from .graph_store import LITERATURE_LAYER
from .knowledge_prompt import (
    KNOWLEDGE_INSTRUCTION, MAX_ALIASES, MAX_DESCRIPTION_CHARS, MAX_SNIPPET_CHARS, SUGGESTED_PREDICATES, normalize,
)
from .search_store import outline


def _draft_json(kb, draft, *, sources=False):
    payload = dict(draft)
    if sources:
        files = []
        for link in kb.oaw_knowledge.sources_for(draft["id"]):
            source = kb.sources.get(_uuid(link["source_id"], "source_id"))
            files.append({"source_id": link["source_id"], "filename": source.filename if source else None,
                          "extracted_at": link["extracted_at"], "missing": source is None})
        payload["sources"] = files
    return payload


def _source_ids(kb, draft_id):
    return [link["source_id"] for link in kb.oaw_knowledge.sources_for(draft_id)]


def _element_key(value):
    """The uuid ``kb.graph.extract`` derives from a non-UUID string id, so the
    elements publishing created can be found again without storing their ids."""
    return uuid.uuid5(uuid.NAMESPACE_URL, value)


# ---------------------------------------------------------------- read


class KnowledgeDrafts(Request):
    operation: Literal["list", "get"] = "list"
    draft_id: str | None = None
    group_id: str | None = None
    all_groups: bool = False
    status: Literal["draft", "published"] | None = None
    limit: int = Field(default=50, ge=1, le=200)


def knowledge_drafts(context, arguments):
    """List or read knowledge drafts. Read-only: editing one is knowledge_update;
    publishing one is a person's click."""
    request = KnowledgeDrafts.model_validate(arguments)
    kb, group, _ = _base(context, request.group_id)
    if request.operation == "list":
        scope = _scope(group, request.all_groups)
        items = kb.oaw_knowledge.list(group_id=str(scope) if scope else None,
                                      status=request.status, limit=request.limit)
        return _bounded({"drafts": [_draft_json(kb, item, sources=True) for item in items]})
    if not request.draft_id:
        raise KnowledgeError("draft_id is required")
    return _bounded({"draft": _draft_json(kb, kb.oaw_knowledge.require(request.draft_id), sources=True)})


# ---------------------------------------------------------------- manage


class KnowledgeDraftManage(Request):
    operation: Literal["open", "create", "update", "delete"]
    draft_id: str | None = None
    group_id: str | None = None
    title: str | None = Field(default=None, max_length=500)
    source_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    # The card sets a run id while an agent extraction is in flight, and clears it after.
    run_id: str | None = Field(default=None, max_length=64)
    clear_run: bool = False


def _converted_sources(kb, group_id, source_ids):
    """The given sources, each required to exist in this group and have markdown."""
    for source_id in source_ids:
        source = kb.sources.get(_uuid(source_id, "source_ids"))
        if source is None:
            raise KnowledgeError(f"No such source: {source_id}")
        if group_id and str(source.collection_ids[0] if source.collection_ids else "") != str(group_id):
            raise KnowledgeError(f"Source {source.filename} belongs to another group")
        _markdown_record(kb, source_id=source_id)
    return list(dict.fromkeys(source_ids))


def knowledge_draft_manage(context, arguments):
    """Open a draft over a set of converted sources, change its title or source set,
    or delete it. Agents use the narrower knowledge_draft_open operation."""
    request = KnowledgeDraftManage.model_validate(arguments)
    if request.operation == "open":
        # Navigation and retry must not create another draft over the same material.
        # A person can continue reading published work; an agent prepares editable drafts.
        return _open_draft(context, KnowledgeDraftOpen.model_validate({
            "source_ids": request.source_ids, "group_id": request.group_id, "title": request.title,
        }), include_published=True)
    if request.operation == "create":
        if not request.source_ids:
            raise KnowledgeError("source_ids are required to open a knowledge draft")
        kb, group, _ = _base(context, request.group_id)
        sources = _converted_sources(kb, group.id, request.source_ids)
        title = request.title or f"{kb.sources.get(_uuid(sources[0], 'source_ids')).filename}" + (
            f" +{len(sources) - 1}" if len(sources) > 1 else "")
        draft = kb.oaw_knowledge.create(group_id=str(group.id), title=title, source_ids=sources,
                                        created_by=_actor(context))
        return _bounded({"draft": _draft_json(kb, draft, sources=True)})
    if not request.draft_id:
        raise KnowledgeError("draft_id is required")
    kb = _client(context)
    current = kb.oaw_knowledge.require(request.draft_id)
    if request.operation == "delete":
        if current["status"] == "published":
            raise KnowledgeError("Unpublish this draft before deleting it")
        kb.oaw_knowledge.delete(current["id"])
        return _bounded({"deleted": current["id"]})
    if current["status"] == "published" and request.source_ids is not None:
        raise KnowledgeError("This draft is published; unpublish it before changing its sources")
    sources = None
    if request.source_ids is not None:
        sources = _converted_sources(kb, current["group_id"], request.source_ids)
    draft = kb.oaw_knowledge.update(
        current["id"], title=request.title, source_ids=sources,
        run_id=None if request.clear_run else (request.run_id if request.run_id is not None else ...))
    return _bounded({"draft": _draft_json(kb, draft, sources=True)})


class KnowledgeDraftOpen(Request):
    source_ids: list[str] = Field(min_length=1, max_length=100)
    group_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)


def knowledge_draft_open(context, arguments):
    """Open or reuse an unpublished draft over exactly these converted sources."""
    return _open_draft(context, KnowledgeDraftOpen.model_validate(arguments))


def _open_draft(context, request, *, include_published=False):
    kb, group, _ = _base(context, request.group_id)
    sources = _converted_sources(kb, group.id, request.source_ids)
    for draft in kb.oaw_knowledge.list(group_id=str(group.id), status=None if include_published else "draft", limit=500):
        if (set(_source_ids(kb, draft["id"])) == set(sources)
                and (request.title is None or draft["title"] == request.title)):
            return _bounded({"draft": _draft_json(kb, draft, sources=True)})
    return knowledge_draft_manage(context, {"operation": "create", **request.model_dump()})


# ---------------------------------------------------------------- outline / lookup


class KnowledgeSourceOutline(Request):
    source_id: str


def knowledge_source_outline(context, arguments):
    """The sections of one converted source as character windows, so a reader can
    take a long document one section at a time (MKB's ``list_markdown_headings``)."""
    request = KnowledgeSourceOutline.model_validate(arguments)
    kb = _client(context)
    record, text = _markdown_record(kb, source_id=request.source_id)
    return _bounded({"source_id": request.source_id, "filename": (record.data or {}).get("filename"),
                     "total_characters": len(text), "sections": outline(text)})


class KnowledgeLookup(Request):
    terms: list[str] = Field(min_length=1, max_length=50)
    draft_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def knowledge_lookup(context, arguments):
    """Concepts already named — in this draft and in the published literature layer —
    whose name or aliases contain any of the terms, with the predicates used around
    them. Consult it before naming a concept, so a new source joins the existing
    graph instead of starting a near-duplicate (MKB's ``search_graph_elements``)."""
    request = KnowledgeLookup.model_validate(arguments)
    kb = _client(context)
    needles = [term.strip().casefold() for term in request.terms if term.strip()]
    if not needles:
        raise KnowledgeError("terms must not be empty")
    in_draft = []
    if request.draft_id:
        draft = kb.oaw_knowledge.require(request.draft_id)
        used = {}
        for item in draft["relations"]:
            for end in (item["source"], item["target"]):
                used.setdefault(end, [])
                if item["predicate"] not in used[end]:
                    used[end].append(item["predicate"])
        for item in draft["entities"]:
            haystack = [item["name"].casefold()] + [alias.casefold() for alias in item.get("aliases") or []]
            hits = [needle for needle in needles if any(needle in text for text in haystack)]
            if hits:
                in_draft.append({"name": item["name"], "aliases": item.get("aliases") or [],
                                 "matched": hits, "predicates": used.get(item["id"], [])})
    published = kb.oaw_graph_store.find_names(needles, layer=LITERATURE_LAYER, limit=request.limit)
    for item in published:
        item.pop("id", None)
    return _bounded({"terms": request.terms, "in_draft": in_draft[:request.limit], "published": published,
                     "predicates": kb.oaw_graph_store.predicate_counts(layer=LITERATURE_LAYER, limit=40)})


# ---------------------------------------------------------------- prompt


class KnowledgeExtractPrompt(Request):
    source_id: str
    draft_id: str | None = None
    # Either narrows the window to one section of ``knowledge_source_outline``;
    # ``offset`` then counts from the section's start.
    section: int | None = Field(default=None, ge=0)
    heading: str | None = Field(default=None, max_length=1000)
    limit: int = Field(default=60_000, ge=500, le=MAX_MARKDOWN_CHARS)
    offset: int = Field(default=0, ge=0)


def knowledge_extract_prompt(context, arguments):
    """Everything needed to name one source's concepts and relations: the fixed
    instruction and suggested predicates, the names and predicates already in the
    draft and the published graph (so the model reuses them), and the markdown —
    one section of it when asked, else windowed like the other prompts."""
    request = KnowledgeExtractPrompt.model_validate(arguments)
    kb = _client(context)
    existing, existing_predicates = [], []
    if request.draft_id:
        draft = kb.oaw_knowledge.require(request.draft_id)
        if request.source_id not in _source_ids(kb, draft["id"]):
            raise KnowledgeError("That source is not part of this draft")
        existing = [{"name": item["name"], "aliases": item.get("aliases") or []}
                    for item in draft["entities"]][:200]
        existing_predicates = _predicate_counts(draft["relations"])
    record, text = _markdown_record(kb, source_id=request.source_id)
    heading_path, section_index = None, None
    if request.section is not None or request.heading:
        sections = outline(text)
        match = None
        if request.section is not None:
            match = sections[request.section] if request.section < len(sections) else None
        else:
            wanted = request.heading.strip().casefold()
            match = next((item for item in sections if item["heading_path"].casefold() == wanted), None) \
                or next((item for item in sections if item["heading_path"].casefold().endswith(wanted)), None)
        if match is None:
            raise KnowledgeError("No such section; call knowledge_source_outline for the list")
        heading_path, section_index = match["heading_path"], match["index"]
        text = text[match["offset"]:match["offset"] + match["length"]]
    window = text[request.offset:request.offset + request.limit]
    published = []
    if existing or window:
        # The published layer's names that this text mentions, so a second draft
        # joins the graph instead of restating it.
        terms = _candidate_terms(window)
        if terms:
            published = [{"name": item["name"], "aliases": item["aliases"]}
                         for item in kb.oaw_graph_store.find_names(terms, layer=LITERATURE_LAYER, limit=60)]
            known = {item["name"].casefold() for item in existing}
            published = [item for item in published if item["name"].casefold() not in known]
    return _bounded({
        "source_id": request.source_id, "draft_id": request.draft_id,
        "filename": (record.data or {}).get("filename"), "instruction": KNOWLEDGE_INSTRUCTION,
        "suggested_predicates": list(SUGGESTED_PREDICATES),
        "existing": existing, "existing_predicates": existing_predicates, "published": published,
        "section": section_index, "heading_path": heading_path,
        "total_characters": len(text), "offset": request.offset,
        "markdown": window, "has_more": request.offset + len(window) < len(text),
    })


_TERM = re.compile(r"[A-Za-z][A-Za-z0-9\-]{3,}")
_STOPWORDS = frozenset("""
this that with from were have been their which these those into than then there where when
what also such using used between about after before under over while however therefore
figure table section results discussion introduction methods conclusion abstract because
""".split())


def _candidate_terms(text, limit=40):
    """Distinct lower-case words of four letters or more, most frequent first, as
    cheap probes into the published graph; the model still decides what to reuse."""
    counts = {}
    for match in _TERM.finditer(text):
        word = match.group(0).casefold()
        if word not in _STOPWORDS:
            counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]


def _predicate_counts(relations, limit=40):
    counts = {}
    for item in relations:
        counts[item["predicate"]] = counts.get(item["predicate"], 0) + 1
    return [{"predicate": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]


# ---------------------------------------------------------------- save


class EntityInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=MAX_ALIASES)
    description: str | None = Field(default=None, max_length=2000)


class RelationInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str = Field(min_length=1, max_length=600)
    target: str = Field(min_length=1, max_length=600)
    # ``type`` is the name the first release used; ``normalize`` reads either.
    predicate: str | None = Field(default=None, max_length=200)
    type: str | None = Field(default=None, max_length=200)
    # The statement itself: one sentence on what the document claims about the pair.
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)
    evidence_level: int | None = Field(default=None, ge=1, le=4)
    snippet: str | None = Field(default=None, max_length=MAX_SNIPPET_CHARS)


class KnowledgeSave(Request):
    draft_id: str
    source_id: str
    entities: list[EntityInput] = Field(min_length=1, max_length=500)
    relations: list[RelationInput] = Field(default_factory=list, max_length=2000)
    # A re-extraction empties the draft first; the other sources are extracted again after.
    replace: bool = False


def knowledge_save(context, arguments):
    """Fold one source's extraction into its draft. The source must be one of the
    draft's; predicates are normalized and unknown endpoints dropped rather than refused."""
    request = KnowledgeSave.model_validate(arguments)
    kb = _client(context)
    draft = kb.oaw_knowledge.require(request.draft_id)
    if draft["status"] == "published":
        raise KnowledgeError("This draft is published; unpublish it before extracting again")
    if request.source_id not in _source_ids(kb, draft["id"]):
        raise KnowledgeError("That source is not part of this draft")
    entities, relations = normalize([item.model_dump() for item in request.entities],
                                    [item.model_dump() for item in request.relations])
    if not entities:
        raise KnowledgeError("No concept had a name")
    if request.replace:
        kb.oaw_knowledge.clear(draft["id"])
    draft = kb.oaw_knowledge.merge(draft["id"], source_id=request.source_id,
                                   entities=entities, relations=relations)
    return _bounded({"draft": _draft_json(kb, draft, sources=True),
                     "added": {"entities": len(entities), "relations": len(relations)}})


# ---------------------------------------------------------------- update


class StoredEntity(EntityInput):
    id: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class StoredRelation(RelationInput):
    id: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class KnowledgeUpdate(Request):
    draft_id: str
    title: str | None = Field(default=None, min_length=1, max_length=500)
    entities: list[StoredEntity] | None = Field(default=None, max_length=2000)
    relations: list[StoredRelation] | None = Field(default=None, max_length=5000)
    expected_revision: int | None = None


def knowledge_update(context, arguments):
    """Edit one draft — rename it, or replace its entity and relation lists (a row
    removed, a name or predicate corrected) — by hand or by an agent asked to in
    conversation. A published draft is in the graph and is not edited in place."""
    request = KnowledgeUpdate.model_validate(arguments)
    kb = _client(context)
    current = kb.oaw_knowledge.require(request.draft_id)
    if current["status"] == "published":
        raise KnowledgeError("This draft is published; unpublish it before editing")
    entities = relations = None
    if request.entities is not None or request.relations is not None:
        # Endpoints may still name an entity by the id it had before this edit renamed
        # it; ``normalize`` maps old ids, names and aliases alike and re-keys everything.
        entities, relations = normalize(
            [item.model_dump() for item in request.entities] if request.entities is not None
            else current["entities"],
            [item.model_dump() for item in request.relations] if request.relations is not None
            else current["relations"])
    draft = kb.oaw_knowledge.update(current["id"], title=request.title, entities=entities,
                                    relations=relations, expected_revision=request.expected_revision)
    return _bounded({"draft": _draft_json(kb, draft, sources=True)})


# ---------------------------------------------------------------- audit


class KnowledgeAuditPrompt(Request):
    draft_id: str


def knowledge_audit_prompt(context, arguments):
    """Everything needed to review one draft before publishing: the instruction, the
    findings the card computed (predicate distribution, look-alike concepts, isolated
    concepts, mirrored pairs, matches in the published graph) and the draft itself.
    Produce typed operations, then submit them with knowledge_audit_apply."""
    request = KnowledgeAuditPrompt.model_validate(arguments)
    kb = _client(context)
    draft = kb.oaw_knowledge.require(request.draft_id)
    if draft["status"] == "published":
        raise KnowledgeError("This draft is published; unpublish it before reviewing it again")
    if not draft["entities"]:
        raise KnowledgeError("This draft has no concepts yet; extract its sources first")
    matches = knowledge_audit.published_matches(draft["entities"], kb.oaw_graph_store, LITERATURE_LAYER)
    return _bounded({
        "draft_id": draft["id"], "revision": draft["revision"], "instruction": knowledge_audit.AUDIT_INSTRUCTION,
        "operations": list(knowledge_audit.OPERATIONS),
        "statistics": knowledge_audit.audit_statistics(draft, matches),
        "draft": {"title": draft["title"], "entities": draft["entities"], "relations": draft["relations"]},
    })


class AuditOperation(BaseModel):
    model_config = ConfigDict(extra="allow")
    # One vocabulary, owned by knowledge_audit: what parse_operations accepts, apply accepts.
    op: Literal[*knowledge_audit.OPERATIONS]  # type: ignore[valid-type]
    reason: str | None = None
    message: str | None = None

    @field_validator("reason", "message", mode="before")
    @classmethod
    def _one_line(cls, value):
        # A model that reasons inside the field must not block the review it proposes.
        return knowledge_audit.clamp_reason(value)


class KnowledgeAuditApply(Request):
    draft_id: str
    operations: list[AuditOperation] = Field(max_length=500)
    expected_revision: int | None = None


def knowledge_audit_apply(context, arguments):
    """Apply a review's operations to the draft — merges, predicate renames, aliases,
    deletions — and record them in the draft's review log for the person to read
    before publishing. Operations naming nothing in the draft are skipped and logged,
    never guessed at. Refuses published drafts."""
    request = KnowledgeAuditApply.model_validate(arguments)
    kb = _client(context)
    draft = kb.oaw_knowledge.require(request.draft_id)
    if draft["status"] == "published":
        raise KnowledgeError("This draft is published; unpublish it before editing")
    operations = [item.model_dump(exclude_none=True) for item in request.operations]
    entities, relations, applied, skipped = knowledge_audit.apply_operations(draft, operations)
    if not entities:
        raise KnowledgeError("The review would leave the draft empty; nothing was applied")
    entry = {"at": datetime.now(timezone.utc).isoformat(), "actor": _actor(context),
             "operations": applied, "skipped": skipped,
             "before": {"concepts": len(draft["entities"]), "relations": len(draft["relations"])},
             "after": {"concepts": len(entities), "relations": len(relations)}}
    updated = kb.oaw_knowledge.update(draft["id"], entities=entities, relations=relations,
                                      review_log=(draft.get("review_log") or []) + [entry],
                                      expected_revision=request.expected_revision)
    return _bounded({"draft": _draft_json(kb, updated, sources=True), "applied": len(applied), "skipped": len(skipped),
                     "review": entry})


# ---------------------------------------------------------------- graph audit


class KnowledgeGraphAuditPrompt(Request):
    group_id: str | None = None
    all_groups: bool = False


def _published_drafts(context, group_id, all_groups):
    kb, group, _ = _base(context, group_id)
    scope = _scope(group, all_groups)
    drafts = kb.oaw_knowledge.list(group_id=str(scope) if scope else None, status="published", limit=500)
    if not drafts:
        raise KnowledgeError("Nothing is published yet; publish a knowledge draft first")
    return kb, drafts


def _merged_draft(drafts):
    """Every published draft's elements as one draft, so the review sees the layer
    as a whole; ids are content keys, so the union is exact."""
    entities, relations = normalize(
        [item for draft in drafts for item in draft["entities"]],
        [item for draft in drafts for item in draft["relations"]])
    return {"title": "Published knowledge", "entities": entities, "relations": relations}


def knowledge_graph_audit_prompt(context, arguments):
    """Everything needed to review the published literature layer as a whole — MKB's
    global review, run over the published drafts instead of the graph directly: the
    instruction, findings over the union of every published draft in scope, and that
    union. The operations go to knowledge_graph_audit_apply, which a person confirms."""
    request = KnowledgeGraphAuditPrompt.model_validate(arguments)
    kb, drafts = _published_drafts(context, request.group_id, request.all_groups)
    merged = _merged_draft(drafts)
    return _bounded({
        "draft_ids": [draft["id"] for draft in drafts], "instruction": knowledge_audit.AUDIT_INSTRUCTION,
        "operations": list(knowledge_audit.OPERATIONS), "statistics": knowledge_audit.audit_statistics(merged),
        "draft": merged,
    })


class KnowledgeGraphAuditApply(Request):
    operations: list[AuditOperation] = Field(min_length=1, max_length=500)
    group_id: str | None = None
    all_groups: bool = False


def knowledge_graph_audit_apply(context, arguments):
    """Apply review operations to every published draft in scope and rebuild the
    literature layer from all published drafts, so what the graph holds is still
    exactly what its drafts assert and unpublishing keeps working. Rewrites published
    knowledge, so it asks for confirmation like publishing does."""
    request = KnowledgeGraphAuditApply.model_validate(arguments)
    kb, drafts = _published_drafts(context, request.group_id, request.all_groups)
    operations = [item.model_dump(exclude_none=True) for item in request.operations]
    if not context.confirmed:
        return {"status": "confirmation_required",
                "reasons": [f"Applying {len(operations)} review operation(s) rewrites the published literature "
                            f"layer across {len(drafts)} published draft(s)."],
                "message": "Apply this review to the published knowledge?"}
    store, graph = kb.oaw_knowledge, kb.oaw_graph_store
    at, actor = datetime.now(timezone.utc).isoformat(), _actor(context)
    applied_anywhere, touched = set(), []
    for draft in drafts:
        entities, relations, applied, _ = knowledge_audit.apply_operations(draft, operations)
        if not entities:
            raise KnowledgeError(f'The review would leave "{draft["title"]}" empty; nothing was applied')
        changed = [item for item in applied if item.get("op") != "flag"]
        applied_anywhere.update(item["index"] for item in applied)
        if not changed:
            continue
        entry = {"at": at, "actor": actor, "scope": "graph", "operations": applied, "skipped": [],
                 "before": {"concepts": len(draft["entities"]), "relations": len(draft["relations"])},
                 "after": {"concepts": len(entities), "relations": len(relations)}}
        store.update(draft["id"], entities=entities, relations=relations,
                     review_log=(draft.get("review_log") or []) + [entry])
        touched.append(draft["id"])
    skipped = [{"index": index, **operation, "skipped": "no published draft holds that element"}
               for index, operation in enumerate(operations) if index not in applied_anywhere]
    # Rebuild the layer from every published draft, not only the scoped ones: a node
    # two groups share must end up with both groups' evidence.
    graph.clear_layer(LITERATURE_LAYER)
    for draft in store.list(status="published", limit=500):
        result = kb.graph.extract(_graph_payload(draft, graph), extractor=lambda payload: payload)
        graph.tag([entity.id for entity in result.entities], [relation.id for relation in result.relations],
                  group_id=draft["group_id"], layer=LITERATURE_LAYER)
    counts = graph.counts(layer=LITERATURE_LAYER)
    return _bounded({"drafts": touched, "applied": len(applied_anywhere), "skipped": skipped,
                     "graph": {"entities": counts["entities"], "relations": counts["relations"]}})


# ---------------------------------------------------------------- review


class KnowledgeReview(Request):
    operation: Literal["publish", "unpublish"]
    draft_id: str
    expected_revision: int | None = None


def _graph_payload(draft, graph):
    """The draft as the ``{entities, relations}`` shape ``kb.graph.extract`` upserts.
    Ids are the stored content keys, so republishing upserts instead of duplicating.
    Every literature node is a ``concept``; the predicate is the graph relation type.
    An element another published draft already wrote keeps what it has: list
    properties union, a description or snippet stays, the stronger evidence wins."""
    def merged(item, lookup, keys):
        existing = lookup(_element_key(item["id"]))
        if existing is None:
            return {}
        current = dict(existing.properties)
        result = {}
        for key in keys:
            seen = [value for value in current.get(key) or [] if isinstance(value, str)]
            result[key] = seen + [value for value in item.get(key) or [] if value not in seen]
        for key in ("description", "snippet"):
            if key in item and not item.get(key) and current.get(key):
                result[key] = current[key]
        if "evidence_level" in item and isinstance(current.get("evidence_level"), int):
            result["evidence_level"] = min(item.get("evidence_level") or 4, current["evidence_level"])
        return result

    entities = [{"id": item["id"], "type": "concept", "name": item["name"],
                 "description": item.get("description"), "aliases": item.get("aliases") or [],
                 "source_ids": item.get("source_ids") or [],
                 **merged(item, graph.get_entity, ("aliases", "source_ids"))}
                for item in draft["entities"]]
    relations = [{"id": item["id"], "source": item["source"], "target": item["target"],
                  "type": item["predicate"], "description": item.get("description"),
                  "evidence_level": item.get("evidence_level"),
                  "snippet": item.get("snippet"), "source_ids": item.get("source_ids") or [],
                  **merged(item, graph.get_relation, ("source_ids",))}
                 for item in draft["relations"]]
    return {"entities": entities, "relations": relations}


def knowledge_review(context, arguments):
    """Publish or unpublish one draft: the person's judgment on what the extraction
    proposed. Publishing asks for confirmation and is the only thing that writes the
    Literature workflow into the knowledge graph; unpublishing removes what it wrote,
    except elements another published draft also asserts."""
    request = KnowledgeReview.model_validate(arguments)
    kb = _client(context)
    store, graph = kb.oaw_knowledge, kb.oaw_graph_store
    draft = store.require(request.draft_id)

    if request.operation == "unpublish":
        if draft["status"] != "published":
            raise KnowledgeError("This draft is not published")
        keep = store.published_element_ids(except_draft_id=draft["id"])
        graph.remove([_element_key(item["id"]) for item in draft["entities"] if item["id"] not in keep])
        graph.remove_relations([_element_key(item["id"]) for item in draft["relations"] if item["id"] not in keep])
        updated = store.update(draft["id"], status="draft", expected_revision=request.expected_revision)
        return _bounded({"draft": _draft_json(kb, updated, sources=True)})

    if draft["status"] == "published":
        raise KnowledgeError("This draft is already published")
    if not draft["entities"]:
        raise KnowledgeError("This draft has no entities yet; extract its sources first")
    if not context.confirmed:
        return {"status": "confirmation_required",
                "reasons": [f"Publishing writes {len(draft['entities'])} entities and "
                            f"{len(draft['relations'])} relations into the knowledge graph as what "
                            "this knowledge base asserts about its literature."],
                "message": f'Publish "{draft["title"]}"?'}
    result = kb.graph.extract(_graph_payload(draft, graph), extractor=lambda payload: payload)
    graph.tag([entity.id for entity in result.entities], [relation.id for relation in result.relations],
              group_id=draft["group_id"], layer=LITERATURE_LAYER)
    updated = store.update(draft["id"], status="published", expected_revision=request.expected_revision)
    return _bounded({"draft": _draft_json(kb, updated, sources=True),
                     "graph": {"entities": len(result.entities), "relations": len(result.relations)}})
