from open_agent_world.plugin_api import PluginDescriptor
from open_agent_world.skill_packages import SkillPackage, register_skill_package


class ToolboxesPlugin:
    descriptor = PluginDescriptor(id="oaw.skills", version="0.2.0", plugin_api_version="1.5",
        name="Skill Toolboxes", description="Build and share portable collections of skills.")

    def register(self, registration):
        register_skill_package(registration, node_type="oaw.skills", package=SkillPackage(), published=False)


def create_plugin():
    return ToolboxesPlugin()
