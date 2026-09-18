"""AtomSculptor's OAW plugin entry point.

This first contribution establishes the durable Structure card.  Agent-team,
SkillPackage, file exchange and the complete editor are layered on this public
document contract rather than on the retired AtomSculptor Sandbox APIs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from open_agent_world.plugin_api import (
    CapabilityDefinition,
    CapabilityGrantDefinition,
    AgentNodeBehavior,
    NodeDocumentAction,
    NodeDocumentDefinition,
    NodeTypeDefinition,
    PackDefinition,
    PluginDescriptor,
    RelationshipDefinition,
)
from open_agent_world.skill_packages import register_skill_package

from . import structure
from .skill_package import atomsculptor_skills
from .runtime import AtomSculptorRuntime


READ = "atomsculptor.structure.read"
WRITE = "atomsculptor.structure.write"
WRITE_INPUT_SCHEMA = structure.ReplaceStructure.model_json_schema()
WRITE_INPUT_SCHEMA["properties"]["expected_revision"] = {
    "type": "integer",
    "minimum": 0,
    "description": "Revision returned by inspect_atom_structure. Re-inspect after a conflict.",
}
WRITE_INPUT_SCHEMA["required"].append("expected_revision")


class StructureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Explicit UI links only.  They neither grant capabilities nor carry
    # credentials; OAW relationships remain the authorization source.
    agent_id: str | None = None
    sandbox_id: str | None = None
    skill_id: str | None = None


class AtomSculptorAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # OAW persists status in legacy-compatible card configuration as well as
    # host metadata.  Accept every lifecycle state because AgentNodeBehavior
    # transitions this value while a Run is active.
    status: Literal["idle", "running", "waiting", "error"] = "idle"
    runtime_provider_id: Literal["atomsculptor.adk-team"] = "atomsculptor.adk-team"
    model: str = "oaw:default"
    max_concurrent_runs: Literal[1] = 1
    inherit_legion_model: Literal[False] = False
    system_instruction: str = "Coordinate atomistic modelling through the connected OAW resources."


async def _read(context, capability, arguments):
    return await context.node_document_action(capability, "inspect", arguments)


async def _write(context, capability, arguments):
    payload = dict(arguments)
    expected_revision = payload.pop("expected_revision", None)
    return await context.node_document_action(
        capability, "replace_structure", payload, expected_revision=expected_revision
    )


class AtomSculptorPlugin:
    descriptor = PluginDescriptor(
        id="atomsculptor",
        version="0.1.0",
        plugin_api_version="1.16",
        name="AtomSculptor",
        description="Agent-assisted atomistic structure modelling.",
    )

    def register(self, registration) -> None:
        registration.register_runtime_provider(
            "atomsculptor.adk-team",
            AtomSculptorRuntime,
            needs_model_connection_resolver=True,
        )
        register_skill_package(
            registration,
            node_type="atomsculptor.skills",
            package=atomsculptor_skills(),
        )
        registration.register_capability(
            CapabilityDefinition(
                kind=READ,
                tool_name="inspect_atom_structure",
                description="Inspect the current atoms, cell, layers and selected stable atom IDs.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            _read,
        )
        registration.register_capability(
            CapabilityDefinition(
                kind=WRITE,
                tool_name="replace_atom_structure",
                description="Replace an AtomSculptor structure using stable atom IDs and a complete validated document. Inspect first and pass its revision as expected_revision.",
                input_schema=WRITE_INPUT_SCHEMA,
            ),
            _write,
        )
        registration.register_node_type(
            NodeTypeDefinition(
                id="atomsculptor.agent",
                label="AtomSculptor Agent",
                description="A Planner, Structure Builder and Materials Project team using OAW resources.",
                icon="bot",
                color="#b07f53",
                deck_id="agents",
                deck_label="Agents",
                deck_icon="bot",
                default_name="AtomSculptor",
                default_size=(320, 210),
                default_status="idle",
                statuses=frozenset({"idle", "running", "waiting", "error"}),
                config_model=AtomSculptorAgentConfig,
                traits=frozenset({"core.agent", "ui.schema-agent.v1"}),
                surfaces={"preview": True, "inspector": True, "workspace": True},
                lifecycle=AgentNodeBehavior(),
                templateable=True,
                template_status="idle",
            )
        )
        registration.register_node_type(
            NodeTypeDefinition(
                id="atomsculptor.structure",
                label="Atom Structure",
                description="A versioned atomistic structure with stable atom identities and layers.",
                icon="atom",
                color="#4d8c9c",
                deck_id="science",
                deck_label="Science",
                deck_icon="atom",
                default_name="Untitled Structure",
                default_size=(520, 420),
                default_status="ready",
                statuses=frozenset({"ready"}),
                config_model=StructureConfig,
                # ``core.file-viewer`` opts into the host's built-in
                # "Follow opened files" relationship, so a connected Sandbox's
                # native file tree can feed this document without new routes.
                traits=frozenset({"atomsculptor.structure", "core.file-viewer"}),
                surfaces={"preview": True, "inspector": True, "workspace": True},
                frontend={"preview": "preview", "body": "workspace", "workspace": "workspace"},
                templateable=True,
                document=NodeDocumentDefinition(
                    model=structure.StructureDocument,
                    initial_value=structure.StructureDocument().model_dump(mode="json"),
                    summarize=structure.summary,
                    actions={
                        "inspect": NodeDocumentAction(lambda value, arguments: value, capability_kind=READ, read_only=True),
                        "replace_structure": NodeDocumentAction(structure.replace, capability_kind=WRITE),
                        "select_atoms": NodeDocumentAction(structure.select),
                        "select_layers": NodeDocumentAction(structure.select_layers),
                    },
                    max_size_bytes=16 * 1024 * 1024,
                ),
            )
        )
        registration.register_relationship(
            RelationshipDefinition(
                id="atomsculptor.structure.inspect",
                label="Inspect structure",
                short_label="inspect",
                description="Allow an Agent to inspect the current structure and stable selected atom IDs.",
                source_traits=frozenset({"core.agent"}),
                target_types=frozenset({"atomsculptor.structure"}),
                capabilities=(CapabilityGrantDefinition(READ),),
                templateable=True,
            )
        )
        registration.register_relationship(
            RelationshipDefinition(
                id="atomsculptor.structure.modify",
                label="Modify structure",
                short_label="modify",
                description="Allow an Agent to atomically replace a validated structure revision.",
                source_traits=frozenset({"core.agent"}),
                target_types=frozenset({"atomsculptor.structure"}),
                capabilities=(CapabilityGrantDefinition(READ), CapabilityGrantDefinition(WRITE)),
                templateable=True,
            )
        )
        registration.register_pack(
            PackDefinition(
                id="atomsculptor.default",
                name="AtomSculptor",
                description="Atomistic structure modelling cards and agents.",
                cards=tuple(registration.nodes),
            )
        )


def create_plugin() -> AtomSculptorPlugin:
    return AtomSculptorPlugin()
