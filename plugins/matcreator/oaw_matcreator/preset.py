from importlib.resources import files
import json

from . import knowledge

from open_agent_world.plugin_api import LegionPresetDefinition, PresetNode, PresetEdge


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


def native_definition():
    """Native orchestration with the same connected research surfaces."""
    layout = {"version": 2, "hidden_sections": [], "root": split("horizontal", .18,
        split("vertical", .6, pane("execution"), pane("jobs")),
        split("horizontal", .76,
            tabs(view("conversation", "conversation"), view("knowledge"), view("structure"),
                 view("sandbox", "preview"), view("artifacts"), view("sandbox")),
            split("vertical", .43, pane("conversation", "sessions"), pane("sandbox", "files"))))}
    nodes = (
        PresetNode(key="group", type="legion", name="MatCreator research", parent_key=None,
            presentation="preview", config={"mode": "group", "description": "Native MatCreator planner and subagents with OAW research tools.", "workspace_layout": layout}),
        # Positions anchor the 96px node; preview/workspace surfaces expand
        # around it. Conversation and Sandbox open as 1020x700 workspaces,
        # flanking the 224x300 central cards. The knowledge container uses its
        # own top-left position and 1000x650 size.
        PresetNode(key="agent", type="matcreator.agent", name="MatCreator Agent", x=1204, y=654, presentation="preview"),
        PresetNode(key="conversation", type="conversation", name="Research conversation", x=502, y=414, presentation="workspace"),
        PresetNode(key="sandbox", type="sandbox", name="Research files & compute", x=1902, y=414, presentation="workspace", config={"network_enabled": True}),
        PresetNode(key="execution", type="core.execution-graph", name="Agent graph", x=504, y=1174, presentation="preview"),
        PresetNode(key="jobs", type="core.jobs", name="Research jobs", x=1204, y=1154, presentation="preview"),
        PresetNode(key="artifacts", type="core.artifact-collection", name="Research artifacts", x=924, y=1234, presentation="preview"),
        PresetNode(key="structure", type="science.structure-viewer", name="Structure viewer", x=1204, y=214, presentation="preview"),
        PresetNode(key="knowledge", type="matcreator.kdg", name="Skills & knowledge", x=1440, y=892, initial_document=initial_knowledge()),
    )
    edges = [PresetEdge(source="agent", target=target, relationship=relationship) for target, relationship in (
        ("conversation", "participate"), ("sandbox", "execute_manage"),
        ("knowledge", "matcreator.kdg.learn"), ("artifacts", "artifact.manage"))]
    edges.append(PresetEdge(source="execution", target="agent", relationship="core.execution-source"))
    edges.append(PresetEdge(source="jobs", target="agent", relationship="core.jobs-manage"))
    edges.extend(PresetEdge(source="structure", target=target, relationship="core.file-preview") for target in ("conversation", "sandbox"))
    return LegionPresetDefinition(id="matcreator.native-research", name="MatCreator research", revision=4,
        description="MatCreator devel planner, execution orchestrator and recursive subagents, with connected OAW tools and views.",
        nodes=nodes, edges=tuple(edges))
