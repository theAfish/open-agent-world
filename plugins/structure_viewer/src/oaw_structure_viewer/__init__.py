from dataclasses import replace
from open_agent_world.plugin_api import PackDefinition, PluginDescriptor, StatelessStateSpec, DeploymentSurface, NodeDeploymentDefinition
from .tutorials import TUTORIALS


class StructureViewerPlugin:
    descriptor = PluginDescriptor(id="science.structure-viewer", version="0.2.0", plugin_api_version="1.38",
                                  name="Structure viewer", description="MatterViz crystal and molecule rendering")

    def register(self, registration):
        card_id = self.descriptor.id
        registration.nodes[card_id] = replace(registration.nodes[card_id],
            state=StatelessStateSpec(), tutorials=TUTORIALS,
            traits=frozenset({"core.file-viewer", "ui.content-surface.v1"}), templateable=True,
            deployment=NodeDeploymentDefinition(surface=DeploymentSurface(file_preview=True)),
        )
        registration.register_pack(PackDefinition(id=card_id + ".default", name="Structure viewer",
                                                 description="Interactive crystal and molecule visualization.", cards=tuple(registration.nodes)))


def create_plugin():
    return StructureViewerPlugin()
