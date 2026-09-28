"""Budgeted component resolution and non-destructive, revisioned intake curation."""
from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document


def timestamp():
    return datetime.now(UTC).isoformat()


def intake_metadata(candidate):
    return candidate.canonical_metadata or candidate.metadata


def intake_keys(candidate):
    from oaw_library.contracts import paper_identity_keys
    return paper_identity_keys(intake_metadata(candidate))


def is_component(candidate):
    return candidate.record_type == "component" or bool(candidate.component_of_dois)


async def resolve_metadata(service, scope_id, scope_revision, doi, identity, capability=None, frontier_id=None):
    """Each extra DOI lookup spends a real persisted reservation; receipts replay."""
    from oaw_library.contracts import ResearchScopeRevision
    from oaw_literature.search import (CrossrefResolveRequest, CrossrefError, SearchBudgetLedger,
        new_search_budget, reserve_search, consume_attempt, request_fingerprint, PaperCandidate)
    request = CrossrefResolveRequest(scope_id=scope_id, scope_revision=scope_revision,
        request_id="metadata-" + hashlib.sha256((identity + "|" + doi).encode()).hexdigest()[:40], doi=doi)
    key = str(scope_revision)
    async with service.services._node_mutation():
        service.authorize(scope_id, "resolve", capability)
        current = read_document(service.services, scope_id)
        value = current["value"]
        if value["paused"] or value["current_revision"] != scope_revision:
            raise ResourceValidationError("Research scope paused or changed before metadata resolution")
        prior = next((item for item in value["metadata_resolutions"] if item["request_id"] == request.request_id), None)
        if prior:
            return (PaperCandidate.model_validate(prior["candidate"]) if prior.get("candidate") else None), prior
        scope = ResearchScopeRevision.model_validate(value["revisions"][-1])
        if frontier_id is not None:
            from backend.literature_frontiers import route_in_scope
            route = route_in_scope(value, scope_id, frontier_id)
            records = [item for item in [*value["search_runs"], *value["metadata_resolutions"]] if item.get("frontier_id") == frontier_id]
            budget = route["budget"]
            exhausted = len(records) >= budget["max_searches"] or sum(item.get("request", {}).get("rows", 1) for item in records) + 1 > budget["max_papers"]
            if records and budget.get("max_duration_seconds") is not None:
                exhausted = exhausted or (datetime.now(UTC) - datetime.fromisoformat(records[0]["started_at"])).total_seconds() >= budget["max_duration_seconds"]
            if exhausted:
                return None, {"request_id": request.request_id, "doi": doi, "status": "route_budget_exhausted"}
        ledger = SearchBudgetLedger.model_validate(value["search_budgets"][key]) if key in value["search_budgets"] else new_search_budget(scope_id, scope)
        try:
            ledger, replay = reserve_search(ledger, request)
        except ValueError as exc:
            return None, {"request_id": request.request_id, "doi": doi, "status": "budget_exhausted", "error": str(exc)}
        if replay:
            return None, {"request_id": request.request_id, "doi": doi, "status": "interrupted", "error": "Reservation exists without a completed metadata receipt"}
        receipt = {"request_id": request.request_id, "doi": doi, "scope_revision": scope_revision,
            "fingerprint": request_fingerprint(request), "status": "running", "started_at": timestamp(),
            "request": request.model_dump(mode="json"), "frontier_id": frontier_id, "parent_request_id": identity}
        service.save(scope_id, {**value, "search_budgets": {**value["search_budgets"], key: ledger.model_dump(mode="json")},
            "metadata_resolutions": [*value["metadata_resolutions"], receipt]}, current["revision"], capability)

    async def before_attempt(_, attempt):
        async with service.services._node_mutation():
            service.authorize(scope_id, "resolve", capability)
            current = read_document(service.services, scope_id)
            value = current["value"]
            if value["paused"] or value["current_revision"] != scope_revision:
                raise ResourceValidationError("Research scope paused or changed before metadata resolution")
            ledger = consume_attempt(SearchBudgetLedger.model_validate(value["search_budgets"][key]), request, attempt)
            service.save(scope_id, {**value, "search_budgets": {**value["search_budgets"], key: ledger.model_dump(mode="json")}}, current["revision"], capability)

    candidate = None
    failure = None
    try:
        result = await service.client.resolve(request, before_attempt=before_attempt)
        candidate = next((item for item in result.candidates if item.metadata.doi == request.doi), None)
        receipt = {**receipt, "status": "complete" if candidate else "not_found", "provider_run": result.run.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json") if candidate else None}
    except (CrossrefError, ValueError) as exc:
        receipt = {**receipt, "status": "failed", "error": str(exc), "provider_run": exc.run.model_dump(mode="json") if isinstance(exc, CrossrefError) else None}
    except BaseException as exc:
        receipt = {**receipt, "status": "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", "error": str(exc)}
        failure = exc
    async with service.services._node_mutation():
        current = read_document(service.services, scope_id)
        receipt["completed_at"] = timestamp()
        service.save(scope_id, {**current["value"], "metadata_resolutions": [receipt if item["request_id"] == request.request_id else item
            for item in current["value"]["metadata_resolutions"]]}, current["revision"])
    if failure:
        raise failure
    return candidate, receipt


async def canonicalize_candidates(service, scope_id, scope_revision, candidates, identity, capability=None, frontier_id=None):
    """Only explicit deposited parent relations can change a work identity."""
    resolved = []
    parents = {}
    for candidate in candidates:
        if not is_component(candidate):
            resolved.append(candidate)
            continue
        if len(candidate.component_of_dois) != 1:
            resolved.append(candidate.model_copy(update={"parent_resolution_status": "missing_or_ambiguous_parent"}))
            continue
        doi = candidate.component_of_dois[0]
        if doi not in parents:
            parents[doi] = await resolve_metadata(service, scope_id, scope_revision, doi, identity, capability, frontier_id)
        parent, receipt = parents[doi]
        if parent and not is_component(parent):
            resolved.append(candidate.model_copy(update={"canonical_metadata": parent.metadata,
                "canonical_provenance": parent.provenance, "canonical_record_type": parent.record_type,
                "parent_resolution_status": "resolved"}))
        else:
            resolved.append(candidate.model_copy(update={"parent_resolution_status": "parent_is_component" if parent else receipt["status"]}))
    return resolved


def remember_intake(value, paper_id, candidate, request_id):
    """Keep original component identity and receipts beside the canonical work."""
    metadata = intake_metadata(candidate)
    prior = value["intake_records"].get(paper_id, {})
    components = list(prior.get("components", []))
    if is_component(candidate):
        component = {"doi": candidate.metadata.doi, "metadata": candidate.metadata.model_dump(mode="json"),
            "record_type": candidate.record_type, "component_of_dois": list(candidate.component_of_dois),
            "provenance": candidate.provenance.model_dump(mode="json"),
            "fulltext_links": [item.model_dump(mode="json") for item in candidate.fulltext_links], "request_id": request_id}
        components = [item for item in components if item["doi"] != component["doi"]] + [component]
    value["intake_records"][paper_id] = {**prior, "paper_id": paper_id, "canonical_doi": metadata.doi,
        "canonical_metadata": metadata.model_dump(mode="json"),
        "canonical_provenance": (candidate.canonical_provenance or candidate.provenance).model_dump(mode="json"),
        "record_type": candidate.canonical_record_type or candidate.record_type, "components": components,
        "screening_status": prior.get("screening_status", "pending"), "screening_reason": prior.get("screening_reason", ""),
        "revision": prior.get("revision", 0) + 1, "updated_at": timestamp()}


def intake_view(service, scope_id):
    service.authorize(scope_id, "scope", None)
    current = read_document(service.services, scope_id)
    value = current["value"]
    seeds = value["revisions"][-1]["seed_paper_ids"] if value["revisions"] else []
    papers = []
    for paper_id in dict.fromkeys([*value["paper_ids"], *seeds]):
        paper = read_document(service.services, paper_id)
        record = value["intake_records"].get(paper_id, {})
        papers.append({**record, "paper_id": paper_id, "paper_revision": paper["revision"], "revision": record.get("revision", 0),
            "metadata": paper["value"]["metadata"], "canonical_metadata": record.get("canonical_metadata") or paper["value"]["metadata"],
            "screening_status": record.get("screening_status", "pending"), "screening_reason": record.get("screening_reason", ""),
            "reading_status": paper["value"].get("reading_status", "unread"), "has_pdf": bool(paper["value"]["pdf"]),
            "components": record.get("components", [])})
    return {"revision": current["revision"], "scope_revision": value["current_revision"], "papers": papers,
        "previews": value["intake_previews"], "search_budgets": value["search_budgets"],
        "pending_candidates": [candidate for run in value["search_runs"] for candidate in run.get("candidates", [])
            if not candidate.get("paper_id") and candidate.get("intake_status") == "needs_parent_resolution"]}


async def review_paper(service, scope_id, arguments):
    async with service.services._node_mutation():
        service.authorize(scope_id, "review_paper", None)
        current = read_document(service.services, scope_id)
        if arguments.get("expected_revision") != current["revision"]:
            raise RevisionConflictError("Reload the intake list before changing a review")
        value = current["value"]
        paper_id = arguments.get("paper_id")
        from backend.literature_records import allowed_papers
        if not isinstance(paper_id, str) or paper_id not in allowed_papers(value):
            raise PermissionDeniedError("Paper is outside this scope")
        prior = value["intake_records"].get(paper_id, {})
        if arguments.get("item_revision") != prior.get("revision", 0):
            raise RevisionConflictError("Paper review changed; reload before saving")
        status = arguments.get("screening_status", prior.get("screening_status", "pending"))
        reason = arguments.get("screening_reason", prior.get("screening_reason", ""))
        if not isinstance(status, str) or status not in {"pending", "included", "excluded"} or not isinstance(reason, str) or len(reason) > 5000:
            raise ResourceValidationError("Choose pending, included or excluded with a bounded reason")
        if status != "pending" and not reason.strip():
            raise ResourceValidationError("Record a reason for inclusion or exclusion")
        paper = read_document(service.services, paper_id)
        reading = arguments.get("reading_status", paper["value"]["reading_status"])
        if not isinstance(reading, str) or (reading not in {"unread", "screened", "close_read"} and reading != paper["value"]["reading_status"]):
            raise ResourceValidationError("Reading progress cannot grant scientific verification")
        if reading == "close_read" and not paper["value"]["pdf"]:
            raise ResourceValidationError("Import the full text before marking it close-read")
        if arguments.get("paper_revision") != paper["revision"]:
            raise RevisionConflictError("Paper changed; reload before saving reading progress")
        entry = {**prior, "paper_id": paper_id, "screening_status": status, "screening_reason": reason.strip(),
            "revision": prior.get("revision", 0) + 1, "updated_at": timestamp(), "updated_by": "desktop",
            "history": [*prior.get("history", []), {"screening_status": prior.get("screening_status", "pending"),
                "screening_reason": prior.get("screening_reason", ""), "reading_status": paper["value"]["reading_status"], "changed_at": timestamp()}]}
        with service.services.database.transaction(immediate=True):
            if reading != paper["value"]["reading_status"]:
                write_document(service.services, paper_id, {**paper["value"], "reading_status": reading}, paper["revision"], actor_id="desktop")
            service.save(scope_id, {**value, "intake_records": {**value["intake_records"], paper_id: entry}}, current["revision"])
        return intake_view(service, scope_id)


async def repair_preview(service, scope_id, arguments):
    identity = (scope_id, "intake-preview-" + uuid4().hex)
    async with service.services._node_mutation():
        service.authorize(scope_id, "repair_preview", None)
        current = read_document(service.services, scope_id)
        value = current["value"]
        if not value["revisions"] or value["paused"]:
            raise ResourceValidationError("Save and resume a bounded scope before metadata lookup")
        capacity = min(value["revisions"][-1]["budget"]["max_parallelism"], 4)
        if len([key for key in service.running if key[0] == scope_id]) >= capacity:
            raise ResourceValidationError("Research scope concurrency budget is exhausted")
        service.running[identity] = asyncio.current_task()
    try:
        return await _repair_preview(service, scope_id, arguments)
    finally:
        service.running.pop(identity, None)


async def _repair_preview(service, scope_id, arguments):
    from backend.literature_records import allowed_papers
    async with service.services._node_mutation():
        service.authorize(scope_id, "repair_preview", None)
        current = read_document(service.services, scope_id)
        if arguments.get("expected_revision") != current["revision"]:
            raise RevisionConflictError("Reload the intake list before previewing normalization")
        value = current["value"]
        requested = arguments.get("paper_ids", value["paper_ids"][:20])
        if not isinstance(requested, list) or not 1 <= len(requested) <= 20 or not all(isinstance(item, str) for item in requested) or len(set(requested)) != len(requested):
            raise ResourceValidationError("Select 1 to 20 distinct scoped Papers")
        if any(item not in allowed_papers(value) for item in requested):
            raise PermissionDeniedError("Preview contains a Paper outside this scope")
        scope_revision = value["current_revision"]
        snapshot = {paper_id: read_document(service.services, paper_id) for paper_id in requested}
        preview_id = "intake-" + uuid4().hex
    proposals = []
    for paper_id, paper in snapshot.items():
        metadata = paper["value"]["metadata"]
        doi = metadata.get("doi")
        if not doi:
            proposals.append({"paper_id": paper_id, "paper_revision": paper["revision"], "status": "missing_doi"})
            continue
        candidate, receipt = await resolve_metadata(service, scope_id, scope_revision, doi, preview_id)
        if not candidate:
            proposals.append({"paper_id": paper_id, "paper_revision": paper["revision"], "status": receipt["status"], "doi": doi})
            continue
        candidate = (await canonicalize_candidates(service, scope_id, scope_revision, [candidate], preview_id))[0]
        usable = not is_component(candidate) or candidate.canonical_metadata is not None
        proposals.append({"paper_id": paper_id, "paper_revision": paper["revision"], "original_metadata": metadata,
            "status": "ready" if usable else "needs_parent_resolution", "candidate": candidate.model_dump(mode="json"),
            "canonical_doi": intake_metadata(candidate).doi if usable else None})
    async with service.services._node_mutation():
        service.authorize(scope_id, "repair_preview", None)
        current = read_document(service.services, scope_id)
        if current["value"]["current_revision"] != scope_revision or current["value"]["paused"]:
            raise RevisionConflictError("Scope changed during preview; receipts remain available")
        preview = {"id": preview_id, "scope_revision": scope_revision, "status": "preview", "created_at": timestamp(),
            "proposals": proposals, "mode": "preserve_cards_and_group_canonical_work"}
        saved = service.save(scope_id, {**current["value"], "intake_previews": [*current["value"]["intake_previews"][-19:], preview]}, current["revision"])
        return {"revision": saved["revision"], "preview": preview}


async def repair_apply(service, scope_id, arguments):
    from oaw_literature.search import PaperCandidate
    from backend.literature_records import allowed_papers
    async with service.services._node_mutation():
        service.authorize(scope_id, "repair_apply", None)
        current = read_document(service.services, scope_id)
        value = deepcopy(current["value"])
        if arguments.get("expected_revision") != current["revision"]:
            raise RevisionConflictError("Reload the repair preview before applying it")
        preview = next((item for item in value["intake_previews"] if item["id"] == arguments.get("preview_id")), None)
        if not preview or preview["scope_revision"] != value["current_revision"]:
            raise ResourceValidationError("Choose a preview from the current scope revision")
        if preview["status"] == "applied":
            return {"revision": current["revision"], "preview": preview, "replay": True}
        for item in preview["proposals"]:
            if item["paper_id"] not in allowed_papers(value) or read_document(service.services, item["paper_id"])["revision"] != item["paper_revision"]:
                raise RevisionConflictError("A source Paper changed after preview; create a new preview")
        for item in preview["proposals"]:
            if item["status"] == "ready":
                remember_intake(value, item["paper_id"], PaperCandidate.model_validate(item["candidate"]), preview["id"])
                value["intake_records"][item["paper_id"]]["original_metadata"] = item["original_metadata"]
        preview.update(status="applied", applied_at=timestamp())
        saved = service.save(scope_id, value, current["revision"])
        return {"revision": saved["revision"], "preview": preview, "replay": False}
