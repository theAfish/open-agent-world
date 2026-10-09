"""A standalone Pack using only the public Python plugin API."""
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import NodeTypeDefinition, PackDefinition, PluginDefinition, PluginDescriptor
from open_agent_world.plugin_api import TutorialDefinition, TutorialStep


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
        tutorials=(TutorialDefinition(
            id="first-greeting", title={"en": "Your first greeting", "zh-CN": "第一次问候"},
            summary={"en": "Personalize this card in two small steps.", "zh-CN": "用两个步骤定制这张卡片。"},
            steps=(
                TutorialStep(id="name", title="Choose a name", body="Open the Greeter card and enter a name."),
                TutorialStep(id="greet", title="Say hello", body="Click **Greet** to save and display your greeting."),
            ),
            document="## Greeter\n\nThe **Name** field is saved when you click **Greet**. You can change it at any time.",
        ),),
    ))
    registration.register_pack(PackDefinition(id="example.greeter", name="Greeter",
        description="Say hello from your own Pack.", cards=("example.greeter.card",),
        tutorials=(TutorialDefinition(id="reference", title="About the Greeter Pack", summary="A standalone example Pack.",
            trigger="manual", document="This Pack adds a **Greeter** card. Add it to a deck, then place it on your canvas."),)))


def create_plugin():
    return PluginDefinition(PluginDescriptor(id="example.greeter", version="0.1.0", plugin_api_version="1.26", name="Greeter"), register)
