# Runtime state

Runtime state is stored independently from card configuration and Run lifecycle
metadata. `StateStore` is the authority for persisted scopes, per-key values,
merge behavior, revisions, and inherited reads.

Each scope has one stable identity for `(scope_kind, owner_id)`. Scope kinds are
open strings: the built-ins are `world`, `agent`, `session`, and `run`, while
plugins and future task/group/controller primitives may introduce additional
kinds. A `StateContext` orders persisted scopes from broadest to most local.
Resolution walks that stack in reverse and reports both the value and its source
scope. Mutation never resolves: `set`, `patch`, and `delete` always require an
explicit destination scope.

```text
world:default
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
permissions, merge policy, durability, and optional default. `set` uses replace
semantics; `patch` applies the field's `replace`, `merge_dict`, `append`, or
`append_unique` policy.

Each key has an independent revision. Supplying `expected_revision` makes a
mutation compare-and-set; a mismatch raises one `RevisionConflictError`. A
snapshot provides the resolved values, source scopes, and revision metadata for
debugging and future checkpointing. State mutation events are live descriptions
of committed changes; the SQLite state tables remain authoritative.

