# Terrain renderer measurements

Historical evidence from 2026-09-29. For the maintained renderer contract, see
[WebGL2 terrain background](../../terrain-webgl.md). Raw `.outputs` paths below
refer to local artifacts from that run; they are not distributed with the repository.

## Historical SVG / WebGL measurements (2026-09-29)

These paired measurements were captured before removing SVG. Reports are retained
below; reproducing the SVG side requires the earlier renderer revision.

Standalone headless Edge 153.0.4234.32, Windows, i7-14700F, RTX 4060 Ti / ANGLE
D3D11, CPU throttle 1. These are local comparisons, not universal FPS claims.
The table shows both reverse-order rounds where applicable.

| Scene / metric | SVG | WebGL2 |
| --- | --- | --- |
| Near zoom p95 | 33.4 / 33.4 ms | 16.8 / 16.8 ms |
| Near zoom GPU process CPU | 103.7 / 104.6% | 13.7 / 13.5% |
| Near pan p95 | 16.8 / 16.8 ms | 16.8 / 16.8 ms |
| Near pan GPU process CPU | 84.7 / 87.6% | 11.2 / 9.1% |
| Wide zoom maximum interval | 233.3 / 233.4 ms | 16.9 / 16.8 ms |
| Wide zoom intervals >34 ms | 4 / 3 | 0 / 0 |
| DPR 2 near zoom p95 | 33.4 / 33.4 ms | 16.8 / 16.8 ms |
| Dark near zoom p95 | 33.4 / 33.4 ms | 16.8 / 16.8 ms |
| 48 cards pan p95 | 50.0 / 50.0 ms | 16.8 / 16.8 ms |
| 48 cards zoom p95 | 66.7 / 50.1 ms | 33.4 / 33.4 ms |
| Continuous pan/zoom maximum interval | 233.3 ms | 16.9 ms |
| Continuous pan/zoom intervals >34 ms | 65 | 0 |

The continuous sequence takes 44.8 seconds with SVG and 37.8 seconds with WebGL2
because the driver awaits input delivery. Both use the same 48 pan passes and
wheel sequences; delivery timing means their continuous camera trajectories are
not frame-for-frame identical. The primary near/wide paired gestures finish at
matching camera transforms. WebGL2 reaches 256 resident textures / 3.40 MiB, then stays there:
518 uploads, 262 evictions, and complete final viewport coverage. No page errors
were recorded in any of the 22 A/B runs.

Cards still incur their own rendering costs. In the 48-card zoom case, WebGL2
renders more frames and GPU process CPU is approximately 127%, versus 116–117%
for SVG. This change removes the background bottleneck; it does not eliminate
card rasterization or optimize the separate card rendering architecture.

Raw A/B report and screenshots:
`terrain-webgl-1790646741826` (`.outputs/terrain-webgl-1790646741826/report.json`).

Separate Chromium traces confirm that the large SVG raster workload is removed:

| Pan + zoom trace | SVG `DoRasterCHROMIUM` | WebGL2 `DoRasterCHROMIUM` |
| --- | --- | --- |
| Near | 5,082 calls / 5,311 ms | 310 calls / 59.8 ms |
| Wide | 1,376 calls / 968 ms | 309 calls / 55.4 ms |

These are summed outer `CrGpuMain/RasterDecoderImpl::DoRasterCHROMIUM` durations;
the nested `Deserializing` events are not added again. Background SVG nodes are
absent in WebGL2 runs, while the surrounding HUD/React Flow UI remains. Residual
raster work is small and similar at near and wide zoom; this is not a claim that
Chromium performs zero raster work anywhere in the page.
Trace files and summaries:
`terrain-webgl-1790647172673` (`.outputs/terrain-webgl-1790647172673/report.json`).

A separate Windows GPU Process Memory counter run sampled the benchmark's GPU
process every five seconds during continuous travel. After the 256-tile cache
filled, dedicated usage stayed between **51.65 and 54.19 MiB**, and shared usage
between **5.52 and 6.52 MiB**; it did not grow with cumulative tile uploads. These
OS counters include the browser's GPU context, swap chains and UI resources, so
they are larger than the 3.40 MiB of application-owned scalar textures. The initial
startup sample was 94.68 MiB dedicated / 26.32 MiB shared, then decreased.
Counter sampling was kept out of the primary timing comparison.
Raw counter samples:
`terrain-webgl-1790647286093` (`.outputs/terrain-webgl-1790647286093/report.json`).

## Historical rollout validation boundary

- 26 focused unit tests passed: scalar identity/halos, texture lifetime/budgets,
  existing terrain/chunks, SVG hook/layer, and wheel normalization.
- Production TypeScript check and Vite build passed. A production preview smoke
  test passed at DPR 2 with zero terrain/grid SVG nodes, then verified the explicit
  SVG flag. Browser console/page errors: none. Writes to the live backend were
  intercepted and the world was projected empty for this smoke check.
- All four new WebGL Playwright cases passed. Existing boundary pan, nested
  knowledge background isolation, selection/marquee, edge auto-pan, two connection
  drag tests and the legacy grid test passed in focused runs.
- The initial combined 11-test run had eight passes, an edge-auto-pan timeout and
  two following selection tests whose cards were outside the inherited viewport.
  Fresh isolated runs passed the edge-auto-pan test and both selection tests.
  Existing cross-test persisted viewport state was not refactored in this change.
- This is standalone headless Edge evidence. VS Code's embedded browser, desktop
  packaging, other GPU drivers and the complete application regression suite were
  not validated here.

## Legacy removal validation (2026-09-29)

- Removed the SVG components, their worker response/path generation/cache, the
  Canvas2D experiment, renderer switches, obsolete CSS and SVG-only tests.
  Field generation, seed semantics and the bounded scalar/GPU caches are retained.
- 17 focused field/scalar/texture/chunk tests and the production build passed.
- Eight focused standalone Edge cases passed: camera/card interaction, DPR/theme/
  seed/resize/context restoration, long-distance cache eviction, missing WebGL2,
  procedural grid pixel alignment, both zoom limits in both themes, distant tile
  streaming and uncached pan coverage. The first combined run passed seven; the
  theme test initially read the theme before initialization, then passed after
  adding the missing readiness assertion.
- Missing WebGL2 leaves the shell theme color visible. Pan, zoom and theme switching
  are verified in that state; no SVG background is mounted, even with the old URL
  flag. Context restoration redraws the same accepted WebGL visual.
- Historical reports above remain available. Maintained terrain benchmarks now
  measure WebGL only; further populated-card optimization is a separate task.
- The cleaned WebGL-only benchmark passed a near-scene smoke run: pan/zoom p95
  both 16.8 ms, maxima 16.9 ms, no intervals over 34 ms and no page errors.
  Raw report (`.outputs/terrain-webgl-1790650455979/report.json`).
