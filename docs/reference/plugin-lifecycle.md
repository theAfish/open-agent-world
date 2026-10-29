# Plugin lifecycle and portability

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

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

Node types whose portable configuration contains card references can provide
`NodeTypeDefinition.template_remap_config(config, node_ids)`. The host calls this
pure function during capture (live IDs to template keys) and deployment (keys to
new IDs), before config validation. Return a new config object and omit or collapse
references absent from the mapping. Legion workspace layouts use this hook; it
does not require a resource payload or change the node's lifecycle contract.

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

## Installation and removal constraints

Use `.oawpack` installation or explicit source paths with a separate development
profile. Do not add third-party dependencies to the host's project file. Existing
installed Python entry points remain supported for legacy projects.

If an optional plugin disappears or fails, retained cards and connections become
unavailable placeholders with their IDs, configuration, layout and data preserved.
They cannot grant capabilities. Restoring the compatible implementation and
restarting restores their behavior. Intentional removal still checks world and
active-work blockers; it does not silently delete those objects.

The Pack Library can disable an installed plugin after its world instances,
relationships and dependent Agent runtimes have been removed. Pack ownership,
collected cards and deck references survive disable and uninstall. Re-enabling or
reinstalling the same stable IDs restores availability. New passive owners can
activate immediately; Python code, loaded-version changes and unloads require
restart. No running Agent or Sandbox task is automatically interrupted.

## Recovery callbacks (API 1.38)

`on_startup` restores transient resources for saved cards. Finish durable writes
atomically and make `on_shutdown` idempotent even after partial startup. Optional
restore failure first revokes the owner and dependent contributions, then calls
bounded cleanup for its attempted and restored cards. Only successful cleanup
permits Core startup to continue. Shutdown attempts other handlers before reporting
an aggregate failure. User data is never rollback scratch space.

Raise `PluginIntegrityError` when plugin persistence or compensation is uncertain.
Database errors, unfinished host transactions, unresolved deletion journals,
security violations and failed cleanup remain fatal. Factories must clean resources
they create before returning an instance. The host cannot recover arbitrary global
Python mutation, an uncooperative callback, a native crash or process termination.
These are trusted in-process plugins, without an Extension Host crash boundary.

Manifest contributions can be combined with advanced registration in one atomic
collector; see [mixed contributions](../developers/declarative-packs.md#mixed-contributions-api-138).


## Backend Run observation (API 1.41)

`NodeLifecycleContext.run_observations` provides `read(viewer_id, agent_id,
run_id)` and `watch(viewer_id, agent_id, run_id, callback)`. The public types
`ObservedRun`, `RunObservations`, and `RunSubscription` are exported from
`open_agent_world.plugin_api`. Older/manually constructed lifecycle contexts
may leave this optional field as `None`.

The viewer must have `core.execution-viewer` and a live directed
`core.execution-source` grant to the Agent. Composite reverse relationships
use the same permission checks as existing execution views. The Run must belong
to that Agent. Reads target the exact Run across sessions; they do not use the
currently selected frontend session. No control permission is granted.

`ObservedRun` contains only `run_id`, `agent_id`, `status`, `updated_at`,
`started_at`, and `finished_at` (ISO timestamps), plus optional `error`.
For failed Runs, `error` is a bounded, host-generated category summary (such as
invalid model, authentication, timeout or rate limit), never the raw exception.
Unknown errors receive a generic message directing the user to Run details.
It excludes prompts, output, credentials, raw error detail and provider internals. Run success is not proof
that a plugin business task completed.

```python
async def on_startup(self, context, node):
    async def changed(run):
        # Match the saved task attempt/run_id before updating business state.
        # Use the plugin's existing safe persistence path for any blocking write.
        await self.update_task(run)

    self.watch = await context.run_observations.watch(
        node.id, self.saved_agent_id, self.saved_run_id, changed)

async def on_shutdown(self, context, node):
    if self.watch:
        await self.watch.close()
```

Registration first subscribes to EventHub and delivers an authoritative initial
snapshot before returning the handle. It then serializes changed snapshots to
the async callback, rechecking live grants before delivery. Permission revocation
pauses delivery without ending the subscription. Restoring a live grant
automatically reconciles the same Run, including terminal changes while revoked.
Deletion stops the subscription. Callbacks must be bounded and idempotent;
a failed later callback is logged and retried on a subsequent event/check.
Initial callback errors propagate to the registering caller. Callbacks run under
the host node-mutation lock; do not block or wait for other Run transitions there.

EventHub is a wake-up signal, not a durable event log. The host rereads Run state
on events and every 30 seconds to reconcile dropped events. Intermediate
transitions may coalesce; this is current-state delivery, not exactly-once event
replay. Subscriptions are process-local: restore saved task/run associations and
register again at startup to reconcile restart-interrupted Runs. Close handles on
node shutdown/removal; host shutdown also cancels remaining subscriptions. This
SDK does not start Agents or create threads in plugins.
