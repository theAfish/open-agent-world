"""First-party library presentation; capture and invocation are host contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import (
    CallableTemplate, CapabilityGrantDefinition, NodeContainerDefinition,
    NodeDocumentAction, NodeDocumentDefinition, NodeSummoningDefinition,
    NodeTypeDefinition, PluginDescriptor, RelationshipDefinition,
    SummoningAction, SummoningPolicy,
)


class Barracks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Agent Barracks", min_length=1, max_length=120)
    instructions: str = "Choose a template when its description fits the task. Supply a specific task and use the returned instance handle for follow-up work."
    policy: SummoningPolicy = Field(default_factory=SummoningPolicy)
    templates: list[CallableTemplate] = Field(default_factory=list)


class Config(BaseModel):
    status: Literal["available"] = "available"


def configure(value, arguments):
    return {**value, **{key: arguments[key] for key in ("name", "instructions", "policy") if key in arguments}}


def remap_template(value, ids):
    # Blueprint-local keys belong to the saved template, not the surrounding world.
    return {**value, "node_id": ids.get(value.get("node_id")), "bindings": [
        {**binding, "external_id": ids.get(binding["external_id"], binding["external_id"])}
        for binding in value["bindings"]]}


class AgentBarracksPlugin:
    descriptor = PluginDescriptor(id="oaw.barracks", version="0.1.0", plugin_api_version="1.6",
                                  name="Agent Barracks", description="Summon an Agent or equipped team from a callable template.")

    def register(self, registration):
        kind = "oaw.barracks.summon"
        async def invoke(context, capability, arguments):
            return await context.summoning_action(capability, arguments)
        registration.register_capability_handler(kind, invoke)
        common = dict(icon="bot", color="#78967b", deck_id="agents", deck_label="Agents", deck_icon="bot",
            default_status="available", statuses=frozenset({"available"}), config_model=Config,
            surfaces={"preview": True, "inspector": True, "workspace": True}, templateable=True)
        registration.register_node_type(NodeTypeDefinition(id="oaw.barracks.template", label="Agent template",
            description="A callable subgraph with one entry Agent.", default_name="Agent template", default_size=(360, 235),
            traits=frozenset({"oaw.agent-template", "ui.agent-template.v1"}), user_creatable=False,
            summoning=NodeSummoningDefinition(kind), document=NodeDocumentDefinition(
                model=CallableTemplate, initial_value={}, remap_references=remap_template,
                summarize=lambda value: {"description": value["description"]}, max_size_bytes=64 * 1024 * 1024), **common))
        registration.register_node_type(NodeTypeDefinition(id="oaw.barracks", label="Agent Barracks",
            description="Save equipped Agents or Legions and let connected Agents summon independent instances.",
            default_name="Agent Barracks", default_size=(1100, 650), traits=frozenset({"ui.agent-barracks.v1"}),
            container=NodeContainerDefinition(member_traits=frozenset({"oaw.agent-template"}),
                document_field="templates", member_type="oaw.barracks.template"),
            summoning=NodeSummoningDefinition(kind, templates_field="templates"),
            document=NodeDocumentDefinition(model=Barracks, initial_value={},
                capture=lambda value: {**value, "templates": []},
                actions={"configure": NodeDocumentAction(configure)},
                summarize=lambda value: {"total": len(value["templates"])}, max_size_bytes=64 * 1024 * 1024), **common))
        registration.register_relationship(RelationshipDefinition(id="oaw.barracks.summon", label="Summon agents", short_label="summon",
            description="Instantiate callable templates from this library or one template card.",
            source_traits=frozenset({"core.agent"}), target_types=frozenset({"oaw.barracks", "oaw.barracks.template"}), templateable=True,
            capabilities=(CapabilityGrantDefinition(kind=kind, tool_prefix="summon_agents",
                description="Call templates from {target_name!r}. Start with list to read instructions, template IDs and when to use them. summon creates an independent equipped Agent/team, runs your prompt, and returns its result and instance_id. message reuses your instance. inspect, stop and reclaim manage your instances; reclaim removes their nodes and workspaces. Recursive summoning requires an explicit library connection in the saved template and shares root task limits.",
                input_schema=SummoningAction.model_json_schema()),)))


def create_plugin():
    return AgentBarracksPlugin()
