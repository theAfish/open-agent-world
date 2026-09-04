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
requested minor. Host 1.1 therefore accepts plugins requiring 1.0 or 1.1, while
rejecting 1.2 and 2.0. Pin `"1.1"` when using Legion template contracts or
delete-transaction dependencies/recovery payloads; otherwise a plugin using only
the original contracts can continue to declare `"1.0"`.

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
        plugin_api_version="1.0",
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
    PluginCompatibilityError,
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
- Delete: prepare from the node and its still-present graph, commit the reversible
  plugin transaction, atomically delete the node and cascading edges, publish the
  authoritative graph events, then run bounded `finalize` work outside the graph
  barrier. A commit or persistence failure invokes rollback and leaves the node
  present. Put irreversible cleanup in idempotent `finalize`, which runs only
  after persistence succeeds. Core Agent deletion reserves new Run admission
  during commit but leaves existing Runs live until graph commit; rollback releases
  the reservation, while finalization cancels Runs and removes provider state. The
  reservation remains fail-closed when finalization becomes cleanup debt and is
  released only after a retry succeeds. This prevents both irreversible pre-commit
  cancellation and a concurrent Run slipping past deletion cleanup. Sandbox commands
  remain untouched before graph commit and are terminated or destroyed only in
  bounded finalization.

`commit`, `rollback`, and `finalize` must be idempotent. `rollback` must tolerate
a partially completed commit and use captured state rather than assuming the
world mutation succeeded. For Plugin API 1.1 deletion transactions, rollback must
also be harmless when the host persisted a `started` marker immediately before a
hard stop but `commit` never entered. A 1.0 plugin with live recovery debt fails
startup closed instead of receiving this newer semantic. During normal execution,
transactions whose commit intent was never recorded are not rolled back.

Deletion recovery is durable. Before the first plugin commit, the host stores a
private journal containing each Card, its outgoing edges, managed-resource record,
owning plugin/package/API versions, topological batch order, and the transaction's
JSON-safe `delete_recovery_payload`. Rows progress from `prepared` to `started` to
`committed`; only an entirely committed batch may delete its graph. If the graph
is still live, startup rolls back in reverse order and fails closed if recovery
cannot finish. If the graph is gone, startup finalizes in forward order; failures
remain diagnostic debt but do not block service. Finalizers have bounded deadlines
and never retain the global graph barrier. A live rollback debt also blocks new
world mutations in the current process so its Card and sidecars cannot drift past
the saved recovery snapshot.

The default `prepare_delete_recovery(...)` calls `prepare_delete(...)`. For a live
graph the host supplies the normal lifecycle context and invokes `rollback`; for a
deleted graph it supplies the saved read-only context and invokes only `finalize`.
It never replays `commit`. Override the hook when either path needs captured
external before-state or migration. `supports_delete_recovery_version(saved,
current)` fails closed on plugin-version changes by default. A transaction exposes
the minimum serializable recovery inputs through `delete_recovery_payload`. Never
put credentials there; cleanup snapshots are private and never returned by HTTP.

Batch deletion commits all selected-node transactions before one atomic database
delete. If one transaction depends on another selected node still existing, set
its `commit_before_node_ids` to those node IDs during preparation. The host
topologically orders commits and finalizers and reverses that order for rollback;
dependency cycles are rejected before any commit.

`on_startup` reconstructs runtime state from persisted nodes. `on_shutdown`
releases process-scoped state. Both may be called again after interrupted process
lifecycles and therefore must also be idempotent.

`NodeLifecycleContext` exposes narrow node, resource, conversation, Agent, and
Sandbox operations. It does not expose the database, HTTP requests, provider SDKs,
or the application service container.

## Legion portability

The Legion and template contracts require Plugin API `"1.1"`.

A Legion is a backend-owned, versioned snapshot of two or more nodes and the
relationships whose two endpoints are both selected. It is a reusable graph
template, not a dynamically registered node type: saving one does not change the
plugin catalog, and deploying one creates fresh node and edge IDs at a translated
position. Connections that leave the selection are intentionally excluded.

Portability is fail-closed. `NodeTypeDefinition.templateable` and
`RelationshipDefinition.templateable` both default to `False`; a plugin must opt
each contribution in explicitly. A passive node whose durable state is completely
represented by validated `config` can set only `templateable=True`; this explicitly
declares its entire config portable. A relationship
with no external or handler-owned state can do the same. Missing, replaced, or
newly non-templateable contributions leave saved Legions visible but incompatible
until their owning plugin is restored.

Use `NodeTemplateHandler`, imported from `open_agent_world.plugin_api`, whenever a
node must project portable configuration (for example, to exclude credentials or
machine-local fields) or capture durable state outside normal `Card.config`.
Capture receives a read-only
`NodeTemplateCaptureContext`; restore receives a separate
`NodeTemplateRestoreContext` and returns the same reversible
`NodeLifecycleTransaction` abstraction used by node lifecycle operations:

```python
from collections.abc import Mapping
from typing import Any

from open_agent_world.plugin_api import (
    Card,
    NodeLifecycleTransaction,
    PluginCompatibilityError,
    NodeTemplateCaptureContext,
    NodeTemplateDependency,
    NodeTemplateHandler,
    NodeTemplateRestoreContext,
)


class DatasetTemplate(NodeTemplateHandler):
    payload_version = 1

    def dependencies(self, config: Mapping[str, Any]):
        return (
            NodeTemplateDependency("runtime_provider", config["index_provider_id"]),
        )

    def capture_config(self, node: Card) -> dict[str, Any]:
        # Keep machine-local fields and credentials outside the blueprint.
        return {
            "dataset_name": node.config["dataset_name"],
            "index_provider_id": node.config["index_provider_id"],
        }

    async def capture(
        self,
        context: NodeTemplateCaptureContext,
        node: Card,
        node_keys: Mapping[str, str],
    ) -> dict[str, Any]:
        del context, node_keys
        return {"index": self.runtime.export_index(node.id)}

    def validate_payload(self, payload, payload_version):
        super().validate_payload(payload, payload_version)
        if set(payload) != {"index"} or not isinstance(payload["index"], dict):
            raise PluginCompatibilityError("invalid dataset template payload")

    async def prepare_restore(
        self,
        context: NodeTemplateRestoreContext,
        node: Card,
        payload: Mapping[str, Any],
        payload_version: int,
        node_ids: Mapping[str, str],
    ) -> NodeLifecycleTransaction:
        del context, node_ids
        self.validate_payload(payload, payload_version)
        return RestoreDatasetIndex(self.runtime, node.id, payload["index"])
```

`payload_version` belongs to the handler's data schema, independently of the
plugin package version. Override `supports_payload_version` when a handler can
restore older versions, validate every supported shape in `validate_payload`, and
branch on the supplied version in `prepare_restore`. `node_keys` maps source IDs
to template-local IDs; `node_ids` maps those local IDs to the fresh instance IDs,
so handler-owned internal references can be remapped without storing source-world
identities. The host invokes `capture()` twice to verify that the portable snapshot
did not change while it was collected. It must therefore be side-effect free and
deterministic for unchanged node/resource state; do not add timestamps, consume
one-shot exports, or mutate external state during capture.

`dependencies(config)` declares any other registered contribution needed to
restore the node. Each `NodeTemplateDependency(kind, id)` uses the same generic
contribution kinds as `PluginRegistry.owner_id`: `node_type`, `relationship`,
`capability_handler`, `runtime_provider`, or `state_schema`. At capture, the host
resolves and stores the contribution's stable plugin owner; that owner is included
in the Legion summary. A missing or re-owned contribution makes the saved Legion
visible but incompatible. A process-injected runtime provider has no stable plugin
owner, so a node that explicitly names one cannot be captured as portable; package
the provider as a registered plugin contribution first.

The host retains every lifecycle and template transaction until the complete
formation succeeds. During a live request, a later node or relationship failure
rolls them back in reverse order, removes the partial graph, and emits no create
events. This is not yet a hard-process-termination transaction; a plugin whose
create/restore side effects require that guarantee needs a future durable formation
recovery hook. Handler commit and rollback therefore have the same idempotency and
cancellation requirements as normal lifecycle transactions. The complete
serialized blueprint is limited to 64 MiB. Captures take a portable-state lease
against active Sandbox commands so read/write mounts cannot produce a mixed
snapshot; ordinary graph reads and edits remain available while a command runs.

Core policies are explicit: Text and Image copy their managed content; Agent and
Sandbox template handlers project only portable config fields, with fresh instances
starting `idle` and `stopped` respectively. Unknown config extras are excluded by
those core projections. Provider credentials are supplied out of band and never
enter a blueprint. Conversation is currently not templateable because silently
dropping its durable sessions and transcript would violate snapshot semantics.
Runs, processes, and transient provider state are not copied.

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
        templateable=True,
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
