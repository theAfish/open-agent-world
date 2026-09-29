# 1,000-card wheel regression (2026-09-29)

The WebGL background exposed a separate camera/input problem under the development
panel's 1,000-card stress fixture. The largest avoidable work was in the browser
renderer: repeated global viewport updates, synthetic Sandbox requests and forced
layout during workspace mounting.

## Cause and change

- XYFlow's programmatic `setViewport` emits `onMoveEnd` through a deferred timer.
  The wheel controller's synchronous `writing` flag had already reset when that
  callback arrived. One 40-event wheel sequence produced **133 world-store
  viewport writes**, causing canvas reconciliation throughout the animation.
  The controller now recognizes its actual camera write in the deferred callback;
  it commits the viewport when the gesture finishes or yields to another control.
- Reversal accumulated against an unseen animation target. It now starts from
  the displayed camera, dropping the unfinished movement in the old direction.
  Same-direction input retains XYFlow's original gain and pointer anchoring.
- RAF timestamps can precede an input handler when the main thread is blocked.
  Progress now uses the current monotonic clock and the input's timestamp, with
  bounds on progress. Queued input does not start a new 180 ms animation tail.
  Pointer/keyboard takeover cancels the animation and rejects older queued input.
- Ephemeral Sandbox cards mounted the full workspace and settings, then requested
  runtime information, files, history, documents and credentials for nonexistent
  IDs. These mount/refresh paths now skip backend requests for ephemeral cards.
  The workspace DOM, real-card behavior and stress fixture remain intact.
- Empty terminal mounts no longer read `scrollHeight`. The old read forced layout
  independently for many newly visible workspaces. Output/draft following remains
  enabled when there is content.

No terrain shader, terrain generation, card presentation default, React Flow
virtualization setting or wheel normalization changed in this fix. The subsequent
[WebGL cleanup](../../terrain-webgl.md) removed the legacy SVG renderer and A/B switch.
Further card rendering optimization remains separate from that cleanup.

## Measurements

Standalone headless Edge, Vite development build, 1920 × 1080, DPR 1, normal CPU
speed, original deterministic `generateStressWorld(1000)` fixture. At the initial
zoom of 0.18 there are 83 mounted card surfaces, including full Sandbox workspaces;
the 1,000 cards remain in the world store. Pan follows two identical closed loops.
Zoom sends 20 forward and 20 reverse wheel events. The rapid reversal phase sends
40 CDP wheel commands every 8 ms without waiting for renderer acknowledgements.
Each phase includes 450 ms for settling. Camera frames, input age, store writes,
React commits, process CPU and CDP metrics are saved in the reports.

Matched trace + CPU-profile diagnostic runs:

| Metric | Before | After |
| --- | ---: | ---: |
| Zoom p95 frame interval | 50.1 ms | 16.8 ms |
| Zoom maximum frame interval | 166.6 ms | 50.0 ms |
| Zoom intervals >34 ms | 15 | 2 |
| Zoom world-store viewport writes | 133 | 1 |
| Zoom main-thread script duration | 1,283 ms | 480 ms |
| Rapid reversal maximum input age | 2,592 ms | 105 ms |
| Rapid reversal maximum frame interval | 883.3 ms | 50.1 ms |
| Pan p95 frame interval | 50.0 ms | 33.4 ms |
| Pan intervals >34 ms | 20 | 5 |
| Pan maximum frame interval | 250.0 ms | 316.6 ms |

The traced pan still had one long outlier. An additional quiet run without trace
or CPU profiling measured pan/zoom/reversal p95 of **33.3 / 16.8 / 33.3 ms**, with
maximum intervals of **50.1 / 50.0 / 33.4 ms**. Three consecutive untraced runs on
the shared desktop ranged from 16.8–33.4 ms for zoom p95 and 33.4–50 ms for pan p95;
part of that repeat overlapped trace postprocessing. All final runs had zero
requests for synthetic node IDs and no page errors. These are diagnostics, not a
guarantee of 60 FPS on every machine.

The remaining cost is card mounting, layout/style work and card painting. GPU
process CPU does not uniformly decrease: the fixed camera presents more frames.
For example, traced zoom GPU CPU was about 76% before and 97% after, measured as
CPU seconds per wall second (one core = 100%). Renderer process CPU fell from
about 127% to 101%. Do not describe this input fix as another GPU/background win.
Reversal deliberately cancels unseen movement, so the final viewport need not
equal the old controller's final viewport for the same signed input sequence.

Local artifacts:

- Before: `.outputs/stress-zoom-confirmed-before-1790648407557/`
- Wheel ownership fix only: `.outputs/stress-zoom-wheel-fixed-1790648545728/`
- Final trace/profile: `.outputs/stress-zoom-final-1790649266644/`
- Three untraced runs: `.outputs/stress-zoom-final-untraced-1790649352139/`
- Quiet untraced run: `.outputs/stress-zoom-final-quiet-1790649468624/`

## Reproduction and validation

From `frontend`, against a running local frontend:

```powershell
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --label check
node scripts/profile-stress-zoom.mjs --origin http://127.0.0.1:5174 --label trace --trace --profile
```

The profiler projects an empty persisted world into its own browser, generates
ephemeral stress cards, suppresses API mutations and sockets, and leaves the user's
saved world untouched. Run it separately from builds/tests/trace processing.

Validation passed: 49 focused Vitest tests across wheel lifecycle/normalization,
Sandbox workspace/card/store and environment saving; three standalone Playwright
tests for the 1,000-card regression, existing wheel gain/drag takeover and reduced
motion; TypeScript and production build. The real XYFlow regression asserts a
single viewport commit, immediate reversal, subpixel pointer anchoring, and zero
synthetic backend requests. Unit coverage additionally exercises stale input,
old RAF timestamps, external camera control and direct-manipulation takeover.
This is not full-suite, VS Code embedded-browser or desktop-runtime validation.
