"""Versioned research intent and bounded, source-linked results on a world node."""
from datetime import UTC, datetime
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from oaw_library.contracts import ResearchScope, ResearchScopeRevision, append_scope_revision
from .search import SearchBudgetLedger


class ScopeConfig(BaseModel):
    description: str = "A bounded literature question, with sources and explicit budgets."


class ExplorationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_id: str | None = None
    entity_id: str | None = None
    frontier_id: str | None = None


class ExplorationRoad(BaseModel):
    """Navigation only; never rewrites SearchRun or evidence provenance."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    parent_id: str | None = None
    anchor_id: str | None = None
    attach_after: str | None = None
    mode: Literal["chain", "branch"] = "chain"
    member_ids: list[str] = Field(default_factory=list, max_length=8000)
    frontier_id: str | None = None
    scope_revision: int = Field(ge=0)


def remap_exploration_config(value, ids):
    # Copied scopes archive prior results. A copied marker must not quietly
    # expose the original scope or pretend its historic entity is still active.
    return {**value, "scope_id": ids.get(value.get("scope_id")), "entity_id": None, "frontier_id": None}


class ScopeDocument(ResearchScope):
    paper_ids: list[str] = Field(default_factory=list, max_length=5000)
    search_budgets: dict[str, SearchBudgetLedger] = Field(default_factory=dict)
    search_runs: list[dict] = Field(default_factory=list, max_length=1000)
    evidence: list[dict] = Field(default_factory=list, max_length=2000)
    methods: list[dict] = Field(default_factory=list, max_length=500)
    micro_skills: list[dict] = Field(default_factory=list, max_length=500)
    snapshots: list[dict] = Field(default_factory=list, max_length=100)
    frontiers: list[dict] = Field(default_factory=list, max_length=500)
    exploration_nodes: list[dict] = Field(default_factory=list, max_length=8000)
    exploration_links: list[dict] = Field(default_factory=list, max_length=16000)
    exploration_roads: list[ExplorationRoad] = Field(default_factory=list, max_length=501)
    path_camps: list[dict] = Field(default_factory=list, max_length=500)
    exploration_tasks: list[dict] = Field(default_factory=list, max_length=1000)
    archived_results: list[dict] = Field(default_factory=list, max_length=100)
    intake_records: dict[str, dict] = Field(default_factory=dict)
    intake_previews: list[dict] = Field(default_factory=list, max_length=20)
    metadata_resolutions: list[dict] = Field(default_factory=list, max_length=3000)
    paused: bool = False
    task_board_id: str | None = None
    knowledge_id: str | None = None


def revise(value, arguments):
    # This desktop-only action has no Agent capability. Scope edits cannot be an
    # implicit way for a Worker to grant itself another search/cost budget.
    current = {key: value[key] for key in ResearchScope.model_fields if key in value}
    addition = ResearchScopeRevision.model_validate({**arguments, "revision": current["current_revision"] + 1,
        "created_at": datetime.now(UTC).isoformat(), "created_by": "desktop"})
    updated = append_scope_revision(current, addition, expected_revision=current["current_revision"])
    return {**value, **updated.model_dump(mode="json")}


def summary(value):
    revisions = value.get("revisions", [])
    return {"question": revisions[-1]["question"] if revisions else "", "scope_revision": value["current_revision"],
        "papers": len(value["paper_ids"]), "searches": len(value["search_runs"]),
        "evidence": len(value["evidence"]), "snapshots": len(value["snapshots"]), "paused": value["paused"]}


def remap(value, ids):
    # A desktop copy starts a new, paused run. Historical reports retain their
    # original world/Paper identities as archives, never as active evidence.
    # Even byte-identical copied PDFs require an explicit source relocation.
    fields = ("search_budgets", "search_runs", "evidence", "methods", "micro_skills", "snapshots", "frontiers",
              "exploration_nodes", "exploration_links", "exploration_roads", "path_camps", "exploration_tasks",
              "intake_records", "intake_previews", "metadata_resolutions")
    archived = deepcopy(value.get("archived_results", []))
    if any(value.get(field) for field in fields):
        if len(archived) >= 100:
            raise ValueError("Research scope archive limit reached; export prior history before copying")
        archived.append({"source_scope_id": value.get("id"), "source_scope_revision": value["current_revision"],
            "paper_ids": deepcopy(value["paper_ids"]), "revisions": deepcopy(value["revisions"]),
            "reason": "Desktop template copy: original provenance only; not current evidence or scientific verification.",
            **{field: deepcopy(value.get(field, {} if field == "search_budgets" else [])) for field in fields}})
    return {**deepcopy(value), "id": ids.get(value.get("id")), "paper_ids": [ids[key] for key in value["paper_ids"] if key in ids],
        "task_board_id": ids.get(value.get("task_board_id")), "knowledge_id": ids.get(value.get("knowledge_id")),
        "revisions": [{**revision, "seed_paper_ids": [ids[key] for key in revision["seed_paper_ids"] if key in ids]} for revision in value["revisions"]],
        **{field: {} if field in {"search_budgets", "intake_records"} else [] for field in fields},
        "archived_results": archived, "paused": True}
