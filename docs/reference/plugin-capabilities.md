# Relationships and capabilities

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

## Relationships, traits, and capabilities

Executable nodes can additionally declare `NodeExecutionDefinition` (API 1.3).
It uses pure callbacks to enumerate work, apply outcomes, and supply an execution
policy. The host handles Agent admission, live permissions, durable attempts,
stop/retry, and restart reconciliation. This contract does not require Task Board
fields, a DAG, or automatic result acceptance. See the complete
[execution contract and example](../execution.md).

API 1.3 also adds `NodeDocumentDefinition.remap_references(value, ids)`. During
Legion capture, `ids` maps live node IDs to template keys; during instantiation it
maps template keys to the new instance's node IDs. Clear external references
instead of carrying authority to an unrelated original Agent. This callback
must remain pure and support older compatible document versions.

Relationships match exact types, required traits, or both:

```python
from open_agent_world.plugin_api import (
    CapabilityDefinition,
    CapabilityGrantDefinition,
    RelationshipDefinition,
)


def query_capability() -> CapabilityDefinition:
    return CapabilityDefinition(
        kind="acme.dataset.query",
        tool_name="query_dataset",
        description="Query the selected dataset.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
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
        ),),
    )
```

Register the capability definition and handler with `register_capability`.
Relationships grant a kind; its definition owns the operation name and schema.
The projection collects all authorized targets into one `query_dataset(target,
query)` tool. It adds the `target` parameter, so omit it from `input_schema`.
Set `target_parameter` for a domain-specific selector such as `sandbox` or `skill`.

Selectors use normalized readable names. Duplicate names receive a short stable
disambiguator; ambiguous names are rejected. Exact unambiguous names and node IDs
are also accepted by the host. Renames, membership changes and collisions can change
the listing; refresh it when a selector no longer resolves. Tool names never depend
on those changes. Runtime adapters should preserve `ScopedToolDefinition.input_schema`,
including selector enums and nested schemas, when exposing tools to a model.

Composite operations declare additional `CapabilitySelector` values. Each maps a
public `parameter` to an internal handler `argument`. Resources must satisfy all
declared `capability_kinds`, `target_traits`, and read-only `document_action` filters
(a capability kind matches any entry in `capability_kinds`).
`include_members=True` extends that document access to its projected collection's
current members. `target_capabilities` requires additional kinds on the primary
target. For example, `run_skill_script(sandbox, skill, script, argv)` requires
Sandbox execution and independently resolves the selected Skill's current read
access. These independent selectors avoid generating tools for every resource pair.

With API 1.11, `required=False` permits omission of a selector without suppressing
the operation when no matching resources exist. Only explicitly supplied optional
selectors are resolved and authorized. See [execution configuration](../execution-configuration.md)
for Environment Profiles, plugin-defined Compute Target fields, credential
boundaries, and the command-only Sandbox injection contract.

Kinds can share an operation name when their descriptions, schemas and selector
contracts are identical; the selected target determines the handler. Conflicting
contracts fail registration atomically. If multiple kinds implement the same
operation for the same target, invocation rejects the ambiguity.

Legacy inline `tool_prefix` metadata is normalized into definitions during plugin
registration. It becomes the stable operation name with a required target selector;
per-target suffixed tool names are no longer exposed. Old internal capability ID
invocations remain supported and still recheck their exact current scope.

A handler
receives `CapabilityContext`, the freshly authorized `Capability`, and untrusted
arguments. `CapabilityContext` is a narrow set of provider-neutral host operations;
plugins normally use the capability scope plus their instance-owned runtime.

The input schema describes an Agent tool but does not replace handler validation.
The host derives capabilities from the current graph immediately before every
invocation. The host resolves all selectors against live resources and verifies
the primary and additional capability scopes before calling the handler. Neither
a selector nor an operation/capability ID is an authorization token. Deleting or
changing a granting edge revokes permission for subsequent invocations, including
calls through a tool exposed before that change.

Connection gestures are unordered. The host stores the endpoints in the canonical
orientation defined by the relationship. A `bidirectional` edge grants the same
relationship capabilities in reverse; use separate relationships for different
meanings.

## Runtime providers

API 1.8 supports plugin-defined Agent card types through the `core.agent` trait
across Runs, Conversations and capability authorization. Reuse public
`AgentNodeBehavior` for transactional lifecycle handling and
`AgentNodeTemplateHandler` for portable configuration (subclass its field set
when needed). Register a distinct node type with its own Pydantic configuration.
Catalog entries expose `config_schema`; the `ui.schema-agent.v1` trait selects
schema-driven scalar settings in the standard Agent workspace. Constant fields
are not editable. `AgentInfo.details` carries JSON-compatible, non-secret runtime
inspection data. See the [Codex plugin](../../packs/codex/README.md).

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
[Runs and runtime providers](../runs.md) for execution semantics.

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
plugin runtime explicitly creates and manages scopes. See [Runtime state](../state.md).

## Agent equipment and Barracks (Plugin API 1.7)

Plugin API 1.26 adds `NodeTypeDefinition.default_equipment`, a tuple of
`DefaultEquipment(key="skills", type="example.tools", relationship="example.tools.use")`
declarations. Only Agents can declare these slots. Targets and relationships
belong to the declaring plugin; targets are inert document resources with an
initial value, without lifecycle handlers, execution or scoped runtime state.
The host creates ordinary private equipment and documents through native world
and StateStore operations, with compensation on failed Agent initialization.
Missing slots are seeded once for existing Agents on startup. Known slots are
not reinstalled after unequipping/deletion. Existing shared resources are not
adopted or removed. Duplicate/summon/Legion/desktop history preserve slot identities
remapping internal node references on copy. Existing documents are not updated
when the plugin changes; edit them manually or create new resources.

World edges describe shared relationships. `Card.parent_id` describes spatial
container membership. `Card.equipment = {owner_id, relationship}` describes
private ownership and an owner-to-resource capability binding. An equipped card
keeps its ID, plugin, configuration, document and lifecycle. It cannot also be a
container member. Ownership and membership together must remain acyclic.

Equipment eligibility comes from the existing relationship registry: any card
that can connect to an Agent can be owned by it. Plugins do not declare a separate
equipment flag, whitelist, or portability requirement. Self-ownership, mixed
container membership and ownership cycles remain invalid.

The binding selects an existing relationship. When omitted, it uses the first
available connection option, just like the connection UI. Text therefore offers
Read and Read/edit; Conversation uses Participate; Sandbox uses Execute.
Reverse-oriented plugin relationships retain their endpoint order and direction.
The same live connection resolver supplies world edges and owned bindings to
capability checks and Conversation participant authorization.

A relationship with no direct capability grants delegates capability discovery
to its target's outgoing relationships. This supports capability adapters through
ordinary connections as well as equipment; visited relationships stop cycles.
Relationships with direct grants stop traversal at their own scoped target.

Portability remains a separate capture/restore contract. Equipping a plugin node
does not imply that its runtime state can be copied. Built-in Conversations
instantiate as new empty sessions, without copying message history.
Sandbox capture preserves the
runtime choice but excludes the active workspace path and files, creating a fresh
managed workspace. External graph connections continue to point at the original
world resources. Equipping never silently deletes existing world edges.

Use the ordinary node create/update API to equip or unequip:

```json
{"parent_id": null, "equipment": {"owner_id": "agent-id", "relationship": "execute"}}
```

`{"equipment": null}` returns the card to world mode. Open the Agent backpack
to reveal slots, then drop a card into the panel. Slots expose inspection,
existing relationship options, unequip and connection controls. Nested
toolbox members also retain connectors. Ports always create normal world edges.
Equipment stays loaded with its owner across chunk boundaries and survives
reload and undo/redo. Portable nodes also support graph capture and duplication. The duplicate
endpoint is `POST /api/nodes/{agent_id}/duplicate`; it uses the same capture /
restore pipeline without creating a Legion preset or starting a Run.

`oaw.barracks` is an ordinary container accepting Agents. Dragging an Agent in or
out changes membership only. The Agent in that container is the live blueprint;
later configuration/equipment changes affect subsequent summons. There is no
Agent Template card, template-save form, capture endpoint, copied-node picker,
entry selector or copy/share dialog. Legion retains explicit team context and
multi-Agent topology; it is not required to package one equipped Agent.

The **Summoning** skill (`oaw.barracks.summoner`) connects to an Agent using
`oaw.barracks.use`. Equip it or connect it as a shared card, then connect its
own port to a Barracks using
`oaw.barracks.summon`. It uses the same relationship traversal in both modes.
Unequipping Summoning revokes its owner's derived summon capability while
preserving the adapter's graph connection. A plain Agent has no inherent summon
capability. A spawned Agent's copied Summoning skill retains its external Barracks edge,
so recursive summoning uses the same mechanism.

`NodeSummoningDefinition(capability_kind)` declares a catalog container with a
document and handler owned by its plugin. The document supplies `instructions`
and `policy`. `SummoningAction` supports `list`, `summon`, `inspect`, `message`,
`stop` and `reclaim`. Listing returns `agents` with ID, name, description and
equipment count. Summon takes `agent_id` and `prompt`; instance operations take
`instance_id`. Human APIs are `GET /api/nodes/{id}/summoning` and
`POST /api/nodes/{id}/summoning/actions`.

Admission takes a stable in-memory snapshot of the current Agent and its private
equipment, then restores through existing plugin ownership validation and
compensating resource lifecycles. Internal equipment bindings are remapped;
external edges retain endpoint IDs, type and plugin ownership. No second durable
blueprint is stored. Instantiation failures roll back created resources.

Each summon starts a normal child Run with `caller_kind="summon"`. All descendants
share root depth, concurrent Run and total-instance limits (defaults 4, 4, 16).
Libraries can tighten these limits. Completed/reclaimed instances still count
toward the root total. Agent calls return the provider turn and instance handle;
human test calls return immediately. Follow-up reuses the same nodes. Stop and
reclaim cascade through summoned descendants. Reclaim removes the instance's
current private equipment, including newly equipped resources, while preserving
unequipped and shared world resources. Ownership roots are persisted separately
from the initial node list. Each Agent can manage only its own summoned instances
through its live capability. Run results and instance records survive restart.

Each new instance also receives a `core.virtual-workspace` container, returned as
`workspace_id`. Its Agent appears inside a dashed frame; equipment remains attached
to that Agent. A dashed `core.generated` edge connects the granting Summoning card
to the workspace (or the Barracks for human calls). Workspace membership grants no
capabilities. Follow-up keeps the same workspace, and Reclaim removes the frame and
its incident connections while detaching unrelated cards the user moved inside.
Existing instances without a workspace retain their previous layout.
New workspaces choose a nearby free region against existing world footprints,
including other libraries and their instances. Placement reserves a gap between
regions; later manual moves or local panel expansion do not rearrange other cards.
Member surfaces stay inside the container's content inset as they expand.
Only `summon` and `message` require non-empty `prompt`; management operations accept
an omitted or empty prompt.

These visuals use catalog contracts: `NodeContainerDefinition.virtual` selects a
dashed spatial frame, while `RelationshipDefinition.generated` marks runtime-created
provenance connections. Generated connections are excluded from manual connection
choices, capability traversal, and template capture. Tools create them through the
same `create_edge` operation as other connections, also allowing undo to restore
a disconnected edge. Existing edges cannot convert to or from generated relations.
They persist with the world until disconnected or their endpoint is deleted; they
do not expire when a Run finishes. This virtual workspace is a canvas grouping,
separate from a Sandbox filesystem workspace.

The reusable rectangle solver and world/container placement interfaces are
documented in [Canvas placement](../layout.md).

### Migrating retired Barracks data

The old `CallableTemplate`, `SummoningCapture` and `templates_field` contracts are
removed. Other earlier plugin contracts remain supported. Startup detects retired
Barracks documents/cards/edges and provides an explicit migration command:

```powershell
backend/.venv/Scripts/python.exe -m backend.migrations.barracks OLD_DATA_ROOT NEW_DATA_ROOT
```

Stop the application first. The command copies into a new directory, keeps the
source untouched, materializes supported single-Agent snapshots and equipment,
converts legacy Agent-to-Barracks edges into equipped Summoning skills, and archives the
old definitions in `legacy-barracks-archive.json`. Point the application at the
new data root after reviewing it. A failed copy retains `MIGRATION_INCOMPLETE`.
Existing Legion templates, multi-Agent snapshots, unsupported resource types,
and direct-to-template capability scopes require restoration in the old version
first; migration refuses to flatten team context or widen a capability scope.
Startup does not run this migration automatically.

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
- Plugin frontends execute trusted browser code. Import host services through
  `@oaw/plugin-api`; rendering error boundaries are not a security sandbox.
