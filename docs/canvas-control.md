# Scoped canvas automation

`ApplicationServices.canvas_control(actor_id, authorize)` provides a backend-only
facade over the existing world services. It installs no Agent, UI, HTTP endpoint,
or tool. The desktop management API remains host-authorized. Automated callers
must receive these bounded operations, not the management credential or services.

The host supplies a synchronous `authorize(actor_id) -> CanvasScope | None`
resolver. It runs anew inside the existing node mutation barrier on every call.
Return `None` or raise `PermissionDeniedError` to revoke access. Grant changes
must use that same barrier. The host may resolve an existing live capability
with `services.capabilities.capability_for_id(actor_id, host_selected_id)` before
returning its scope. Scope storage and the future Minister's grant mechanism are
intentionally not introduced here. Neither the resolver, actor identity nor scope
is an argument exposed to an automated caller.

```python
from backend.canvas_control import CanvasScope

# Host code, not an Agent-selected rectangle or permission list.
scope = CanvasScope(
    bounds={"x": 0, "y": 0, "width": 3000, "height": 2000},
    operations={"query", "create", "move", "configure", "connect"},
    node_types={"agent", "text", "conversation"},
    relationships={"read"},
)
grants = {actor_id: scope}
control = services.canvas_control(actor_id, grants.get)
view = await control.query()
result = await control.move_card(node_id, {"x": 400, "y": 300}, view["versions"])
```

## Scope and consequential changes

Bounds enclose each card's entire **saved logical rectangle** (`position` plus
`size`), independent of React Flow, zoom or temporary surface sizes. Node types,
bounds, operations, and optional explicit `node_ids` intersect. Empty operation,
type and relationship sets grant nothing. A fixed ID allowlist excludes newly
created identities; creation requires a spatial/type grant instead.

The facade checks both previous and resulting positions, implicitly moved
descendants, parents/owners, deleted equipment, incident connections and their
endpoints. Connection changes also check the existing capability broker's
forwarded resources, upstream actors and owned members. Membership changes also
check connections to the affected containers. Every affected existing object needs a
matching version. Effects outside the grant are rejected before lifecycle work.
Deleting a nonempty container still requires explicitly including its members or
detaching them first. Generated provenance connections cannot be edited here.

Query returns projected cards, stored and derived equipment connections, explicit
`external` flags, and version tokens. Boundary connections include endpoint IDs
without loading external card configuration. A query never grants control of
those endpoints. Equipment can be unequipped with `detach_card`; creating new
equipment bindings, document collection seeds, resource content edits, document
actions, credentials, execution and plugin-specific operations retain their
dedicated host/capability contracts.

## Operations and conflicts

- `query()` and `read_card(node_id)`
- `create_card(request, versions)`
- `update_card(node_id, patch, versions)` for name, position, size, parent and config
- `move_card`, `resize_card`, `set_card_config`, `detach_card`
- `delete_cards(node_ids, versions)`
- `connect_cards(source, target, relationship, versions, direction="forward")`
- `update_edge(edge_id, patch, versions)` and `disconnect_cards(edge_id, versions)`

These return ordinary JSON-compatible data or raise existing domain errors.
Updates return all changed cards, including implicitly moved descendants. Resize
changes saved size; it does not run the frontend's surface-dependent reflow.
Versions contain revision and creation time so restoring the same ID cannot make
an old version valid again. Missing dependencies and stale versions raise
`RevisionConflictError`; reread and reconsider the operation. A new connection
or member can invalidate a destructive/moving operation even if its root's
revision did not change.

Desktop card/edge patch requests additionally accept optional `expected_revision`.
Single deletion accepts the same query parameter; batch deletion accepts optional
`expected_revisions`. Existing callers may omit these for compatibility. The
automation facade always requires the complete affected version set.

## Configuration policy

Use annotations on the existing Pydantic fields:

```python
title: str = Field(default="", json_schema_extra={
    "agentReadable": True, "agentWritable": True,
})
token: str = Field(default="", json_schema_extra={"secret": True})
```

Unannotated and extra fields are neither readable nor writable by automation.
`secret`, `privileged`, and password/write-only schemas override both permissions; `immutable` and Pydantic
`frozen` override writes. V1 supports explicitly annotated scalar fields only;
nested objects/collections remain private even if their parent is annotated.
Use existing document capabilities/actions for richer data. Policy is applied
to creation, patches, validated changes and returned config. A validator cannot
indirectly change a protected field. Config patches retain the existing shallow
merge semantics; field removal and general undo are outside this foundation.

Builtin writable fields are limited to Agent instructions/role, Conversation
description and Legion description/instructions. Resource filenames are readable.
Model/provider selection, Sandbox policy and host bindings, runtime status and
credentials are protected. This adds no plaintext secret storage. Third-party
plugins remain trusted code and must correctly declare their field policies.

## Frontend synchronization

The frontend preserves graph revisions independently from resource revisions,
applies card/edge events directly and rejects older entity updates. Session deletion
versions prevent late responses from resurrecting deleted objects while allowing
restoration with a new creation time. Chunk snapshots
reconcile absent cards and owned descendants within their coverage. Concurrent
events invalidate in-flight refresh/load responses. The existing event stream
adds a process ID and increasing sequence; gaps and heartbeat watermarks trigger
resynchronization, including when a slow subscriber loses the final graph event.
The stream is still a live view, not a durable audit log.

There is no new transaction framework, persistent undo, Blueprint format, layout
engine or ownership hierarchy. Lifecycle transactions, graph batches and template
handling remain owned by the existing services.
