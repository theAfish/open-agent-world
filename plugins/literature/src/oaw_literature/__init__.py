"""Auditable literature research resources and independently reusable skills."""
from open_agent_world.plugin_api import (PluginDescriptor, NodeTypeDefinition, NodeDocumentDefinition,
    NodeDocumentAction, CapabilityDefinition, CapabilityGrantDefinition, RelationshipDefinition, PackDefinition)

from .skills import register_literature_skills
from .scope import ScopeConfig, ScopeDocument, ExplorationConfig, remap_exploration_config, revise, summary, remap


class LiteraturePlugin:
    descriptor = PluginDescriptor(id="research.literature", version="0.1.0",
        plugin_api_version="1.14", name="Literature Research",
        description="Source-linked literature research and reusable skills", requires_plugins=("research.library", "matcreator"))

    def register(self, registration):
        register_literature_skills(registration)
        registration.register_node_type(NodeTypeDefinition(id="literature.scope", label="Research scope",
            description="A bounded literature question, search history and traceable evidence", icon="compass", color="#82928e",
            deck_id="tools", deck_label="Tools", deck_icon="boxes", default_name="Literature question", default_size=(420,340),
            default_status="available", statuses=frozenset({"available"}), config_model=ScopeConfig, templateable=True,
            traits=frozenset({"literature.scope"}), surfaces={"preview":True,"inspector":True,"workspace":True},
            frontend={"body":"scope","workspace":"scope"},
            document=NodeDocumentDefinition(model=ScopeDocument, max_size_bytes=16*1024*1024,
                identity_field="id",
                initial_value=ScopeDocument().model_dump(mode="json"), summarize=summary, remap_references=remap,
                actions={"revise":NodeDocumentAction(revise), "read":NodeDocumentAction(lambda value,args:value,read_only=True,
                         capability_kind="literature.read")})))
        async def invoke(context,capability,arguments):
            return await context.literature_action(capability,arguments)
        for suffix, label, icon in (("index", "Literature index", "library"),
                                    ("trail", "Exploration trail", "signpost"),
                                    ("finding", "Research finding", "compass")):
            registration.register_node_type(NodeTypeDefinition(id=f"literature.{suffix}", label=label,
                description="A scope-linked exploration view; semantic connections grant no Agent permissions.",
                icon=icon, color="#82928e", deck_id="tools", deck_label="Tools", deck_icon="boxes",
                default_name=label, default_size=(360, 300), default_status="available", statuses=frozenset({"available"}),
                config_model=ExplorationConfig, templateable=True, template_remap_config=remap_exploration_config,
                traits=frozenset({"literature.exploration"}), surfaces={"preview":True,"inspector":True,"workspace":True},
                frontend={"preview":suffix,"body":suffix,"workspace":suffix}))
        for kind,tool,operations in [("literature.read","literature_read",["scope","paper","contracts","snapshots"]),
                                     ("literature.research","literature_research",["search","resolve","pause","resume","record","snapshot","frontier","explore","evaluate_frontier"]),
                                     ("literature.organize","literature_organize",["organize"])]:
            organize_arguments = {"type":"object", "properties": {
                "expected_revision":{"type":"integer", "description":"Current document revision returned by literature_read scope."},
                "action":{"type":"string", "enum":["sync","add","link","core_collection","attach_camp","stage_task",
                    "migrate_roads","reorder","reparent","route_mode","move_member","layout"],
                    "description":"sync projects results; add/link/core_collection organize source-linked findings; attach_camp/stage_task prepare work. migrate_roads builds ordered roads (layout=true explicitly repositions existing entities); reorder uses the complete member_ids; reparent attaches an existing child road to parent_id; route_mode selects chain/branch; move_member relocates navigation membership only; layout explicitly repositions one road subtree or all roads. Navigation never changes source provenance."},
                "kind":{"type":"string", "enum":["web","perspective"]}, "title":{"type":"string"},
                "rationale":{"type":"string", "description":"Required for actions other than sync; explain selection, source interpretation or proposed work."},
                "frontier_id":{"type":"string", "description":"ID of an existing current route."},
                "paper_ids":{"type":"array", "items":{"type":"string"}, "description":"Existing scoped Papers only; required for core_collection."},
                "url":{"type":"string", "description":"HTTP(S) source URL; required for a web finding."},
                "source":{"type":"string", "description":"Existing exploration entity ID, not arbitrary world node ID."},
                "target":{"type":"string"}, "relation":{"type":"string", "enum":["supports","contrasts","related"]},
                "barracks_id":{"type":"string", "description":"Existing explicitly available Barracks; attachment grants no summon access."},
                "strategy":{"type":"string", "enum":["close_read","method","branch_search"]},
                "paper_id":{"type":"string"}, "method_id":{"type":"string"},
                "road_id":{"type":"string", "description":"Existing exploration_roads ID: trunk or route:<frontier_id>."},
                "parent_id":{"type":["string","null"], "description":"Existing parent road ID, or null to depart directly from this scope initial signpost; cyclic parenting is forbidden."},
                "attach_after":{"type":["string","null"], "description":"Parent-road entity to branch from; omit for its endpoint."},
                "mode":{"type":"string", "enum":["chain","branch"]},
                "member_ids":{"type":"array", "items":{"type":"string"}, "description":"Every member of the selected road exactly once, in requested order."},
                "entity_id":{"type":"string", "description":"Existing non-trail entity to move to a road; use reparent for a trail."},
                "index":{"type":"integer", "minimum":0}, "layout":{"type":"boolean"}},
                "required":["expected_revision","action"], "additionalProperties":False}
            registration.register_capability(CapabilityDefinition(kind=kind,tool_name=tool,target_parameter="scope",
                description="Coordinate this scope's external research map and basecamp index: source-backed findings, shared perspectives, core Paper collections and path camps. Topology grants no tools; stage_task needs a separate task-board manage grant and never starts a run." if kind.endswith("organize") else
                "Read only this scope's Papers and source-linked results." if kind.endswith("read") else
                "Search within this scope's desktop-approved hard budget. Results create deduplicated Paper metadata. record kind=micro_skill drafts a bounded research/search strategy from held metadata or a stored source abstract when full text is unavailable; read the MicroSkill contract first. Fulltext MethodSpec source requirements remain unchanged. No PDF upload, paid model call or scientific execution is implied.",
                input_schema={"type":"object","properties":{"operation":{"type":"string","enum":operations},
                    "arguments":organize_arguments if kind.endswith("organize") else {"type":"object"}},"required":["operation"],"additionalProperties":False}),invoke)
        for identifier,kinds in [("literature.read",["literature.read"]),("literature.research",["literature.read","literature.research"]),
                                  ("literature.coordinate",["literature.read","literature.research","literature.organize"])]:
            registration.register_relationship(RelationshipDefinition(id=identifier,label="Coordinate literature exploration" if identifier.endswith("coordinate") else "Research literature" if identifier.endswith("research") else "Read scoped literature",
                short_label="coordinate" if identifier.endswith("coordinate") else "literature",
                description="Coordinate this scope's index, external findings, shared perspectives, core collections and route camps, with budgeted search. No general canvas, task-board or summon authority is implied." if identifier.endswith("coordinate") else "Explicit access to the Papers listed in this research scope; research also permits budgeted public metadata queries.",
                source_traits=frozenset({"core.agent"}),target_types=frozenset({"literature.scope"}),templateable=True,
                capabilities=tuple(CapabilityGrantDefinition(kind=kind) for kind in kinds)))
        graph_types = frozenset({"literature.scope", "literature.index", "literature.trail", "literature.finding", "library.paper", "oaw.barracks", "text"})
        for relation in ("contains", "discovers", "method", "supports", "contrasts", "related", "camp", "road"):
            registration.register_relationship(RelationshipDefinition(id=f"literature.{relation}",
                label=f"Literature {relation}", short_label=relation,
                description="Research topology only. Does not grant tools, reading access, or scientific verification.",
                source_types=graph_types, target_types=graph_types, generated=True))
        registration.register_pack(PackDefinition(id="research.literature.default",name="Literature Research",
            description="Bounded research questions and independently reusable literature skills.",cards=tuple(registration.nodes)))
        from .preset import register_literature_preset
        register_literature_preset(registration)


def create_plugin():
    return LiteraturePlugin()
