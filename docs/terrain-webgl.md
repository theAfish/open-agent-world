# WebGL2 terrain background

The world background uses one native WebGL2 canvas under React Flow. The legacy
SVG renderer, its path cache and the Canvas2D experiment have been removed. Cards,
edges, selection, hit testing, wheel normalization and viewport persistence remain
owned by React Flow and the existing canvas controllers. No new runtime dependency
is required.

## Rendering and resource ownership

`TerrainBackground` mounts the renderer and subscribes directly to the React Flow
camera store. Camera updates schedule one animation frame; they do not set React
state or write background DOM styles. `TerrainRendererWebGL` draws a procedural
world-anchored dot grid, then the visible terrain tile quads.

The terrain worker only returns scalar tiles, sampling the original seeded field
without marching squares or SVG path construction. Each 2048-world-unit tile
retains the existing resolution of 56 cells and adds a one-sample halo:
a 59 x 59 `Float32Array`. The worker
transfers a copy while retaining its bounded cache. Adjacent halos sample the same
world coordinates, including negative coordinates.

Each tile becomes one `R32F` texture. The fragment shader interpolates the field,
fills elevation bands and computes contour coverage from screen derivatives.
Contour widths remain 1.15 / 1.65 CSS pixels throughout a zoom gesture, including
DPR compensation. Colors and opacity use the existing theme tokens. The accepted
WebGL visual style is retained; it does not reproduce every smoothed SVG path.
Grid density changes continuously between nested world-coordinate dot lattices.

Panning and zooming only update camera/draw uniforms for resident tiles. Crossing
coverage boundaries requests missing tiles, visible first, with a one-tile prefetch
ring. Obsolete worker replies are ignored. Tile data is uploaded only on first
arrival, seed changes, after eviction/revisit, or context restoration. Large world
coordinates are rebased before passing float uniforms to the GPU.

Both the worker scalar cache and GPU texture cache are bounded to 256 tiles. The
GPU cache also has a 4 MiB byte budget and deletes evicted textures. At resolution
56, 256 textures contain **3,564,544 bytes (3.40 MiB)**. The main thread retains
bounded CPU recovery copies of those same samples. The canvas backing store is
separately capped at 16,777,216 pixels and the device's maximum dimensions. Browser
swap-chain/driver allocations are additional to these application-owned budgets.

Resize and DPR notifications update backing dimensions only when necessary. Theme
changes only update color uniforms. On context loss the canvas is hidden, revealing
only the shell's theme background color. Restoration recreates programs and
reuploads the retained bounded tiles, then displays the canvas again. Permanent
initialization/worker/upload failure, or a viewport exceeding the 256-visible-tile
budget, also leaves the theme background visible; cards and canvas interaction
remain available. There is no secondary terrain renderer to mount or rasterize.

## Diagnostics

Renderer selection by URL or `VITE_TERRAIN_RENDERER` has been removed. Old
`terrainRenderer` query parameters are ignored. The canvas exposes
`data-terrain-status` (`loading`, `ready`, `context-lost`, `unavailable`) and
`data-terrain-error` on failures. No ordinary user-facing configuration is needed.

Read `document.querySelector('.terrain-webgl-background').terrainStats` in DevTools
for tile counts, bytes, uploads, evictions, coverage, draws and context restores.
This getter does not mutate the DOM or update React during gestures.

## Reproduction

From `frontend`, run:

```powershell
node scripts/benchmark-terrain-webgl.mjs
node scripts/benchmark-terrain-webgl.mjs --scenes near,wide --trace
node scripts/benchmark-terrain-webgl.mjs --scenes long --memory
$env:PLAYWRIGHT_CHANNEL='msedge'
node scripts/run-e2e.mjs e2e/terrain-webgl.spec.ts
```

The benchmark starts an isolated backend on 8028 and frontend on 5188, retains
its data and reports under `.outputs/terrain-webgl-<timestamp>`, and never changes
the user's world. Ports and browser channel are configurable. It uses the same
1600 x 1000 viewport, pan trajectory and wheel sequence as the original empty
canvas investigation. It now runs WebGL-only repetitions. Separate trace runs
record raster activity; traced timing is not mixed into ordinary frame timings.

Scenes include near and wide zoom, DPR 2, dark theme, 48 real text cards, and
continuous travel through hundreds of tiles with zoom and reversal. Runtime event
streams are held fixed during measurement. Frame intervals are `requestAnimationFrame`
observations, not measured display presentation times. GPU process CPU is reported
as a percentage of one CPU core, not hardware GPU utilization.

The focused Playwright tests also cover card dragging/persistence, cursor-anchored
zoom, texture reuse, live theme/seed changes, resize, DPR, cache eviction, forced
context loss/restoration, procedural grid anchoring, and interaction without WebGL2. CDP's DPR
override does not emit a monitor-change event in headless Edge, so that test
explicitly dispatches resize after changing DPR.

## Related reading

Read [canvas control](canvas-control.md) for camera ownership and
[canvas performance](canvas-performance.md) for profiling guidance.
The [terrain measurement record](internal/performance/terrain-webgl.md) preserves
dated comparisons and their validation boundaries in the repository.
