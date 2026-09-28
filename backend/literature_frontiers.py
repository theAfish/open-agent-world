"""Persisted bounded routes; state is projected from actual searches and sources."""
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import ResourceValidationError, RevisionConflictError, PermissionDeniedError, NotFoundError
from backend.node_documents import read_document, write_document


def route_payload(value):
    from oaw_literature.snapshots import FrontierRoute
    return {key:value[key] for key in FrontierRoute.model_fields if key in value}


def evaluation_context(services, value):
    from backend.literature_records import allowed_papers
    from oaw_literature.evidence import canonical_sha256
    papers = {}
    for identifier in sorted(allowed_papers(value)):
        try:
            document = read_document(services,identifier)["value"]
            papers[identifier] = {"version":document.get("current_document_version_id"),"metadata":document.get("metadata")}
        except (NotFoundError, ResourceValidationError):
            papers[identifier] = None
    return canonical_sha256({"intent":value["revisions"][-1] if value["revisions"] else None,
        "papers":papers,"evidence":value["evidence"],"searches":value["search_runs"],
        "snapshot":value["snapshots"][-1] if value["snapshots"] else None})


def route_in_scope(value, scope_id, frontier_id):
    from oaw_literature.snapshots import validate_frontier_route
    route = next((item for item in value["frontiers"] if item["id"] == frontier_id), None)
    if route is None or not value["revisions"]:
        raise ResourceValidationError("Choose an existing route in this research scope")
    try:
        validate_frontier_route(route_payload(route), scope_id=scope_id,
            scope_revision=value["revisions"][-1], scope_paper_ids=value["paper_ids"])
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc
    return route


def project_route(services, value, route, running=(), context_hash=None):
    records = [run for run in value["search_runs"] if run.get("frontier_id") == route["id"]]
    paper_ids = list(dict.fromkeys(key for run in records for key in run.get("paper_ids", [])))
    state = "unsearched"
    if records:
        latest = records[-1]
        if latest["status"] == "running":
            state = "searching" if latest["request_id"] in running else "failed"
        elif latest["status"] in {"failed", "cancelled"}:
            state = latest["status"]
        elif latest["status"] == "complete":
            receipt = latest.get("provider_run") or {}
            admitted = latest.get("admitted_candidate_count",receipt.get("candidate_count",0))
            state = "found" if admitted else "filtered_empty" if receipt.get("raw_item_count") else "no_results"
        else:
            state = "cancelled"
    # Discovery does not constitute scientific support. Only revalidated source
    # records can contribute an evidence state; replacements invalidate anchors.
    from backend.literature_records import source_validator
    from oaw_literature.evidence import Evidence, validate_evidence_sources
    validator = source_validator(services, value)
    evidence, members = [], set(paper_ids) | set(route["source_paper_ids"])
    for item in value["evidence"]:
        try:
            parsed = Evidence.model_validate(item)
            if any(source.paper_id in members for source in parsed.sources):
                evidence.append(validate_evidence_sources(parsed,validator))
        except (ValueError, ResourceValidationError, PermissionDeniedError, NotFoundError):
            continue
    evidence_state = "none"
    if evidence:
        relations = {item.relation for item in evidence}
        if {"supports", "contradicts"} <= relations or "contradicts" in relations:
            evidence_state = "conflicted"
        elif any(item.scientific_verification == "reviewed" and item.relation == "supports" for item in evidence):
            evidence_state = "reviewed_support"
        elif relations == {"insufficient"}:
            evidence_state = "insufficient"
        else:
            evidence_state = "located_unreviewed"
    elif any(candidate.get("metadata", {}).get("source_abstract") for run in records for candidate in run.get("candidates", [])):
        evidence_state = "abstract_only"
    budget_records = records + [run for run in value.get('metadata_resolutions', []) if run.get('frontier_id') == route['id']]
    slots = sum(run.get("request", {}).get("rows", 1) for run in budget_records)
    evaluation = route.get("evaluation")
    if evaluation:
        evaluation = {**evaluation,"stale":evaluation.get("context_hash") != (context_hash or evaluation_context(services,value))}
    return {**route, "evaluation":evaluation, "discovery_state":state, "evidence_state":evidence_state,
        "request_ids":[run["request_id"] for run in records], "paper_ids":paper_ids,
        "budget_used":{"searches":len(budget_records), "papers":slots},
        "stale":route["scope_revision"] != value["current_revision"]}


def add_frontier(services, scope_id, arguments, capability=None):
    from oaw_literature.snapshots import validate_frontier_route
    current = read_document(services, scope_id)
    if arguments.get("expected_revision") != current["revision"]:
        raise RevisionConflictError("Reload the scope before proposing a route")
    value = current["value"]
    if not value["revisions"]:
        raise ResourceValidationError("Save a bounded research question first")
    scope = value["revisions"][-1]
    actor = capability.agent_id if capability else "desktop"
    try:
        previous = None
        if arguments.get('continue_from'):
            if capability is not None:
                raise PermissionDeniedError('Continuing a historical direction requires desktop review')
            previous = next((item for item in value['frontiers'] if item['id'] == arguments['continue_from']), None)
            if previous is None:
                raise ValueError('Unknown historical direction')
        proposed = arguments.get("value")
        if proposed is None:
            budget = dict(scope["budget"])
            budget.update(max_searches=min(budget.get("max_searches") or 1, 1),
                max_papers=min(budget.get("max_papers") or 5, 5),max_parallelism=1)
            proposed = {"id":str(uuid4()),"scope_id":scope_id,"scope_revision":value["current_revision"],
                "query":arguments.get("query"),"missing_evidence":arguments.get("missing_evidence"),
                "rationale":arguments.get("rationale"),"budget":budget,
                "cost":{"amount":None,"currency":None,"provenance":"Public Crossref metadata search has no API fee; local computation and labor costs are not estimated."},
                "proposal_kind":"agent_hypothesis" if capability else "user"}
        if previous:
            from backend.literature_records import allowed_papers
            proposed = {**proposed, 'continued_from': previous['id'],
                'source_paper_ids': [key for key in previous.get('source_paper_ids', []) if key in allowed_papers(value)]}
        elif proposed.get('continued_from'):
            raise ValueError('Use continue_from after reviewing the historical direction')
        proposed = {**proposed,"proposed_by":actor,"discovery_state":"unsearched","evidence_state":"none"}
        if capability and proposed.get("proposal_kind") == "user":
            proposed["proposal_kind"] = "agent_hypothesis"
        route = validate_frontier_route(proposed,scope_id=scope_id,scope_revision=scope,scope_paper_ids=value["paper_ids"])
        if any(item["id"] == route.id for item in value["frontiers"]):
            raise ValueError("Route IDs are immutable; propose a new route for a changed question")
    except (ValueError, TypeError) as exc:
        raise ResourceValidationError(str(exc)) from exc
    return write_document(services,scope_id,{**value,"frontiers":[*value["frontiers"],route.model_dump(mode="json")]},current["revision"],actor_id=actor)


def check_route_budget(value, scope_id, frontier_id, request):
    route = route_in_scope(value,scope_id,frontier_id)
    if request.query != route["query"] or request.scope_revision != route["scope_revision"]:
        raise ResourceValidationError("Search must match the saved route's query and revision")
    records = [item for item in [*value["search_runs"], *value.get('metadata_resolutions', [])] if item.get("frontier_id") == frontier_id]
    budget = route["budget"]
    if len(records) >= budget["max_searches"] or sum(item["request"].get("rows",1) for item in records)+request.rows > budget["max_papers"]:
        raise ResourceValidationError("This route's search/Paper budget is exhausted")
    if any(item["status"] == "running" for item in records):
        raise ResourceValidationError("This route already has a reserved search; inspect it before another")
    if records and budget.get("max_duration_seconds") is not None:
        elapsed = (datetime.now(UTC)-datetime.fromisoformat(records[0]["started_at"])).total_seconds()
        if elapsed >= budget["max_duration_seconds"]:
            raise ResourceValidationError("This route's time budget is exhausted")
    return route


async def explore(host, scope_id, arguments, capability=None):
    # The search method rechecks the route and reserves both budgets under the
    # world mutation lock, preventing parallel requests from overspending.
    async with host.services._node_mutation(read_only=True):
        host.authorize(scope_id,"explore",capability)
        current = read_document(host.services,scope_id)
        route = route_in_scope(current["value"],scope_id,arguments.get("frontier_id"))
        request = {"expected_revision":arguments.get("expected_revision"),"scope_revision":route["scope_revision"],
            "request_id":arguments.get("request_id"),"query":route["query"],"rows":min(5,route["budget"]["max_papers"])}
        scope = current["value"]["revisions"][-1]
        if scope.get("start_year"): request["from_year"] = scope["start_year"]
        if scope.get("end_year"): request["until_year"] = scope["end_year"]
    return await host.search(scope_id,"search",request,capability,frontier_id=route["id"])


async def evaluate_frontier(host, scope_id, arguments, capability=None):
    from oaw_literature.evaluator import evaluate_literature
    from backend.literature_records import allowed_papers
    from backend.literature_snapshots import list_snapshots
    async with host.services._node_mutation():
        host.authorize(scope_id,"evaluate_frontier",capability)
        current = read_document(host.services,scope_id)
        if arguments.get("expected_revision") != current["revision"]:
            raise RevisionConflictError("Reload the scope before evaluating a route")
        value = current["value"]
        route = route_in_scope(value,scope_id,arguments.get("frontier_id"))
        projected = project_route(host.services,value,route,[key[1] for key in host.running if key[0] == scope_id])
        snapshots = [item for item in list_snapshots(host.services,scope_id)["items"]
            if item["snapshot"]["scope_revision"] == value["current_revision"]]
        latest = snapshots[-1] if snapshots else None
        usable = latest if latest and latest["freshness"]["status"] == "current" else None
        # Default is a local explainable rule. Merely opening a mode or pressing
        # this button never silently starts a paid model request.
        result = await evaluate_literature(route_payload(projected),value["revisions"][-1],
            snapshot=usable["snapshot"] if usable else None,
            scope_paper_ids=allowed_papers(value))
        result["snapshot_context"] = {"version":usable["snapshot"]["version"] if usable else None,
            "excluded_reasons":latest["freshness"]["reasons"] if latest and not usable else []}
        result["context_hash"] = evaluation_context(host.services,value)
        updated = {**route,"evaluation":{**result,"evaluated_at":datetime.now(UTC).isoformat(),
            "recorded_by":capability.agent_id if capability else "desktop"}}
        saved = host.save(scope_id,{**value,"frontiers":[updated if item["id"] == route["id"] else item for item in value["frontiers"]]},current["revision"],capability)
        return {"revision":saved["revision"],"item":project_route(host.services,saved["value"],updated),"evaluation":updated["evaluation"]}
