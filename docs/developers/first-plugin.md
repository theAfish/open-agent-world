# Your first plugin

**Goal:** create a Hello card with a saved `message` field. You will make three files; the host supplies the card interface and persistence. Finish [development setup](setup.md) first and stop the development server before adding a package.

The complete [Hello World example](../../examples/plugins/hello_world/README.md) is included in the repository. You can copy it or create the files below yourself. Choose one approach so you only load one copy.

## 1. Create the package

From the repository root, create this structure:

```text
plugins/hello_world/
  pyproject.toml
  oaw.pack.toml
  src/
    oaw_hello/
      __init__.py
```

Put this in `pyproject.toml`:

```toml
[project]
name = "open-agent-world-plugin-hello"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.11,<3"]

[project.entry-points."open_agent_world.plugins"]
community-hello = "oaw_hello:create_plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/oaw_hello"]
```

The entry point tells OAW which factory to call. The loader discovers packages directly inside `plugins/`; setup installs their editable metadata and prepares declared dependencies before dev starts. Add `oaw.pack.toml` to declare the same identities for source development and publication:

```toml
schema_version = 3
[distribution]
id = "community.hello"
name = "Hello World"
[compatibility]
plugin_api = "1.35"
oaw = ">=0.1.0,<1"
[plugin]
owner = "community.hello"
packs = ["community.hello.starter"]
node_types = ["community.hello.message"]
```

This example uses only the host's Pydantic dependency. Additional adapter libraries
are prepared through `scripts/install-plugins.py --profile plugin-tutorial plugins/hello_world` using the
backend interpreter. Scientific workers and Sandbox libraries belong in declared
environments; see [Pack installation and distribution](../pack-distribution.md).
Startup discovery reads declarations without downloading or installing anything.

## 2. Define the card and pack

Put this in `src/oaw_hello/__init__.py`:

```python
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import (
    NodeTypeDefinition, PackDefinition, PluginDescriptor, PluginRegistration,
)


class HelloConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="Hello, world!", min_length=1, max_length=200)


class HelloPlugin:
    descriptor = PluginDescriptor(
        id="community.hello",
        version="0.1.0",
        plugin_api_version="1.35",
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
```

The descriptor identifies the plugin. `version` versions your package; `plugin_api_version` is the API level it requires and matches the publication declaration.

`HelloConfig` validates the editable message. `register_node_type` contributes the card and `register_pack` makes it collectible. The `deck_*` fields provide catalog category metadata; users still choose their own decks. The host persists the configuration without a lifecycle handler.

Keep registration deterministic: create no files, network connections, or background workers here.

## 3. Run it

Windows:

```powershell
./scripts/setup.ps1 -Profile plugin-tutorial -SkipBuild
./scripts/dev.ps1 -AgentRuntime mock -Profile plugin-tutorial
```

Linux/macOS:

```sh
bash scripts/setup.sh --profile plugin-tutorial --skip-build
bash scripts/dev.sh --agent-runtime core.mock --profile plugin-tutorial
```

In OAW:

1. Open **Pack & Card Library** and open **Hello World**.
2. Add **Hello card** to your active deck.
3. Place it from the bottom tray and open its configuration controls.
4. Change `message` to **My first plugin works** and save.
5. Restart with the same profile and check that your message remains.

If startup fails, read the loader's error for the package name, identifier, or import that failed. If the pack appears but the tray is empty, collect the card and add it to the active deck. There is no Agent tool in this example yet.

## 4. Make it yours

Change the label, description, default message, or add another scalar configuration field. Restart after Python changes. When creating a separate plugin, choose your own stable namespace and rename its distribution, Python package, entry point, descriptor, node and pack IDs together. Do this before users persist instances of it.

Delete your Hello cards before uninstalling the Pack in the Library. The uninstall
preview checks active uses and shared dependencies; source files and saved Deck
references remain available for restoration.

## Next steps

Run the example's [discovery and persistence test](testing.md), then [give an Agent a tool](agent-tools.md) or [add a custom interface](frontend.md).
