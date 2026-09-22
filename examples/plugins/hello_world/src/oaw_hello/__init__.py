"""A passive card: the host provides persistence and configuration UI."""

from pydantic import BaseModel, ConfigDict, Field

from open_agent_world.plugin_api import (
    NodeTypeDefinition,
    PackDefinition,
    PluginDescriptor,
    PluginRegistration,
)


class HelloConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="Hello, world!", min_length=1, max_length=200)


class HelloPlugin:
    descriptor = PluginDescriptor(
        id="community.hello",
        version="0.1.0",
        plugin_api_version="1.14",
        name="Hello World",
        description="Your first OAW card.",
    )

    def register(self, registration: PluginRegistration) -> None:
        registration.register_node_type(NodeTypeDefinition(
            id="community.hello.message",
            label="Hello card",
            description="A card with an editable message.",
            icon="sparkles",
            color="#397c78",
            deck_id="community.hello.cards",
            deck_label="Hello World",
            deck_icon="sparkles",
            default_name="My first plugin",
            default_size=(300, 190),
            default_status="ready",
            statuses=frozenset({"ready"}),
            config_model=HelloConfig,
        ))
        registration.register_pack(PackDefinition(
            id="community.hello.starter",
            name="Hello World",
            description="The first-card tutorial pack.",
            cards=("community.hello.message",),
        ))


def create_plugin() -> HelloPlugin:
    return HelloPlugin()
