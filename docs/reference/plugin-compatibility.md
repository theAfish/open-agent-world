# Plugin API feature requirements

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

## Minimum feature levels

Use the minimum version required by every API your Pack calls. Older feature
levels remain compatible unless the contract explicitly states otherwise. The
following requirements are API facts, not desktop release availability.

Plugin API 1.40 provides `CapabilityContext.read_artifact_file(capability, collection_id,
version_id, path, max_bytes=...)` for trusted plugin handlers to consume an authorized
Artifact Collection file or a sent Conversation attachment. The host checks the
destination capability, its selectors and required grants, independent `artifact.read`
access, and Conversation session visibility throughout the read. The immutable
manifest, pinned file and complete checksum are verified before bytes reach the
handler. The default and maximum transfer size is 32 MiB; handlers may request a
smaller limit. Bytes remain backend data: return provenance and operation results
to the Agent, not the raw file. Use this helper before `node_resource_action` when
importing a file into plugin-owned storage; that action rechecks its destination
grants, selectors and every successfully read source's access before dispatching
the write under the graph mutation lock.

Plugin API 1.39 provides [Pack IDE visual resources (Experimental workflow)](../developers/pack-ide.md): a
`[designs]` mapping binds structured card presentation to existing resource
actions. The IDE, external editors and authorized Agents share ordinary Schema 3
source files. Visual changes preserve handwritten Python and custom frontend
slots; new visual-only projects use the same Schema 3 builder and installer.

Plugin API 1.37 provides [declarative cards](../developers/declarative-packs.md) using
Schema 3 contributions compiled into the existing registry. Passive cards can
omit Python factories and repeated owner/Pack/card identities. Advanced plugins
keep their existing lifecycle, capability and runtime contracts.

Plugin API 1.36 provides `NodeDeploymentDefinition.from_document()`, shared Workspace
sections and connected file-preview publication. Existing views and business
handlers work in the published Workspace without a second API implementation;
see [plugin deployment](../plugin-deployment.md).

Plugin API 1.33 provides `BackgroundJobProvider`, a host-owned durable job ledger and
model-free observation through the shared continuation loop. Providers submit or
attach external work; the host retains identity, authorization and continuation
admission across turns and restarts. See [long computations and durable waiting](../execution.md#long-computations-and-durable-waiting-api-133).

Plugin API 1.32 provides opt-in [context budgeting and compaction](../developers/context-compaction.md)
for runtime plugins, and connection-specific context/output limits on
`RuntimeModelConnection`. Plugin runtimes retain their own context storage and
orchestration. Run completion now retains Agent capacity until provider stream
cleanup settles; terminal result events alone do not grant capacity.

Plugin API 1.31 provides typed execution edges, history revisions, attempt metadata
and revision-bound pagination. Plugin API 1.30 provides the built-in Agent graph, `NodeExecutionSource`,
`ExecutionNode` / `ExecutionSnapshot`, and `AgentCapabilityProvider.publish_execution`.
See the [execution graph contract](../developers/execution-graph.md) for runtime,
Barracks, Summoning and custom-card sources. Frontend operations can reveal their
content through [`host.workspace.activate()`](../developers/workspace-design.md#activate-content-after-an-operation).

Plugin API 1.29 provides two trusted runtime services on `AgentCapabilityProvider`:
`resolve_model(model)` returns the same run-local `RuntimeModelConnection` as
`ModelConnectionResolver.resolve_runtime`: adapter, `model_id`, base URL,
non-repr API key, image support and saved context/output limits. The `model`
property remains an alias for older adapters. `runtime_state_directory(agent_id)`
returns an Agent-specific directory under the host data root. Resolve model
connections at invocation time; never put credentials in card config, templates,
events or process arguments. These services do not grant access to connected
resources: adapters must still call `list_tools` and `invoke_tool` for live graph
authorization. Runtime checkpoints stay out of Legion templates.

Plugin API 1.28 provides optional `WorkspaceLayoutMetadata` on `NodePresentation`.
The frontend SDK exposes shared application content helpers and `composeWorkspace`
recipes over the existing v2 layout. These leave saved layouts and runtime
ownership unchanged; see [workspace design](../developers/workspace-design.md).

Plugin API 1.23 provides declarative `state` policies, bound `ctx.state` / `host.state`,
and host-owned shared/session namespaces. Legacy plugins remain unchanged. See
[Card state lifecycle](../card-state.md) for declarations, migration, defaults and
stateless capabilities.

Plugin API 1.22 provides Agent-directed delegation to `NodeExecutionDefinition` with
`summoning=True`. Plugins reuse `WorkItem`/`WorkOutcome` for task readiness and
acceptance; the host owns the attempt ledger, Run binding and Summoning admission.
`CapabilityContext.node_delegation_action` supports `delegate`, `wait`, `collect`
and `stop`. Dispatch requires both the work-source control capability and a live
Summoning grant to the selected Barracks. `DelegationRequest`, `DelegationWait`
and `DelegationStop` define bounded inputs. `WorkItem.metadata` provides display
references. Preset nodes can use `owner_key` and `equipment_relationship` for
private equipment. Frontend views use `host.delegationAction` for collection and
targeted Stop; see the MatCreator plugin for a complete example.

Plugin API 1.21 provides opt-in deployment of existing plugin Workspace views and sections through `NodeDeploymentDefinition` and `DeploymentSurface`. See [plugin deployment](../plugin-deployment.md) for scoped fields/actions, frontend integration and a runnable example.

Start with the installable [Greeter example](../../examples/plugins/greeter/README.md), then consult [package discovery](plugin-registration.md#package-structure-and-discovery), [the public API](plugin-registration.md#public-plugin-api), and the contracts below.

Plugin API 1.20 provides an opt-in, transient plugin visual-capture path. A trusted
plugin may register an active workspace renderer with the frontend host; the
backend sends its pixels to a model only through an explicitly granted visual
capability. Screenshots are revision-checked and never stored in node documents
or runtime logs.

Plugin API 1.16 provides `NodePresentation` for supported surfaces, initial appearance,
and compact-card opening behavior (see the node definition contract below). It also
lets a trusted Agent runtime opt into an OAW-managed model connection resolver. The
resolver is injected only in the backend and returns the selected adapter, model,
base URL and secret without storing credentials in plugin card state or frontend code.
Plugin API 1.15 provides optional pack artwork and accent colors through registered
public image assets (see [Card Library](../card-library.md#pack-appearance)).
Plugin API 1.14 provides `PackDefinition` and `registration.register_pack(...)`.
Every card definition belongs to a pack. Opening an owned pack collects its cards;
users then choose cards for persistent named decks. Registration alone never adds
cards to a user's collection or tray. See [Packs, Card Library and Decks](../card-library.md)
for manifests, migration, persistence and disable semantics.

Bundled Packs may include canonical host cards (for example `core.jobs` in
MatCreator). Collection records keep the card's defining plugin as owner and
track each opened Pack separately in `source_pack_ids`; including a host card
does not transfer implementation or runtime ownership. Installed external Pack
distributions still contain only their own cards.

Plugin API 1.13 provides projected read-only document actions (`project=True`),
document update validators and declarative `NodeDocumentTransformation`s.
Projected actions return bounded plugin query results through the same revision
and capability checks as document actions. Transformations accept only inert
document collections; the host checks source/destination revisions and atomically
commits target updates and source consumption. They are explicit user actions,
never relationships or implicit execution grants. The frontend SDK now exposes
document actions, reads, downloads, card discovery and explicit transformations;
container plugins can provide a normal workspace view. These primitives belong
in core because persistence, rollback, events and lifecycle authority belong to
the host. See the [MatCreator demo](../../plugins/matcreator/README.md).

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

Node `status` is host metadata, validated against the node definition's `statuses`.
Strict plugin configuration models do not need a `status` field: create, update,
and restore requests pass it at the top level. For compatibility, configuration
models that declare `status` or allow extra fields retain the legacy config copy.
