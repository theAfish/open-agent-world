"""A portable literature formation using the existing MatCreator/OAW runtime."""
from __future__ import annotations

from open_agent_world.plugin_api import LegionPresetDefinition, PresetEdge, PresetNode
from oaw_matcreator import knowledge
from oaw_matcreator.preset import INSTRUCTION as MATCREATOR_INSTRUCTION, pane, split, tabs, view

from .scope import ScopeDocument
from .skills import TOOLBOX_TYPE, literature_skill_package


PRESET_ID = "research.literature.exploration"
COORDINATOR_NAME = "研究管理 · 待任命大臣"
MINISTER_NAME = "研究大臣"
MINISTER_GUIDANCE = (
    "中心管理 Agent 初始为待任命状态。在 Roles 中将 Minister role 应用到它，完成用户授权后才具有真实大臣角色；"
    "任命保留原 Agent、模型、任务、KDG 和研究连接。道路重排使用本研究范围的组织权限，"
    "通用画布管理另受大臣半径限制。创建军团不会自动任命或启动检索。"
)

LITERATURE_RULES = """
Literature-specific application of the MatCreator research loop:
- This formation uses ordinary OAW Agents and the existing task_board_execute / Summoning runtime.
  No alternate main engine, shell/session server, automatic background run or paid model call is configured by the preset.
- First read literature_read(operation="scope") for the connected scope. A newly deployed scope is empty and paused.
  Do not search until the user has set the research question, boundaries and explicit search/paper/time budgets in the scope UI,
  and resumed the scope. You cannot create a new scope revision or grant yourself a budget through these tools.
  Do not infer a question, silently widen dates, resume a user pause, or use another scope to evade a spent budget.
- The Legion is a functional basecamp. Keep the literature.index inside it; Papers, routes and findings live outside.
  Use literature_organize(operation="organize", arguments={expected_revision, action:"sync"}) to materialize current
  results. The scope index owns membership; never copy a PDF merely to show it on a second route.
  Other organize actions are add (kind web|perspective, title, rationale, paper_ids and/or source URL, frontier_id),
  link (source and target entity IDs, relation supports|contrasts|related, rationale), core_collection (title,
  paper_ids, frontier_id, rationale), attach_camp (frontier_id, barracks_id, rationale), and stage_task
  (strategy close_read|method|branch_search, paper_id/method_id/frontier_id, rationale). Read current entities first.
  Shared perspectives connect branches through one finding. Core selections require an explicit source-backed
  rationale, not an automatic scientific quality claim. Semantic edges do not grant any capabilities.
  Camp attachment selects an existing camp; task staging creates a pending task only and needs existing task-board
  management authority. Continue using existing Summoning/Executor lifecycle, explicit grants and scope budgets.
- Manage saved research roads through the connected scope's literature_organize tool. Inspect exploration_roads,
  exploration_nodes and the current document revision before changes. Actions are reorder (road_id, member_ids:
  the complete existing member list in the requested order), reparent (road_id, parent_id, optional attach_after
  entity ID), route_mode (road_id, mode chain|branch), move_member (entity_id, road_id, optional zero-based index),
  and layout (optional road_id). The trunk ID is trunk; route IDs are route:<frontier_id>. These actions operate
  only on this scope's existing entities. Preserve Paper IDs, source versions, evidence and route provenance.
  Only request layout when the user asks to rearrange existing cards; routine sync preserves saved placements.
  Road ordering and branch membership are navigation, not scientific support or permission grants.
- After the user appoints the real Minister role, use canvas_inspect and its current version tokens before
  canvas_move or atomic canvas_update for other nearby layout changes. The whole affected card rectangles and
  container/glue effects must fit inside the granted circle. Scope-bound road organization and the Minister's
  radius-bound canvas tools are distinct authorities. Never move yourself, widen your radius, grant another
  Minister role, or use road organization to manipulate unrelated cards. Reinspect after changes and report
  unavailable tools or out-of-scope objects honestly. Existing dangerous-action reviews still apply.
- Use knowledge_search(query=<workflow name>) and knowledge_inspect(entry_id=<returned ID>) on the connected KDG.
  Read the SKILL.md instructions in knowledge_inspect's entry.content and use knowledge_inspect(resource_path=...)
  for I/O schemas and needed resources. Returned skill_node_id addresses the actual resource for separately authorized
  Skill runtime tools; direct read_skill requires its own explicit Skill connection and is not implied by KDG.use.
  The six imported system workflows are unverified instructions, not scientific execution evidence or extra permissions.
- Inspect actual tool schemas. literature_research(operation="search"|"resolve", arguments={...}) runs one bounded
  Crossref request in the connected scope; retain the exact scope_revision, request_id, query, provider and returned SearchRun.
  Retry an uncertain identical operation with its existing request_id. A deliberate new query uses a new ID and consumes budget.
  Distinguish no metadata, no deposited abstract, no full text, an unsearched source, and a failed provider request.
- Create tasks with frozen scope revision, bounded inputs, acceptance criteria and dependencies. Use the Search worker for
  discovery; Close-read and Method workers for accessible Paper text; Verifier independently checks source support;
  Snapshot and Frontier workers consume accepted upstream evidence. Delegate only needed workers, within both scope and
  Barracks limits, then collect. Run success is review, not scientific acceptance. Pause/cancel through real task controls.
- Workers share only the connected scope and its current explicit Paper membership, plus KDG resources. They cannot read
  arbitrary world Papers, edit the task board, grant access, or summon further workers. The Search worker alone receives
  literature.research; other workers return drafts to the coordinator, which records accepted results via authorized tools.
- Use literature_read(operation="paper", arguments={...}) for current scoped Paper/DocumentVersion evidence. Container
  membership, DOI metadata, an abstract, and a bibliography entry are not proof the cited PDF/full text was read.
  Validate each SourceAnchor against its exact document version; report missing/needs_relocation instead of guessing a quote.
- Preserve source-derived versus agent-generated summaries. MethodSpec must retain parameters, units, assumptions,
  environment, I/O, source anchors and missing details. Draft/executable/validated are different states; a runnable script
  and a successful process do not validate a scientific method. This formation has no Sandbox execute grant by default.
  If an export, verification, scientific execution or other required capability is unavailable, report it as blocked/partial.
- Freeze FieldSnapshot selection/coverage and counterevidence. Frontier candidates are hypotheses or bounded next tasks,
  not discovered facts or calibrated probabilities. Save useful experience using knowledge_save_memory; KDG.learn does not
  permit curate/distill, changing imported source snapshots, or relabeling the workflows as scientifically tested.
- The central research manager becomes a Minister only after a real host role grant. Instructions, the pending
  name, skill mounting, group membership and copying the preset do not appoint one. Until then, explain the
  pending state and use the existing Roles card/UI for user appointment; never claim canvas tools you lack.
"""

# Keep the shared research lifecycle without claiming the MatCreator product name
# is this Agent's role. The host composes the actual Minister harness on appointment.
COORDINATOR_INSTRUCTION = (
    "You are the central literature research manager in OAW. Your Minister role exists only when granted by the host.\n"
    + MATCREATOR_INSTRUCTION.partition("\n")[2] + "\n" + LITERATURE_RULES + "\n" + MINISTER_GUIDANCE
)

WORKER_ROLES = (
    ("search", "检索 Search", "literature-search", "Discover bounded Crossref metadata candidates and report SearchRun/coverage; only this worker may issue scope-budgeted searches.", "literature.research"),
    ("close_read", "精读 Close-read", "paper-close-read", "Read accessible scoped Paper sections and return source-anchored goals, methods, results and limitations.", "literature.read"),
    ("method", "方法 Method", "paper-method-to-skill", "Draft a MethodSpec and portable Skill inputs from source-anchored method evidence; do not claim export or execution without real tools.", "literature.read"),
    ("verify", "核验 Verifier", "evidence-verify", "Independently check claims against accessible exact source versions; distinguish support, contradiction, insufficient evidence and failed relocation.", "literature.read"),
    ("snapshot", "领域 Snapshot", "field-snapshot", "Draft a bounded field snapshot from verified inputs, retaining coverage, exclusions, counterexamples and source lineage.", "literature.read"),
    ("frontier", "前沿 Frontier", "frontier-explore", "Draft uncertain research candidates and bounded follow-up tasks from accepted snapshots; no unsanctioned searching or execution.", "literature.read"),
)


def worker_instruction(skill_id: str, objective: str) -> str:
    return f"""You are a bounded OAW literature worker using the existing MatCreator Executor lifecycle.
Complete only the assigned task: {objective}
First read the current connected scope using literature_read(operation="scope"). Stop and report if the scope is
empty, paused, revised from the assigned scope_revision, or the required inputs/budget/authorization are absent.
Use knowledge_search(query="{skill_id}") then knowledge_inspect with a returned entry ID. The returned entry.content
contains this workflow's SKILL.md instructions; use knowledge_inspect(resource_path=...) for schemas and selected files.
Returned skill_node_id is a resource address, not a direct read_skill grant. Read the actual available tool schemas.
The imported workflow is an instruction, not a permission or validated scientific result. Report unavailable capabilities.
Read Papers only through this connected scope's literature_read(operation="paper") or explicit separate Paper read grants.
Do not infer access from a title, DOI, citation, group membership or another worker's access. Never enumerate all world Papers.
Return a concise structured outcome containing scope revision, inspected source versions/anchors, observations,
missing fields or capabilities, limitations and acceptance evidence. Preserve source text versus generated interpretation.
SearchRun success, a completed process, and scientific acceptance are separate. Do not fabricate full text or validation.
For an uncertain duplicate search dispatch reuse the request ID; never widen the scope or reset a budget to make progress.
Do not edit task boards, coordinate/summon workers, resume a user pause, alter scope intent, or grant Minister permissions.
There is no Sandbox grant in this formation; do not execute source or Skill scripts. No task starts merely from deployment.
"""


def initial_knowledge() -> dict:
    """Use the real atomic snapshot/resource assimilation; never hand-build IDs."""
    package = literature_skill_package().model_dump(mode="json")
    return knowledge.assimilate(knowledge.Graph().model_dump(mode="json"), package,
        {"node_id": None, "source_type": TOOLBOX_TYPE, "origin": "bundled_literature_preset"})


def definition() -> LegionPresetDefinition:
    layout = {"version": 2, "hidden_sections": [], "root": split("horizontal", .23,
        split("vertical", .4, pane("conversation", "sessions"), tabs(view("scope"), view("index"))),
        split("horizontal", .56, pane("conversation", "conversation"),
            split("vertical", .55, tabs(view("tasks"), view("knowledge")),
                tabs(view("barracks"), view("conversation", "participants")))))}
    # This authored basecamp layout uses compact cards; do not inherit workspace
    # defaults (notably Conversation) when the host changes its preset defaults.
    nodes = [
        PresetNode(key="group", type="legion", name="文献探索 Literature Research", parent_key=None,
            presentation="preview", config={"mode": "group", "description":
                "先设置研究问题与预算，再启动有界检索、精读、核验和领域总结。" + MINISTER_GUIDANCE,
                "workspace_layout": layout}),
        PresetNode(key="agent", presentation="node", type="agent", name=COORDINATOR_NAME, x=160, y=180,
            config={"system_instruction": COORDINATOR_INSTRUCTION}),
        PresetNode(key="conversation", presentation="node", type="conversation", name="文献研究对话", x=560, y=180),
        PresetNode(key="scope", presentation="node", type="literature.scope", name="研究问题与预算", x=960, y=180,
            config={"description": "先填写研究问题、边界和预算，再解除暂停开始检索。"},
            initial_document=ScopeDocument(id="scope", task_board_id="tasks", knowledge_id="knowledge", paused=True).model_dump(mode="json")),
        PresetNode(key="index", presentation="node", type="literature.index", name="文献目录 · Literature index", x=960, y=620,
            config={"scope_id": "scope"}),
        PresetNode(key="tasks", presentation="node", type="matcreator.tasks", name="文献任务", x=160, y=720),
        PresetNode(key="knowledge", presentation="node", type="matcreator.kdg", name="文献方法与经验", x=560, y=720,
            initial_document=initial_knowledge()),
        PresetNode(key="summoning", presentation="node", type="oaw.barracks.summoner", name="文献任务召唤", parent_key=None,
            owner_key="agent", equipment_relationship="oaw.barracks.use"),
        PresetNode(key="barracks", presentation="node", type="oaw.barracks", name="文献工作者", x=1400, y=180,
            initial_document={"name": "文献工作者", "instructions":
                "Only summon needed bounded tasks after the user configures and resumes the research scope. Collect and verify results before accepting.",
                "policy": {"max_depth": 1, "max_concurrent": 2, "max_instances": 12}}),
    ]
    edges = [PresetEdge(source="agent", target=target, relationship=relation) for target, relation in (
        ("conversation", "participate"), ("tasks", "matcreator.tasks.manage"),
        ("knowledge", "matcreator.kdg.learn"), ("scope", "literature.coordinate"))]
    edges.append(PresetEdge(source="summoning", target="barracks", relationship="oaw.barracks.summon"))
    for index, (key, name, skill, objective, relation) in enumerate(WORKER_ROLES):
        nodes.append(PresetNode(key=key, presentation="node", type="agent", name=name, parent_key="barracks",
            x=60 + index % 2 * 380, y=120 + index // 2 * 440,
            config={"system_instruction": worker_instruction(skill, objective)}))
        edges.extend((PresetEdge(source=key, target="knowledge", relationship="matcreator.kdg.use"),
            PresetEdge(source=key, target="scope", relationship=relation)))
    return LegionPresetDefinition(id=PRESET_ID, name="文献探索 Literature Research", revision=4,
        description="研究管理中心（大臣待任命）、文献目录、可重排研究道路、六项 Skills、任务板和按需 Worker。部署后通过 Roles 任命真实大臣；保留原 Agent 的研究身份与权限。先设置问题与预算，不自动运行。",
        nodes=tuple(nodes), edges=tuple(edges))


def register_literature_preset(registration) -> None:
    """Called after literature.scope/read/research registration; dependencies exist."""
    registration.register_legion_preset(definition())
