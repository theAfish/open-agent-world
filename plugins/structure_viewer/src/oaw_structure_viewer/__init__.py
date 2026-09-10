from pydantic import BaseModel, ConfigDict
from open_agent_world.plugin_api import NodeTypeDefinition, PackDefinition, PluginDescriptor


class ViewerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructureViewerPlugin:
    descriptor = PluginDescriptor(id="science.structure-viewer", version="0.1.0", plugin_api_version="1.14",
                                  name="Structure viewer", description="MatterViz crystal and molecule rendering")

    def register(self, registration):
        registration.register_node_type(NodeTypeDefinition(
            id="science.structure-viewer", label="Structure viewer", description="Follow crystal and molecule files opened in connected windows.",
            icon="atom", color="#67a69b", deck_id="science", deck_label="Science", deck_icon="atom",
            default_name="Structure viewer", default_size=(340, 240), default_status="ready", statuses=frozenset({"ready"}),
            config_model=ViewerConfig, traits=frozenset({"core.file-viewer"}), templateable=True,
            frontend={"preview": "preview", "body": "viewer", "workspace": "viewer"},
            surfaces={"preview": True, "inspector": True, "workspace": True},
        ))
        registration.register_pack(PackDefinition(id="science.structure-viewer.default", name="Structure viewer",
                                                 description="Interactive crystal and molecule visualization.", cards=tuple(registration.nodes)))


def create_plugin():
    return StructureViewerPlugin()
