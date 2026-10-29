# Temporal Braid: model audit and implementation

> Historical record, not current product or deployment instructions. Verify against the current source before use.


Date: 2026-10-15. Scope: OAW's built-in Agent graph and the existing execution-source
contract. This supersedes the presentation mapping in the October 13 audit;
provider scheduling, persistence and authorization remain authoritative.

## Observed contracts

| Dimension | Current evidence | Viewer mapping and limit |
| --- | --- | --- |
| Agent identity | `ExecutionNode.actor_id` / `actor_label`; host Run fallback records `run.agent_id`; MatCreator's step runner publishes the executing step Agent identity. | Stable actor color on nodes and labels. Neither workstream IDs nor observation-source names substitute for missing actors. |
| Execution | `execution_id`, `plan_id`, `parent_id`; MatCreator publishes orchestration/execution containers as context. | Metadata and explicit containment. They do not imply dependency, chronology or fixed lanes. |
| Event/history | Full snapshots stored in Run lifecycle; `execution_updated` and Run events invalidate authorized reads; `history_id`, revision-bound pagination and retention policies already exist. | One task-attempt vertex with current recorded state. Tool evidence contracts onto its explicitly recorded owner. No invented status-transition events or additional persistent state. |
| Time and parallelism | `started_at`, `finished_at`, `sequence`; scheduler edges and observed intervals. | Start chronology, sequence fallback, unknown time indication. Overlapping recorded intervals compete for distinct routing space even for one Agent/workstream. Order does not create edges. |
| Workstream | Explicit `workstream_id`, stable across an attempt's retries; one Agent can execute multiple streams. | Thin continuity guide between successive non-overlapping records in that stream. A guide is not causal and is excluded from dependency traversal. Lane positions can be reused by other work. |
| Dependencies | Typed `control`, `data`, `retry`, `replan`, `fork`, `merge`; omitted edge arrays may promote legacy `dependency_ids`, explicit empty arrays are authoritative. | Only recorded edges enter causal focus. Parallel relation types share geometry, retaining each record, artifact reference and evidence. |
| Cross Execution | Exact endpoints may carry different execution IDs; consumed inputs/graph logger record cross-plan data deliveries. | Normal typed edges, folded when distant. A connection between independent source namespaces is never guessed. |
| Acceptance | `merge` requires an explicit producer record. `fork` records delegation; returned data does not prove acceptance. | No merge inferred from success, containment or a child result. The synthetic fixture's merge is explicitly declared. |
| Status | Host status is an open string. MatCreator includes waiting, blocked and needs_replanning; host failure reconciliation only changes recorded active states. | Shapes/glyphs distinguish known state families. Unknown labels remain visible. No frontend status propagation or scheduler changes. |
| Unavailable history | Truncated snapshots and unresolved endpoints are valid. Native unavailable data must not be published as an empty graph. | Existing refresh/error behavior retained. Disabled endpoint references remain visible; no placeholder nodes or speculative joins. |

Inspected paths: `backend/plugins/execution_graph.py`, `backend/execution_graph.py`,
`frontend/src/plugins/executionSources.ts`, `frontend/src/cards/ExecutionGraph.tsx`,
MatCreator's `native_projection.py`, `agents/graph_logger.py`, and
`agents/execution_agent/step_executor_runner.py`. Public contract details remain in
[execution-graph.md](../developers/execution-graph.md).

## Layout decisions

The previous renderer connected independent chronological neighbors and rendered
blocked edges as disconnected stubs. Workstreams could override occupied lanes,
and allowing a cable to pass through another event in the same stream visually
implied an unrecorded intermediate dependency. Long data references also reserved
lanes as though they were ongoing control flow.

Outgoing cables and node glyphs use the source/current Agent's original six-color
palette, including during hover and selection. Focus only changes line weight and
opacity. Completed vertices use 8px solid dots in fixed 12px SVG cable openings;
row hover or keyboard focus grows only the dot to fill the gap. Each graph has a
unique mask, with holes bounded by the visible rows. This keeps gaps clear even
when a row is dimmed and avoids moving cable endpoints on hover. Running rings breathe; waiting
vertices are single clock faces; failure/rework use bolder cross and
diamond/exclamation glyphs. The 18px clock is centered within its 15px vertex
using flex alignment, fixing the implicit grid's 1.5px right/down offset.
Waiting hands rotate independently on 12s/144s CSS cycles around a fixed dial
center; paused and blocked clocks stay still. Reduced motion disables both
breathing and hand rotation.

The replacement allocates four reusable positions from explicit continuity,
structural predecessors and observed durations. Existing node allocations remain
stable across updates, selection and late insertions. Serial successors can
inherit their predecessor's lane despite a later outgoing reference; concurrent
events cannot inherit an occupied overlapping predecessor. Data edges do not
reserve historical lanes. Route checks inspect every intermediate vertex,
including vertices in the same workstream. Fork curves depart near the source;
join curves approach near the target. Routing uses vertical/horizontal rails and
tangent quarter-circle SVG arcs, with a 14px maximum radius bounded by available
space. Folded and self-referential relations remain endpoint references.
Local cables reuse an inner rail once its previous cable has ended, using two
opposed circular arcs for the S transition. Forks can similarly start on an
inner rail and move outward before later work occupies it. Compaction checks
both row crossings and inter-row cable occupancy across the remaining corridor;
it never moves event vertices or adds a dependency. The `compact` fixture uses
recorded task intervals and explicit fork/merge edges to exercise both cases.

Long, reverse, self and obstructed connections remain exact endpoint references.
Both endpoints expose a count and navigation. Hover and selection only style
existing cables; they do not reveal folded references, even with both endpoints
visible. The previous automatic reveal applied to the entire transitive focus,
so hovering an ancestor could add three unrelated-looking bypass lines at
6/11/16px to the left of the normal 24px first rail. That path generator and its
focus-dependent selection have been removed. Folded references have no SVG
stubs. Neighboring unrelated events have no cable. A complete directed traversal
highlights ancestors and descendants separately, excluding siblings and shared
workstream guides. Node virtualization, atomic paging and viewport anchoring
continue to use the existing component.

## Validation

- Focused frontend suite: 32 tests covering source identity, real edge evidence,
  three-way fan-in, parallel work, handoff, failed attempts/retries, reverse result
  returns, missing endpoints, custom statuses, stable append/backfill and cycles.
- Layout/traversal stress case: 10,000 task vertices and 49,985 typed edges,
  checked against a 5-second regression ceiling. This measures local computation,
  not end-to-end runtime or browser frame rate.
- Browser fixtures: 300/500/1000px in light and dark, native MatCreator adapter
  projection, live changes, paging, custom states, causal focus, Home/End and
  reduced motion. Actual SVG curve samples must not intersect unrelated vertices.
- Compact-routing follow-up: 29 graph/layout tests and 13 browser scenarios,
  including inner-rail release, occupied-rail rejection, stable cable paths
  after append, both S-bend directions, and TypeScript validation.
- Focus-visibility follow-up: reproduced three extra descendant/reverse/self
  cables on ancestor hover. The regression now verifies unchanged SVG endpoints
  and paths on hover, no left-side bypass on selection/scroll, and working folded
  relation navigation. All 30 graph/layout tests, 14 browser scenarios and
  TypeScript passed. Three browser cases initially could not load Vite scripts
  (`net::ERR_NO_BUFFER_SPACE`); their targeted rerun passed without code changes.
- The 1,200-event browser scrolling scenario bounds mounted rows below 25 and
  records two-animation-frame sampling latency in
  `.outputs/project-history/scroll-metrics.json`. This is not a single-frame FPS
  measurement. Screenshots live beside that file.
- A second browser stress case uses 10,000 vertices / 49,985 relations. Local
  Edge sampling mounted at most 17 rows and 17 curves, with no horizontal overflow;
  two-frame p95 was approximately 35 ms. Full 9,999-ancestor focus also completed. Results are
  in `.outputs/project-history/contract-limit-metrics.json`; the test uses a
  conservative 250 ms two-frame interaction regression ceiling.
- Host execution-history regression tests: 17 passed. The Pack's full engine
  orchestration test was skipped because `OAW_MATCREATOR_MANAGED` was not enabled;
  the separate native adapter/projection browser fixture passed. Production build
  and TypeScript passed (existing large-chunk advisory). The isolated host browser
  scenario passed publication, persisted history union, navigation and reload.

Fixtures use real public/native data shapes and local host persistence. These
checks do not claim new remote model runs, scientific acceptance, installed
desktop behavior or universal performance across hardware.
