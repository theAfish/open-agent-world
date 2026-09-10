from importlib.resources import files
import json
from open_agent_world.plugin_api import (
    PackDefinition, PluginDescriptor, NodeTypeDefinition, NodeDocumentDefinition, NodeDocumentAction,
    NodeDocumentTransformation, NodeDocumentDownload, CapabilityDefinition,
    CapabilityGrantDefinition, RelationshipDefinition,
)
from open_agent_world.skill_packages import SkillPackage, register_skill_package, register_skill_node, SkillContainerDefinition, ToolboxConfig
from . import knowledge as k

READ = "matcreator.kdg.read"

class MatCreatorPlugin:
    descriptor = PluginDescriptor(id="matcreator", version="0.1.0", plugin_api_version="1.14", name="MatCreator")

    def register(self, registration):
        for name in ("core", "simulation", "ai", "research"):
            package = SkillPackage.model_validate(json.loads(files(__package__).joinpath("packages", name + ".json").read_text(encoding="utf-8")))
            register_skill_package(registration, node_type="matcreator." + name, package=package)
        register_skill_node(registration, node_type="matcreator.kdg.skill", user_creatable=False)
        actions = {"read": NodeDocumentAction(lambda value, args: value, capability_kind=READ, read_only=True)}
        reads = {"search": (k.Query, k.query), "expand": (k.Query, k.query), "inspect": (k.Inspect, k.inspect),
                 "statistics": (k.Model, lambda value, args: k.summary(value))}
        writes = {"edit": (k.Edit, k.edit), "save_memory": (k.Memory, k.remember),
                  "distill": (k.Distill, k.distill), "connect": (k.Edge, k.connect),
                  "delete_entry": (k.EntryId, k.delete_entry), "delete_relationship": (k.EdgeId, k.delete_relationship)}
        actions["record_use"] = NodeDocumentAction(k.record_use, capability_kind="matcreator.kdg.inspect")
        for operation, (model, handler) in {**reads, **writes}.items():
            read = operation in reads
            async def invoke(context, capability, arguments, op=operation, is_read=read):
                arguments = dict(arguments)
                revision = arguments.pop("expected_revision", None)
                result = await context.node_document_action(capability, op, arguments, expected_revision=revision)
                if op == "inspect" and arguments.get("resource_path") is None:
                    used = await context.node_document_action(capability, "record_use", {"entry_id": arguments["entry_id"]}, expected_revision=result["revision"])
                    result["revision"] = used["revision"]
                    result["value"]["entry"]["usage_count"] += 1
                return result if is_read else {"revision": result["revision"], "summary": result["summary"]}
            schema = model.model_json_schema()
            if not read:
                schema["properties"]["expected_revision"] = {"type": "integer", "minimum": 0}
                schema.setdefault("required", []).append("expected_revision")
            # Each operation owns a capability kind; read alias below projects skill members.
            operation_kind = "matcreator.kdg." + operation
            actions[operation] = NodeDocumentAction(handler, capability_kind=operation_kind, read_only=read, project=read)
            registration.register_capability(CapabilityDefinition(kind=operation_kind, tool_name="knowledge_" + operation,
                target_parameter="knowledge", description={"search": "Find a bounded working subgraph, then inspect or expand selected IDs.",
                "expand": "Expand one-hop neighborhoods of selected IDs with filters and pagination.",
                "inspect": "Read one entry, its provenance and addressable Skill resources. Scripts require separate Sandbox authorization.",
                "statistics": "Inspect graph counts.", "edit": "Create or update local graph knowledge with an expected revision; imported edits become user-owned and preserve their source snapshot.",
                "save_memory": "Save execution experience for later explicit review.", "distill": "Review pending memories into a durable Heuristic or Procedure with evidence.",
                "connect": "Explicitly create a validated semantic relationship.",
                "delete_entry": "Delete a local graph entry and its relationships; keep source snapshots and Skill resources.",
                "delete_relationship": "Delete one selected semantic relationship explicitly."}[operation], input_schema=schema), invoke)
        async def read(context, capability, arguments):
            result = await context.node_document_action(capability, "read", {})
            return {"revision": result["revision"], "summary": result["summary"]}
        registration.register_capability(CapabilityDefinition(kind=READ, tool_name="knowledge_resources", target_parameter="knowledge",
            description="Authorize reading this graph's Skill resources; use knowledge_inspect for selected resource IDs.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False}), read)
        registration.register_node_type(NodeTypeDefinition(id="matcreator.kdg", label="Know-Do Graph", description="Evolving scientific knowledge, resources and reviewed experience.",
            icon="network", color="#879a9d", deck_id="tools", deck_label="Tools", deck_icon="boxes", default_name="Know-Do Graph",
            default_size=(1000, 650), default_status="available", statuses=frozenset({"available"}), config_model=ToolboxConfig,
            traits=frozenset({"knowledge.graph"}), surfaces={"preview": True, "inspector": True, "workspace": True}, templateable=True,
            frontend={"workspace": "workspace"},
            container=SkillContainerDefinition(member_type="matcreator.kdg.skill", max_members=1000, member_display="workspace"),
            document=NodeDocumentDefinition(model=k.Graph, initial_value=k.Graph().model_dump(), actions=actions,
                summarize=k.summary, validate_update=k.validate_update, capture=lambda value: {**value, "skills": []}, max_size_bytes=32 * 1024 * 1024,
                transformations={"assimilate": NodeDocumentTransformation("Assimilate Toolset", frozenset({"oaw.skill-package"}), k.assimilate)},
                downloads={"snapshots": lambda value: NodeDocumentDownload("knowledge-packages.json", json.dumps(value["snapshots"], ensure_ascii=False, indent=2).encode(), "application/json")})))
        registration.register_relationship(RelationshipDefinition(id="matcreator.kdg.use", label="Use knowledge", short_label="knowledge",
            templateable=True,
            description="Retrieve knowledge and read its resources. Execution requires a separate Sandbox grant.",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"matcreator.kdg"}),
            capabilities=tuple(CapabilityGrantDefinition(kind=kind) for kind in [READ, *["matcreator.kdg." + key for key in reads]])))
        registration.register_relationship(RelationshipDefinition(id="matcreator.kdg.learn", label="Use and learn", short_label="learn",
            templateable=True,
            description="Retrieve knowledge and record execution memories; durable review stays explicit.",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"matcreator.kdg"}),
            capabilities=tuple(CapabilityGrantDefinition(kind=kind) for kind in [READ, *["matcreator.kdg." + key for key in reads], "matcreator.kdg.save_memory"])))
        registration.register_relationship(RelationshipDefinition(id="matcreator.kdg.curate", label="Curate knowledge", short_label="curate",
            templateable=True,
            description="Explicitly authorize editing local graph knowledge and reviewing memories; source snapshots stay immutable.",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"matcreator.kdg"}),
            capabilities=tuple(CapabilityGrantDefinition(kind=kind) for kind in [READ, *["matcreator.kdg." + key for key in [*reads, *writes]]])))
        registration.register_pack(PackDefinition(id='matcreator.default', name='MatCreator',
            description='Scientific skills and a Know-Do Graph.', cards=tuple(registration.nodes)))

def create_plugin():
    return MatCreatorPlugin()
