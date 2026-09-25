"""A standalone Pack using only the public Python plugin API."""
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import NodeTypeDefinition, PackDefinition, PluginDefinition, PluginDescriptor


class GreeterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="World", min_length=1, max_length=80)
    greeting: str = Field(default="Hello, World!", max_length=120)


def register(registration):
    registration.register_node_type(NodeTypeDefinition(
        id="example.greeter.card", label="Greeter", description="A greeting from an independently installed Pack.",
        icon="sparkles", color="#748b65", deck_id="example.greeter", deck_label="Greeter", deck_icon="sparkles",
        default_name="Greeter", default_size=(320, 220), default_status="available", statuses=frozenset({"available"}),
        config_model=GreeterConfig, frontend={"body": "greeting", "settings": "greeting"}, templateable=True,
    ))
    registration.register_pack(PackDefinition(id="example.greeter", name="Greeter",
        description="Say hello from your own Pack.", cards=("example.greeter.card",)))


def create_plugin():
    return PluginDefinition(PluginDescriptor(id="example.greeter", version="0.1.0", plugin_api_version="1.23", name="Greeter"), register)
