# Legion team spaces

A Legion has two representations: a reusable library template and a live `legion`
card in the world. A live card owns settings and a durable `legion:<card_id>` state
scope. Its members remain ordinary world nodes with a nullable `parent_id`.

Select ungrouped cards and choose **Form Legion**, or place an empty Legion from
the Fields deck. Select the Legion and additional cards to add those cards using
**Add selected cards**. **Detach** removes membership while retaining the card,
its world position, and its edges. An Agent must finish or stop its active Runs
before changing membership. This version supports one Legion per member, up to
100 members, and no nested Legions.

The container header moves the entire team. Positions remain absolute world
coordinates in storage; React Flow receives relative positions for child nodes.
Batch moves include members exactly once, and viewport loading retains a visible
container and its members together. Resizing establishes the minimum space;
expanded member surfaces can enlarge its rendered bounds. Container removal
requires detaching its members first, or explicitly deleting them in the same
batch. External edges continue to work across the container boundary.

The banner's **Dissolve** action removes the container while keeping member nodes,
positions and their connections. The trash button **Delete Legion and members**
deletes the container and all members as one batch. Both actions support undo/redo,
including restoration of saved shared variables. Library presets are unaffected.

## Runtime settings and state

- **Team instruction** is appended to each member Agent's own instruction at Run
  start. A member's optional role is included in that context.
- **Team model override** replaces the member model when configured, unless that
  Agent disables **Use team model override**. It never rewrites Agent config.
- **Pause team** prevents new member Runs, including delegated turns. Existing
  Runs continue; resuming the team allows new Runs again.
- **Shared variables** provides named rows with Text, Number, On / off, or JSON
  values. Add and remove rows without editing a whole JSON object; blank names,
  duplicate names, invalid numbers and invalid JSON show validation errors.
  Values are stored as a JSON object in `StateStore`, limited to 64 KiB.
  **Save variables** replaces it using compare-and-set revisions. On a conflict,
  the draft is retained; reload the latest state before reconciling changes.
- Member Agents receive `read_legion_state` and, when enabled,
  `patch_legion_state`. Patches merge top-level keys and require the last read
  revision. Every invocation rechecks membership and read/write mode. Joining a
  Legion grants these state tools only; sandbox, resource and Agent communication
  capabilities continue to require their existing edges.

The Run scope records a snapshot of the effective team context. Runtime Providers
receive it in `InvocationContext.group_context`; the effective instruction and
model are also passed through the provider-neutral `AgentConfig`. The state stack
is `world -> legion -> agent -> session (optional) -> run`. The snapshot is data
at Run start, while the state tools read the live authority. The ADK session is
not a second writable copy of the team state.

## Templates and API

First **Form Legion**, configure the live team's settings and shared variables,
then optionally **Save to library**. Saving to the library also saves any pending
shared-variable draft. It captures the container, settings, current shared variables,
members, internal edges, and existing portable resource payloads. Membership uses
template-local keys, which are remapped on instantiation. Each deployment receives
an independent copy of the saved variables; changes to the source or another copy
do not alter the preset. Older templates without variable presets start empty.
Run history and host-bound sandbox folders continue to be excluded.

The frontend deploys older flat templates inside a new Legion. For compatibility,
`POST /api/legions/{template_id}/instances` preserves the original flat behavior
unless `as_group: true` is provided; templates containing containers always retain
their membership structure.

```text
POST /api/legion-groups                      {name, node_ids}
PATCH /api/nodes/{member_id}                 {parent_id: legion_id | null}
PATCH /api/nodes/{legion_id}                 {config: {...}}
GET /api/legion-groups/{legion_id}/state
PUT /api/legion-groups/{legion_id}/state      {value, expected_revision}
```

Group formation is one database transaction and emits world events only after
commit. Grouping and membership changes participate in frontend undo/redo.

## Extension boundary

This layer provides persistent team identity, membership and context. It does
not yet execute a plan, schedule a DAG, automatically dispatch roles, or recover
external jobs. Those should be separate Controller/Task/Job primitives using the
Legion identity and state, rather than interpreting permission edges as task
dependencies. Role names are descriptive and do not trigger scheduling.
