# Plugin development

Open Agent World has a working **trusted backend plugin foundation**. A third-party
Python package can register node types, relationships, Agent tools, node lifecycle
handlers, Runtime Providers, and state schemas without modifying the core.

The backend remains authoritative for data and permissions. It publishes the
serializable part of the plugin registry through `GET /api/catalog`, and the canvas
uses that catalog to provide a generic UI for plugin-defined objects.

Today, "pluggable" has three precise meanings:

1. The host discovers standard Python entry points. During development, a plugin can
   be mounted temporarily with one command-line parameter.
2. Registered definitions automatically enter storage validation, the canvas
   catalog, connection matching, capability derivation, and runtime dispatch.
3. Adding a plugin does not require a plugin-specific `if` or `switch` in the core.

This is not yet an untrusted plugin sandbox, marketplace, or dynamic React module
system. Plugins execute inside the FastAPI process and must be reviewed before they
are installed. Adding or removing a plugin requires a restart. Before removing one,
delete or migrate every persisted node that depends on it.

## Run the complete example first

The repository includes an installable
[Greeter plugin](../examples/plugins/greeter/README.md). It exercises the most common
end-to-end path:

```text
Python entry point
  -> PluginRegistry
     -> catalog -> palette/card/edge
     -> lifecycle -> plugin-owned runtime
     -> relationship -> scoped Agent tool -> capability handler
```

Complete the normal repository setup, then run this from the repository root:

```powershell
./scripts/setup.ps1
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./examples/plugins/greeter
```

The UI will contain a **Community examples** deck and a **Greeter** node. Create an
Agent and a Greeter, then connect them with **Greet with**. The edge immediately
grants the Agent a tool named similarly to
`greet_with_friendly_greeter_<id>`. Deleting the edge immediately revokes it.

Run the example's integration test without changing `backend/pyproject.toml`:

```powershell
uv run --project backend --with-editable ./examples/plugins/greeter `
  python -m pytest -p no:cacheprovider examples/plugins/greeter/tests
```

`-PluginPath` accepts multiple paths during development:

```powershell
./scripts/dev.ps1 -PluginPath ./plugins/one,./plugins/two
```

To unmount a temporary plugin, stop the application and restart it without that
path. If the world contains nodes owned by the plugin, delete them through the UI or
API first. Otherwise, the backend cannot restore the lifecycle of those unknown
node types.

## Choose an extension shape

Most plugins combine a few shared extension points rather than requiring a new
framework for every category.

| Goal | Register | Automatically enters the main flow |
| --- | --- | --- |
| Add a canvas object or data source | `NodeTypeDefinition` | Yes: creation, validation, storage, catalog, generic UI |
| Add connection semantics | `RelationshipDefinition` | Yes: frontend/backend matching, canonical direction, edge storage |
| Give Agents a new tool | Capability handler plus relationship grant | Yes: graph-derived scope and invocation-time authorization |
| Manage an external service or local runtime | `NodeLifecycleHandler` | Yes: startup, shutdown, create, update, delete, create rollback |
| Integrate another model or Agent engine | `register_runtime_provider` | Yes: Run scheduling, events, cancellation, concurrency policy |
| Declare durable runtime state | `register_state_schema` | Yes: types, merges, revisions, scoped persistence |
| Add a custom React card or workspace | No dynamic API yet | No: contribute a reviewed core frontend renderer |
| Add an HTTP route or middleware | No public plugin API yet | No: do not depend on private FastAPI objects |

If a plugin only needs to display configuration and relationships, the generic
frontend is sufficient. If it needs a fully custom interaction model, implement the
backend behavior as a plugin and contribute its renderer to the core frontend
separately. A backend plugin must not download or inject arbitrary browser code.

## Minimal package structure

Use a conventional `src` layout:

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

The entry point is the important part of `pyproject.toml`:

```toml
[project]
name = "acme-open-agent-world-plugin"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.11,<3"]

[project.entry-points."open_agent_world.plugins"]
acme = "acme_oaw_plugin:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/acme_oaw_plugin"]
```

The backend is not yet published as a standalone Plugin SDK on PyPI, so the example
does not declare a fictional core dependency. Test a plugin with its target Open
Agent World checkout and state the compatible Git commit or release tag. Do not copy
core types into the plugin package.

The entry point must expose a synchronous registration function:

```python
from backend.plugins import PluginRegistry


def register(registry: PluginRegistry) -> None:
    ...
```

Keep module import and `register()` deterministic and free of I/O. Do not connect to
a network, start threads, or create external resources during registration. The
loader sorts plugins by entry-point name. A broken import, duplicate identifier, or
registration error prevents backend startup instead of silently weakening graph or
permission semantics.

## Nodes: configuration and generic UI

A node definition drives both backend validation and the frontend catalog:

```python
from pydantic import BaseModel, ConfigDict, Field

from backend.plugins import NodeTypeDefinition, PluginRegistry


class DatasetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = "http://127.0.0.1:9000"
    timeout_seconds: float = Field(default=10, gt=0, le=120)


def register(registry: PluginRegistry) -> None:
    registry.register_node_type(NodeTypeDefinition(
        id="acme.dataset",
        label="Dataset",
        description="Queryable external dataset",
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
    ))
```

Node definition rules:

- Prefix every third-party identifier with a stable organization namespace, such as
  `acme.dataset`. IDs are at most 128 characters and use lowercase letters, digits,
  and the separators `._:/-`.
- `config_model()` must work without arguments because palette creation needs a
  `default_config`.
- Configuration must serialize to JSON. Prefer `extra="forbid"` so spelling errors
  are rejected instead of silently persisted.
- `default_status` must be a member of `statuses`.
- Traits are composable contracts. Prefer matching a relationship by trait instead
  of enumerating another plugin's concrete node type.
- `surfaces` may declare `preview`, `inspector`, and `workspace`. Plugin nodes
  currently receive a generic preview, a read-only scalar configuration inspector,
  and a generic workspace placeholder.
- Supported node icon keys are `bot`, `file-text`, `image`, `workflow`,
  `messages-square`, and `sparkles`. Unknown keys safely fall back to a puzzle icon.
  Deck icons support `bot`, `boxes`, `workflow`, `folder`, `layers`, `sparkles`,
  `star`, and `zap`.

The Pydantic model is the authoritative configuration contract. Both creation and
updates pass through it; do not duplicate authoritative validation in the frontend.
The generic inspector does not currently generate an edit form. Submit non-default
configuration through the API or a plugin-owned external control plane:

```powershell
$node = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/nodes `
  -ContentType application/json `
  -Body '{"type":"acme.dataset","config":{"timeout_seconds":20}}'

Invoke-RestMethod -Method Patch `
  -Uri "http://127.0.0.1:8000/api/nodes/$($node.id)" `
  -ContentType application/json `
  -Body '{"config":{"timeout_seconds":30}}'
```

## Lifecycle: give a node behavior

A node without `lifecycle` is a passive, persisted world object. Implement
`NodeLifecycleHandler` when a node must connect to a database, restore a device,
maintain a plugin-owned adapter, or clean up external resources:

```python
from backend.plugins import NodeLifecycleContext, NodeLifecycleHandler
from backend.world.models import Card, CardCreate, CardPatch


class DatasetLifecycle(NodeLifecycleHandler):
    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        await runtime.open(node.id, node.config)

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        await runtime.close(node.id)

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        await runtime.create(node.id, node.config)

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        await runtime.delete(node.id, missing_ok=True)

    async def on_update(
        self, context: NodeLifecycleContext, node: Card, request: CardPatch
    ) -> None:
        merged = {**node.config, **(request.config or {})}
        await runtime.update(node.id, merged)

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        await runtime.delete(node.id, missing_ok=True)
```

Set `lifecycle=DatasetLifecycle()` on the node definition. The lifecycle context
exposes narrow interfaces for nodes, resources, conversations, and optional Agent
and Sandbox operations. It does not expose HTTP requests, provider SDKs, database
connections, or the complete application service container.

Lifecycle ordering and failure semantics:

- Startup: after Run recovery, call `on_startup` for each persisted node.
- Creation: persist the node, call `on_create`, then publish the creation event.
- Failed creation: call `on_create_rollback`, delete the persisted node, and re-raise
  the original exception.
- Update: pass the pre-update node and patch to `on_update`, then update world
  storage if the callback succeeds.
- Delete: call `on_delete`, then remove the node and its edges if it succeeds.
- Shutdown: call `on_shutdown` for each node, then stop the Run manager.

Every external side effect created by `on_create` must be reversible. Make callbacks
idempotent and cancellation-safe. `on_startup` must reconstruct in-process state
from persisted nodes alone. An update or delete callback error prevents the matching
world mutation; do not suppress an error and leave a false-success state.

## Relationships and Agent tools

Relationships match endpoints by exact node type, required traits, or both. Traits
are the preferred way to interoperate with other plugins:

```python
from typing import Any

from backend.capabilities import Capability
from backend.errors import ResourceValidationError
from backend.plugins import (
    CapabilityGrantDefinition,
    PluginRegistry,
    RelationshipDefinition,
)


async def query_dataset(
    host: Any, capability: Capability, arguments: dict[str, Any]
) -> dict[str, Any]:
    del host
    query = arguments.get("query")
    if set(arguments) != {"query"} or not isinstance(query, str) or not query.strip():
        raise ResourceValidationError("query requires one non-empty string")
    return await runtime.query(capability.target_id, query)


def register(registry: PluginRegistry) -> None:
    registry.register_capability_handler("acme.dataset.query", query_dataset)
    registry.register_relationship(RelationshipDefinition(
        id="acme.query",
        label="Query",
        short_label="query",
        description="The Agent can query this dataset.",
        source_traits=frozenset({"core.agent"}),
        target_traits=frozenset({"acme.queryable"}),
        directions=frozenset({"forward"}),
        capabilities=(CapabilityGrantDefinition(
            kind="acme.dataset.query",
            tool_prefix="query_dataset",
            description="Query dataset {target_name!r}.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Dataset query."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),),
    ))
```

Register a capability handler before any relationship that references it.
`input_schema` describes tool arguments to Runtime Providers; the handler must still
validate every actual invocation.

`Capability` provides `agent_id`, `target_id`, `target_type`, `target_name`, `kind`,
and the concrete capability ID. Immediately before dispatch, the host derives the
capability again from the current graph. A capability ID is therefore not a durable
authorization token. Removing or changing an edge revokes access immediately.

The handler's first argument is currently a POC host execution object, not a stable
public service API. Community plugins should treat it as opaque whenever possible,
as the example does, and perform work through `Capability` plus their own
provider-neutral runtime. Do not depend on `ApplicationServices`, raw databases,
HTTP objects, or private core attributes.

Connection gestures are unordered. If only the reverse orientation satisfies a
relationship, the frontend and backend swap endpoints and persist the canonical
source-to-target order. A `bidirectional` edge grants the same relationship
capabilities in reverse. Use two relationships when the two directions have
different meanings.

## Runtime Providers

To integrate a different Agent engine, implement `backend.agents.RuntimeProvider`
and register its factory:

```python
from backend.plugins import PluginRegistry


def register(registry: PluginRegistry) -> None:
    registry.register_runtime_provider(
        "acme.runtime",
        lambda capability_provider, **options: AcmeRuntimeProvider(
            capability_provider=capability_provider,
            **options,
        ),
    )
```

A provider implements `create_agent`, `update_agent`, `delete_agent`, `execute`,
`stop`, and `get_agent`. `execute(agent_config, invocation_context, runtime_input)`
emits provider-neutral `AgentEvent` values. A Run becomes terminal only when an event
explicitly requests a terminal `run_status`; naturally exhausting the stream leaves
the Run in `waiting`.

Use the injected `AgentCapabilityProvider` to enumerate and invoke graph-derived
tools. Do not query the world database directly. An Agent node selects the provider
with `runtime_provider_id="acme.runtime"`. The provider owns credentials and SDK
sessions; never persist them in card configuration or runtime events. See
[Runs and runtime providers](runs.md) for the complete Run and event contract.

## State schemas

Register a schema for durable runtime state instead of putting frequently changing
execution data into node configuration:

```python
from backend.plugins import PluginRegistry
from backend.state import MergePolicy, StateFieldDefinition, StateSchema


def register(registry: PluginRegistry) -> None:
    registry.register_state_schema(StateSchema(
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

State schema IDs must be namespaced. A field can declare its value type, allowed
scope kinds, read visibility, write permissions, merge policy, and default value.
State storage adds optimistic per-key revisions. See [Runtime state](state.md).

Registering a schema declares a contract but does not create a scope. The plugin
runtime that owns the workflow must explicitly create and manage its state scope.

## Installation, publishing, and removal

Prefer `-PluginPath` during development. It uses `uv run --with-editable` and does
not change backend dependency files. To attach a plugin permanently to one checkout,
add it as a project dependency:

```powershell
uv add --project backend --editable ./path/to/my-plugin
```

After publishing, replace the local path with the distribution name. Verify a
running installation through the public catalog:

```powershell
$catalog = Invoke-RestMethod http://127.0.0.1:8000/api/catalog
$catalog.node_types | Where-Object id -Like 'acme.*'
$catalog.relationships | Where-Object id -Like 'acme.*'
```

Remove a plugin in this order:

1. Export or migrate data owned by the plugin.
2. Delete every node owned by the plugin. Its edges will be deleted and capabilities
   revoked with it.
3. Stop the application.
4. Remove its `-PluginPath`, or run
   `uv remove --project backend acme-open-agent-world-plugin`.
5. Restart and inspect the catalog.

There is no orphan-node migration API yet. Do not uninstall a package while
persisted nodes still depend on it.

## Test that a plugin really enters the main flow

Do not stop at testing that `register()` returns successfully. A minimum integration
test should verify:

1. The catalog contains the registered node and relationship.
2. The backend rejects invalid configuration and statuses.
3. Nodes can be created, updated, and deleted, including lifecycle side effects and
   create rollback.
4. Forward and reverse connection gestures produce canonical persisted endpoints.
5. Creating a relationship grants the Agent a target-scoped tool.
6. The handler executes and validates its arguments again.
7. Removing an edge or node makes an old capability ID unusable.
8. `on_startup` reconstructs runtime state after a backend restart.
9. A broken plugin produces an actionable startup error instead of being ignored.

The Greeter
[integration test](../examples/plugins/greeter/tests/test_greeter_plugin.py)
demonstrates catalog integration, lifecycle updates, canonical connection order,
tool derivation, argument validation, invocation, and revocation. Plugins that call
external systems should also test failed-create rollback with a fake adapter and run
an opt-in acceptance test with separate credentials.

## Design and security checklist

- Namespace every identifier and keep it stable after publication. Changing an ID
  is a data migration.
- Keep `register()` declarative and deterministic. Put I/O in lifecycle callbacks or
  capability handlers.
- Compose traits instead of modifying core `CardType` values or adding
  plugin-specific branches.
- Treat `input_schema` as a description and validate every handler invocation as
  untrusted input.
- Put timeouts and response-size limits around external calls. Never include secrets
  in error messages.
- Persist only non-sensitive configuration. Read credentials from the environment or
  a plugin-owned secret manager.
- Make lifecycle callbacks idempotent and cancellation-safe, and roll back partial
  creation side effects.
- Do not emit hidden reasoning, raw provider objects, or sensitive responses in
  runtime events.
- Never cache a capability as authorization. Let the host re-authorize every tool
  call from the current graph.
- Test upgrade and removal migrations, and back up important worlds before upgrading.

## Current boundary and next steps

The answer to "does the project have a plugin development foundation?" is **yes: the
trusted backend extension path is connected to the main flow**. Community developers
can independently build data sources, tools, external-service adapters, Agent
Runtimes, and state extensions and insert them into the canvas and permission graph
through the generic UI.

A complete community ecosystem will still need a separately published stable Plugin
SDK, API version negotiation, manifests and dependency declarations, orphan-data
migrations, a formal narrow Capability Context, an installation manager, and a
security-reviewed frontend extension mechanism. Those are roadmap items, not
features promised by the current entry-point foundation.
