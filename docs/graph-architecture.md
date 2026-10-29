# Relationship map architecture

OAW's relationship maps use the shared `NetworkMap` frontend contract. Knowledge
Base, MatCreator and Visualization adapt their own data to it. The World editor
retains React Flow; task dependencies, document study surfaces and scientific 3D
models have different interaction and persistence requirements.

The actual pinned renderer dependencies are in
[frontend/package.json](../frontend/package.json). Check that manifest when
changing the renderer; an upstream release date is not a host compatibility guarantee.

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
GPU buffers again for each crossed node.
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

Use a disposable, non-sensitive snapshot. The benchmark writes JSON and PNGs
into `.outputs/graph-audit/`; these are local artifacts rather than fixtures.

The benchmark records layout Worker time, load-to-ready time, RAF intervals
during pointer/wheel interaction, hover intervals, event-to-next-frame time,
search response, CDP JavaScript heap after GC and eight mount/unmount cycles.
Frame rates are **browser RAF measurements**, not proof of GPU/compositor frame
delivery. Heap numbers include the preview and its bounded session caches and do
not measure total GPU/process memory. Synthetic data uses eight topics, mixed
knowledge types, directed local links and cross-topic relations.
