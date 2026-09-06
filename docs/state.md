# Runtime state

Runtime state is stored independently from card configuration and Run lifecycle
metadata. `StateStore` is the authority for persisted scopes, per-key values,
merge behavior, revisions, and inherited reads.

Each scope has one stable identity for `(scope_kind, owner_id)`. Scope kinds are
open strings: the built-ins are `world`, `legion`, `agent`, `session`, and `run`, while
plugins and future task/group/controller primitives may introduce additional
kinds. A `StateContext` orders persisted scopes from broadest to most local.
Resolution walks that stack in reverse and reports both the value and its source
scope. Mutation never resolves: `set`, `patch`, and `delete` always require an
explicit destination scope.

```text
world:default
  -> legion:<legion card id> (for members)
  -> agent:<agent id>
    -> session:<context id> (when present)
      -> run:<run id>
```

Every Run receives a fresh Run scope, while all Runs for one Agent share its
durable Agent scope. The Run scope is always last in the invocation's
`StateContext`. Provider SDK sessions are unrelated implementation details and
remain inside their runtime provider.

Schemas are registered through `PluginRegistry.register_state_schema`, including
the built-in `core.world`, `core.agent`, `core.session`, and `core.run` schemas.
A field declares its value type, allowed scope kinds, read visibility, write
permissions, merge policy, and optional default. All `StateStore` values are
durable. `set` uses replace semantics; `patch` applies the field's `replace`,
`merge_dict`, `append`, or `append_unique` policy.

Each key has an independent revision. Supplying `expected_revision` makes a
mutation compare-and-set; a mismatch raises one `RevisionConflictError`. A
snapshot provides the resolved values, source scopes, and revision metadata for
debugging and future checkpointing. State mutation events are live descriptions
of committed changes; the SQLite state tables remain authoritative.


## Node documents

Plugin API 1.2 adds `node_document:<node_id>` scopes using `core.node_document`.
These resource-like documents are not inherited by Agent/Legion scope stacks.
Agents access them only through currently granted document capabilities. The host
validates against the node's declared Pydantic model and uses one revision for the
whole document, allowing tasks and dependencies to change atomically. Node deletion
cleans up its document in the same database transaction. See the
[Task Board plugin](../plugins/task_board/README.md) for the first implementation.

Plugin API 1.3 adds a host-owned `execution` field to this scope. It records batch
status and recent attempt metadata separately from the plugin's editable document.
Execution history is not copied into Legion templates; document settings and
internal executor references are copied and remapped. Node deletion removes this
history alongside the document. Canvas undo restores the saved document, not the
host execution history. Durable Run records remain separate.

Run scopes now also preserve the latest provider text in `output_text`. The
structured `result` field remains available independently to providers/plugins.
