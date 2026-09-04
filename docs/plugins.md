# Plugin development

Open Agent World loads trusted Python plugins through the
`open_agent_world.plugins` entry-point group. A plugin is an application-scoped
object with a stable descriptor and a synchronous registration method. Each
application registry gets a fresh plugin instance, so executable state belongs to
that application rather than to an imported module.

The backend is authoritative for plugin identity, storage validation, lifecycle
dispatch, relationship matching, capability authorization, runtime providers, and
state schemas. It publishes serializable plugin, node, and relationship metadata
through `GET /api/catalog`; the canvas renders plugin definitions generically.

Plugins execute inside the FastAPI process. Install only reviewed packages.

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

The loader sorts entry points by name, calls each factory once per registry, and
installs the returned plugin. Import, construction, and registration must be
deterministic and free of network calls, threads, or external resource creation.
An import, compatibility, validation, or identifier collision error prevents host
startup.

During development, mount an editable package without changing backend dependency
files:

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./path/to/my-plugin
```

Test the actual packaging and discovery path the same way:

```powershell
uv run --project backend --with-editable ./path/to/my-plugin `
  python -m pytest -p no:cacheprovider ./path/to/my-plugin/tests
```

The repository's [Greeter plugin](../examples/plugins/greeter/README.md) is the
canonical compact example.

## Public Plugin API

Plugin code imports host contracts only from `open_agent_world.plugin_api`:

```python
from open_agent_world.plugin_api import (
    PLUGIN_API_VERSION,
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
        plugin_api_version=PLUGIN_API_VERSION,
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

## Registration

`PluginRegistration` is scoped to one descriptor. It provides:

- `register_node_type`
- `register_relationship`
- `register_capability_handler`
- `register_runtime_provider`
- `register_state_schema`

The plugin instance owns any runtime adapters used by its callbacks:

```python
class DatasetPlugin:
    descriptor = PluginDescriptor(
        id="acme.dataset",
        version="1.2.0",
        plugin_api_version=PLUGIN_API_VERSION,
    )

    def __init__(self) -> None:
        self.runtime = DatasetRuntime()

    def register(self, registration: PluginRegistration) -> None:
        lifecycle = DatasetLifecycle(self.runtime)
        registration.register_capability_handler(
            "acme.dataset.query", self.query
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

`surfaces` declares generic `preview`, `inspector`, and `workspace` availability.
The backend does not load plugin-supplied browser code.

## Lifecycle transactions

A passive node omits `lifecycle`. A stateful node implements
`NodeLifecycleHandler`, which prepares a `NodeLifecycleTransaction` for create,
update, and delete. Preparation must validate and capture the before/after state
without visible side effects. The returned transaction owns the reversible side
effect:

```python
from open_agent_world.plugin_api import (
    Card,
    CardCreate,
    CardPatch,
    NodeLifecycleContext,
    NodeLifecycleHandler,
    NodeLifecycleTransaction,
)


class RuntimeMutation(NodeLifecycleTransaction):
    def __init__(self, runtime, node_id, before, after):
        self.runtime = runtime
        self.node_id = node_id
        self.before = before
        self.after = after

    async def commit(self) -> None:
        await self.runtime.set(self.node_id, self.after)

    async def rollback(self, error: BaseException) -> None:
        await self.runtime.set(self.node_id, self.before)


class DatasetLifecycle(NodeLifecycleHandler):
    def __init__(self, runtime):
        self.runtime = runtime

    async def prepare_create(self, context, node: Card, request: CardCreate):
        return RuntimeMutation(self.runtime, node.id, None, node.config)

    async def prepare_update(
        self,
        context: NodeLifecycleContext,
        current: Card,
        updated: Card,
        request: CardPatch,
    ):
        return RuntimeMutation(
            self.runtime, current.id, current.config, updated.config
        )

    async def prepare_delete(self, context, node: Card):
        return RuntimeMutation(self.runtime, node.id, node.config, None)
```

Host ordering and compensation are fixed:

- Create: prepare, persist the node identity, commit the plugin transaction, then
  publish the event. A lifecycle failure rolls back the plugin transaction and
  deletes the node. A persistence failure occurs before plugin commit.
- Update: prepare the validated before/after nodes, commit the plugin transaction,
  persist the update, then publish the event. A lifecycle or persistence failure
  invokes rollback and leaves the persisted node unchanged.
- Delete: prepare from the node and its still-present graph, commit the plugin
  transaction, delete the node and cascading edges, then publish events. A
  lifecycle or persistence failure invokes rollback and leaves the node present.

`commit` and `rollback` must be idempotent. `rollback` must tolerate a partially
completed commit and use captured state rather than assuming the world mutation
succeeded. The host completes rollback under cancellation and re-raises the
original error; compensation failures are attached as exception notes.

`on_startup` reconstructs runtime state from persisted nodes. `on_shutdown`
releases process-scoped state. Both may be called again after interrupted process
lifecycles and therefore must also be idempotent.

`NodeLifecycleContext` exposes narrow node, resource, conversation, Agent, and
Sandbox operations. It does not expose the database, HTTP requests, provider SDKs,
or the application service container.

## Relationships, traits, and capabilities

Relationships match exact types, required traits, or both:

```python
from open_agent_world.plugin_api import (
    CapabilityGrantDefinition,
    RelationshipDefinition,
)


def query_relationship() -> RelationshipDefinition:
    return RelationshipDefinition(
        id="acme.query",
        label="Query",
        short_label="query",
        description="The Agent can query this dataset.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"acme.queryable"}),
        capabilities=(CapabilityGrantDefinition(
            kind="acme.dataset.query",
            tool_prefix="query_dataset",
            description="Query dataset {target_name!r}.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),),
    )
```

Register capability handlers before relationships that reference them. A handler
receives `CapabilityContext`, the freshly authorized `Capability`, and untrusted
arguments. `CapabilityContext` is a narrow set of provider-neutral host operations;
plugins normally use the capability scope plus their instance-owned runtime.

The input schema describes an Agent tool but does not replace handler validation.
The host derives capabilities from the current graph immediately before every
invocation. A capability ID is a locator, never an authorization token. Deleting or
changing its granting edge revokes permission immediately.

Connection gestures are unordered. The host stores the endpoints in the canonical
orientation defined by the relationship. A `bidirectional` edge grants the same
relationship capabilities in reverse; use separate relationships for different
meanings.

## Runtime providers

Agent-engine integrations implement the public `RuntimeProvider` contract and
register a factory:

```python
def register_provider(registration: PluginRegistration) -> None:
    registration.register_runtime_provider(
        "acme.runtime",
        lambda capability_provider, **options: AcmeRuntimeProvider(
            capability_provider=capability_provider,
            **options,
        ),
    )
```

The provider receives `AgentCapabilityProvider`, which lists and invokes currently
authorized graph tools. Providers emit provider-neutral `AgentEvent` values and
must not query world storage directly. Credentials and SDK sessions remain inside
the provider instance and are never persisted in node configuration or events. See
[Runs and runtime providers](runs.md) for execution semantics.

## State schemas

Register durable, frequently changing runtime state as a schema rather than node
configuration:

```python
from open_agent_world.plugin_api import (
    MergePolicy,
    StateFieldDefinition,
    StateSchema,
)


registration.register_state_schema(StateSchema(
    id="acme.research",
    fields={
        "findings": StateFieldDefinition(
            value_type=list[str],
            allowed_scope_kinds=frozenset({"acme.research"}),
            merge_policy=MergePolicy.APPEND_UNIQUE,
            default=[],
        ),
    },
))
```

Schema IDs are namespaced. Fields define value type, scope, visibility, write
permission, merge policy, and default. Registration declares the contract; the
plugin runtime explicitly creates and manages scopes. See [Runtime state](state.md).

## Installation and removal constraints

For a checkout-local permanent installation:

```powershell
uv add --project backend --editable ./path/to/my-plugin
```

Restart after installing or removing plugins. Before removal, export or migrate
plugin-owned data and delete every node owned by that plugin. Node deletion removes
its edges and immediately revokes derived capabilities. If persisted nodes or edges
remain, startup fails with an ownership-aware unavailable-plugin diagnostic. The
host never loads an unknown object under a generic fallback behavior.

## Trust and security boundary

- Plugins are trusted in-process Python code with the backend user's permissions.
- Registration is declarative; external I/O belongs in lifecycle transactions or
  capability handlers.
- Persist only non-secret configuration. Read credentials from the environment or
  a plugin-owned secret manager.
- Validate all handler arguments, apply timeouts and response-size limits, and do
  not place secrets in errors or runtime events.
- Use `CapabilityContext` and `NodeLifecycleContext`; never retain private host
  objects or raw database connections.
- Make lifecycle commit, rollback, startup, and shutdown idempotent and safe under
  cancellation.
- Never cache capability authorization. Re-derive it from the live graph for every
  privileged operation.
- Plugin packages cannot inject arbitrary frontend code. Custom browser rendering
  requires a separately reviewed core frontend contribution.

The Greeter integration test verifies editable entry-point discovery, descriptor
ownership, independent runtime state across registries, lifecycle updates,
canonical graph orientation, Agent tool invocation, validation, and immediate
permission revocation.
