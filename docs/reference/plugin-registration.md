# Plugin registration and card types

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

## Public Plugin API

Plugin code imports host contracts only from `open_agent_world.plugin_api`:

```python
from open_agent_world.plugin_api import (
    NodeTypeDefinition,
    PluginDescriptor,
    PluginRegistration,
    RelationshipDefinition,
)
```

This package exposes the deliberate plugin boundary: descriptors, registration,
node and relationship definitions, lifecycle contracts and models, capabilities
and their narrow context, provider-neutral Agent runtime contracts, state-schema
contracts, and errors safe for plugin use. Do not import `backend.*`, FastAPI
objects, database connections, service containers, or provider SDK objects.

## Identity, compatibility, and ownership

Every plugin provides a descriptor:

```python
class DatasetPlugin:
    descriptor = PluginDescriptor(
        id="acme.dataset",
        version="1.2.0",
        plugin_api_version="1.0",
        name="Acme Dataset",
        description="Queryable datasets for Agents.",
    )

    def register(self, registration: PluginRegistration) -> None:
        ...


def create_plugin() -> DatasetPlugin:
    return DatasetPlugin()
```

Plugin IDs and contribution IDs are stable lowercase identifiers using letters,
digits, and `._:/-` separators. Changing an ID changes durable identity and
requires an explicit data migration.

`plugin_api_version` is the plugin's literal minimum host contract, not the
installed host's current version. Compatibility follows semantic `major.minor`
rules: the major version must match and the host minor must be at least the
requested minor. Host 1.3 accepts plugins requiring 1.0 through 1.3, while
rejecting 1.4 and 2.0. Pin `"1.1"` when using Legion template contracts or
delete-transaction dependencies/recovery payloads; otherwise a plugin using only
the original contracts can continue to declare `"1.0"`.
Use `"1.2"` for documents and `"1.3"` for work-source execution or document
reference remapping.
Use `"1.4"` for document seeds or document downloads. Use `"1.5"` for
`NodeContainerDefinition`. The current skill toolbox helper requires `"1.10"`.
Use `"1.10"` for `CapabilityDefinition`, `CapabilitySelector`, and operation tools
with target selectors. Use `"1.11"` for optional selectors (`required=False`),
shared read-only document capabilities, and execution configuration models.
Open containers retain
ordinary child nodes and their external relationships; see
[Skill Toolboxes](../skill-toolboxes.md#independent-cards-and-open-spaces).

Registration is staged. The registry validates the complete contribution set and
publishes it atomically only if registration succeeds. Every node type,
relationship, capability handler, runtime provider, and state schema records its
owning plugin. Node and edge rows also retain owner IDs. At startup the host rejects
persisted objects when their plugin is absent, no longer provides the recorded
contribution, or disagrees with the stored owner. The error names the object,
contribution, and required plugin.

`GET /api/catalog` includes plugin descriptors plus `plugin_id` on each node and
relationship definition. This metadata is suitable for diagnostics and future
installation management; executable callbacks remain backend-only.

## Package structure and discovery

Use a conventional `src` package:

```text
my-plugin/
  pyproject.toml
  README.md
  src/
    acme_oaw_plugin/
      __init__.py
  tests/
    test_plugin.py
```

Declare a zero-argument plugin factory as the entry point:

```toml
[project]
name = "acme-open-agent-world-plugin"
version = "1.2.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.11,<3"]

[project.entry-points."open_agent_world.plugins"]
acme-dataset = "acme_oaw_plugin:create_plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/acme_oaw_plugin"]
```

Normal backend startup scans immediate package directories under the repository's
`plugins/` folder. Advanced packages declare the entry-point group above in their
`pyproject.toml`; pure declarative cards need only Schema 3 contributions. Both
`src` and flat Python package layouts are supported. No
`-PluginPath` flag or editable installation is needed for these local packages.
Declare installation intent in `oaw.pack.toml` and prepare additional adapter
libraries with the backend interpreter running `scripts/install-plugins.py`.
The shared adapter layer is separate from isolated host workers and Sandbox
environments; [the distribution guide](../pack-distribution.md) covers all three.

The loader also discovers installed entry points. An installed entry with the
same name and factory as a local declaration is loaded only once. Local packages
are ordered by directory name, and entry points by name. The loader calls each
factory once per registry and installs the returned plugin. Import, construction, and registration must be
deterministic and free of network calls, threads, or external resource creation.
Optional import, compatibility, validation and registration failures make the
affected owner and dependent plugins **Unavailable**. Independent plugins and Core
can continue. Registration publishes atomically. Strict `pack check` and CI fail
on these errors. Core initialization and unsafe persistence/security failures
still block startup; the host does not silently replace their behavior.

For packages outside `plugins/`, discover a source project without changing
backend dependency files:

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./path/to/my-plugin
```

Prepare explicit dependencies through setup's `--plugin-path` first. For a strict
check of this project and a disposable card recovery test:

```powershell
./backend/.venv/Scripts/python.exe scripts/oaw-pack.py check ./path/to/my-plugin `
  --lifecycle --card acme.card
```

Start with [declarative Notes](../developers/declarative-packs.md). The
[Greeter plugin](../../examples/plugins/greeter/README.md) demonstrates advanced Python
lifecycle and legacy entry-point compatibility.

## Registration

`PluginRegistration` is scoped to one descriptor. It provides:

- `register_node_type`
- `register_relationship`
- `register_capability` (definition and handler)
- `register_capability_handler` (legacy inline relationship definitions)
- `register_runtime_provider`
- `register_state_schema`

The plugin instance owns any runtime adapters used by its callbacks:

```python
class DatasetPlugin:
    descriptor = PluginDescriptor(
        id="acme.dataset",
        version="1.2.0",
        plugin_api_version="1.10",
    )

    def __init__(self) -> None:
        self.runtime = DatasetRuntime()

    def register(self, registration: PluginRegistration) -> None:
        lifecycle = DatasetLifecycle(self.runtime)
        registration.register_capability(
            query_capability(), self.query
        )
        registration.register_node_type(dataset_node(lifecycle))
        registration.register_relationship(query_relationship())
```

Do not create a module-global runtime singleton. A factory may be called multiple
times in one process for tests, multiple application instances, or isolated worlds.

## Nodes and configuration

A node definition is both the persistence validation contract and the generic UI
description:

```python
from pydantic import BaseModel, ConfigDict, Field

from open_agent_world.plugin_api import NodeTypeDefinition


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str = "default"
    index_provider_id: str = Field(
        default="acme.index",
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$",
    )
    endpoint: str = "http://127.0.0.1:9000"
    timeout_seconds: float = Field(default=10, gt=0, le=120)


def dataset_node(lifecycle: DatasetLifecycle) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        id="acme.dataset",
        label="Dataset",
        description="A queryable external dataset.",
        icon="file-text",
        color="#6f7d73",
        deck_id="acme.data",
        deck_label="Acme data",
        deck_icon="boxes",
        default_name="New Dataset",
        default_size=(320, 210),
        default_status="available",
        statuses=frozenset({"available", "indexing", "error"}),
        config_model=DatasetConfig,
        traits=frozenset({"acme.queryable"}),
        lifecycle=lifecycle,
    )
```

The configuration model must construct with no arguments so the catalog can emit
`default_config`. Both create and update requests are validated by this model.
Prefer `extra="forbid"`. Traits are composable contracts used by relationships;
they avoid dependencies on another plugin's concrete node IDs.

`user_creatable` defaults to `True`. Set it to `False` for managed node types that
must exist in the registry for persistence, rendering, or template restoration but
must only be created through a dedicated domain operation. Managed types remain in
the catalog for existing-node rendering, but are omitted from card decks and are
rejected by the generic node-creation API. This flag is independent of
`templateable`: restoration support never implies standalone creation.

Plugin API 1.16 adds `NodeTypeDefinition.presentation`:

```python
from open_agent_world.plugin_api import NodePresentation

# Pass this to NodeTypeDefinition(presentation=...).
presentation = NodePresentation(
    states=("node", "preview", "workspace"),
    initial="workspace",
    open="workspace",
)
```

The four host surfaces are `node` (compact node), `preview` (small card),
`inspector` (details), and `workspace` (canvas window). `states` is any nonempty,
unique subset; its order does not change the host's size hierarchy. Both `initial`
and `open` must belong to it. `initial` applies on the first appearance of an
instance in the current application profile. Subsequent refreshes, reloads and
undo restoration preserve its saved surface. `open` is the destination when
clicking a compact node or small card. Closing skips unsupported surfaces and
restores the previously chosen compact form. A single-surface card has no
collapse action. Explicit settings/workspace requests fall back to `open` if
their requested surface is unsupported.

Conversation and Sandbox use the example above: new instances open as windows,
can collapse to small cards or nodes, and reopen directly as windows. Plugins
can keep all four surfaces while setting `open="workspace"`, or start directly
in `inspector`. Changing a declaration preserves supported saved states and
redirects removed states to `open`. Display state does not start a runtime.

Legacy `surfaces` declarations remain supported: `node` is implicit, `preview`
is the initial state when available, and opening prefers `inspector`, then
`workspace`, then `preview`. An explicit `presentation` takes precedence;
the catalog also derives the legacy `surfaces` flags from it for older clients.
The definition controls generic card surfaces; specialized container views still
own their member layout. Frontend content uses the existing `preview`, `body`
(inspector), and `workspace` slots.
