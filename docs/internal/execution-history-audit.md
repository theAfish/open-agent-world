# Agent Graph audit and implementation boundary

> Historical record, not current product or deployment instructions. Verify against the current source before use.


Date: 2026-10-13. Scope: the inspected OAW checkout and its internalized MatCreator
Pack. The baseline is the implementation before the execution-history refactor,
after MatCreator backend ownership moved into the Pack. Status labels describe
code and persisted records, not assumptions about upstream capabilities.

## Audit matrix

| Capability | Baseline | Evidence / owner | Gap and implemented change | Current boundary |
| --- | --- | --- | --- | --- |
| Node roles and containment | Partially Supported | Pack `agents/graph_logger.py`; host `plugins/execution_graph.py` | Native orchestrator/planning/execution/step identities were reduced to generic kinds. Added `role`, `execution_id`, `plan_id`; retained separate `parent_id`. | Containment is never a dependency. Tool calls remain detailed execution records; they are not all promoted into independent graph nodes. |
| States and timing | Partially Supported | Pack logger; host `execution_graph._projection` | Native start/end times were discarded; successful chat Runs could paint pending work successful. Added timestamps and stopped success propagation from Run completion. | Failed/cancelled/interrupted Runs reconcile only active `running`/`created` nodes; pending/waiting states remain recorded facts. |
| Durable identity and retries | Partially Supported | Pack step runner, logger and recovery journal | Deterministic logical-step IDs could overwrite attempts. Added distinct attempt IDs, stable logical-plan scope, attempt numbers and explicit retry links. | Legacy records cannot recover attempts that were already overwritten. |
| Replanning | Partially Supported | Pack orchestrator and planning tools | Reused planning nodes hid phase history. Separate planning activities now record explicit retry/replan relationships. | Ordinary later planning is not automatically declared a replan. |
| Within-plan control DAG | Already Supported | Pack `thinking_agent/planning.py`, `_graph_dependency_step_ids` | Existing declared DAG, fan-in/fan-out and ready-node checks were reused. Exact predecessor attempts are recorded. | Planned control and realized scheduler evidence remain distinguishable. Missing historical attempts are not guessed from execution numbers. |
| Cross-execution control references | Partially Supported | Pack step runner and logger | Existing resume lookup could confuse reused logical IDs across plans. Lookup now uses plan/attempt identity. | Resume references stay scoped to the same durable plan; a new plan does not inherit edges merely because names match. |
| Explicit cross-plan data input | Missing | New Pack `agents/consumed_inputs.py`; step runner; history tool | Added `consumed_inputs` with exact successful `step_id`, same-plan `node_id`, or versioned `artifact_ref`. The runner resolves and delivers the referenced value. | Only explicit inputs create lineage. Arbitrary `prior_context` prose does not. |
| Multiple consumers / multiple inputs | Partially Supported | Existing control DAG; new typed logger edges | Control branching existed; data branching lacked a recorded contract. Typed `data` edges now preserve observed deliveries, including recursive child results returned to their caller. | Repeated access is a dependency relationship, not a full event-by-event read audit. |
| Artifact origin and version | Partially Supported | Pack `resources.py`; host Artifact tools | Versioned OAW references existed without producer/consumer graph records. Successful publication records the observed producer; authorized inspection/delivery records access edges. | Inspection proves metadata/result delivery, not file-byte consumption or how the model reasoned. Earlier producers without records remain unknown. |
| Parallel activity | Partially Supported | Pack async Step runners; timestamps; presentation model | Native concurrency existed but the projection discarded time/phase detail. Explicit timestamps and grouping now support bounded concurrent lanes. | Scheduling stays Pack-owned. MatCreator's one-active-Run admission policy is unchanged; the viewer does not create parallel Executions. |
| Historical graph recovery | Partially Supported | Native graph JSON, session checkpoint, recovery journal; host Run lifecycle | History writes now use atomic replacement and monotonic revisions. Recovery restores recorded attempt identity/result; host projections persist complete bounded histories. | No dependence on browser memory. Legacy missing metadata cannot be reconstructed reliably; surviving raw Pack history is republished by a later invocation. |
| Live refresh and stale data | Partially Supported | Host provider publication, events, execution-source SDK | Added full-snapshot semantics, stale-revision rejection, revision-bound pages and atomic frontend assembly. Old visible data survives an incomplete refresh. | Events invalidate reads; they are not dependency deltas. Session/source changes and revocation invalidate access. |
| Large histories | Missing at required scale | Host snapshot limits; Pack projection; frontend history model | Removed the 500-node ceiling. Added 10,000-node / 50,000-edge / 8-MiB bounded projections, paging and collapsed/windowed presentation. | Bounds are explicit, not an unlimited-history guarantee. Oversized worker transport retains durable history and reports unavailable projection without aborting execution. |
| Cross-session result retrieval | Missing | Pack runtime scope; host session authorization | Preserved the existing session boundary. No cross-session lookup API was introduced. | Stable within-session identity is not permission or discovery across sessions. |

## Data chain and ownership

The Pack owns execution and scheduling:

1. [`planning.py`](../../plugins/matcreator/oaw_matcreator_engine/agents/thinking_agent/planning.py)
   validates declared DAGs and explicit consumed inputs.
2. [`step_executor_runner.py`](../../plugins/matcreator/oaw_matcreator_engine/agents/execution_agent/step_executor_runner.py),
   [`graph_logger.py`](../../plugins/matcreator/oaw_matcreator_engine/agents/graph_logger.py) and
   [`recovery.py`](../../plugins/matcreator/oaw_matcreator_engine/agents/execution_agent/recovery.py)
   execute, record attempt evidence and recover durable state.
3. [`resources.py`](../../plugins/matcreator/oaw_matcreator_engine/resources.py)
   uses OAW public tool calls and live authorization for Artifact publication/access.
4. [`native_adapter.py`](../../plugins/matcreator/oaw_matcreator/native_adapter.py),
   [`native_worker.py`](../../plugins/matcreator/oaw_matcreator/native_worker.py) and
   [`native_projection.py`](../../plugins/matcreator/oaw_matcreator/native_projection.py)
   expose bounded persisted history through the existing worker protocol and
   `AgentCapabilityProvider.publish_execution`.

The core owns the provider-neutral contract and authorized observation:
[`backend/plugins/execution_graph.py`](../../backend/plugins/execution_graph.py),
[`backend/capabilities/provider.py`](../../backend/capabilities/provider.py),
[`backend/execution_graph.py`](../../backend/execution_graph.py) and
[`backend/api/node_resources.py`](../../backend/api/node_resources.py). Run lifecycle
storage remains authoritative for published snapshots. Tied creation timestamps
use insertion order rather than random UUID order. No MatCreator engine module
is imported into these services.

The frontend owns presentation only:
[`executionSources.ts`](../../frontend/src/plugins/executionSources.ts) exposes
authorized reads; `readExecutionHistory` in
[`ExecutionGraph.tsx`](../../frontend/src/cards/ExecutionGraph.tsx) assembles pages;
[`executionHistory.ts`](../../frontend/src/cards/executionHistory.ts)
indexes typed edges and builds folded history/focus presentations;
[`ExecutionGraph.tsx`](../../frontend/src/cards/ExecutionGraph.tsx) renders them.
History adjacency is not an edge. Folded groups and bounded lanes do not rewrite
source nodes, dependencies, statuses or scheduling.

## Minimal public contract change

Plugin API **1.31** adds node role/phase identity, sequence, start/end timestamps,
attempt/retry/replan fields and versioned Artifact references; `ExecutionEdge`
distinguishes `control`, `data`, `retry` and `replan`. Snapshots add history identity,
revision and full/page transport metadata. Existing sources remain compatible:
omitted `edges` promotes only declared `dependency_ids` into control edges;
an explicitly supplied edge array, including an empty array, is authoritative.

The existing read endpoint gains optional `limit` and opaque `cursor`. Every page
rechecks live authorization. A changed snapshot returns 409 rather than mixing
revisions. Providers continue publishing full replacements; no patch merge or
second execution model was added. See the [public contract](../developers/execution-graph.md).

## Evidence and validation limits

Host regression coverage exercises persisted 1,200-node histories with sparse
cross-execution fan-in, exact page assembly, missing-endpoint references, revocation,
stale revisions, tied Run timestamps, terminal-state semantics, byte limits and
actual application shutdown/reopen. Pack tests use its real logger, plan schema,
recovery journal and runner input-delivery path with local controlled responses;
these are not proof of live scientific services or scientific correctness.

The initial audit justified data-contract changes before the UI migration:
chronology alone could not provide the requested provenance. The implementation
records new evidence prospectively and preserves unknowns in old history. This
document distinguishes those checks from packaged-desktop and live-service validation.

### Completed local verification

- Backend + Pack focused suite: **67 passed, 1 skipped**. Includes the real
  Pack engine and ADK against a deterministic local model endpoint, checkpoint
  reload, recursive tools, delivered inputs and final snapshot revisions.
- Frontend and wheel-camera regression suite: **25 passed**; TypeScript and
  production Vite build passed. The build still reports large-chunk advisories.
- Playwright against the real fixture API and Run store: **3 passed**. Exercises
  sparse E01/E09/E17 → E20 dependencies, source/back navigation, 120 executions
  and 1,200 nodes, page assembly, dynamic edge/status updates, stable selection,
  browser reload, session switching and retained prior history between Runs.
- Inspected light/dark screenshots at 300, 400, 500 and 760px, plus composed
  Workspace, sparse local DAG and large-history views. The 1,200-node case keeps
  fewer than 25 history rows mounted, at most three rails, and no horizontal
  overflow. Local pan/wheel gestures at a 0.75 outer canvas scale leave the outer
  viewport and persisted card position unchanged.
- No installed-desktop, real-model or live scientific-service acceptance was
  performed. These are bounded interaction checks, not a universal frame-rate
  benchmark for arbitrary graph shapes.
