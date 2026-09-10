"""Plugin-owned knowledge semantics, persisted through OAW node documents."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator
from open_agent_world.skill_packages import Skill, SkillPackage

Kind = Literal["capability", "procedure", "heuristic", "memory"]
Relation = Literal["dependency", "prerequisite", "refinement_of", "related_workflow", "derived_from", "heuristic_for", "related_memory", "replacement"]

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Entry(Model):
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = Field(min_length=1, max_length=200)
    type: Kind = "capability"
    summary: str = Field(default="", max_length=1000)
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    resources: list[dict] = Field(default_factory=list)
    trust: float = Field(default=0.5, ge=0, le=1)
    verification: Literal["unverified", "reviewed", "tested"] = "unverified"
    usage_count: int = Field(default=0, ge=0)
    refinement: Literal["published", "pending", "refined", "rejected"] = "pending"
    owner: Literal["publisher", "user"] = "user"
    session_id: str | None = None
    success: bool | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class Edge(Model):
    id: str = Field(default_factory=lambda: uuid4().hex)
    source: str
    target: str
    relation: Relation

class Graph(Model):
    entries: list[Entry] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    snapshots: dict[str, dict] = Field(default_factory=dict)

    @model_validator(mode="after")
    def topology(self):
        nodes = {e.id: e for e in self.entries}
        if len(nodes) != len(self.entries) or len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Duplicate graph identity")
        keys = set()
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
                raise ValueError("Relationships require two existing distinct entries")
            key = (edge.source, edge.target, edge.relation)
            if key in keys:
                raise ValueError("Duplicate relationship")
            keys.add(key)
            source, target = nodes[edge.source], nodes[edge.target]
            if edge.relation in {"dependency", "prerequisite", "related_workflow"} and not {source.type, target.type} <= {"capability", "procedure"}:
                raise ValueError("Workflow relationships require Capability or Procedure endpoints")
            if edge.relation == "replacement" and source.type != target.type:
                raise ValueError("Replacement entries must have the same knowledge type")
            if edge.relation == "heuristic_for" and (source.type != "heuristic" or target.type not in {"procedure", "capability"}):
                raise ValueError("heuristic_for links a Heuristic to a Capability or Procedure")
            if edge.relation == "related_memory" and "memory" not in {source.type, target.type}:
                raise ValueError("related_memory requires a Memory endpoint")
        return self

class Query(Model):
    query: str = ""
    ids: list[str] = Field(default_factory=list, max_length=100)
    types: list[Kind] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    source: str = ""
    include_memory: bool = True
    min_trust: float = Field(default=0, ge=0, le=1)
    review: bool = False
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=60, ge=1, le=200)

def summary(value):
    return {"entries": len(value["entries"]), "relationships": len(value["edges"]),
            "skills": len(value["skills"]), "snapshots": len(value["snapshots"])}

def validate_update(old, new):
    for key, snapshot in old["snapshots"].items():
        if new["snapshots"].get(key) != snapshot:
            raise ValueError("Assimilated source snapshots are immutable")
    entries = {e["id"]: e for e in new["entries"]}
    for entry in old["entries"]:
        replacement = entries.get(entry["id"])
        if replacement is None:
            continue  # Local graph deletion leaves the immutable source snapshot intact.
        if entry["provenance"].get("snapshot"):
            if replacement["provenance"] != entry["provenance"] or replacement["resources"] != entry["resources"]:
                raise ValueError("Imported knowledge must retain its source provenance and resources")
        if entry["owner"] == "publisher" and replacement["owner"] == "publisher":
            if {k: v for k, v in entry.items() if k != "usage_count"} != {k: v for k, v in replacement.items() if k != "usage_count"}:
                raise ValueError("Edited imported knowledge must become user-owned")

def query(value, arguments):
    q = Query.model_validate(arguments)
    tokens = re.findall(r"\w+", q.query.casefold())
    candidates = []
    for entry in value["entries"]:
        if q.types and entry["type"] not in q.types or not q.include_memory and entry["type"] == "memory":
            continue
        if entry["trust"] < q.min_trust or q.review and (entry["type"] != "memory" or entry["refinement"] != "pending"):
            continue
        if q.source and q.source.casefold() not in json.dumps(entry["provenance"]).casefold():
            continue
        text = " ".join([entry["title"], entry["summary"], entry["content"], *entry["tags"], *entry["aliases"]]).casefold()
        score = sum(token in text for token in tokens)
        if tokens and not score and not q.ids:
            continue
        candidates.append((score, entry))
    edges = [e for e in value["edges"] if not q.relations or e["relation"] in q.relations]
    if q.ids:
        neighbors = set(q.ids)
        for edge in edges:
            if edge["source"] in q.ids or edge["target"] in q.ids:
                neighbors.update([edge["source"], edge["target"]])
        candidates = [(score, e) for score, e in candidates if e["id"] in neighbors]
    candidates.sort(key=lambda item: (-item[0], item[1]["type"] != "capability", item[1]["title"], item[1]["id"]))
    page = [entry for _, entry in candidates[q.offset:q.offset + q.limit]]
    if q.ids:
        present = {e["id"] for e in page}
        page.extend(e for _, e in candidates if e["id"] in q.ids and e["id"] not in present)
    visible = {e["id"] for e in page}
    projected = [{k: e[k] for k in ("id", "title", "type", "summary", "trust", "verification", "refinement")} for e in page]
    return {"nodes": projected, "edges": [e for e in edges if e["source"] in visible and e["target"] in visible],
            "total": len(candidates), "next_offset": q.offset + q.limit if len(candidates) > q.offset + q.limit else None,
            "statistics": summary(value)}

class Inspect(Model):
    entry_id: str
    resource_path: str | None = None

def inspect(value, arguments):
    args = Inspect.model_validate(arguments)
    entry = next((e for e in value["entries"] if e["id"] == args.entry_id), None)
    if entry is None:
        raise ValueError("Entry not found")
    resources = []
    for resource in entry["resources"]:
        skill = next((s for s in value["skills"] if s["id"] == resource["skill_id"]), None)
        if skill:
            resources.append({**resource, "skill_node_id": skill.get("node_id"), "files": list(skill["files"])})
            if args.resource_path is not None:
                if args.resource_path not in skill["files"]:
                    raise ValueError("Resource path not found")
                return {"path": args.resource_path, "content": skill["files"][args.resource_path]}
    return {"entry": entry, "resources": resources,
            "relationships": [e for e in value["edges"] if args.entry_id in (e["source"], e["target"])]}

class Edit(Model):
    entry_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    type: Kind = "capability"
    summary: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    trust: float = Field(default=0.5, ge=0, le=1)
    verification: Literal["unverified", "reviewed", "tested"] = "unverified"

def edit(value, arguments):
    args = Edit.model_validate(arguments)
    old = next((e for e in value["entries"] if e["id"] == args.entry_id), None)
    if args.entry_id and old is None:
        raise ValueError("Entry not found")
    entry = Entry.model_validate({**(old or {}), **args.model_dump(exclude={"entry_id"})}).model_dump(mode="json")
    if old and old["owner"] == "publisher":
        entry.update(owner="user", refinement="pending")
    return {**value, "entries": [e for e in value["entries"] if not old or e["id"] != old["id"]] + [entry]}

class Memory(Model):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    success: bool | None = None

def remember(value, arguments):
    args = Memory.model_validate(arguments)
    entry = Entry(title=args.title, type="memory", content=args.content, session_id=args.session_id,
                  success=args.success, provenance={"session_id": args.session_id}).model_dump(mode="json")
    return {**value, "entries": [*value["entries"], entry], "edges": [*value["edges"],
        *[Edge(source=entry["id"], target=key, relation="related_memory").model_dump() for key in args.source_ids]]}

class Distill(Model):
    memory_ids: list[str] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    type: Literal["heuristic", "procedure"] = "heuristic"
    evidence: str = Field(min_length=1)

def distill(value, arguments):
    args = Distill.model_validate(arguments)
    result = deepcopy(value)
    memories = [e for e in result["entries"] if e["id"] in args.memory_ids]
    if len(memories) != len(set(args.memory_ids)) or any(e["type"] != "memory" or e["refinement"] != "pending" for e in memories):
        raise ValueError("Choose pending Memories for explicit review")
    entry = Entry(title=args.title, type=args.type, content=args.content, verification="reviewed",
        refinement="refined", provenance={"memory_ids": args.memory_ids, "review_evidence": args.evidence}).model_dump(mode="json")
    result["entries"].append(entry)
    for memory in memories:
        memory["refinement"] = "refined"
        result["edges"].append(Edge(source=entry["id"], target=memory["id"], relation="derived_from").model_dump())
    return result

def connect(value, arguments):
    edge = Edge.model_validate(arguments).model_dump()
    return {**value, "edges": [*value["edges"], edge]}

class EntryId(Model):
    entry_id: str

class EdgeId(Model):
    edge_id: str

def delete_entry(value, arguments):
    key = EntryId.model_validate(arguments).entry_id
    entry = next((e for e in value["entries"] if e["id"] == key), None)
    if entry is None:
        raise ValueError("Entry not found")
    return {**value, "entries": [e for e in value["entries"] if e["id"] != key],
            "edges": [e for e in value["edges"] if key not in (e["source"], e["target"])]}

def delete_relationship(value, arguments):
    key = EdgeId.model_validate(arguments).edge_id
    if not any(e["id"] == key for e in value["edges"]):
        raise ValueError("Relationship not found")
    return {**value, "edges": [e for e in value["edges"] if e["id"] != key]}

def record_use(value, arguments):
    key = EntryId.model_validate(arguments).entry_id
    return {**value, "entries": [{**e, "usage_count": e["usage_count"] + 1} if e["id"] == key else e for e in value["entries"]]}

def assimilate(value, source, provenance):
    package = SkillPackage.model_validate(source)
    canonical = package.model_dump(mode="json")
    for skill in canonical["skills"]:
        skill["node_id"] = None
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    if digest in value["snapshots"]:
        raise ValueError("This exact package is already assimilated")
    result = deepcopy(value)
    result["snapshots"][digest] = {"package": canonical, "provenance": provenance, "sha256": digest}
    index = {}
    imported = []
    for skill in package.skills:
        key = digest[:12] + "-" + hashlib.sha256(skill.id.encode()).hexdigest()[:16]
        stored = skill.model_copy(update={"id": key, "node_id": None})
        result["skills"].append(stored.model_dump(mode="json"))
        metadata = skill.defaults.get("metadata", {})
        kind = metadata.get("entry_type", "capability")
        kind = kind if kind in {"capability", "procedure", "heuristic"} else "capability"
        entry = Entry(id=key, title=skill.name, type=kind, summary=skill.description, content=skill.instructions,
            tags=metadata.get("tags", []), owner="publisher", refinement="published",
            resources=[{"skill_id": key, "path": "SKILL.md"}],
            provenance={**provenance, "snapshot": digest, "package_id": package.package_id, "version": package.version,
                        "upstream": skill.defaults.get("upstream", {}), "requirements": skill.defaults.get("runtime")}).model_dump(mode="json")
        result["entries"].append(entry)
        index[skill.defaults.get("upstream", {}).get("path", skill.id)] = key
        index[skill.name] = key
        imported.append((entry, skill, metadata))
        for path, content in skill.files.items():
            if not isinstance(content, str) or not path.endswith(".md") or path.endswith("SKILL.md") or not path.startswith(("references/", "assets/")):
                continue
            title = next((line.lstrip("# ").strip() for line in content.splitlines() if line.startswith("#")), path.rsplit("/", 1)[-1].removesuffix(".md"))
            if not re.search(r"relax|scf|workflow|convert|transform|conductivity|finetun|ordering|label|read-results|execution|submission", path):
                continue
            procedure = Entry(id=hashlib.sha256((key + path).encode()).hexdigest()[:32], title=title[:200], type="procedure", summary=content[:240], content=content,
                owner="publisher", refinement="published", provenance=entry["provenance"],
                resources=[{"skill_id": key, "path": path}]).model_dump(mode="json")
            result["entries"].append(procedure)
            result["edges"].append(Edge(source=procedure["id"], target=key, relation="refinement_of").model_dump())
    seen = set()
    for entry, skill, metadata in imported:
        for dependency in metadata.get("dependent_skills", []):
            target = index.get(dependency)
            pair = (entry["id"], target)
            if target and target != entry["id"] and pair not in seen:
                seen.add(pair)
                result["edges"].append(Edge(source=entry["id"], target=target, relation="dependency").model_dump())
    return Graph.model_validate(result).model_dump(mode="json")
