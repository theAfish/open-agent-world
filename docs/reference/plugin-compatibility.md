# Plugin API feature requirements

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

## Which version to declare

| Version | Meaning |
| --- | --- |
| npm `@oaw/plugin-api` (currently 1.1.0 in this checkout) | Development package: build helpers and generated TypeScript declarations |
| `PluginDescriptor.plugin_api_version` and `[compatibility].plugin_api` | Minimum host feature level used by the Pack; keep these declarations equal |
| `FrontendPlugin.apiVersion` and `compatibility.frontend_api` (1) | Frontend module loading protocol, independent of host feature additions |
| OAW release and `[compatibility].oaw` | Application release compatibility range |

Using a newer SDK package does not upgrade the recipient's host. For example,
an SDK 1.1.0 build using `host.credentials` needs Plugin API 1.42, while a build
using only `Tooltip` needs 1.41. Optional features may be tested for availability;
required features must be covered by the declared minimum. Generate/package the
SDK from the host checkout you target; package versions do not imply an npm
publication. Old Packs need no version change for host features they do not use.

## Minimum feature levels

Use the minimum version required by every API your Pack calls. Older feature
levels remain compatible unless the contract explicitly states otherwise. The
following requirements are API facts, not desktop release availability.

Plugin API 1.47 unifies referenced file transfers at 512 MiB per file and
256 KiB per chunk. The host publishes its policy at `/api/files/policy` (also
under the published Workspace API); browser clients do not carry separate size
constants. `RuntimeExecution.read_file_stream`, streamed Conversation publication,
and `CapabilityContext.stream_artifact_file` / `open_artifact_file` use this policy.
`receive_file` lends a disk-backed, verified `TransferredFile` with a seekable
stream, size, digest and `chunks()` iterator. Consume and close it within its context.

Frontend `host.resourceFileAction(action, arguments, files)` sends binary multipart
files separately from JSON metadata. Resource handlers receive those temporary
leases in `NodeResourceContext.files`; agent capability handlers pass leases with
`node_resource_action(..., files=...)` after authorized artifact reads. File handles
and host paths are never accepted from model arguments. Resource upload batches
retain the existing 1000-entry bound and have a 512 MiB total content budget.
Authorization is checked throughout reads and again before persistence; cancellation
joins the consumer before disposing its files. Existing byte-returning APIs remain
small-file compatibility helpers, not the default file path. Parser expansion,
model input, inline images and control frames have separate budgets.

`RuntimeExecution.policy` is an immutable, host-owned `RuntimeExecutionPolicy`.
Personal and unspecified providers default to no declared isolation and
`workspace-write`; `remote=True` grants nothing. Only a verified dedicated VM
may opt into an `environment` default. Harness connectors may offer a host-default
setting that resolves this policy inside that VM, while explicit read-only or
workspace-write choices remain strict. The effective policy must participate in
runtime cache and session identities. This contract does not approve interactive
permission requests or grant root access on the host.

Plugin API 1.46 adds `AgentCapabilityProvider.stream_conversation_attachment`
and `RuntimeExecution.write_file_stream(path, chunks)`. Together they stage a
sent attachment as an execution-workspace file without buffering its entire
contents or embedding them in a model prompt. Transfers support files up to
512 MiB with chunks of at most 256 KiB. The source iterator must be exhausted
before committing: live Run/session authorization and the stored checksum are
checked through EOF. Interruption aborts the staging file. Destinations stay
within the selected execution root and must be new files; existing files are
never overwritten. Native and relayed execution reuse the Sandbox uploader.
The existing 32 MiB byte-returning APIs and relay-frame limits are unchanged.

Plugin API 1.45 adds `RuntimeExecution.model_connection(reference)`, an async
context manager accepting `oaw:default` or an `oaw:model:...` reference. It reuses
the existing model settings and yields a `RuntimeModelConnection` whose base URL
is private and whose API key is an execution-scoped token. The stored provider
key remains in the host. Model, endpoint and credentials resolve together;
disabled or changed connections fail explicitly. OpenAI and Anthropic compatible
HTTP protocols are supported. Other adapters are rejected without falling back
to another model. Closing either the context or execution revokes the connection.

This uses the existing private forwarding transport with opt-in `path_prefix`
and `stream` options. Registered destinations remain fixed; remote code cannot
choose a host URL. Model streaming is incremental and bounded. Browser model
selection reuses public `useModelOptions`; no credentials enter card settings.

Acquire the model inside the existing execution context and keep both contexts
open until the CLI finishes:

```python
async with capabilities.runtime_execution(agent_id, run_id, workspace=workspace, home=home) as execution:
    async with execution.model_connection("oaw:default") as model:
        # Adapt this CLI's own provider configuration to model.adapter,
        # model.model_id and model.base_url. Only pass model.api_key as a
        # child-process environment variable; it is a temporary proxy token.
        process = await execution.spawn(argv, env={"HARNESS_MODEL_TOKEN": model.api_key or ""})
        await process.wait()
```

This API provides protocol access, not a CLI's configuration format. The Pack
maps the returned connection to that Harness's public provider configuration.
Existing independent Harness settings remain available when the Pack does not
request a model connection. OpenAI routes are `/v1/chat/completions` and
`/v1/responses`; Anthropic routes are `/v1/messages` and
`/v1/messages/count_tokens`, including the SDK's `beta=true` query and
`anthropic-beta` header. Other routes, arbitrary queries and redirects are rejected.
Credential rotation takes effect on the next request. A changed model or endpoint
requires a new execution. No token or upstream error body belongs in UI or logs.

Plugin API 1.44 adds host-selected `AgentCapabilityProvider.runtime_execution`
and `NodeResourceContext.open_execution`, including duplex processes, bounded
file transfer and private loopback tool forwarding. Native paths remain personal
host paths; hosted compositions must explicitly supply an execution provider.
Resource handlers may be async. Packs using this contract declare 1.44 in both
their descriptor and manifest; they do not choose a cloud SDK or silently fall
back to host execution.

The execution session supplies `workspace`, `home`, `environment`, and an optional
host-managed `command`. A CLI adapter uses `spawn(argv, env=...)`, the returned
process's byte streams, `read_file` / `write_file`, and `forward_http` for a
host-issued loopback tool endpoint. File transfers are bounded to 32 MiB and reuse
the existing no-follow file policy. Local adapters may discover their existing
CLI when `remote` is false; remote adapters must use a command configured inside
that environment. Dependency declarations targeting `host-worker` still describe
the host and are not implicitly installed in a remote environment.

`runtime_execution_configuration(agent_id)` and a resource action's bound
`execution_configuration()` read configuration without starting a process or
allocating a cloud VM. Open sessions only for live Runs or explicit resource
actions. The host tracks sessions through cancellation and shutdown; an
unconfirmed cleanup keeps the node unavailable. Native execution preserves
personal directories and is not an OS security sandbox. Remote providers own
their isolation, credential filtering, persistent roots, durable allocation
receipts, and confirmed teardown. The stdlib relay supports private process,
file, and tool traffic over a provider-owned duplex transport without public
callbacks. It does not provision a cloud environment by itself.

Hosted runtimes declare `RuntimeProvider.supports_managed_execution = True` after
adopting these execution APIs, using authorized Sandbox tools, or running a fixed
Pack-owned pipeline in its declared managed environment. Fixed pipelines such as
XRD remain host computations; this declaration does not move them into a remote
Sandbox. General CLI Harness commands must use the host-selected execution API. The host
rejects legacy adapters before Agent creation or execution; personal installations
remain compatible. This declaration is a compatibility check for trusted code,
not an OS boundary or a substitute for reviewing an installed backend Pack.

Plugin API 1.43 provides `AgentCapabilityProvider.conversation_attachments`,
`read_conversation_attachment` and `publish_conversation_attachment` for trusted
runtime providers. They list sent files, read verified bytes and retain generated
bytes in the current Run's Conversation session, with live session and graph
authorization. See [runtime file transfer](plugin-capabilities.md#runtime-conversation-files-plugin-api-143).

Plugin API 1.42 adds `NodePresentation.content_controls="side" | "bottom"` for
`ui.content-surface.v1`, named host-worker environments through
[`PluginContext.environment(name)`](plugin-lifecycle.md#declared-host-worker-environments),
and the card-scoped human [`host.credentials`](plugin-frontend.md#human-credential-configuration)
interface. These use existing host controls, environment providers and credential
authorization; they introduce no alternate window, installer or secret store.

Plugin API 1.41 provides shared frontend `Tooltip` and `Popover` components with
theme tokens, top-layer positioning, viewport collision handling and keyboard
dismissal. See [workspace design](../developers/workspace-design.md#tooltips-and-popovers-plugin-api-141).

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
