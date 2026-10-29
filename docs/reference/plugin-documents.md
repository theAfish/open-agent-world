# Documents, actions and resources

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

## Native file resources

Plugin API 1.20 adds `PluginDescriptor.requires_plugins`, a tuple of plugin IDs.
The loader orders local and installed plugins by these dependencies before
registration, and reports missing dependencies or cycles before installing them.
Direct registry installation also requires dependencies to be installed first.
Declare these when a preset references another plugin's node types, for example
MatCreator's `requires_plugins=("science.structure-viewer",)`.

Plugin API 1.19 adds host observation budgets to Sandbox execution and package
installation, plus `CapabilityContext.wait_sandbox_operation(...)`. Long operations
return a running receipt; plugins can await that ID without relaunching work.
Existing direct host calls remain synchronous when the observation budget is
omitted; Agent-facing tools opt into a one-second default observation budget.
See [Shared Python and operation waiting](../shared-python-runtime.md#agent-execution-and-waiting).

Plugin API 1.18 adds `registration.register_legion_preset(LegionPresetDefinition(...))`.
Import `LegionPresetDefinition`, `PresetNode` and `PresetEdge` from
`open_agent_world.plugin_api`. Preset IDs must use the plugin namespace. Declare
portable node keys, types, configuration, positions, presentation levels,
optional initial documents/template payloads and internal relationships. An omitted
`PresetNode.presentation` follows the node type's current `presentation.initial`;
an explicit level remains an authored override. Update preset spacing when a node's
default surface changes. Include
a `legion` node with `parent_key=None`; member references and workspace section
owners use template keys. The host resolves contribution owners, validates the
formation before registration commits, and deploys through the existing Legion
transaction. A disabled owner or referenced node/relationship plugin hides its
presets. The bottom Legions deck displays plugin presets, supports click/drag
deployment and copying references into custom decks. Presets cannot be deleted
from the Legions deck; deployed copies can be edited and saved as ordinary
Legions. See `plugins/matcreator/oaw_matcreator/preset.py` for an example.

Plugin API 1.17 adds `NodeResourceAction` for native files that cannot be
represented as JSON node documents. Register a `resource_actions` mapping on
`NodeTypeDefinition`; each synchronous handler receives a `NodeResourceContext`
with a host-selected storage directory, cancellation event, actor ID and
desktop-only confirmation flag. Handlers run off the event loop while the host
holds the normal node mutation lock, and must observe cancellation and impose
finite execution limits. Agents invoke them through
`CapabilityContext.node_resource_action`; their live capability kind must match
the registered action. Frontend views call `host.resourceAction` through the
authenticated control-plane API. Lifecycle handlers obtain the same directory
from `context.resources.node_storage_path(node.id)` and own reversible creation
and journaled post-delete cleanup. Do not create files during registration.

Resource contexts also provide an optional `resolve_secret(reference)` callback
for trusted backend handlers. It resolves the live node's encrypted binding,
then a global environment setting, then the server environment. Resolve inside
the executing job; never put values in card state, durable job parameters,
responses, or logs. Credential writes use host-only settings endpoints, not
Agent capabilities. Standalone callers may omit this callback.

Alongside it, `resolve_model(reference)` resolves an OAW model reference
(`oaw:default` or `oaw:model:…`) to a `RuntimeModelConnection` for a plugin that
must call a model itself from its own job — the knowledge base's image
transcription, for example. The same rules apply: resolve at execution time, use
the key for that one request, and never persist it. Prefer the host bridge
(`backend/api/knowledge_bridge.py` is the pattern) whenever the call can be
made from the backend instead.

For native resources without a canvas snapshot, set `deletion_warning` on the
node definition. The UI confirms permanent deletion, clears undo history after
success, and rejects copy or undo operations that would silently lose the native
data. See [the SQLite plugin](../../plugins/sqlite/README.md) for the complete example.

## Node documents and actions (Plugin API 1.2)

`NodeTypeDefinition.document` accepts a public `NodeDocumentDefinition` with a
Pydantic `model` (constructible with defaults), an action map, optional `summarize`,
and optional portable `capture` transformation. `NodeDocumentAction` contains a
pure synchronous `(value, arguments) -> value` handler, `read_only`, and an optional
`capability_kind` owned by the same plugin. API 1.11 also permits a read-only
action to reuse a capability already declared by an installed read-only document
action. Writes retain the ownership requirement. Handlers validate their own argument
models. They receive JSON data copies, never host databases or service containers.

The host stores one validated document per node in StateStore, outside Card config.
A successful write increments its revision and emits a State event; the node's
delete transaction removes the document. Documents default to a 256 KiB limit;
`max_size_bytes` lets a definition set its content budget (skill toolboxes use
16 MiB to include assets). Actions
are serialized with node mutations and require `expected_revision` for writes.
Read-only actions validate arguments without committing. `replace` is reserved for
human/host full-document restoration and cannot be called using Agent permissions.

Write actions may define an optional asynchronous `prepare(value, arguments)`
callback for external I/O. The host checks access, editability and the expected
revision, copies both inputs, releases the node mutation gate, and awaits the
callback. Its returned dictionary becomes the pure handler's arguments. Before
applying that handler, the host reacquires the gate and checks the live node,
action identity, capability, editability and original revision again. Failed or
cancelled preparation does not write the document; concurrent edits require a
fresh retry. Preparation must not mutate graph state or perform irreversible
external actions. `read_only` actions cannot define `prepare`, and prepared
actions must be invoked outside any caller-owned node mutation gate.

Human-facing APIs:

```text
GET  /api/nodes/{id}/document
POST /api/nodes/{id}/actions/{action}  {arguments, expected_revision?}
```

Both return `{value, revision, summary}`. Capability handlers call
`context.node_document_action(capability, action, arguments, expected_revision)`;
the host re-derives the capability inside the mutation gate and requires its kind
to match that action. Registering an action alone grants Agents no access.

A templateable node's document is captured automatically, using `capture(value)`
when supplied, and validated on deployment. Capture checks the source revision
again after asynchronous resource handlers finish. Incompatible document models
make the preset unavailable; plugin authors must keep their data contract compatible
or provide a deliberate migration. Canvas delete/undo restores the untransformed
saved document, including progress.

`has_document` is exposed in the node catalog. Browser rendering remains reviewed
host code. The first supported structured view is selected by the trait
`ui.task-board.v1`, with the task/value/summary/action contract implemented by the
[Task Board](../task-board.md). Other plugins can adopt that
contract; different visual document types require an explicit host renderer.

## Document seeds and downloads (Plugin API 1.4)

`NodeDocumentDefinition.initial_value` optionally supplies validated initial JSON.
The host copies it into the node's document at creation, starting at revision 1.
This pins published presets independently of later plugin versions. Definitions
without an explicit seed retain their existing lazy defaults and revision 0.
Always read the current revision before writing or restoring a document.

`NodeDocumentDefinition.downloads` maps names to pure synchronous callbacks taking
the saved document and returning `NodeDocumentDownload(filename, content,
media_type)`. The host exposes these through
`GET /api/nodes/{id}/document/downloads/{name}` as attachment responses. This is a
human download surface; it does not grant an Agent a new capability or execute
exported code. Callbacks receive JSON data, not service containers or filesystem
access. The normal trusted-plugin boundary still applies to the callback itself.

The public `open_agent_world.skill_packages` helper uses these contracts to
register editable, portable skill toolboxes and export ordinary entry-point
plugin packages. See [Skill Toolboxes](../skill-toolboxes.md) for
the authoring contract and `ui.skill-package.v1` renderer. Curated collections
remain owned plugin contributions; the core does not maintain a parallel skill
registry, plugin marketplace, or content-hash identity scheme.

## Open containers (Plugin API 1.5)

`NodeContainerDefinition` is the shared declarative base for container nodes.
Assign it to `NodeTypeDefinition.container`. Use it directly, or inherit a small
specialization with different defaults:

```python
from dataclasses import dataclass
from open_agent_world.plugin_api import NodeContainerDefinition

@dataclass(frozen=True, slots=True)
class ResourceShelf(NodeContainerDefinition):
    member_traits: frozenset[str] = frozenset({"core.resource"})
    min_size: tuple[int, int] = (800, 500)
    content_inset: tuple[int, int, int, int] = (24, 110, 24, 24)
    max_members: int = 40

# In the plugin's NodeTypeDefinition:
# container=ResourceShelf()
```

The catalog exposes `container`: accepted member traits, minimum size, reserved
content insets (left/top/right/bottom), capacity, whether the container itself can
have a parent, and whether its border offers connection handles. All required
member traits must match. Membership is a tree; cycles, including cycles formed
by a batch, are rejected. `parentable=False` keeps a specialized container at the
world root. Legion uses this restriction because its team context is not nested.

The host owns parent/child persistence, absolute world coordinates, movement of
descendants, chunk visibility, copying and restoring the tree. Both Legion and
Skill Toolbox use the same `ContainerFrame`, membership actions and drop targeting.
Dropping selects the deepest eligible frame at the node center. Overlapping peers
prefer the smaller frame. Moving out detaches or joins the eligible surrounding
container. Position and membership are one undoable action. Moving a parent and
an explicitly positioned child applies each descendant's nearest moved ancestor
exactly once.

The common frame supplies resizing, border handles, header dragging, member
selection, detaching, dissolution and deletion. Dissolution keeps direct members
and their subtrees; deletion includes descendants. Specialized reviewed UI adds
its header content and controls by composition. A plugin without a specialized
UI gets the standard frame. `ui.legion.v1` adds team settings and shared variables;
`ui.skill-package.v1` adds the toolbox editor and export workflow.

Spatial membership grants no capabilities. Relationships still declare exactly
which node can expose which tools. Legion's direct Agent members retain its team
state, pause and model behavior; an Agent in an ordinary plugin container inherits
none of these. Existing node lifecycle validation also continues to run on
membership changes, including active-run restrictions.

A container can optionally project child documents into its own `document_field`.
Its `member_type` must be an owned document-only node type. Entries use `name` and
host-provided `node_id`; accepted children must implement the collection's document
shape. Child nodes remain the content source. The collection's capture callback
omits projected entries because subgraph capture independently preserves children
and their relationships. See the skill helper for this specialization.

## Durable outputs

Plugin API 1.12 adds scoped artifact publication, inspection, working-copy
materialization and explicit retention release. See [lifecycle and artifact
contracts](../lifecycle-artifacts.md) for authorization, idempotency, migration and
recovery guarantees. Ordinary document `artifacts` metadata does not grant access
or retain file bytes.
