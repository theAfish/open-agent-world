# Canvas interaction performance

## Wide viewport terrain (2026-09-21)

At zoom 0.12, a 1920 x 1080 viewport retains 77 terrain SVG tiles, with
4,414,821 characters of path data. Geometry already runs in a worker, but the
browser still rasterizes these paths while panning. Hiding terrain or promoting
the small overview tiles to compositor layers removed the sustained frame gap
in the same scene. Main-thread `Paint` duration alone did not capture this cost.

`ContourLayer` now promotes tiles only below zoom 0.45, releasing that hint at
higher zoom to avoid retaining large textures. Terrain paths and resolution are
unchanged. Memoized tiles reuse their SVG elements across worker publications
and LOD replacements. A constant-time chunk-boundary signature avoids building
and sorting the entire coverage list on every pointer move; the shared chunk
bounds calculation preserves prefetch and visible-first worker scheduling.

Matching before/after runs in isolated headless Chrome, with 4x CPU throttling
and no cards (to isolate terrain), measured 180 pointer moves across four sweeps:

| Measurement | Before | After |
| --- | ---: | ---: |
| 95th percentile frame interval | 33.4 ms | 16.8 ms |
| Maximum frame interval | 49.9 ms | 17.3 ms |
| Accumulated JavaScript duration | 1.572 s | 0.597 s |
| Accumulated main-thread task duration | 3.703 s | 2.474 s |

These are local browser measurements, not universal FPS or populated-world
guarantees. The in-app browser bridge rejected the connection, so these runs
used the repository's independent browser runner. Report artifacts are under
`.outputs/viewport-wide-before.json` and `.outputs/viewport-wide-after.json`.

`viewport-wide.spec.ts` checks real pan displacement, overview coverage across
uncached terrain, theme screenshots, high-zoom layer release and stroke width,
and resizing to 2560 x 1440. Frame timings remain diagnostics rather than
hardware-dependent pass/fail thresholds. Run from `frontend`:

```powershell
$env:OAW_WIDE_REPORT = '../.outputs/viewport-wide.json'
node scripts/run-e2e.mjs e2e/viewport-wide.spec.ts
```

Set `OAW_WIDE_COMPARE=1` to additionally profile hidden and unpromoted terrain
in the same browser and capture the unpromoted dark-theme comparison.

Validation: 12 focused unit tests, the production build, and five focused
browser cases passed (wide viewport, uncached terrain, grid, minimap, populated
pan). The first wide-viewport regression used a fixed count of zoom clicks and
stopped at LOD 56; the test now waits for each actual zoom target and passed on
rerun. Light/dark screenshots were inspected against unpromoted rendering.
The full frontend and browser suites were not run.

## Findings and changes (2026-09-16)

Dragging updated the controlled React Flow nodes on every pointer move. Several
unrelated consumers then did work for the entire scene:

- Edge endpoint mapping depended on the live node array, rebuilding every edge
  even when only a position changed. It now depends on canonical visibility and
  ownership; React Flow still updates the connected edge geometry live.
- Terrain, background, minimap and visual overlays rendered with the canvas
  parent. They now retain their render results and subscribe to the state they
  actually display. Each minimap rectangle tracks its own node.
- World updates recreated node data and styles for unchanged cards. Reconciliation
  now shares unchanged objects and retains live selection and valid measurements.
  Card contents receive content/selection/drag state, independently of position.
- Preview cards mounted hidden inspector editors and their subscriptions.
  Inspectors now mount on first use and stay mounted on collapse, preserving
  local drafts. Workspace lifecycle remains unchanged.
- Drag hit testing scanned all cards and mixed DOM writes with geometry reads.
  It now tests mounted equipment targets, skips unsupported transformations,
  reads hit geometry before painting hints, and ignores unchanged drag intent.
  Connection hover geometry is not measured while a button is held.
- Parent/equipment traversal repeatedly searched whole arrays. An immutable
  snapshot ID index is shared across traversal callers. Flow portals cache their
  owning element while checking ownership, including nested graphs and removal.
- Deck springs now calculate destinations and stacking order only when the
  active slot changes. Pointer traversal still uses fixed hit slots.
- Dropping into a container now sends `parent_id` with creation, eliminating the
  subsequent PATCH and keeping placement in one undo operation.

## Measurement

### Held Deck previews

The original measurements below covered moving instantiated nodes and completing
a short Deck drop. They did **not** measure the ghost following the pointer while
a Deck card is held. That path used native HTML drag-and-drop (`draggable`), so
optimizing React Flow nodes could not change the browser/OS drag image.

Deck gestures now use pointer capture and a disposable, pointer-transparent copy
of the printed card face. Movement coalesces into one `requestAnimationFrame`
update to `translate3d`; there is no interpolation, world node, or React position
state in the tracking loop. Drop hit tests run once per moving frame, and the
release coordinates are checked again even if a scheduled frame has not run.
Registered canvas and Deck surfaces share placement, equipment, transformation,
transfer and discard behavior. Native file drops still use the existing handler.

The session cleans up on release, Escape, pointer cancellation, lost capture,
window blur, hidden document, or Deck unmount/change. A completed drag suppresses
its synthetic click; ordinary clicks and keyboard activation remain available.
Pointer drops consult the same tutorial boundary as native drops.

`deck-pointer-drag.spec.ts` holds a Text card over a 30-card scene and samples
60 pointer moves before releasing it. In the isolated Chrome development run,
the maximum and 95th-percentile frame intervals were both 16.8 ms, and all
next-frame preview positions matched the pointer within 0.001 CSS pixels.
It observed zero native drag starts and zero creation requests while held;
release created exactly one card. These are local automated-browser observations,
not a measurement of desktop OS drag-image latency or a universal FPS guarantee.

The same suite checks cancellation, Deck transfer/discard, mouse click, keyboard,
touch input and tutorial placement. Run it with placement regressions:

```powershell
node scripts/run-e2e.mjs e2e/deck-pointer-drag.spec.ts e2e/canvas-placement.spec.ts
```

The console prints `DECK_HELD_PREVIEW` diagnostics and the held-card screenshot
is written to `.outputs/deck-held-preview.png`.

Held-preview validation: 399 unit tests across 75 files passed with
`node node_modules/vitest/vitest.mjs run src --maxWorkers=2 --minWorkers=1`,
and the production build passed. Eight focused browser cases passed across
Deck tracking/input/tutorial, placement, inline Toolset transformation and
Minister-role drop runs. The initial unconstrained unit run timed out while
loading an unrelated plugin view; the bounded-concurrency rerun passed without
changing that plugin or its test. The entire browser suite was not run.

### Original instantiated-node and completed-drop comparison

Isolated Chrome, Vite development build, 1600 × 1000 viewport, 30 persisted cards
(6 Agents and 24 Text cards), 60 pointer moves, followed by a real HTML drag from
the deck and creation through `/api/card-library/nodes`. The backend uses the
mock runtime and a disposable test profile; ordinary viewport culling stays on.

The initial comparison used the same fixture and measurement code on both sides:

| Accumulated browser work | Before | After initial optimization |
| --- | ---: | ---: |
| Drag: JavaScript | 573 ms | 241 ms |
| Drag: layout | 20.6 ms | 3.0 ms |
| Deck drag and placement: JavaScript | 211 ms | 58 ms |
| Deck drag and placement: layout | 25.5 ms | 4.8 ms |

After deferred inspector mounting, a follow-up run recorded 221 ms of script
work for dragging and 43 ms for deck placement. Drag frame intervals stayed at
16.8 ms or below; placement had one 117 ms interval although its 95th percentile
was 16.8 ms. The placement completion check in this follow-up waits for the new
card to be visible instead of polling the server's node count. The initial table
is the strictly matching before/after comparison.

These are cumulative durations over each gesture, **not creation latency or FPS**.
Frame timings are recorded as diagnostics, without hardware-dependent pass/fail
thresholds. Subsequent runs can still have occasional long frames during native
dragging; this is not a guarantee of 60 FPS for all hardware, plugins or world sizes.
The development-only React commit counters are diagnostics, not an exhaustive
count of memoized component renders. CDP timings are the comparison metric.

Run from `frontend` in PowerShell:

```powershell
$env:OAW_PERF_REPORT = '../.outputs/canvas-performance.json'
node scripts/run-e2e.mjs e2e/canvas-performance.spec.ts
```

The runner resets only `.open-agent-world/playwright` and starts services on
5177/8017. Run one isolated runner at a time. The report contains frame intervals
and CDP script/layout/style durations; `.outputs/canvas-performance.png` shows the
resulting scene.

## Regression coverage

`canvas-placement.spec.ts` checks direct equipment/container creation, ownership
through undo/redo, deferred editor mounting and draft preservation. Selection,
equipment surfaces, Glue, and focused relationship tests cover live updates
during marquee selection, group motion, resizing, boundaries and persistence.

Older Glue fixtures were updated to the shared backend layout and current English
labels. Compact card fixtures now use the same 96 × 96 base size as real deck
creation and wait for collapse geometry before choosing a drag origin. Preview
geometry assertions use the current 224-pixel width.

Validation: 398 unit tests across 75 files and the production build passed.
Thirteen focused browser cases passed across the performance/placement,
selection, equipment, Glue and relationship runs. This is focused coverage,
not a claim that the repository's entire browser suite passed.
