# Execution history and dependencies

Plugin API **1.31** extends the execution-source contract with stable history
identity, ordering, timestamps and typed relationships. Producers own execution
and dependency semantics. The built-in **Agent graph** (`core.execution-graph`)
owns project activity history and dependency exploration. Connect graph to source
using **Observe execution** (`core.execution-source`); this grants observation,
never tools or execution control.

## Built-in graph presentation

The viewer presents Agent task attempts in chronological order. Retries and
independent tasks remain separate, even when they share an Agent. Tool calls
owned by an attempt appear in its details; tools with no known owner remain
visible. All authorized connected sources appear together with namespaced IDs;
the source selector and session selection restrict that view.

Click an operation to open its inline details, or use search and the arrow keys
to locate it. Updates preserve the reading position and selection. The viewer
does not automatically fold completed attempts or move contained work next to
its parent. Four reusable routing positions represent overlapping work, not
permanent Agent identities or branches.

Selecting an operation highlights recorded upstream and downstream relations.
Workstream guides and shared colors or positions do not imply dependencies.
Long or conflicting connections become counted, navigable references at both
endpoints. Missing endpoints remain explicitly unavailable. Opening a detail or
following a reference does not change execution.

Mark runtime-only containers as `record_type: context` and unexecuted plan
definitions as `record_type: planned`. Plans remain available in planner details;
`realized_by` links them to attempts. Publish sanitized tool names, timestamps and
outcomes as evidence, excluding private arguments and conversation traces.
Cross-event tool dependencies retain their original endpoint evidence when
presented at the owning attempt; internal tool edges do not become artificial
self-dependencies.

For viewer changes, run `npm run test:e2e:graph` from `frontend`. Keep per-run
screenshots and timing results outside this API reference.

## Provider contract

An Agent runtime publishes full snapshots during its current invocation:

```python
await provider.publish_execution(config.agent_id, context.run_id, {
    "history_id": "session-history-identity",
    "revision": 17,
    "title": "Build and inspect a structure",
    "phase": "running",
    "nodes": [
        {"id": "build-attempt-1", "kind": "plan", "role": "step",
         "execution_id": "execution-1", "plan_id": "plan-1", "sequence": 0,
         "label": "Build", "status": "success", "attempt": 1,
         "started_at": "2026-10-13T08:00:00+00:00",
         "finished_at": "2026-10-13T08:01:00+00:00",
         "artifact_refs": ["oaw:artifact:collection:version"]},
        {"id": "inspect-attempt-1", "kind": "plan", "role": "step",
         "execution_id": "execution-20", "plan_id": "plan-2", "sequence": 1,
         "label": "Inspect", "status": "pending", "attempt": 1},
    ],
    "edges": [
        {"id": "build-before-inspect", "source": "build-attempt-1",
         "target": "inspect-attempt-1", "kind": "control"},
        {"id": "structure-input", "source": "build-attempt-1",
         "target": "inspect-attempt-1", "kind": "data",
         "artifact_ref": "oaw:artifact:collection:version",
         "evidence": "explicit input binding"},
    ],
})
```

The host validates current invocation ownership and persists the snapshot with
the Run. No viewer needs to exist. `history_id` must identify one durable history
scope; `revision` must increase whenever that history changes. Delayed writes
with an equal or lower positive revision are ignored within the same Run and
history. Legacy providers may omit the revision or use zero; the host sequences
these writes. Default `retention: snapshot` replaces the complete snapshot,
including an explicit empty one. Providers can opt into `retention: history`:
the host durably unions nodes and edges by ID, including prior turns with the
same history, source, provider and conversation/session. Omitted records remain;
updates to existing IDs replace their fields. The union is revalidated against
transport limits before persistence. A `page` or `next_cursor` publication is
rejected in either mode. Custom read-only sources own their own retention.

`ExecutionNode` retains `id`, `label`, `kind` (`agent`, `plan`, `tool`), `status`,
`parent_id`, `dependency_ids`, `detail` and `result`. Additive fields are `role`,
`execution_id`, `plan_id`, `sequence`, `started_at`, `finished_at`, `attempt`,
`retry_of`, `replan_of` and `artifact_refs`. Node IDs must stay stable across
updates; separate attempts need separate IDs. `sequence` is chronological order,
not an edge. Unknown timestamps and relationships should remain absent.

Optional activity fields are `actor_id`, `actor_label`, `workstream_id`,
`workstream_label`, `operation`, `record_type` (`activity`, `context`, `planned`)
and `realized_by`. Actor identity and task identity are different: one Agent can
own multiple tasks. A task must keep its identity across retries. The operation
field supplies a presentation hint such as `edit`, `plan`, `run` or `merge`;
it does not authorize any action or establish a relationship.

`ExecutionEdge` has a unique `id`, `source`, `target`, `kind` and optional
`artifact_ref` / `evidence`. Its direction is source to consumer or successor.

| Relationship | Meaning |
| --- | --- |
| `control` | The target must wait for the source. |
| `data` | The target consumes an explicitly identified source result. |
| `retry` | The target is a subsequent attempt of the source. |
| `replan` | The target is replanning explicitly triggered by the source activity. |
| `fork` | The source explicitly launched the target task; not a control/data prerequisite. |
| `merge` | The source result was explicitly accepted/applied at the target operation. |

Legacy `dependency_ids` means **control dependency only**. If `edges` is omitted,
the host promotes those declared dependencies into typed control edges. An
explicit edge array (including `[]`) is authoritative. A provider may expose
both shorthand and matching control edges for compatibility. Parentage expresses
containment, and overlapping timestamps show observed concurrency; neither
creates control or data dependencies. A shared execution number, neighboring
history row or artifact label is not provenance evidence. Explicit edges may
retain unresolved endpoints; viewers should show unavailable references rather
than silently inventing nodes. A successful status is never evidence of merge.

Full snapshots are bounded to **10,000 nodes, 50,000 edges and 8 MiB**. Providers
that exceed this must bound their own projection and set `truncated: true`.
The host no longer silently clips rich projections to their first 500 nodes.
Artifacts remain immutable references; the projection is not artifact content or
an authority to access them.

## Reads, pagination and live updates

`GET /api/nodes/{viewer}/execution-sources/{source}` remains a full read by default.
Optional `limit` (1-2,000) and opaque `cursor` parameters select a transport page.
Responses include:

```text
mode: full | page
history_id: string
revision: non-negative integer
total_nodes: total projected node count
next_cursor: string | null
nodes, edges, title, phase, run_id, previous_run_id, truncated
```

Assemble every page before replacing the visible graph. An edge travels with its
target node's page; edges with unresolved targets travel on the final page. Thus
page assembly preserves each original edge exactly once, including distant
cross-execution references. Cursors bind to the entire current projection. If
any content changes, the next read returns **409**; discard the incomplete
assembly and restart from the first page while keeping the prior visible graph.
Do not append a new revision onto an old one. Every page rechecks the viewer's
live connection and session, so revocation takes effect immediately.

Publication emits `execution_updated` carrying Run and conversation/session IDs,
plus `history_id` and `revision` in its payload. Events are invalidations, not
graph deltas. The authorized read is the source of truth; polling can recover
missed events. Keep the existing graph during a refresh, and clear it when the
source/session changes or authorization is revoked. A request completing late
must not overwrite a newer selection or request.

## Host Run fallback and recovery

Without a detailed provider projection, every `core.agent` exposes all its Runs
and descendants in the selected session and current provider. Summoning libraries
expose their actual attempts and follow-up turns. Private summon context IDs are
resolved through Run ancestry. Summoning adapters observe only currently
connected libraries; removing a library connection removes its records.

Run creation order remains deterministic even when timestamps tie. A newer
rejected send cannot replace a still-active Run. While a new Run has no detailed
projection, the latest detailed graph from the same source, session and runtime
provider remains visible: `run_id` and `phase` describe the current Run and
`previous_run_id` identifies the retained graph. Its first full projection
replaces the retained one for snapshot providers. History providers keep earlier
operations through their persisted union.

A successful conversational Run does **not** mark planned or active internal
nodes successful. Provider states remain authoritative. After Run failure,
cancellation or interruption, only nodes recorded as `running` or `created` are
reconciled to that terminal state. `pending`, `waiting`, completed and replan
states stay unchanged. This prevents plan approval and checkpoint turns from
claiming unexecuted work is complete.

Snapshots persist in Run lifecycle storage and survive backend restarts. Legion
templates contain connections and presentation, never runtime history. The
MatCreator Pack translates its own persisted execution history at its public
runtime boundary; no MatCreator model enters the core.

## Custom sources

```python
from open_agent_world.plugin_api import NodeExecutionSource, ExecutionSnapshot

def read_execution(context):
    value = context.state.get()["value"].get("execution", {})
    return ExecutionSnapshot.model_validate(value).model_dump(mode="json")

# NodeTypeDefinition:
execution_source = NodeExecutionSource(read=read_execution)
```

Registration adds `core.execution-source`. The reader must return a full
snapshot; the host applies pagination. Resource dispatch holds the mutation lock
and cancellation lifetime until the read finishes. Handlers must be read-only,
bounded and credential-free. `context.state` is bound to the source's declared
shared/session namespace.

Custom viewers declare `core.execution-viewer` and use frontend SDK
`host.executionSources.list()`, `.read(sourceId)` and `.subscribe(listener)`.
Source connections and progress notifications both require rereads. These
builder/Workspace endpoints are not exposed on published application surfaces.
