"""Portable literature workflows; registration never performs research or I/O services."""
from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from open_agent_world.skill_packages import Skill, SkillPackage, register_skill_package

PACKAGE_ID = "research.literature"
PACKAGE_VERSION = "0.1.0"
TOOLBOX_TYPE = "research.literature.toolbox"
SKILL_IDS = (
    "literature-search", "paper-close-read", "paper-method-to-skill",
    "evidence-verify", "field-snapshot", "frontier-explore",
)

RUNTIME_ADAPTER = """# OAW runtime adapter

Use actual connected tools, never call the workflow's older illustrative names.
Both tools select the connected `scope` using the normal OAW target parameter.

1. `literature_read(operation="scope")` returns document revision and the frozen
   intent's current_revision, explicit Paper IDs, runs, budgets and results.
2. `literature_read(operation="contracts")` returns the installed Evidence,
   MethodSpec, MicroSkill and scope schemas. Read the needed schema before writing a record.
3. `literature_research(operation="search", arguments={expected_revision,
   scope_revision, request_id, query, rows, from_year?, until_year?})` performs one
   real Crossref page. This replaces literature_search and atomic Paper upsert.
   Use `resolve` with doi instead of query/rows for one DOI. Never omit current
   scope year bounds or widen the user scope. A replay uses the same request_id.
4. `literature_read(operation="paper", arguments={paper_id, view:"metadata"})`
   reads held identity. `view:"anchors", page:1` returns exact, versioned local
   PDF text blocks and SourceAnchors. Reuse these source objects verbatim when
   citing a paragraph. With `page:1` and no view it returns ordinary page text.
   This replaces paper_read_sections/paper_read_evidence. With no PDF, continue
   metadata screening and draft bounded research/search Micro-Skills from held
   metadata or the actual stored source_abstract; no invented anchors or abstracts.
5. `literature_research(operation="record", arguments={expected_revision,
   kind:"evidence"|"method"|"micro_skill", value:<schema-valid object>, item_revision?:1})`
   validates scoped source bytes and writes a result. This replaces
   research_record_evidence and method extraction storage. For Evidence the host
   records the real extractor, and new evidence starts at revision1 with no
   scientific_reviews. Method status remains draft without a host execution.
   When full text is unavailable, use kind="micro_skill" with MicroSkill fields:
   id (unique with a micro- prefix), name, paper_id, paper_revision from paper metadata read, basis="metadata"
   or "abstract", purpose, steps (bounded strings), and nonempty missing list.
   The host binds the current held metadata/abstract and records a draft research
   strategy. Do not submit sources, page anchors, status, reviews or execution
   claims. Abstract basis requires a real stored source_abstract; otherwise use
   metadata. Missing experimental parameters remain missing. A Micro-Skill is
   not a reconstructed experimental MethodSpec and cannot use its export or
   assimilation routes. Revisions require the current item_revision.
6. Read-only workers return drafts to the coordinator; they must not claim a
   persisted result. The coordinator can submit those drafts under its explicit
   scope grant. Losing that grant immediately revokes further access.
7. Method ZIP export and KDG import are available in the desktop scope interface.
   A packaged method does not grant execution. Use separately authorized Sandbox
   and task tools for a scientific run and report actual receipt/artifact IDs.
8. Pause/resume uses literature_research with expected_revision. Never resume a
   user-paused scope automatically. Switching the main canvas AutoResearch mode
   is only presentation and does not call these operations.
9. `literature_read(operation="snapshots")` returns immutable snapshots with
   current source freshness and differences. `literature_research(operation=
   "snapshot", arguments={expected_revision, expected_version, mode:"submit",
   value:<FieldSnapshot>})` appends an attributed source-backed version. Read its
   schema from contracts. Use real completed current-scope request_ids; distinguish
   metadata, abstract and located fulltext evidence. No invented core set or facts.
10. `literature_research(operation="frontier", arguments={expected_revision,
    value:<FrontierRoute>})` saves a finite route. The host owns extractor identity
    and projects discovery/evidence from actual records. `explore` with
    {expected_revision,frontier_id,request_id} executes one Crossref page within
    both scope and route budgets. Never duplicate interrupted requests implicitly.
    `evaluate_frontier` with {expected_revision,frontier_id} records a local
    explainable rule priority and explicit Jev-unavailable status. A rule score is
    not a probability or a scientific judgment; no paid provider is silently used.

Existing read_paper/library.write can also be used when directly granted. The
Skill's report JSON schemas describe reports, not host mutation arguments. When
the requested host feature is not in the installed contracts, report partial.
"""


def _text_files(root, prefix=""):
    """Read only declared package assets, preserving portable relative paths."""
    result = {}
    for item in sorted(root.iterdir(), key=lambda path: path.name):
        name = prefix + item.name
        if item.is_dir():
            result.update(_text_files(item, name + "/"))
        elif name not in {"SKILL.md", "metadata.json"}:
            result[name] = item.read_text(encoding="utf-8")
    return result


def literature_skill_package() -> SkillPackage:
    """Return a fresh package, so each card retains independent local content."""
    root = files("oaw_literature").joinpath("skills")
    skills = []
    for skill_id in SKILL_IDS:
        folder = root.joinpath(skill_id)
        metadata = json.loads(folder.joinpath("metadata.json").read_text(encoding="utf-8"))
        instructions = folder.joinpath("SKILL.md").read_text(encoding="utf-8")
        assets = _text_files(folder)
        assets["references/oaw-runtime.md"] = RUNTIME_ADAPTER
        instructions += "\n## Installed OAW adapter\n\nRead references/oaw-runtime.md first. It maps illustrative tool names above to the implemented scoped tools. Read-only workers return drafts; only an explicitly authorized coordinator persists results.\n"
        digest = hashlib.sha256(json.dumps(
            {"instructions": instructions, "files": assets}, sort_keys=True,
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        skills.append(Skill(
            id=skill_id, name=metadata["name"], description=metadata["description"],
            instructions=instructions, files=assets,
            defaults={
                "metadata": {"entry_type": "procedure", "tags": ["literature", skill_id],
                             "content_sha256": digest},
                "origin": {"kind": "system_workflow", "package_id": PACKAGE_ID,
                           "version": PACKAGE_VERSION, "scientific_execution_claim": "none"},
                "contracts": {"input_schema": "schemas/input.schema.json",
                              "output_schema": "schemas/output.schema.json"},
                "required_capabilities": ["literature.read"] + (["literature.research"] if skill_id == "literature-search" else []),
                "missing_capability_policy": "Return blocked or partial with explicit missing capabilities; never invent results or permissions.",
            },
        ))
    return SkillPackage(
        package_id=PACKAGE_ID, version=PACKAGE_VERSION,
        name="Literature research skills", author="OAW",
        description="Six independently mountable, source-traceable literature workflows.",
        instructions=("Read only the skills needed for the research question. These instructions do not grant "
                      "tools, network, paper access, credentials or execution rights. Preserve source identities, "
                      "scope revisions and explicit unknowns. Tool availability and authorization must be checked "
                      "on each task. Execution success is separate from scientific validation."),
        skills=skills,
    )


def register_literature_skills(registration) -> None:
    """Use the host's standard Toolbox, direct Skill capabilities and exporter."""
    register_skill_package(registration, node_type=TOOLBOX_TYPE, package=literature_skill_package())
