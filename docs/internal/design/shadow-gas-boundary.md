# Activated collection gas boundary

Enable **移出成员** on an expanded Shadow Collection at http://127.0.0.1:5173/.
The effect is a procedural WebGL 1 fragment shader, not an image asset or a fluid simulation. No new dependencies. The React/SVG collection, hit target, ports and cards remain native OAW components.

## Implementation and tuning

- `frontend/src/effects/shadowGasRenderer.ts`: `GAS_MATERIAL` holds amplitude, secondary amplitude, smoke width, wavelength, speed, curl magnitude, fog opacity, padding and resolution caps. A rasterized polygon is converted to a signed chamfer distance field with two linear-time sweeps. The field is packed into RG16 in an RGBA8 texture; its quantization is much finer than an 8-bit distance channel. Only local geometry changes rebuild/upload this texture.
- The fragment shader uses traveling low-frequency noise, a smoothly blended local vortex warp and folded density bands. A calm interior and original safety envelope are preserved. Noise translates continuously; it does not multiply a sampled normal by unbounded elapsed time (which would amplify gradient errors into streaks).
- `frontend/src/effects/shadowGas.ts`: 450 ms activation ramp and a shared 30 updates/s scheduling cap (not a measured frame-rate guarantee). The superseded radial SVG wave/dashed-ribbon generator was removed.
- `frontend/src/effects/ShadowGasBoundary.tsx`: lazy renderer acquisition and an SVG foreignObject containing a pointer-transparent 2D output canvas. All instances share one off-DOM WebGL context and fixed-size render target. Each frame copies the just-rendered tile synchronously into its output canvas, without screenshotting DOM or creating a context per collection. IntersectionObserver, document visibility, collapse state, reduced-motion preference and unmount control subscription lifetime.
- `frontend/src/cards/ShadowCollection.tsx` and `shadowCollection.css`: real release-mode integration and cross-over with the original static feathered surface.
- The safety margin remains `SHADOW.hullPadding` / `SHADOW.padding` in `state/shadowCollection.ts`. The center remains uniform. SVG core opacity uses source-over compensation `.9*(1-s)/(1-.9*s)` against the shader's `.9*s`, keeping the combined interior alpha at `.9` throughout activation, without a seam between interior and exterior. No decorative path changes member coordinates, membership, IDs, port IDs, edge endpoints or hit testing.

The base envelope and logical connection ports remain stable. Mist can move beyond them, but is pointer-transparent. This intentionally avoids a new release target that moves under the pointer. The renderer is independent of business state and samples only the generated distance field, never arbitrary DOM pixels.

## Verification

- Shader upgrade, actual in-app browser: visible smoke at distinct times, complete fade-out, reactivation, whole-collection drag and undo, individual Codex drag and undo, Text member removal (Other count 3 -> 2) and undo restoration (2 -> 3), zoom-in. Test membership/layout changes were undone; 8 members and 16 world relationships remain.
- Compared actual DOM snapshots during animation: base path, all 8 visible card rectangles and all 8 visible existing edge paths remained identical. Browser error log was empty. External reference-edge creation had been verified for the preceding SVG implementation; it was not repeated for this shader change. Connection/port code is unchanged.
- Unit tests cover signed-distance polarity, field bounds, shared frame scheduling, activation/exit, offscreen and collapsed pause, reduced-motion and WebGL-unavailable fallback, context-loss fallback and renderer cleanup. Existing shadow geometry/layout tests remain in place.
- Frontend suite: 41 files / 232 tests passed. TypeScript application check passed.
- Screenshots of actual running frames were displayed in the task. No reference screenshot was used as a texture.

## Limits

This is stylized 2D density/domain warping, not volumetric fluid advection or an exact reproduction of a generated reference. A fixed 1280-pixel render target and 512-pixel distance-field long side bound rendering cost; high zoom can expose softened raster detail. Shared WebGL-to-2D copies still cost compositing bandwidth, and layout changes rebuild a distance texture on the main thread. No measured FPS guarantee, GPU timing or large-multi-collection stress benchmark is claimed. WebGL unavailable/failed/lost or reduced motion retains the original static soft silhouette. After context loss the instance stays in fallback until remounted; no automatic context-recovery loop is attempted.
