# Persistent card finishes

Pack opening assigns a single print finish to each newly acquired collection entry. The authoritative probability table is `backend/card_finishes.py::CARD_FINISH_WEIGHTS`: normal 72%, foil 16%, rainbow 6%, starlight 4%, laser 2%. The small `weighted_choice` utility accepts one random sample, supports relative and zero weights, and validates its inputs. Pack generation previously had no seeded random stream.

## Ownership and persistence

OAW's collection currently has one owned entry per card type, and decks reference those entries. Finish belongs to `CollectionEntry`, never `NodeTypeCatalogItem`. Opening overlapping packs adds provenance to the existing entry without granting another copy or rolling again. This feature intentionally does not change that collection model.

Only explicit `open_pack` creation of a new entry calls `roll_card_finish`. Reconciliation, legacy import, repeated pack requests and deck moves preserve the stored value. Library JSON in SQLite's application settings includes finish. Existing entries missing the field deserialize to `normal`.

Placement through `/card-library/nodes` copies the owned finish to the new `Card`. World instances persist their own finish in an additive SQLite `cards.finish` column with a `normal` default; old world cards are not recolored from today's collection. The immutable presentation field travels through API normalization, undo restoration, agent duplication, saved legion blueprints, summoning and published workspace projections. Two world instances of the same type can retain different finishes. There is no render-time randomness or finish mutation endpoint.

## Material rendering

`CardFinishLayer` renders a view-dependent laminate over designated artwork. Holo is visible at its neutral resting angle and changes continuously with viewing direction. The persisted `rainbow` ID still names Holo; collection metadata and finish probabilities are unchanged. Normal cards mount no material DOM.

### Explicit print stack

1. **Substrate:** the existing stock colour and card silhouette.
2. **Printed artwork:** the image, illustration or hero colour field (`data-material-layer="artwork"`).
3. **Laminate:** a pointer-transparent, region-masked canvas at z-index 3. The shader supplies the colour travel, interference contours and fine surface response.
4. **Protected top print:** the complete title/body sheet, icons, badges, labels and dividers at z-index 4. These elements are also excluded from the coating map, so their ink and backing retain their original contrast.
5. **Clearcoat:** a small achromatic specular contribution within the laminate shader. It shares the coating mask and does not tint protected copy.

`CardFace` marks its layers explicitly. World card/LOD layouts retain their geometry and use semantic selectors for protected content. Factory illustrations remain below their laminate, with other factory elements above it. No blend mode is applied to the whole card. Workspace, inspector and container chrome stay matte; `surface="chrome"` remains a compatibility guard.

### Optical model and material identities

`cards/cardMaterial.ts` holds composable optical properties and the persisted-finish presets. `cardMaterialShader.ts` uses a fixed white studio source, a normalized view/half vector and a Fresnel-like response. It has no time uniform.

- **Macro:** a low-frequency warped film-thickness field produces broad aurora regions. The view vector changes the optical path, phase and warp curvature together. Bands bend and reorganize rather than linearly translating a fixed texture.
- **Meso:** nested interference contours derive from that same film field. Their directional ridges brighten and fade without replacing the broad colour structure.
- **Micro:** fine, fixed laminate variation supplies subtle specular breakup. Its contrast fades with raster resolution to avoid thumbnail noise.

The wavelength lobes and secondary diffraction order are a physically inspired approximation, not a calibrated spectral simulation.

- **Normal:** unchanged printed stock, with no laminate.
- **Foil:** broad neutral silver reflections of light and dark studio shapes, with fine directional brushing.
- **Holo:** the strongest chromatic response, with flowing cyan, pink, gold, green, violet and blue regions. Artwork remains visible beneath the film.
- **Starlight:** a small fixed population of independently angle-lit prismatic flakes, short cross flares and gentle neutral gloss.
- **Laser:** engraved radial/parallel grooves in fixed print coordinates, dispersed through a directional Bragg-angle gate. The engraving extinguishes away from its viewing window.

### Region masks

`cardMaterialMask.ts` measures untransformed DOM layout to build an RGB coating map: red artwork, green frame, blue icon rim and yellow accents. Black is uncoated stock or protected content. Artwork is strongly coated; the frame has a separate response and icon rims receive only a small neutral reflection. Exclusions are applied last with padding to protect antialiased ink.

Custom surfaces can use `data-material-region="artwork"`, `"accent"`, `"icon"`, `"background"` or `"text"`. Use `data-material-layer="protected"` for top print and retain the component's normal positioning. The entire text sheet is protected, including its backing and dividers, rather than only individual glyph rectangles. Region resize, child/content changes and explicit region-role changes rebuild the mask. Tilt, fan transforms and canvas zoom do not move the coating relative to the print.

At title/copy boundaries, a full-width feather dissolves the coating into the stock before the protected text begins. This avoids rectangular blank cutouts around labels. Finished world-card icons use a soft radial exclusion and transparent backing so the symbol reads as printed ink rather than a separate white tile; the same treatment applies to compact and preview layouts.

### Input, rendering and fallback

`useCardFinish` coalesces pointer input without React state updates. Only collectible `CardStock` faces opt into geometric perspective tilt; canvas card geometry remains unchanged. `CardMaterialCanvas` smoothly approaches the target view with a short, finite interpolation and returns to the neutral laminate on leave. Once settled, it schedules no more frames. Touch and reduced-motion cards retain a static finish.

A single offscreen WebGL context serves all visible card canvases. Rasters are capped at 640 x 800 for standard/showcase and 256 x 320 for thumbnails. Offscreen work is stopped, and observers/listeners/pending frames are released on unmount. HMR disposes the shared GPU resources.

If WebGL is unavailable or lost, `cardMaterialFallback.ts` evaluates the same broad optical field on a CPU raster capped at 256 x 320. The full-resolution coating mask is applied **after** upsampling so protected text cannot pick up filtered colour. The fallback approximates the material identities with reduced micro detail.

## Development preview

Start Vite and open `/?card-finishes`, or use **DEV / F3 / Card finish preview**. The gallery works without a backend and changes no collection data.

- **Pointer / tilt:** direct interaction, smoothly returning to visible neutral material at rest.
- **Representative angles:** fixed characteristic views for comparing all five finishes.
- **Slow light sweep:** a development-only view sweep, capped at 24 updates per second, paused when hidden/offscreen or under reduced motion. Production cards have no autonomous sweep.
- **Printed test pattern:** four printed colour/value patches, curves and fine linework beneath the laminate.
- **Layer comparison:** original print, isolated masked Holo, and the completed card. All three share the same angle; the isolated film exposes protected cutouts. Pointer poses are synchronized between specimens.
- **200 thumbnails:** passive rendering/performance inspection, with sweep disabled.

Dark/light stock and responsive layouts allow contrast and mask inspection across sizes. The shared collectible face retains its five layouts; see [Card design language](card-design.md).

## Validation

- `backend/tests/test_card_finishes.py`: deterministic weight boundaries, invalid distributions, exactly one sample per roll, new entry assignment, overlap/reopen/stale requests, legacy defaults, SQLite restart, deck moves, placement, duplication, legion capture, restore and deployed workspace persistence.
- `frontend/src/cards/cardFinish.test.ts`, `CardFinishLayer.test.tsx`: compatibility, stable rendering, pointer coalescing, cleanup, interaction pass-through, reduced motion and 200 passive materials.
- `frontend/src/api/client.test.ts`: API load/create/restore preservation.
- Library, pack, world card and `NodeWorkspace.finish.test.tsx` component tests cover stored finish propagation, stale parent snapshots, reveal timing and legacy cards.
- `ContainerFinishes.test.tsx` verifies matte container/collection chrome and retained equipped-card finishes. The Library browser scenario checks placement, undo/redo and reload against the saved finish, plus three viewport sizes.
- `frontend/e2e/card-finishes.spec.ts`: broad chromatic coverage at rest and across angles, continuous/reversible colour travel, neutral silver and sparse flakes, directional engraving, zero material alpha over protected content, pixel-stable top-print interiors, synchronized layer comparison, resize/mobile masks, WebGL fallback, one shared GPU context, reduced motion and no idle animation callbacks for 200 cards. Hand/world dragging, semantic zoom and matte chrome checks remain covered. Screenshots and numeric measurements are diagnostic artifacts.
- `frontend/e2e/library-preview-layout.spec.ts`: showcase/thumbnail tilt, full preview visibility and independent scrolling at desktop, narrow and mobile sizes, including long descriptions and a full collection.

Run `npm.cmd test -- --maxWorkers=2 --minWorkers=1` and `npm.cmd run build` in `frontend`, and `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_card_finishes.py` from the repository root. The repository currently has no configured formatter; retain surrounding conventions and check whitespace with `git diff --check`.

For browser material checks, start Vite, set `OAW_E2E_BASE_URL` to its local URL and run `node node_modules/@playwright/test/cli.js test e2e/card-finishes.spec.ts`. The existing `run-e2e.mjs` harness starts an isolated backend for Library integration checks.

## Main implementation files

Backend: `card_finishes.py`, `card_library.py`, `api/card_library.py`, `world/models.py`, `world/store.py`, `persistence/database.py`, `legions/models.py`, `services.py`, `deployment_workspace.py`.

Frontend: `cards/cardFinish.ts`, `cards/CardFinishLayer.tsx`, `cards/cardFinish.css`, `cards/useCardFinish.ts`, `components/CardFace.tsx`, `shell/LibraryCard.tsx`, `shell/CardLibrary.tsx`, `shell/LibraryPack.tsx`, `shell/libraryCatalog.ts`, `palette/ComponentPalette.tsx`, `cards/CardFrame.tsx`, `cards/NodeWorkspace.tsx`, `legions/LegionWorkspace.tsx`, `state/cardLibrary.ts`, `types/world.ts`, `api/client.ts`, `debug/FinishPreview.tsx`, `debug/DevelopmentPanel.tsx`, `main.tsx`, associated surface CSS and finish translations in `i18n/messages.json`.

Custom card integration: `cards/ContainerFrame.tsx`, `cards/Equipment.tsx`, `cards/ShadowCollection.tsx`, `cards/equipment.css`, `cards/shadowCollection.css`, `legions/legionWorkspace.css` and `theme.css`.
