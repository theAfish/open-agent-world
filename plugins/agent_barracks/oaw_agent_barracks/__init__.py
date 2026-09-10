"""Reusable configured Agents and an equipable summoning adapter."""
from open_agent_world.plugin_api import PackDefinition
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import (
    CapabilityGrantDefinition, NodeContainerDefinition,
    NodeDocumentAction, NodeDocumentDefinition, NodeSummoningDefinition,
    NodeTypeDefinition, PluginDescriptor, RelationshipDefinition,
    SummoningAction, SummoningPolicy,
)


class Barracks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Agent Barracks", min_length=1, max_length=120)
    instructions: str = "Choose an Agent suited to the task. Supply a specific task and use its instance handle for follow-up work."
    policy: SummoningPolicy = Field(default_factory=SummoningPolicy)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["available"] = "available"


class AgentBarracksPlugin:
    descriptor = PluginDescriptor(id="oaw.barracks", version="0.2.0", plugin_api_version="1.14",
        name="Agent Barracks", description="Reusable configured Agents with private equipment.")

    def register(self, registration):
        kind = "oaw.barracks.summon"
        async def invoke(context, capability, arguments):
            return await context.summoning_action(capability, arguments)
        registration.register_capability_handler(kind, invoke)
        common = dict(color="#78967b", default_status="available", statuses=frozenset({"available"}), config_model=Config,
            surfaces={"preview": True, "inspector": True, "workspace": True}, templateable=True)
        barracks_card = dict(icon="bot", deck_id="agents", deck_label="Agents", deck_icon="bot", **common)
        skill_card = dict(icon="sparkles", deck_id="tools", deck_label="Tools", deck_icon="boxes", deck_revision=2, **common)
        skill_card["surfaces"] = {"preview": True, "inspector": True, "workspace": False}
        registration.register_node_type(NodeTypeDefinition(id="oaw.barracks", label="Agent Barracks",
            description="Place configured Agents here to make them available for summoning.",
            default_name="Agent Barracks", default_size=(1100, 650), traits=frozenset({"ui.agent-barracks.v1"}),
            container=NodeContainerDefinition(member_traits=frozenset({"core.agent"})),
            summoning=NodeSummoningDefinition(kind),
            document=NodeDocumentDefinition(model=Barracks, initial_value={},
                actions={"configure": NodeDocumentAction(lambda value, args: {**value, **args})}), **barracks_card))
        registration.register_node_type(NodeTypeDefinition(id="oaw.barracks.summoner", label="Summoning",
            description="Equip on an Agent, then connect this skill to an Agent Barracks to grant summons.",
            default_name="Summoning", default_size=(360, 235), traits=frozenset({"oaw.summoner"}), **skill_card))
        registration.register_relationship(RelationshipDefinition(id="oaw.barracks.use", label="Use Summoning", short_label="use",
            description="Use this Agent's equipped Summoning skill.", source_traits=frozenset({"core.agent"}),
            target_traits=frozenset({"oaw.summoner"}), templateable=True))
        registration.register_relationship(RelationshipDefinition(id=kind, label="Summon agents", short_label="summon",
            description="Choose and instantiate a configured Agent from this Barracks.",
            source_traits=frozenset({"oaw.summoner"}), target_types=frozenset({"oaw.barracks"}), templateable=True,
            capabilities=(CapabilityGrantDefinition(kind=kind, tool_prefix="summon_agents",
                description="List Agents in {target_name!r}, then summon with agent_id and prompt. Each instance owns fresh equipment and keeps external connections shared. message continues an instance; inspect, stop and reclaim manage your instances. Recursive summons share root task limits.",
                input_schema=SummoningAction.model_json_schema()),)))
        registration.register_pack(PackDefinition(id='oaw.barracks.default', name='Agent Barracks',
            description='Reusable configured Agents and summoning.', cards=tuple(registration.nodes)))


def create_plugin():
    return AgentBarracksPlugin()
