"""Bundled starting formations, deployed through the Legion template contract."""
from datetime import UTC, datetime

from backend.errors import NotFoundError
from backend.legions.models import LegionBlueprint, LegionRecord, LegionTemplateDependency, LegionTemplateEdge, LegionTemplateNode
from backend.plugins.presets import LegionPresetDefinition
from backend.plugins.registry import PluginRegistry


PRESETS = {
    "assistant": ("General assistant", "An Agent and a Conversation for everyday questions and ideas."),
    "coding": ("Coding workspace", "An Agent, a Conversation and a Sandbox for building and running code."),
    "team": ("Multi-Agent collaboration", "A planner, a builder and a reviewer connected to one shared Conversation."),
}


def preset_record(preset_id: str, registry: PluginRegistry) -> LegionRecord:
    for preset in registry.legion_presets():
        if preset.id == preset_id:
            return plugin_preset_record(preset, registry)
    if preset_id not in PRESETS:
        raise NotFoundError(f"blueprint {preset_id!r} does not exist")
    name, description = PRESETS[preset_id]
    nodes: list[LegionTemplateNode] = []
    edges: list[LegionTemplateEdge] = []

    def node(key, type_id, label, x, y, *, config=None):
        definition = registry.node_type(type_id)
        handler = definition.template_handler
        nodes.append(LegionTemplateNode(
            key=key, parent_key=None if key == "group" else "group",
            type=type_id, plugin_id=registry.node_type_owner_id(type_id), name=label,
            position={"x": x, "y": y}, size={"width": definition.default_size[0], "height": definition.default_size[1]},
            expanded=False, status=definition.template_status or definition.default_status,
            config=registry.validate_config(type_id, config or {}),
            payload_version=handler.payload_version if handler else None,
            payload={} if handler else None,
            presentation={"level": "preview", "base_level": "preview"},
        ))

    def edge(source, target, relationship, direction="forward"):
        edges.append(LegionTemplateEdge(
            key=f"edge-{len(edges) + 1}", source=source, target=target, relationship=relationship,
            plugin_id=registry.relationship_owner_id(relationship), direction=direction,
        ))

    node("group", "legion", name, 0, 0, config={"mode": "group", "description": description})
    if preset_id == "team":
        for key, label, x, instruction in (
            ("planner", "Planner", 190, "Clarify the goal and coordinate a concrete plan with the Builder and Reviewer."),
            ("builder", "Builder", 510, "Implement the plan and report results to the Planner and Reviewer."),
            ("reviewer", "Reviewer", 830, "Review the proposed work, identify problems and verify the result."),
        ):
            node(key, "agent", label, x, 280, config={"system_instruction": instruction})
            edge(key, "conversation", "participate")
        node("conversation", "conversation", "Team conversation", 510, 700)
        edge("planner", "builder", "communicate", "bidirectional")
        edge("builder", "reviewer", "communicate", "bidirectional")
        edge("planner", "reviewer", "communicate", "bidirectional")
    else:
        node("agent", "agent", "Coding assistant" if preset_id == "coding" else "Assistant", 190, 280)
        node("conversation", "conversation", "Conversation", 550, 280)
        edge("agent", "conversation", "participate")
        if preset_id == "coding":
            node("sandbox", "sandbox", "Sandbox", 910, 280)
            edge("agent", "sandbox", "execute")

    # Layout references use the same portable keys as the formation itself.
    if preset_id == "coding":
        nodes[0].config["workspace_layout"] = {
            "version": 2, "root": {"kind": "split", "axis": "horizontal", "ratio": 0.35,
                "first": {"kind": "pane", "view": {"card_id": "conversation"}},
                "second": {"kind": "pane", "view": {"card_id": "sandbox"}}},
            "hidden_sections": [],
        }
    now = datetime(2026, 9, 16, tzinfo=UTC)
    return LegionRecord(
        id=preset_id, name=name, description=description, created_at=now, updated_at=now, revision=1,
        blueprint=LegionBlueprint(bounds={"width": max(n.position.x + n.size.width for n in nodes),
                                          "height": max(n.position.y + n.size.height for n in nodes)},
                                  nodes=nodes, edges=edges),
    )


def plugin_preset_record(preset: LegionPresetDefinition, registry: PluginRegistry) -> LegionRecord:
    nodes = []
    for item in preset.nodes:
        definition = registry.node_type(item.type)
        if not definition.templateable:
            raise ValueError(f"Preset node type {item.type!r} must be templateable")
        handler = definition.template_handler
        config = registry.validate_config(item.type, item.config)
        if item.presentation not in definition.resolved_presentation().states:
            raise ValueError(f"Unsupported preset presentation for {item.type!r}")
        dependencies = []
        if handler:
            handler.validate_payload(item.payload, handler.payload_version)
            dependencies = [LegionTemplateDependency(kind=dep.kind, id=dep.id,
                plugin_id=registry.owner_id(dep.kind, dep.id)) for dep in handler.dependencies(config)]
        elif item.payload:
            raise ValueError("Preset payload requires a template handler")
        if item.initial_document is not None:
            if definition.document is None:
                raise ValueError("Preset initial document requires a document node")
            definition.document.model.model_validate(item.initial_document)
        nodes.append(LegionTemplateNode(
            key=item.key, type=item.type, parent_key=item.parent_key,
            owner_key=item.owner_key, equipment_relationship=item.equipment_relationship,
            plugin_id=registry.node_type_owner_id(item.type), name=item.name,
            position={"x": item.x, "y": item.y},
            size={"width": definition.default_size[0], "height": definition.default_size[1]},
            expanded=False, status=definition.template_status or definition.default_status,
            config=config, initial_document=item.initial_document,
            payload_version=handler.payload_version if handler else None,
            payload=item.payload if handler else None, dependencies=dependencies,
            presentation={"level": item.presentation,
                          "base_level": "node"},
        ))
    edges = [LegionTemplateEdge(key=f"edge-{index}", source=item.source, target=item.target,
                relationship=item.relationship, direction=item.direction,
                plugin_id=registry.relationship_owner_id(item.relationship))
             for index, item in enumerate(preset.edges)]
    now = datetime(2026, 9, 18, tzinfo=UTC)
    return LegionRecord(id=preset.id, name=preset.name, description=preset.description,
        revision=preset.revision, created_at=now, updated_at=now,
        blueprint=LegionBlueprint(nodes=nodes, edges=edges, bounds={
            "width": max(n.position.x + n.size.width for n in nodes),
            "height": max(n.position.y + n.size.height for n in nodes)}))
