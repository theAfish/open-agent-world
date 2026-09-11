"""Native spatial collection: membership is identity-preserving, not execution authority."""
from typing import Literal
from pydantic import BaseModel
from backend.plugins.containers import NodeContainerDefinition
from backend.plugins.registry import NodeTypeDefinition, RelationshipDefinition, CapabilityDefinition, CapabilityGrantDefinition


class ShadowCollectionConfig(BaseModel):
    display_state: Literal["minimal", "stacked", "expanded"] = "minimal"


async def inspect_collection(context, capability, arguments):
    return await context.collection_members(capability)


def register_shadow_collection(registry):
    registry.register_node_type(NodeTypeDefinition(
        id="core.shadow-collection", label="Shadow collection", description="Identity-preserving collection of world nodes",
        icon="boxes", color="#77766f", deck_id="fields", deck_label="Fields", deck_icon="workflow",
        default_name="New Collection", default_size=(132, 128), default_status="available",
        statuses=frozenset({"available"}), config_model=ShadowCollectionConfig,
        traits=frozenset({"core.field", "ui.shadow-collection.v1"}),
        container=NodeContainerDefinition(min_size=(132, 128), content_inset=(0, 0, 0, 0)),
        templateable=True,
    ))
    registry.register_relationship(RelationshipDefinition(
        id="core.collection.reference", label="Reference collection", short_label="collection",
        description="Reference the collection itself; no member execution or content permissions are granted.",
        target_types=frozenset({"core.shadow-collection"}), templateable=True,
    ))
    registry.register_capability(CapabilityDefinition(
        kind="core.collection.inspect", tool_name="list_collection_members", target_parameter="collection",
        description="List direct member identities and types. Member contents and actions require their own connections.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False}), inspect_collection)
    registry.register_relationship(RelationshipDefinition(
        id="core.collection.inspect", label="Inspect collection", short_label="list members",
        description="Read member identities without invoking them or granting access to their contents.",
        source_traits=frozenset({"core.agent"}), target_types=frozenset({"core.shadow-collection"}),
        capabilities=(CapabilityGrantDefinition(kind="core.collection.inspect"),), templateable=True,
    ))
