# Persistent card finishes

Pack opening assigns a single print finish to each newly acquired collection entry. The authoritative probability table is `backend/card_finishes.py::CARD_FINISH_WEIGHTS`: normal 72%, foil 16%, rainbow 6%, starlight 4%, laser 2%. The small `weighted_choice` utility accepts one random sample, supports relative and zero weights, and validates its inputs. Pack generation previously had no seeded random stream.

## Ownership and persistence

OAW's collection currently has one owned entry per card type, and decks reference those entries. Finish belongs to `CollectionEntry`, never `NodeTypeCatalogItem`. Opening overlapping packs adds provenance to the existing entry without granting another copy or rolling again. This feature intentionally does not change that collection model.

Only explicit `open_pack` creation of a new entry calls `roll_card_finish`. Reconciliation, legacy import, repeated pack requests and deck moves preserve the stored value. Library JSON in SQLite's application settings includes finish. Existing entries missing the field deserialize to `normal`.

Placement through `/card-library/nodes` copies the owned finish to the new `Card`. World instances persist their own finish in an additive SQLite `cards.finish` column with a `normal` default; old world cards are not recolored from today's collection. The immutable presentation field travels through API normalization, undo restoration, agent duplication, saved legion blueprints, summoning and published workspace projections. Two world instances of the same type can retain different finishes. There is no render-time randomness or finish mutation endpoint.

## Material rendering

The v1 surface engine is a layered, parameter-driven model. Material IDs do not appear in either optical kernel: a preset supplies weights to the same reflection, interference, diffraction, flake and clearcoat lobes.

### Shared print stack

The [card production model](card-design.md) owns the design; this engine renders its ordered process plates. Existing cards without `production.layers` retain the following legacy stack:

1. **Stock / substrate:** authored paper, grain and cut edge.
2. **Printed artwork:** permanent DOM/SVG/illustration, visible without any surface process.
3. **Selective finishing:** local UV gloss, metal stamping, emboss relief and edge foil.
4. **Laminate / optical film:** an optional thin film above the finishing.
5. **Protected top print:** titles, copy, symbols, labels and controls, composited above the optical passes. Transparent ink and intentionally uncoated backed labels have distinct semantics.

The two legacy surface passes are evaluated separately, then composited film-over-finishing into one pointer-transparent canvas at z-index 3. Protected DOM ink remains at z-index 4. Continuous laminate is visible through its transparent surroundings; only an explicitly uncoated label or region cuts a hole in the surface passes. Workspace and inspector chrome stay matte.

An explicit `production.layers` stack replaces those two aggregate passes. Each entry renders an independent masked plate in array order; repeats are supported. A laminate can precede emboss, foil, ink or UV, and later plates composite over earlier ones. An empty array renders no processes. Legacy base artwork stays below the stack. With `print.layered: true`, authored ink content joins that stack through `InkPrintLayer`, so text, shapes, illustrations and live fields can print before or after any coating. Exact coverage controls which regions receive each explicit process; unlike legacy masking, it does not automatically exclude text or controls. This is a visual approximation of authored process order, not a simulation of adhesive chemistry or physical manufacturing feasibility.

Normal is a complete uncoated design with zero tooling and film energy. Its canvas is elided outside debug/proof views. Film alpha is capped at 0.32, including clearcoat and flakes, in both kernels. Default holo/aurora recipes are substantially below that ceiling. Metal may be opaque locally, but only inside its tooling mask.

### Schema and presets

`cards/cardMaterial.ts` owns `CardMaterial`, schema version 1, bounds, presets and environment normalization. `configureMaterial` merges partial groups without mutating presets, clamps values and rejects nonfinite inputs. Protection is not a user-tunable weight.

| Group | Parameters | Responsibility |
| --- | --- | --- |
| laminate | opacity, roughness, metalness | Film energy and neutral metallic reflection |
| response | specular, iridescence, diffraction, sparkle | Weights of the shared optical lobes |
| pattern | scale, brush, domains, flow, grooves | Anchored surface structure / thickness |
| clearcoat | strength | Achromatic area-light highlight |
| mask | artwork, frame, accent | Coating weights, applied before mandatory protection |

Low-level optical descriptors retain six canonical IDs: `normal / foil / holo / aurora / laser / starlight`. Production recipes expose four representative MVP treatments:

- **Normal:** uncoated print, zero film and clearcoat energy.
- **Foil:** selective champagne-gold or silver tooling on printed accents and the cut edge; no optical film by default. The low-level legacy foil descriptor remains available to specialist callers.
- **Holo:** embossed domains with angular diffraction and spectral separation.
- **Aurora:** continuous warped thickness with broad interference colour travel.
- **Laser / starlight:** extension presets using the same groove and flake parameters; no separate rendering branches. Shared clearcoat can remain visible outside the laser diffraction window.

Storage remains independent: existing `CardFinish` IDs and reward probabilities are unchanged. `materialForFinish('rainbow')` maps to `aurora` to preserve the old flowing-film identity. Holo is a distinct laminate, selectable in the production editor; this change does not add new inventory rewards.

### Light, view and optical evaluation

`CardMaterialOptions.pose` controls the card-local view independently from `environment.light`, `intensity` and `ambient`. Both tooling and film share the view and light. The renderer resolves a view vector, light vector and half vector once conceptually for all lobes, using the actual surface aspect ratio. Surface fields remain anchored to print coordinates; changing the pose changes reflected energy and optical phase. There is no time uniform or random animation state.

`cardMaterialShader.ts` is the shared GLSL evaluator. `cardMaterialOptics.ts` is its pure CPU reference. They use the same parameter contract and equations; GPU precision, flake hashes and raster resolution can differ. This is a physically inspired RGB approximation, not a calibrated spectral/PBR simulation or an environment-map renderer. The existing DOM artwork supplies the base colour; the renderer supplies selective tooling and the bounded optical overlay. Emboss is a mask-gradient lighting approximation; it does not displace geometry.

### Mask contract

`MaterialMask` contains two immutable, opaque canvases:

- `regions`: R = artwork, G = frame, B = accent. Channels can overlap.
- `protection`: white = protected, black = available for coating.

Both renderers apply this film coverage:

```text
coverage = max(artwork * weight, frame * weight, accent * weight)
         * (1 - protection)
```

Stamping follows B (accent) or R (artwork), edge foil follows G (frame), and spot UV follows R. Emboss derives a local relief gradient from the selected tooling region. Every finishing contribution is multiplied by mandatory protection before compositing.

Separate textures preserve RGB values independently from alpha premultiplication. Protection always wins, regardless of draw order, preset or parameter overrides.

New layouts declare `data-material-region="artwork|frame|accent"` and distinguish two kinds of protected print:

- `data-material-layer="top-print"`: transparent foreground ink at z-index 4. It does **not** exclude laminate from its bounding rectangle. Use it for titles, copy, icons and metadata printed over a continuous coated board. Controls inside this layer inherit its compositing policy.
- `data-material-layer="protected"` (or legacy region `text|icon|background`): an intentional uncoated label/panel, including its backing. Its rectangle and antialias padding exclude both finishing passes. Use this only when the authored design calls for that cutout.

`WorldCardPrint` gives full, mid and far canvas cards a complete artwork plane and a shared printed accent rule. Header/body wrappers must not create a stacking context above the film; only top-print information does. Built-in image previews remain below it. Unknown plugin previews default to protected information widgets; authored factory art supplies its own layer contract. Workspace and inspector chrome stay matte. Legacy selectors for explicitly backed collectible/factory layouts remain confined to the DOM-mask adapter. Nested card surfaces own their masks and are excluded from the parent's coating.

The adapter measures untransformed layout coordinates, so outer card tilt, dragging and canvas zoom do not move the masks relative to print. Resize, content, role, class and child style changes invalidate masks; pose-only CSS writes on the host do not. Arbitrarily transformed inner artwork or text is outside this rectangular DOM adapter's MVP contract; a custom renderer caller can supply authored mask canvases.

### Integration

Existing callers can continue to pass `finish`. The adapter resolves it to production intent. New card authoring supplies an explicit recipe:

```tsx
const production = productionForFinish('foil');
production.laminate = { type: 'holo', strength: 0.45, roughness: 0.38 };

<CardStock size="standard" materialOptions={{ production }}>
  <CardFace icon={<MyIcon />} label="Protected title"
    description="Protected information" artwork={<MyArtwork />} />
</CardStock>
```

`cardProduction.ts` separates production intent from optical parameters. `compileProduction` outputs the legacy normalized finishing pass plus a `CardMaterial`. `compileProductionLayer` outputs one independent optical plate; the process renderer supplies its coverage, colour and relief direction. A zero film strength extinguishes all film lobes, including flakes. Direct `material` overrides remain available for optical diagnostics; they do not replace production presets in the normal editor.

Explicit masks select all card pixels, text glyphs, shapes, artwork, legacy accents/frame regions, specific element IDs, a classic pattern, or a bounded embedded PNG. PNG coverage follows alpha or luminance, can be inverted, and defaults to aspect-preserving containment (`cover` and `stretch` are explicit alternatives). Every mask is clipped to the authored card silhouette. Reordering modifies composition without rewriting any element geometry or the mask payload.

Create a material by adding preset data. Add a new optical primitive only if the schema needs a genuinely new response; implement it in both reference kernels and validate its masked output. Do not add a finish-ID branch, full-card CSS filter or independent per-preset animation.

### Rendering lifecycle and fallback

One offscreen WebGL context serves all visible cards via 2D snapshots. The renderer resolves inputs, uploads region/protection masks, binds shared uniforms, evaluates tooling and optical lobes, applies their masks and composites below DOM print. Rasters are capped at 640 x 800 for standard/showcase and 256 x 320 for thumbnails.

Pointer input is coalesced and finite interpolation stops when settled. Offscreen work stops; unmount releases observers, listeners and pending callbacks. Production has no autonomous animation. Touch and reduced-motion input retain a static finish. A controlled pose disables pointer tilt.

When WebGL is unavailable or lost, expensive optical fields are evaluated at a bounded 192 x 240 resolution. The fallback then applies finishing masks, emboss gradients and mandatory exclusion at the original canvas resolution. This preserves thin tooling lines and prevents colour leaking into protected print. Optical micro-detail and floating-point precision can differ from the GPU. GPU restoration is used on the next render request. HMR disposes shared GPU resources.

## Development inspector

Run Vite and open `/?card-materials`. No backend is required. The page changes no collection data.

- Four representative presets with identical artwork, light, view and mask semantics.
- Independent view X/Y and studio-light X/Y.
- Composite, uncoated print, selective finishing, optical film, region, protection and film-coverage views.
- Shared production controls for stock, printed pattern, finishing and laminate; preset reset and resolved JSON.
- Renderer/settled markers and a forced CPU fallback.
- Light/dark surroundings, thumbnails, pointer interaction and optional laser/starlight extension presets.

The existing `/?card-finishes` gallery remains a compatibility and performance harness for saved IDs, layer comparisons, opt-in development sweeps and 200 passive thumbnails. It links to the inspector.

## Validation

- `cardMaterial.test.ts`: schema bounds, immutable presets, storage compatibility, zero-energy normal, finite and deterministic evaluation, continuous view response and independent lighting.
- `card-materials.spec.ts`: four-preset WebGL integration, one GPU context, pixel-stable top print, independent masks, role/style invalidation, responsive fallback and editable parameters.
- `card-finishes.spec.ts`: retained collection-surface tests, legacy IDs, hand/world/LOD layouts, drag and zoom, matte chrome, reduced motion, fallback and 200 passive cards with zero idle animation callbacks.
- `cardProduction.test.ts` and `productionPresets.test.ts` cover independent passes, zero film strength and persistent recipes; `card-production.spec.ts` covers the seven-stage editor and mobile layout.
- `card-world-materials.spec.ts`: real React Flow cards in GPU and CPU modes, continuous coating through title/body spacing, stable solid ink pixels, coated images, selective foil rules, light/dark themes, LOD and collapse.
- Existing card, Library, pack and factory unit tests continue to cover ownership and surface integration. Backend ownership and probability logic are unchanged.

Run from `frontend`:

```powershell
npm.cmd test -- --maxWorkers=2 --minWorkers=1
npm.cmd run build
# In another terminal, start npm.cmd run dev and use its URL:
$env:OAW_E2E_BASE_URL='http://127.0.0.1:5173'
$env:PLAYWRIGHT_CHANNEL='msedge' # or installed Chrome
npx.cmd playwright test e2e/card-production.spec.ts e2e/card-materials.spec.ts e2e/card-finishes.spec.ts e2e/card-design.spec.ts e2e/card-world-materials.spec.ts
```

Browser screenshots and numeric assertions check invariants rather than fixing artistic brightness or sparkle counts to a single preset revision.

## Main implementation files

Backend: `card_finishes.py`, `card_library.py`, `api/card_library.py`, `world/models.py`, `world/store.py`, `persistence/database.py`, `legions/models.py`, `services.py`, `deployment_workspace.py`.

Frontend: `cards/cardFinish.ts`, `cards/CardFinishLayer.tsx`, `cards/cardFinish.css`, `cards/useCardFinish.ts`, `components/CardFace.tsx`, `shell/LibraryCard.tsx`, `shell/CardLibrary.tsx`, `shell/LibraryPack.tsx`, `shell/libraryCatalog.ts`, `palette/ComponentPalette.tsx`, `cards/CardFrame.tsx`, `cards/NodeWorkspace.tsx`, `legions/LegionWorkspace.tsx`, `state/cardLibrary.ts`, `types/world.ts`, `api/client.ts`, `debug/FinishPreview.tsx`, `debug/DevelopmentPanel.tsx`, `main.tsx`, associated surface CSS and finish translations in `i18n/messages.json`.

Custom card integration: `cards/ContainerFrame.tsx`, `cards/Equipment.tsx`, `cards/ShadowCollection.tsx`, `cards/equipment.css`, `cards/shadowCollection.css`, `legions/legionWorkspace.css` and `theme.css`.
