"""Handwritten behavior: appearance is edited independently in project resources."""
from dataclasses import replace

from pydantic import BaseModel, ConfigDict, Field

from open_agent_world.plugin_api import (
    NodeResourceAction,
    PackDefinition,
    PluginDefinition,
    PluginDescriptor,
)


class Dimensions(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    a: float = Field(default=3, gt=0, le=1000)
    b: float = Field(default=4, gt=0, le=1000)
    c: float = Field(default=5, gt=0, le=1000)


def analyze_structure(context, arguments):
    """Calculate orthogonal unit-cell volume from validated lengths in angstroms."""
    dimensions = Dimensions.model_validate(arguments)
    volume = dimensions.a * dimensions.b * dimensions.c
    return {"result": f"Orthogonal cell volume: {volume:g} A^3", "volume": volume, "status": "complete"}


def register(registration):
    card_id = "example.analysis-card"
    registration.nodes[card_id] = replace(
        registration.nodes[card_id],
        resource_actions={"analyze_structure": NodeResourceAction(analyze_structure)},
        templateable=True,
    )
    registration.register_pack(PackDefinition(
        id=card_id + ".default", name="Cell Analysis", cards=(card_id,),
        description="Handwritten Python behavior with an independently editable card design.",
    ))


def create_plugin():
    return PluginDefinition(PluginDescriptor(
        id="example.analysis-card", version="0.1.0", plugin_api_version="1.39", name="Cell Analysis",
    ), register)
