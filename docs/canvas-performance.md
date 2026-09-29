# Canvas interaction performance

Use this guide to choose a reproducible canvas performance check. Read
[canvas control](canvas-control.md) for interaction ownership,
[placement](layout.md) for geometry, and [the WebGL2 renderer](terrain-webgl.md)
for terrain resource budgets and diagnostics.

## Choose the workload

| Behavior | Tool | What it covers |
| --- | --- | --- |
| Terrain drawing and tile lifetime | [Terrain benchmark](../frontend/scripts/benchmark-terrain-webgl.mjs) | Near/wide views, populated scenes, continuous travel, trace and memory modes |
| Dense-world wheel input | [Stress zoom profiler](../frontend/scripts/profile-stress-zoom.mjs) | Camera timing, input age, viewport writes, and synthetic-card requests |
| Moving cards and completing Deck drops | [Canvas performance scenario](../frontend/e2e/canvas-performance.spec.ts) | Frame intervals and browser script/layout/style work |
| Holding a Deck preview | [Deck pointer scenario](../frontend/e2e/deck-pointer-drag.spec.ts) | Pointer tracking, cancellation, drop semantics, keyboard and touch |
| Placement correctness | [Placement scenario](../frontend/e2e/canvas-placement.spec.ts) | Ownership, equipment/container placement, persistence, and undo |

## Run focused checks

From `frontend`, with repository dependencies and a Playwright browser installed:

```sh
node scripts/run-e2e.mjs e2e/canvas-performance.spec.ts
node scripts/run-e2e.mjs e2e/deck-pointer-drag.spec.ts e2e/canvas-placement.spec.ts
node scripts/benchmark-terrain-webgl.mjs --scenes near,wide
```

For an existing local frontend, the dense-world profiler accepts its origin:

```sh
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --label check
```

Follow each tool's prerequisites and output paths. Use an isolated test world and
run one server-owning browser runner at a time. Review the report, browser errors,
and final camera/card state; a timing number alone does not establish correct behavior.

## Compare like with like

Record the revision, browser, operating system, hardware, viewport, DPR, build mode,
fixture, camera trajectory, CPU throttle, and whether tracing was enabled. Compare
the same workload before and after a change. Keep traced diagnostics separate
from untraced timing runs.

Distinguish input latency, frame intervals, React updates, layout/paint, and GPU
process CPU. An improvement in one does not establish an improvement in all of
them. Report which focused checks, production builds, and browser scenarios ran;
do not describe focused evidence as a complete regression run or a universal FPS guarantee.

## Historical evidence

The [canvas investigation record](internal/performance/canvas.md) preserves the
earlier SVG, drag, and Deck comparisons with their original validation boundaries.
The [terrain measurements](internal/performance/terrain-webgl.md) and
[1,000-card input investigation](internal/performance/stress-zoom.md) cover later
changes. These are dated repository records, not current acceptance results.

Continue with [Contribute to OAW](contributing/index.md) for the change workflow
and [the CI guide](../.github/CI.md) for broader checks.
