"""Trusted, instance-scoped OAW runtime integration; import has no side effects."""

from open_agent_world.plugin_api import PackDefinition
from importlib.resources import files

from open_agent_world.plugin_api import AgentNodeBehavior, NodeTypeDefinition, PluginAsset, PluginDescriptor, PluginRegistration

from .runtime import CodexRuntime
from .card import CodexCardConfig, CodexCardTemplate


class CodexPlugin:
    descriptor = PluginDescriptor(
        id="openai.codex", version="0.3.0", plugin_api_version="1.14",
        name="Codex", description="Run Codex in a local project with OAW graph tools.",
    )

    def register(self, registration: PluginRegistration) -> None:
        registration.register_asset(PluginAsset(
            id="logo", content=files(__package__).joinpath("assets/openai.svg").read_bytes(),
            media_type="image/svg+xml",
        ))
        registration.register_runtime_provider("openai.codex", CodexRuntime)
        registration.register_node_type(NodeTypeDefinition(
            id="openai.codex.agent", label="Codex Agent", description="Local Codex with its own OAW session and connected tools.",
            icon="bot", icon_asset="logo", frontend={"settings": "settings"},
            color="#42877b", deck_id="agents", deck_label="Agents", deck_icon="bot",
            default_name="Codex", default_size=(300, 190), default_status="idle",
            statuses=frozenset({"idle", "running", "waiting", "error"}),
            config_model=CodexCardConfig, traits=frozenset({"core.agent", "ui.schema-agent.v1"}),
            surfaces={"preview": True, "inspector": True, "workspace": True},
            lifecycle=AgentNodeBehavior(), templateable=True, template_status="idle",
            template_handler=CodexCardTemplate(),
        ))
        registration.register_pack(PackDefinition(id='openai.codex.default', name='Codex Agent',
            description='A Codex worker with connected OAW tools.', cards=tuple(registration.nodes)))


def create_plugin() -> CodexPlugin:
    return CodexPlugin()
