"""Visualization consumes the public dataset protocol, never provider internals."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import (
    NodeDataConsumer, NodePresentation, NodeTypeDefinition, PackDefinition, PluginDescriptor,
    RelationshipDefinition, StatelessStateSpec,
)
from .icons import assets
from .tutorials import CARD_TUTORIALS, PACK_TUTORIALS

PREFIX = "data.visualization"


class ChartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = ""
    schema_id: str = ""
    x: str = ""
    y: str = ""
    series: str = ""
    aggregate: Literal["none", "count", "sum", "mean", "min", "max"] = "none"
    limit: int = Field(default=2000, ge=1, le=10000)
    entity_type: str = ""
    relation_type: str = ""


class VisualizationPlugin:
    descriptor = PluginDescriptor(id=PREFIX, version="0.1.0", plugin_api_version="1.27",
                                  name="Data visualization")

    def register(self, registration):
        for asset in assets():
            registration.register_asset(asset)
        for kind, label in (("graph", "Graph"), ("line", "Line chart"),
                            ("bar", "Bar chart"), ("scatter", "Scatter plot"), ("histogram", "Distribution")):
            registration.register_node_type(NodeTypeDefinition(
                id=f"{PREFIX}.{kind}", label=label, description="Visualize a connected dataset",
                icon="workflow", icon_asset=f"chart-{kind}", color="#518bba", deck_id="visualization", deck_label="Visualization", deck_icon="workflow",
                default_name=label, default_size=(720, 480), default_status="available",
                statuses=frozenset({"available"}), config_model=ChartConfig,
                traits=frozenset({"data.visualization"}), state=StatelessStateSpec(), tutorials=CARD_TUTORIALS,
                data_consumer=NodeDataConsumer(source_field="source_id", schema_field="schema_id",
                    kinds=("table", "graph") if kind == "graph" else ("table",)),
                presentation=NodePresentation(states=("node", "preview", "inspector", "workspace"),
                    initial="inspector", open="inspector", sizes={"inspector": {"width": 680 if kind == "graph" else 720, "height": 600 if kind == "graph" else 480}}),
                frontend={"preview": "preview", "body": "chart", "workspace": "chart"},
                templateable=True,
                template_remap_config=lambda config, mapping: {**config, "source_id": mapping.get(config.get("source_id"), "")},
            ))
        registration.register_relationship(RelationshipDefinition(
            id=f"{PREFIX}.source", label="Visualization data", short_label="data",
            description="Read schemas and bounded datasets for this chart.",
            source_traits=frozenset({"data.visualization"}), target_traits=frozenset({"data.source"}),
            data_read=True, templateable=True,
        ))
        registration.register_pack(PackDefinition(id=f"{PREFIX}.default", name="Data visualization",
            description="Connected graph, line, bar, scatter and distribution charts.",
            cards=tuple(registration.nodes), accent_color="#518bba", tutorials=PACK_TUTORIALS))


def create_plugin():
    return VisualizationPlugin()
