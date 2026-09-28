"""Host-owned binding for metadata/abstract research Micro-Skill drafts."""
from datetime import UTC, datetime

from backend.errors import PermissionDeniedError, ResourceValidationError, RevisionConflictError
from backend.node_documents import read_document, write_document


def record_micro_skill(services, scope_id, arguments, capability=None):
    from backend.literature_records import allowed_papers
    from oaw_literature.evidence import canonical_sha256
    from oaw_literature.micro import CLAIM_SCOPE, MicroSkill

    if set(arguments) - {"kind", "value", "expected_revision", "item_revision"}:
        raise ResourceValidationError("Micro-Skill authority and source bindings are assigned by the host")
    current = read_document(services, scope_id)
    if type(arguments.get("expected_revision")) is not int or arguments["expected_revision"] != current["revision"]:
        raise RevisionConflictError("Reload the research scope before recording a Micro-Skill")
    try:
        proposed = MicroSkill.model_validate(arguments.get("value"))
    except ValueError as exc:
        raise ResourceValidationError(str(exc)) from exc
    if proposed.paper_id not in allowed_papers(current["value"]):
        raise PermissionDeniedError("Micro-Skill references a Paper outside this research scope")
    if services.world.get_card(proposed.paper_id).type != "library.paper":
        raise ResourceValidationError("Micro-Skill source must be a Paper")
    paper = read_document(services, proposed.paper_id)
    if proposed.paper_revision != paper["revision"]:
        raise RevisionConflictError("Paper metadata changed; read its current revision before recording a Micro-Skill")
    metadata = paper["value"]["metadata"]
    abstract = metadata.get("source_abstract", "")
    if proposed.basis == "abstract" and not abstract.strip():
        raise ResourceValidationError("Abstract basis requires an actual stored source abstract; choose metadata and retain the missing information")
    if not any(metadata.get(field) for field in ("title", "doi", "source_url", "source_abstract")):
        raise ResourceValidationError("Micro-Skill requires stored Paper metadata or a source abstract")
    items = current["value"].get("micro_skills", [])
    existing = next((item for item in items if item["id"] == proposed.id), None)
    if existing:
        if type(arguments.get("item_revision")) is not int or arguments["item_revision"] != existing["revision"]:
            raise RevisionConflictError("Micro-Skill revision changed; inspect it before revising")
        if len(existing.get("previous", [])) >= 50:
            raise ResourceValidationError("Micro-Skill history limit reached; retain/export its history before creating another strategy")
    elif "item_revision" in arguments:
        raise RevisionConflictError("New Micro-Skills have no prior item revision")
    actor = capability.agent_id if capability else "desktop"
    digest = canonical_sha256(metadata)
    payload = proposed.model_dump(mode="json")
    item = {**payload, "revision": existing["revision"] + 1 if existing else 1,
            "record_type": "micro_skill", "kind": "micro_skill", "skill_kind": "research_strategy", "status": "draft",
            "claim_scope": CLAIM_SCOPE, "recorded_by": actor, "recorded_at": datetime.now(UTC).isoformat(),
            "metadata_snapshot": metadata, "metadata_sha256": digest,
            "sources": [{"paper_id": proposed.paper_id, "paper_revision": paper["revision"], "basis": proposed.basis,
                "title": metadata.get("title", ""), "doi": metadata.get("doi"), "source_url": metadata.get("source_url"),
                "quote": abstract if proposed.basis == "abstract" else "", "metadata_sha256": digest}],
            "steps": [{"instruction": step, "origin": "research_strategy"} for step in proposed.steps],
            "previous": [*existing.get("previous", []), {key: value for key, value in existing.items() if key != "previous"}] if existing else []}
    value = {**current["value"], "micro_skills": [other for other in items if other["id"] != proposed.id] + [item]}
    result = write_document(services, scope_id, value, current["revision"], actor_id=actor)
    return {"revision": result["revision"], "kind": "micro_skill", "item": item}
