"""Host boundary for scoped public search, durable budgets and atomic Paper intake."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document
from backend.state import StateContext
from backend.world.models import CardCreate
from backend.literature_exploration import paper_position


def now():
    return datetime.now(UTC).isoformat()


class LiteratureService:
    def __init__(self, services):
        from oaw_literature.search import CrossrefClient
        self.services = services
        self.client = CrossrefClient()
        self.running = {}

    def authorize(self, scope_id, operation, capability):
        node = self.services.world.get_card(scope_id)
        if node.type != "literature.scope":
            raise ResourceValidationError("Select a literature research scope")
        if capability is not None:
            live = self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            kind = "literature.organize" if operation == "organize" else "literature.read" if operation in {"scope", "paper", "contracts", "snapshots"} else "literature.research"
            if live.target_id != scope_id or live.kind != kind:
                raise PermissionDeniedError("This connection does not authorize the requested literature operation")
        if operation not in {"scope", "paper", "contracts", "snapshots"}:
            self.services.node_execution.assert_editable(scope_id, allow_delegated=True)
        return node

    def save(self, scope_id, value, revision, capability=None):
        context = self.services.run_manager.current_context if self.services.run_manager else None
        return write_document(self.services, scope_id, value, revision,
            actor_id=capability.agent_id if capability else None, run_id=context.run_id if context else None)

    async def invoke(self, scope_id, operation, arguments, capability=None):
        if operation in {"explore", "evaluate_frontier"}:
            from backend.literature_frontiers import explore, evaluate_frontier
            return await (explore if operation == "explore" else evaluate_frontier)(self,scope_id,arguments,capability)
        if operation in {"search", "resolve"}:
            return await self.search(scope_id, operation, arguments, capability)
        async with self.services._node_mutation(read_only=operation in {"scope", "paper", "contracts", "snapshots"}):
            self.authorize(scope_id, operation, capability)
            current = read_document(self.services, scope_id)
            value = current["value"]
            if operation == "organize":
                from backend.literature_exploration import organize
                return await organize(self, scope_id, arguments, capability)
            if operation == "contracts":
                from oaw_literature.evidence import Evidence
                from oaw_literature.methods import MethodSpec
                from oaw_literature.scope import ScopeDocument
                from oaw_literature.micro import MicroSkill
                from oaw_literature.snapshots import FieldSnapshot, FrontierRoute
                return {"Evidence":Evidence.model_json_schema(),"MethodSpec":MethodSpec.model_json_schema(),"MicroSkill":MicroSkill.model_json_schema(),
                    "FieldSnapshot":FieldSnapshot.model_json_schema(),"FrontierRoute":FrontierRoute.model_json_schema(),
                    "Scope":ScopeDocument.model_json_schema(), "write_instruction":"Use record with expected_revision, kind evidence|method|micro_skill and value. MicroSkill uses current scoped Paper metadata or its actual source abstract with paper_revision; it has no fulltext anchors. Fulltext source anchors come from paper view=anchors; no fabricated review or run receipts."}
            if operation == "scope":
                # A restart never silently resends a network operation whose
                # durable reservation exists but whose result is uncertain.
                result = deepcopy(current)
                from backend.literature_records import source_validator
                from oaw_literature.evidence import EvidenceSource
                from backend.errors import NotFoundError
                validate_source = source_validator(self.services,value)
                source_status = {}
                for evidence in value["evidence"]:
                    for source in evidence["sources"]:
                        try:
                            source_status[source["id"]] = validate_source(EvidenceSource.model_validate(source))
                        except (PermissionDeniedError, NotFoundError, ResourceValidationError, ValueError):
                            source_status[source["id"]] = "missing"
                result["value"]["evidence_source_status"] = source_status
                from backend.literature_frontiers import project_route, evaluation_context
                context_hash = evaluation_context(self.services,value) if any(route.get("evaluation") for route in value["frontiers"]) else None
                result["value"]["frontiers"] = [project_route(self.services,value,route,
                    [key[1] for key in self.running if key[0] == scope_id],context_hash) for route in value["frontiers"]]
                for run in result["value"]["search_runs"]:
                    if run["status"] == "running" and (scope_id, run["request_id"]) not in self.running:
                        run["status"] = "interrupted"
                        run["error"] = "No live request; reservation retained. Inspect before a deliberate new search."
                return result
            if operation in {"snapshot", "snapshots"}:
                from backend.literature_snapshots import create_snapshot, list_snapshots
                return list_snapshots(self.services,scope_id) if operation == "snapshots" else create_snapshot(self.services,scope_id,arguments,capability)
            if operation == "frontier":
                from backend.literature_frontiers import add_frontier
                from backend.literature_exploration import project_results
                with self.services.database.transaction(immediate=True):
                    add_frontier(self.services,scope_id,arguments,capability)
                    return await project_results(self,scope_id,capability)
            if operation == "paper":
                paper_id = arguments.get("paper_id")
                seeds = value["revisions"][-1]["seed_paper_ids"] if value["revisions"] else []
                if paper_id not in {*value["paper_ids"], *seeds}:
                    raise PermissionDeniedError("This Paper is outside the connected research scope")
                if self.services.world.get_card(paper_id).type != "library.paper":
                    raise ResourceValidationError("Source is not a Paper")
                from oaw_library import read
                document = read_document(self.services, paper_id)
                if arguments.get("view") == "anchors":
                    from backend.literature_records import paragraph_sources
                    return paragraph_sources(self.services,paper_id,arguments.get("page",1))
                if arguments.get("view") == "relocate":
                    from backend.literature_records import relocate_selection
                    return relocate_selection(self.services,paper_id,arguments)
                return {"paper_id": paper_id, "revision": document["revision"], "value": read(document["value"],
                    {key: item for key, item in arguments.items() if key in {"page", "view"}})}
            if operation == "record":
                from backend.literature_records import record
                with self.services.database.transaction(immediate=True):
                    result = record(self.services,scope_id,arguments,capability)
                    if result["kind"] in {"method", "micro_skill"}:
                        from backend.literature_exploration import project_results
                        projected = await project_results(self,scope_id,capability)
                        result["revision"] = projected["revision"]
                    return result
            if operation == "review":
                from backend.literature_records import review_evidence
                with self.services.database.transaction(immediate=True):
                    return review_evidence(self.services,scope_id,arguments,capability)
            if operation == "assimilate_method":
                from backend.literature_records import assimilate_method
                return assimilate_method(self.services,scope_id,arguments.get("method_id"),arguments,capability)
            if operation in {"pause", "resume"}:
                if arguments.get("expected_revision") != current["revision"]:
                    raise RevisionConflictError("Reload the research scope before changing its execution state")
                result = self.save(scope_id, {**value, "paused": operation == "pause"}, current["revision"], capability)
                if operation == "pause":
                    for (owner, _), task in list(self.running.items()):
                        if owner == scope_id:
                            task.cancel()
                return result
            raise ResourceValidationError("Unknown literature operation")

    def identities(self):
        """Metadata lookup without decoding every uploaded PDF on each query."""
        from oaw_library.contracts import PaperMetadata, paper_identity_keys
        result = {}
        for node in self.services.world.list_cards():
            if node.type != "library.paper":
                continue
            scope = self.services.card_state.scope(node.id)
            raw = self.services.state.resolve(StateContext((scope,)), "document").value or {}
            metadata = raw.get("metadata") or {key: node.config.get(key, "") for key in ("doi", "year")}
            try:
                keys = paper_identity_keys(PaperMetadata.model_validate(metadata))
            except ValueError:
                continue
            for key in keys:
                result.setdefault(key, set()).add(node.id)
        return result

    async def search(self, scope_id, operation, arguments, capability=None, *, frontier_id=None):
        from oaw_library import PaperDocument
        from oaw_library.contracts import ResearchScopeRevision
        from oaw_literature.search import (CrossrefSearchRequest, CrossrefResolveRequest, CrossrefError,
            SearchBudgetLedger, new_search_budget, reserve_search, consume_attempt, request_fingerprint)
        arguments = dict(arguments)
        revision = arguments.pop("expected_revision", None)
        model = CrossrefSearchRequest if operation == "search" else CrossrefResolveRequest
        try:
            request = model.model_validate({**arguments, "scope_id": scope_id})
        except ValueError as exc:
            raise ResourceValidationError(str(exc)) from exc
        identity = (scope_id, request.request_id)
        async with self.services._node_mutation():
            node = self.authorize(scope_id, operation, capability)
            current = read_document(self.services, scope_id)
            value = current["value"]
            previous = next((run for run in value["search_runs"] if run["request_id"] == request.request_id), None)
            if previous:
                if previous.get("frontier_id") != frontier_id:
                    raise ResourceValidationError("Request ID belongs to a different research route")
                if previous["fingerprint"] != request_fingerprint(request):
                    raise ResourceValidationError("Request ID already belongs to a different search")
                return {"replay": True, "run": {**previous, "status": "interrupted" if previous["status"] == "running" and identity not in self.running else previous["status"]},
                        "revision": current["revision"]}
            if revision != current["revision"]:
                raise RevisionConflictError("Reload the research scope and supply its current document revision")
            if value["paused"]:
                raise ResourceValidationError("Research scope is paused")
            if value["current_revision"] != request.scope_revision:
                raise RevisionConflictError("Research intent changed; use its current scope revision")
            scope = ResearchScopeRevision.model_validate(value["revisions"][-1])
            if operation == "search" and (
                (scope.start_year is not None and (request.from_year is None or request.from_year < scope.start_year)) or
                (scope.end_year is not None and (request.until_year is None or request.until_year > scope.end_year))):
                raise ResourceValidationError("Search dates must remain inside the saved research scope")
            if frontier_id is not None:
                from backend.literature_frontiers import check_route_budget
                check_route_budget(value,scope_id,frontier_id,request)
            if len([key for key in self.running if key[0] == scope_id]) >= min(scope.budget.max_parallelism, 4):
                raise ResourceValidationError("Research scope concurrency budget is exhausted")
            key = str(request.scope_revision)
            ledger = SearchBudgetLedger.model_validate(value["search_budgets"][key]) if key in value["search_budgets"] else new_search_budget(scope_id, scope)
            try:
                ledger, replay = reserve_search(ledger, request)
            except ValueError as exc:
                raise ResourceValidationError(str(exc)) from exc
            if replay:
                raise ResourceValidationError("A prior reservation has no completed result; inspect it before a new request")
            record = {"request_id": request.request_id, "fingerprint": request_fingerprint(request), "request": request.model_dump(mode="json"),
                "scope_revision": request.scope_revision, "started_at": now(), "status": "running", "paper_ids": [],
                "actor_id": capability.agent_id if capability else "desktop"}
            if frontier_id is not None:
                record["frontier_id"] = frontier_id
            self.save(scope_id, {**value, "search_budgets": {**value["search_budgets"], key: ledger.model_dump(mode="json")},
                "search_runs": [*value["search_runs"], record]}, current["revision"], capability)
            self.running[identity] = asyncio.current_task()

        async def before_attempt(attempt_request, attempt):
            async with self.services._node_mutation():
                self.authorize(scope_id, operation, capability)
                current = read_document(self.services, scope_id)
                value = current["value"]
                if value["paused"] or value["current_revision"] != request.scope_revision:
                    raise ResourceValidationError("Research scope paused or changed before the network attempt")
                ledger = consume_attempt(SearchBudgetLedger.model_validate(value["search_budgets"][key]), request, attempt)
                self.save(scope_id, {**value, "search_budgets": {**value["search_budgets"], key: ledger.model_dump(mode="json")}}, current["revision"], capability)
        try:
            result = await (self.client.search(request, before_attempt=before_attempt) if operation == "search" else self.client.resolve(request, before_attempt=before_attempt))
            from backend.literature_intake import canonicalize_candidates, intake_metadata, intake_keys, is_component, remember_intake
            result = result.model_copy(update={"candidates": tuple(await canonicalize_candidates(
                self, scope_id, request.scope_revision, result.candidates, request.request_id, capability, frontier_id))})
            async with self.services._node_mutation():
                self.authorize(scope_id, operation, capability)
                current = read_document(self.services, scope_id)
                value = current["value"]
                stale = value["paused"] or value["current_revision"] != request.scope_revision
                created, linked, conflicts, bindings = [], [], [], {}
                accepted, filtered_out = [], []
                for candidate in result.candidates:
                    if is_component(candidate) and candidate.canonical_metadata is None:
                        filtered_out.append({"identity_keys": list(candidate.identity_keys), "reason": "needs_parent_resolution",
                            "detail": candidate.parent_resolution_status})
                        continue
                    year = intake_metadata(candidate).year
                    if year is not None and ((scope.start_year is not None and year < scope.start_year) or
                        (scope.end_year is not None and year > scope.end_year)):
                        filtered_out.append({"identity_keys":list(candidate.identity_keys),"year":year,"reason":"outside_scope_years"})
                    else:
                        accepted.append(candidate)
                index = self.identities()
                with self.services.database.transaction(immediate=True):
                    for candidate in accepted if not stale else ():
                        matches = set().union(*(index.get(key, set()) for key in intake_keys(candidate)))
                        if len(matches) > 1:
                            conflicts.append({"identity_keys": candidate.identity_keys, "paper_ids": sorted(matches), "status": "identity_conflict"})
                            continue
                        if matches:
                            paper_id = next(iter(matches))
                            if capability is not None:
                                from backend.literature_records import allowed_papers
                                if paper_id not in allowed_papers(value):
                                    conflicts.append({"identity_keys":list(candidate.identity_keys),
                                        "status":"existing_identity_requires_desktop_link"})
                                    continue
                        else:
                            metadata = intake_metadata(candidate)
                            count = len(value["paper_ids"]) + len(created)
                            paper = await self.services._create_card(CardCreate(type="library.paper", name=(metadata.title or metadata.doi or "Paper")[:200],
                                parent_id=None, position=paper_position(self.services, node, count, frontier_id),
                                config={"authors": "; ".join(metadata.authors)[:4000], "year": str(metadata.year or ""), "doi": metadata.doi or ""}), _publish_event=False)
                            paper_id = paper.id
                            document = read_document(self.services, paper_id)
                            write_document(self.services, paper_id, PaperDocument(metadata=metadata).model_dump(mode="json"), document["revision"])
                            created.append(paper)
                            for strong_key in intake_keys(candidate):
                                index.setdefault(strong_key, set()).add(paper_id)
                        remember_intake(value, paper_id, candidate, request.request_id)
                        linked.append(paper_id)
                        for strong_key in candidate.identity_keys:
                            bindings[strong_key] = paper_id
                    complete = {**record, "status": "completed_stale" if stale else "complete", "completed_at": now(),
                        "admitted_candidate_count":len(accepted) if not stale else 0,"filtered_out":filtered_out,
                        "provider_run": result.run.model_dump(mode="json"), "candidates": [{**item.model_dump(mode="json"),
                            "intake_status":next((entry["reason"] for entry in filtered_out if set(entry["identity_keys"]) & set(item.identity_keys)), "candidate"),
                            "paper_id": next((bindings[key] for key in item.identity_keys if key in bindings), None)} for item in result.candidates],
                        "paper_ids": list(dict.fromkeys(linked)), "created_paper_ids": [paper.id for paper in created], "conflicts": conflicts}
                    updated = self.save(scope_id, {**value, "paper_ids": list(dict.fromkeys([*value["paper_ids"], *linked])),
                        "search_runs": [complete if item["request_id"] == request.request_id else item for item in value["search_runs"]]}, current["revision"], capability)
                    if not stale:
                        from backend.literature_exploration import project_results
                        updated = await project_results(self,scope_id,capability)
                for paper in created:
                    self.services._publish_card_created_nowait(paper)
                return {"run": complete, "revision": updated["revision"], "replay": False}
        except BaseException as exc:
            # Save an execution receipt even on cancellation/revocation. This
            # changes no Paper and grants no result access to a revoked caller.
            async with self.services._node_mutation():
                try:
                    current = read_document(self.services, scope_id)
                    provider_run = getattr(exc, "run", None) or getattr(exc, "search_run", None)
                    failed = {**record, "status": "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                        "completed_at": now(), "error": str(exc), "provider_run": provider_run.model_dump(mode="json") if provider_run else None}
                    self.save(scope_id, {**current["value"], "search_runs": [failed if item["request_id"] == request.request_id else item for item in current["value"]["search_runs"]]}, current["revision"])
                except Exception:
                    pass  # Node deletion may remove the scope; never resurrect it.
            if isinstance(exc, (asyncio.CancelledError, PermissionDeniedError, ResourceValidationError)):
                raise
            raise ResourceValidationError(str(exc)) from exc
        finally:
            self.running.pop(identity, None)

    async def close(self):
        tasks = list(set(self.running.values()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def service(services):
    if services.literature is None:
        services.literature = LiteratureService(services)
    return services.literature
