# Relationship maps and graph audit

[Architecture](architecture.md) / [Canvas performance](canvas-performance.md)

OAW uses one relationship-map implementation for its official knowledge and data
Packs. The world editor, document study surface and task semantics retain their
own representations. This audit covers all official source Packs under `plugins/`,
the host frontend, the public frontend SDK, package manifests and runtime loading.

## Migration matrix

| Surface and purpose | Previous implementation and data | Decision and current location |
| --- | --- | --- |
| MatCreator Know-Do Graph, including Skill/Toolset knowledge | Nested React Flow, large circular DOM nodes, custom SVG curves, synchronous pairwise force layout; knowledge IDs, typed directed relations, revisioned document actions | **Migrate.** `plugins/matcreator/frontend/index.tsx` adapts to host `NetworkMap`. Keep server search, pagination, inspection, editing, review, assimilation and resource ownership. |
| Knowledge Base published entity network | Another nested React Flow, duplicate layout and SVG edge component; entity UUIDs, relation UUIDs and `source_id`/`target_id`; graph written only by approved facts | **Merge.** `plugins/knowledge_base/frontend/GraphMap.tsx` is now a data adapter. Published facts, schemas, drafts, provenance and HTTP contracts stay unchanged. |
| Visualization graph card | Canvas 2D graph branch with type-based clusters and golden-angle positions; graph Dataset or table-derived endpoints | **Migrate.** `plugins/visualization/frontend/index.tsx` uses `NetworkMap`; `plot.ts` keeps supplied edge IDs and assigns view-only IDs to table-derived edges. Other charts retain Canvas 2D. |
| World canvas, Legion/container relations, capability connections | React Flow with persisted card geometry, ports, selection, resizing, connections, undo and permission semantics | **Retain React Flow.** `frontend/src/canvas/WorldCanvas.tsx` is an editor, not a force-directed knowledge map. |
| Library document study surface | `plugins/library/frontend/StudyCanvas.tsx`: positioned rich annotations and SVG document/page branches | **Retain.** Reading order, annotation geometry, content editing and persistence are the primary interactions. |
| MatCreator and generic task boards | DOM task/status columns and dependency data; backend task DAG and continuation/review state | **Retain.** `plugins/matcreator/frontend/TaskBoard.tsx`, `plugins/task_board/frontend/`, `open_agent_world/task_graph.py`; no replacement of task execution or acceptance semantics. |
| Structure Viewer/Atomsculptor atom and bond networks | MatterViz/Three and scientific structure data | **Retain.** These are 3D scientific models, not knowledge relations. |
| XRD computation and backend graph models | Python scientific/computational graph structures and domain algorithms | **Retain.** No frontend knowledge renderer to merge. |
| World terrain and contour layers | Host WebGL terrain with card-derived geography | **Retain.** Independent world coordinates, ownership and rendering budget. |
| Agent Barracks, Card Factory, Codex, Literature, Skill Packages, SQLite and other official views | Cards, forms, lists, code/content views or data-source providers; no additional standalone relationship renderer | **No migration.** Their capabilities and data contracts are unchanged. |

Removed the two `graphLayout.ts` implementations, two `MapEdge.tsx` components,
MatCreator `mapGeometry.ts`, their obsolete geometry/layout tests and graph-only
CSS. Removed Visualization's old graph renderer from `ChartCanvas.tsx`.
`useNestedFlowGestures` remains a public SDK export for existing third-party
Packs; official relationship maps no longer use it.

## Dependencies and version decision

Checked on 2026-10-09 against package metadata, installed source/types and
upstream releases. Sigma **4.0.0 was released on 2026-10-08**; it is no longer
accurate to describe the current release as beta. It is a newly rewritten,
WebGL2-only renderer. This migration pins the mature Sigma 3 implementation to
avoid coupling the rollout to that new major release. See the
[official Sigma releases](https://github.com/jacomyal/sigma.js/releases).

| Dependency | Version | Responsibility |
| --- | --- | --- |
| `sigma` | 3.0.3 | WebGL nodes, directed edges, camera, labels and visibility |
| `@sigma/edge-curve` | 3.1.0 | Official curved arrow program, including parallel relations |
| `graphology` | 0.26.0 | Mutable directed multigraph; opaque node/edge identity, loops, neighbors and incremental updates |
| `graphology-layout-forceatlas2` | 0.10.1 | Bounded ForceAtlas2 with Barnes-Hut, inside a module Worker |
| `graphology-communities-louvain` | 2.0.2 | Deterministic inferred communities when there is no explicit topic |
| `d3-contour` | 4.0.2 | Small density contours in the same Worker; already present transitively, now declared directly |
| `@types/d3-contour` | 3.0.6 | Development-only TypeScript declarations |

The [Graphology ForceAtlas2 API](https://graphology.github.io/standard-library/layout-forceatlas2.html)
provides the layout algorithm; OAW's single module Worker also performs community
detection and contours so none of those computations runs on the UI thread.
Sigma's current WebGL layer/contour package targets v4, so this implementation uses
a small Canvas 2D contour layer with the **same Sigma projection**. It does not
introduce a second camera or a custom general-purpose WebGL renderer.

React Flow stays installed for the world editor. D3 dependencies used by React
Flow or MatterViz stay installed; no direct D3 force or Cytoscape renderer was
found to remove. No Python Pack dependencies or backend persistence migrations
are required.

## Ownership and rendering boundary

```text
Pack-owned document actions / resources / Dataset
                    |
          NetworkData adapter (IDs unchanged)
                    |
       @oaw/plugin-api: NetworkMap (lazy)
          |                         |
     React controls            MapEngine
  search, type, topics,       Graphology + Sigma
  details and callbacks       one graph + camera
                                    |
                           layout.worker.ts
                      FA2 + Louvain + contours
```

`frontend/src/graph/types.ts` defines the small view contract. `kind` is a
knowledge type, while `topic` is an independent semantic grouping. Explicit
topics take precedence; otherwise Louvain operates on an undirected proximity
projection, and recurring tags or a representative title name the community.
The actual relation graph retains direction, labels, parallel edges and loops.
Relations whose endpoints are outside a loaded page are omitted from that view,
never deleted from the document.

`model.ts` diffs nodes and edges in place. A label edit does not rebuild the graph
or rerun layout. A topology/grouping change sends a new bounded Worker request;
the previous request is terminated and stale replies cannot update the view.
Previously placed nodes are fixed, including user-dragged nodes. New neighbors
start near saved anchors. ForceAtlas2 determines the global layout from graph
relationships; topic grouping never places communities into rows or rescales them
into boxes. Incremental updates preserve existing coordinates. Explicit **Arrange** discards
the current view positions and recomputes them.

Positions are a session-only LRU cache: at most six map keys and 12,000 coordinates
per key. There is no persisted layout migration, server cache, background service
or document write from navigation. Cache keys contain the owning card/source so
unrelated cards do not share positions.

## Continuous interaction

The global map, community islands and node focus use one coordinate system.
Zoom changes label density, relation detail and aggregate opacity. Eight opacity
steps blend spatially separable groups larger than 20 entries into small aggregate markers; clicking
one focuses its existing members. Cross-community aggregate links retain global
connectivity. Interwoven topics with overlapping centers keep their stars instead
of collapsing into overlapping markers. Selected neighborhoods remain available through aggregation.
No zoom event starts a layout Worker or reconstructs the domain graph.

Contours follow node density and within-community relationships instead of
rectangular containers. Unlinked singleton nodes have no artificial contour.
Type colors are independent of topic grouping: Capability is blue, Procedure
purple, Heuristic yellow, Limitation red and Memory light gray. The shared renderer
recognizes all five; MatCreator's existing backend still accepts its original four
types (it has no separate Limitation type). Quiet colors use the host's
light/dark surface tokens; there are no perpetual simulations or glow effects.
Reduced-motion preferences disable camera/layout interpolation.

Clicking a node frames its nearest neighbors and highlights its neighborhood,
then calls the Pack's existing inspector. The bounded back stack restores camera
and selection, including when following a relationship first loads a missing
neighbor from the server. That focus waits for the incremental layout to settle.
Hover updates only the caption and cursor. Its hit test transforms the pointer
once to graph coordinates, rather than projecting every node and uploading all
GPU buffers again for each crossed node. This change was driven by a measured
10,000-node hover bottleneck (about 8 FPS before, about 60 FPS after).
Double-click/Expand delegates server expansion to the Pack. The generic data
card gets a relationship inspector when no business inspector is supplied.
Local search finds loaded nodes; server-backed search and pagination remain
explicit Pack operations. MatCreator disables the map-local search input and keeps
one **Search knowledge** entry point, covering all stored entries including content,
summaries and aliases, rather than only the currently loaded nodes.
Local filters preserve positions and camera context;
Fit can frame the filtered results.

Pointer handling compensates for the outer world canvas's CSS scale before
passing coordinates to Sigma. Left-drag moves a node or pans empty space,
middle-drag pans, Shift-drag selects, the wheel zooms at the pointer, and two
pointers pinch. Controls own their bubbling events. Keyboard arrows, +/- and
Home provide pan, zoom and fit; search results provide keyboard-accessible entry
selection. Canvas focus does not commandeer text inputs in business inspectors.

## Lifecycle, runtime Packs and release loading

Unmount terminates the Worker, cancels animation frames, removes event listeners,
disconnects resize/theme observers, calls `sigma.kill()` to release WebGL, and
clears the transient graph. Changing `graphKey` uses the same cleanup. Errors
show retry controls; a WebGL initialization failure leaves a searchable entry
list so the Pack's details remain reachable.

The shared component is exported from both `src/plugins/sdk.ts` and the standalone
Pack bundler's host-runtime allowlist. Packs import it from `@oaw/plugin-api` and
do not bundle their own Sigma or Graphology. The host loads the renderer lazily;
Vite emits the Worker as a separate same-origin asset. Development dependency
prebundling includes the Worker algorithms to avoid an unexpected first-use reload.

MatCreator and Visualization whole frontend modules, and Knowledge Base's graph
adapter, were built and imported through the standalone SDK bundler with no
duplicated graph dependencies. The complete Knowledge Base workspace still has
pre-existing private host imports (model settings, Markdown and deployment APIs),
so it is validated as a bundled official view, not claimed to be an independently
installable source Pack. This refactor does not broaden that existing boundary.

## Reproduce verification

From `frontend/`:

```sh
npm test
npm run build
npm run build:pack-sdk
npm ci --dry-run --ignore-scripts
node scripts/run-e2e.mjs e2e/network-map.spec.ts
node scripts/run-e2e.mjs e2e/matcreator.spec.ts e2e/visualization.spec.ts
node scripts/run-e2e.mjs e2e/knowledge-background.spec.ts e2e/knowledge-interaction.spec.ts
npm run dev -- --port 5189
```

Open `http://127.0.0.1:5189/?network-map` for the development-only preview. It
supports real `NetworkData` JSON, 240/1,000/10,000-node fixtures, theme changes and
repeated mounting. Then run:

```sh
node scripts/benchmark-network-map.mjs ../.outputs/graph-audit/real-knowledge.json
```

The snapshot used for this audit was read from the current development profile
without modifying its SQLite database: 54 MatCreator entries and 44 relations.
Only map IDs, labels, kinds and tags were exported; content, Skill files and
credentials were not copied. The benchmark writes JSON and PNGs into
`.outputs/graph-audit/`, which are local artifacts rather than repository fixtures.

The benchmark records layout Worker time, load-to-ready time, RAF intervals
during pointer/wheel interaction, hover intervals, event-to-next-frame time,
search response, CDP JavaScript heap after GC and eight mount/unmount cycles.
Frame rates are **browser RAF measurements**, not proof of GPU/compositor frame
delivery. Heap numbers include the preview and its bounded session caches and do
not measure total GPU/process memory. Synthetic data uses eight topics, mixed
knowledge types, directed local links and cross-topic relations.

Final local measurements (Chrome 155.0.8059.39, Windows, 1440×1000, DPR 1,
28 reported logical processors; 2026-10-09):

| Graph | Nodes / edges | Worker layout | Load to ready | Pan / hover RAF | Search | JS heap after GC |
| --- | --- | --- | --- | --- | --- | --- |
| Current knowledge snapshot | 54 / 44 | 23.6 ms | 398 ms | 60 / 60 FPS | 34 ms | 6.49 MiB |
| Synthetic | 1,000 / 2,953 | 839.5 ms | 1,805 ms | 60 / 60 FPS | 53 ms | 8.76 MiB |
| Synthetic | 10,000 / 29,953 | 7,393.6 ms | 9,458 ms | 60 / 60 FPS | 157 ms | 30.04 MiB |

These measurements include the natural-layout and five-color follow-up. The
earlier grid-packed baseline is saved as `performance-grid-baseline.json`.
Pan frame p95 was 16.8 ms; hover p95 was 16.9–17.0 ms. No interaction
long tasks above 50 ms were recorded in the pan/wheel sampling window. The first
10,000-node layout is still a multi-second operation, and the UI shows its
progress rather than running an endless simulation. Loaded JS heap includes the
graph, buffers, preview state and session caches; it is not total process memory.

All eight unmount samples had **zero active Workers, zero live WebGL contexts and
zero canvases**. From the second sample, DOM nodes stayed at 106 and event
listeners at 166; post-GC heap ranged from 10.50 to 10.72 MiB, below the first
sample's 10.76 MiB. This found no obvious retained-renderer leak over eight cycles;
it is not a long-duration soak test.

## Validation record and limits

The final measurements and screenshots are recorded in `.outputs/graph-audit/`.
The related browser suite is run in two groups because the existing E2E launcher
has a 120-second total deadline. These groups passed 8 + 8 tests. Four additional
production-mode browser tests exercised MatCreator, published Knowledge Base data
and Visualization using the packaged JavaScript/CSS/Worker assets served by the
production launcher; the corresponding asset requests returned HTTP 200.

The natural-layout follow-up reran eight relevant browser tests and the final
zoom/search/filter/unmount test after adapting aggregation to interwoven topics.
Frontend regression: all 158 files / 1,022 tests passed with six test workers.
Backend regression: 31 passed and one skipped (a native Python with ASE must be
explicitly provisioned for that scientific execution test). SDK declaration build,
standalone graph-adapter import checks and lockfile dry-run installation passed.
Existing API contracts, document revisions, fact approval, permissions and
persistent coordinate formats are unchanged.

Two existing repository checks remain outside this rendering change:

- `knowledge-interaction.spec.ts`'s ordinary container-reflow test expects width
  below 1,100; the current untouched world resize implementation preserves member
  positions and clamps the width at 1,180. The graph gesture/edit tests pass;
  this unrelated assertion was not weakened to make the suite green.
- `scripts/docs.py check` stops on Chinese prose in existing English-path pages
  `card-factory.md`, `creator-packs.md` and `developers/card-design.md`. The new
  architecture page passes its source check and appears exactly once in navigation.
  A successful full documentation-site build is not claimed.

Frontend unit coverage includes identity, direction, parallel/self relations,
metadata-only updates, deterministic layout, independent type/topic assignment,
stable expansion coordinates, unlinked nodes and bounded cache behavior.
Browser coverage includes real HTTP publication, inspection, edits, deletions,
pagination, relationship traversal, outer zoom scaling, filters, semantic zoom,
focus history, themes, resize and disposal.

Installed Windows/macOS desktop binaries, WebKit and physical low-end GPUs have
not been validated in this local run. The packaged application uses the same
production frontend assets, but that is not a substitute for installed desktop
acceptance. Model execution and external scientific services are outside this
rendering change and are not claimed as tested.
