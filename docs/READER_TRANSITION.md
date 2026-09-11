# Fullscreen PDF transition

## Preview and scope

Run the existing local frontend/backend, open a Paper inspector, then click the
book icon. The transition is integrated into the normal reader, not a separate
rendering implementation. A development-only calibration/preview page is at
`http://127.0.0.1:5173/dev/reader-transition.html`. Select a local Paper there to
exercise the same reader. The page is not an entry in the production build.

## Implementation

- `plugins/library/frontend/readerTransition.ts`: seven source locations, spread
  440 ms, fast-open minimum 160 ms, reveal 460 ms, feather fraction 0.72
  (13-stop smootherstep ramp with zero endpoint slopes, shared by both directions),
  background blur 16 px, filtered-image mix 0 → 0.5 during 65 ms, then **linear**
  0.5 → 0.75. Coverage has its own smoothstep progression.
- `GlassTransition.tsx`: one transparent `backdrop-filter` surface. Seven CSS
  gradient masks are combined with additive/source-over alpha into one coverage
  mask; the layer opacity is applied once after masking. No smoke texture, tint,
  screenshot, individual full-screen filters, particle or displacement shader.
  This implements `C = (1-M*a)S + M*a*Blur(S)` up to browser rounding.
- `useReaderEntrance.ts`: spreading → waiting → revealing → complete, plus
  explicit failed/cancelled branches. Readiness can shorten spreading. The 45 s
  stall guard produces a recoverable **failure**, never a success/reveal signal.
  Timers and animation frames are cancelled on unmount; late callbacks ignored.
- `index.tsx`: modal is portalled to `document.body` to avoid the canvas's
  transformed/filter ancestors. Final-size reader content is laid out but hidden
  and inert while preparing. The modal and backdrop stay transparent; a separate
  cancel control remains available. Failed loads can retry with the full-document
  cache evicted. Only one PDF reader/document is prepared per opening.
- `PdfReading.tsx` / `PdfPageSurface.tsx`: fit before mounting the target page;
  render readiness requires both the actual canvas RenderTask and selectable
  TextLayer, followed by two paint opportunities. Reported scale is checked
  against current available dimensions. Resize/scale/DPR changes invalidate
  stale rendering. A page-render failure cannot report successful readiness.
- `decodePdf.ts`: decode Base64 in 256 KiB encoded chunks with a yield between
  chunks. Avoids synchronous whole-document `Uint8Array.from(atob(...))` and its
  per-character callback. Cancellation stops subsequent chunks/PDF.js loading.

Revealing changes only a feathered multi-origin mask and decreasing optical blur
on the already positioned full-screen content, not its size or position. It is
not a whole-screen crossfade. The old workspace remains underneath until the new
surface covers it. At completion the temporary glass layer is unmounted and all
mask/filter/inert restrictions are removed. Reduced motion skips spatial
animation but still waits for real rendering. Devices reporting ≤4 CPU threads
use five sources and 10 px background blur. Unsupported filters/masks use an
immediate ready-state replacement, not a pretend glass effect.

## Validation — 2026-09-10

Commands (from `frontend`):

```
npx tsc --noEmit -p tsconfig.app.json
npx vitest run src/cards/readerEntrance.test.tsx src/cards/decodePdf.test.ts src/cards/paperCache.test.ts src/canvas/importPdf.test.ts
npx playwright test --config playwright.reader.config.ts
```

- Type check passed; 11 unit tests passed.
- Six isolated Edge/Chromium browser tests passed: actual PDF rendering and text
  layer; zoom/annotation controls and scrolling; repeated open; delayed response
  stays hidden; cancel with late callbacks; retry after invalid PDF; reduced
  motion; DPR 2 and resize during reveal; pixel-mixture calibration.
- Pixel calibration used a static text/stripe/card DOM and screenshots at layer
  opacities 0, 1, 0.5, 0.75 with identical coverage. After the softer-edge update,
  on 191,149 changed pixels mean per-channel error against the mixture equation
  was **0.340/255** at 0.5 and **0.386/255** at 0.75. On 211,806 unchanged pixels,
  maximum drift was 1/255.
  This checks real backdrop sampling, not a background-color alpha proxy.
- In-app browser: visually checked local glass against the calibration DOM;
  opened the actual Ionic conduction paper to completed, full-size, selectable
  reading state. A measured 3,427,010-byte paper decode took 75.3 ms elapsed
  including yields; maximum synchronous decode chunk 2.8 ms. The preview's scoped
  Long Tasks observer recorded no >50 ms tasks during that one opening.
- Test artifacts live under ignored `.open-agent-world/reader-transition-tests`.
  The test PDF is generated in memory and APIs are intercepted; no test writes
  touch research nodes, annotations, or managed files.

## Reverse return transition

Returning from a completed reader uses `complete → concealing → retracting →
cancelled/unmounted`: reverse the final-position document mask and optical blur
for 360 ms, then reverse the original seven-source backdrop mask and its filtered
mix for 320 ms. Both durations are in `readerTransition.ts`. The existing PDF
stays mounted through the exit (no second load). Buttons and Escape share this
path; repeated exits and late render/failure callbacks cannot restart it.
Cancellation during initial loading remains immediate. Reduced motion skips the
spatial exit. Pending document writes still settle before the parent closes.

## Performance limits

These are single-machine development observations, not a measured FPS guarantee.
GPU paint/compositing time is not represented by Long Tasks. Updating a full-screen
mask/filter still has raster/compositing cost, especially on large high-DPR
screens; only one backdrop filter is used, but it is not free. Waiting freezes
the mask to avoid continuous redraw work. PDF.js parsing, fonts, vector graphics,
text-layer construction, JSON parsing and allocation may still cause long tasks
on more complex documents. No every-frame DOM screenshot or new graphics runtime
was introduced. Cross-browser pixel equivalence beyond the tested Chromium
environments is not claimed.

Reference: [MDN backdrop-filter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)
describes real backdrop sampling and ancestor backdrop-root limitations.
