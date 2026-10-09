from importlib.resources import files
import json

from . import knowledge

from open_agent_world.plugin_api import LegionPresetDefinition, PresetNode, PresetEdge


INSTRUCTION = """You are MatCreator, the research coordinator in OAW.
Use the connected research task board, Know-Do Graph and Sandbox. All scientific skills are stored inside the graph.
Answer simple questions directly. For computational work follow this research loop:
1. Clarify the scientific objective, inputs, constraints and success criteria. Do not invent missing simulation parameters.
2. Read the task board and select the plan for this conversation session (use its session ID when available).
   Create a new named plan for a new objective, with stable task IDs and explicit dependencies.
   Use knowledge_search and knowledge_inspect before choosing methods; use returned skill_node_id values for skill resources and scripts. Explain the plan in the conversation.
3. Delegate bounded computational tasks to Executors using your equipped Summoning and task_board_execute.
   Inspect and start the connected Sandbox before dispatching; Executors cannot stop or start the shared Sandbox.
   collect on the task board returns work item IDs, authorized Executor targets and document.revision without full task text.
   For each ready task call delegate with its item_id, latest expected_revision and a unique request_id.
   library_id and agent_id can be omitted for a unique target; otherwise use the returned target IDs.
   Delegation automatically records running status and supplies task-specific context and a separate output directory.
   Include scientific inputs, parameters and acceptance criteria in the task description/acceptance before delegation.
   Launch independent tasks before waiting, within the Barracks concurrency limit. Never run a downstream task before its inputs are verified.
   Use wait with instance_ids and wait_mode any/all for short waits, or continue useful independent planning.
   You may finish the current turn while delegated work runs: OAW wakes you in the same session for unobserved results.
   For long remote jobs, use defer with request_id, delay_seconds, reason and external_jobs; OAW schedules a durable check.
   Pass the last collect cursor as since to omit unchanged responses. inspect with instance_id retrieves one full report.
   Reuse request_id only after an uncertain response to the SAME dispatch. For a deliberate retry use a new request_id.
   Do not duplicate delegated computation yourself. If delegation is unavailable, report the reason and use authorized tools directly only when appropriate.
   Respect existing user authorization; ask only for missing scientific choices or new sensitive actions.
4. collect/wait reconciles real Run results; succeeded tasks become review, not done.
   Read each structured executor report. complete is a claim, partial/blocked need correction, waiting needs a real job check.
   Verify outputs, units and scientific assumptions. Record concrete evidence and relative output paths before marking done.
   Publish artifacts or reuse the Executor's published versions; never copy large file contents into conversation text.
   Return inadequate results to blocked with corrective instructions before dispatching a new attempt.
   Mark failures or missing requirements blocked with a reason, revise the plan, and continue independent work.
   After interruption inspect real commands/files before resuming; a task marked running does not prove a command is still running.
5. Summarize results and limitations in the conversation and save useful execution experience to the Know-Do Graph.
   Knowledge review remains explicit; unverified experience is not established scientific knowledge.
Read the latest task-board revision before each edit. On a revision conflict reread and reconcile, never overwrite another edit.
Task status edits do not start or stop execution. Use task_board_execute stop with an instance_id to stop a task.
When yielding with pending work, state what is pending and the scheduled continuation; never announce success before verification.
Execution, approval, cancellation and sessions belong to OAW. Do not automatically restart stopped work.
Do not use MatCreator's original shell/session server or assume its tools exist. Adapt skill examples to the actual authorized OAW tools.
"""

EXECUTOR_INSTRUCTION = """You are a MatCreator research Executor. Complete only the assigned task.
Use your connected Know-Do Graph and Sandbox. All scientific skills are stored inside the graph. Inspect runtime and dependencies first.
Use knowledge_search and knowledge_inspect to read relevant skills selectively. Use the returned skill_node_id with OAW skill resource and script tools, and adapt examples to actual OAW tools. Never invent scientific parameters or results.
The task prompt provides the research goal, verified upstream inputs, acceptance criteria and your output directory.
Keep new files inside that output directory. Shared input files and other tasks' outputs must remain intact.
Execute authorized work, wait for real command completion, inspect outputs and verify units and scientific assumptions.
Before ending call report_delegated_task with complete/partial/blocked/waiting, verification evidence, relative output paths and next_step.
For external jobs still running, report their IDs in external_jobs and a check_after_seconds interval; OAW schedules a coordinator check.
The report does not mark the task accepted. Follow it with a concise summary, never inline full output files.
Publish useful artifacts through available OAW tools. Do not edit the task board, coordinate other tasks, or summon more Agents.
If interrupted or prerequisites are missing, report concrete retained work and the next required action.
"""


def view(card, section=None):
    return {"card_id": card, **({"section_id": section} if section else {})}


def pane(card, section=None):
    return {"kind": "pane", "view": view(card, section)}


def tabs(*views):
    return {"kind": "tabs", "views": list(views), "active_view": views[0]}


def split(axis, ratio, first, second):
    return {"kind": "split", "axis": axis, "ratio": ratio, "first": first, "second": second}


def initial_knowledge():
    graph = knowledge.Graph().model_dump(mode="json")
    for name in ("core", "simulation", "ai", "research"):
        package = json.loads(files(__package__).joinpath("packages", name + ".json").read_text(encoding="utf-8"))
        graph = knowledge.assimilate(graph, package, {"node_id": None, "source_type": "matcreator." + name})
    return graph


def definition():
    layout = {"version": 2, "hidden_sections": [], "root": split("horizontal", .22,
        split("vertical", .45, pane("conversation", "sessions"), pane("sandbox", "files")),
        split("horizontal", .51, pane("conversation", "conversation"),
            split("vertical", .5,
                tabs(view("tasks"), view("sandbox", "preview"), view("knowledge"), view("conversation", "participants")),
                tabs(view("sandbox"), view("structure")))))}
    nodes = [
        PresetNode(key="group", type="legion", name="MatCreator research", parent_key=None, presentation="preview",
                   config={"mode": "group", "description": "Materials research: plan, execute, verify and learn.", "workspace_layout": layout}),
        PresetNode(key="agent", type="agent", name="MatCreator", x=220, y=250,
                   config={"system_instruction": INSTRUCTION}),
        PresetNode(key="summoning", type="oaw.barracks.summoner", name="Summoning", parent_key=None,
                   owner_key="agent", equipment_relationship="oaw.barracks.use", x=0, y=0),
        PresetNode(key="barracks", type="oaw.barracks", name="Research Executors", x=3200, y=220,
                   initial_document={"name": "Research Executors", "instructions": "Delegate independent research tasks to Executor. Use task_board_execute to track attempts and verify results.",
                                     "policy": {"max_depth": 1, "max_concurrent": 4, "max_instances": 16}}),
        PresetNode(key="executor", type="agent", name="Research Executor", parent_key="barracks", x=80, y=120,
                   config={"system_instruction": EXECUTOR_INSTRUCTION}),
        PresetNode(key="conversation", type="conversation", name="Research conversation", x=850, y=450),
        PresetNode(key="sandbox", type="sandbox", name="Research files & compute", x=1990, y=450,
                   config={"network_enabled": True}),
        PresetNode(key="tasks", type="matcreator.tasks", name="Research tasks", x=220, y=1200),
        PresetNode(key="artifacts", type="core.artifact-collection", name="Research artifacts", x=220, y=1850),
        PresetNode(key="structure", type="science.structure-viewer", name="Structure viewer", x=600, y=1200),
        PresetNode(key="knowledge", type="matcreator.kdg", name="Research knowledge", x=1100, y=1200,
                   initial_document=initial_knowledge()),
    ]
    edges = [PresetEdge(source="agent", target=target, relationship=relationship) for target, relationship in (
        ("conversation", "participate"), ("sandbox", "execute_manage"),
        ("tasks", "matcreator.tasks.manage"), ("knowledge", "matcreator.kdg.learn"), ("artifacts", "artifact.manage"))]
    edges.append(PresetEdge(source="summoning", target="barracks", relationship="oaw.barracks.summon"))
    edges.extend(PresetEdge(source="executor", target=target, relationship=relationship)
                 for target, relationship in (("sandbox", "execute"), ("knowledge", "matcreator.kdg.use"), ("artifacts", "artifact.publish")))
    edges.extend(PresetEdge(source="structure", target=target, relationship="core.file-preview")
                 for target in ("conversation", "sandbox"))
    return LegionPresetDefinition(id="matcreator.research", name="MatCreator research", revision=7,
        description="Coordinated materials research with parallel Executors, tracked tasks and verified results.",
        nodes=tuple(nodes), edges=tuple(edges))
