# Persistent card finishes

Pack opening assigns a single print finish to each newly acquired collection entry. The authoritative probability table is `backend/card_finishes.py::CARD_FINISH_WEIGHTS`: normal 72%, foil 16%, rainbow 6%, starlight 4%, laser 2%. The small `weighted_choice` utility accepts one random sample, supports relative and zero weights, and validates its inputs. Pack generation previously had no seeded random stream.

## Ownership and persistence

OAW's collection currently has one owned entry per card type, and decks reference those entries. Finish belongs to `CollectionEntry`, never `NodeTypeCatalogItem`. Opening overlapping packs adds provenance to the existing entry without granting another copy or rolling again. This feature intentionally does not change that collection model.

Only explicit `open_pack` creation of a new entry calls `roll_card_finish`. Reconciliation, legacy import, repeated pack requests and deck moves preserve the stored value. Library JSON in SQLite's application settings includes finish. Existing entries missing the field deserialize to `normal`.

Placement through `/card-library/nodes` copies the owned finish to the new `Card`. World instances persist their own finish in an additive SQLite `cards.finish` column with a `normal` default; old world cards are not recolored from today's collection. The immutable presentation field travels through API normalization, undo restoration, agent duplication, saved legion blueprints, summoning and published workspace projections. Two world instances of the same type can retain different finishes. There is no render-time randomness or finish mutation endpoint.

## Material rendering

`frontend/src/cards/CardFinishLayer.tsx` is a pointer-transparent, self-clipped decorative layer. A normal card creates no material DOM. The artwork stays in the existing renderer. Shared `CardStock` integrates Library and deck faces; workspace surfaces use the same layer. Library inspection enables the showcase material, while text-heavy inspectors and legion workspaces restrict decoration to their headers/tabs.

Expanded containers, equipped cards and shadow collections also use the shared layer on their existing chrome. The fallback positioning selector has zero specificity so it cannot override absolute headers or custom drag geometry.

- **Foil:** silver response, fine grain and a broad reflection with weak spectral color.
- **Rainbow:** a saturated cyan, violet and magenta spectrum, a clear reflection and microtexture.
- **Starlight:** a smoked base and a fixed, non-tiling flake plate beneath a clear coat.
- **Laser:** an engraved holographic relief and a directional diffraction highlight.

### Regional physical shading

Every special finish uses `CardMaterialCanvas`, including collection showcases, hand thumbnails, placed world cards and their semantic zoom projections. A single shared offscreen WebGL renderer draws procedural material maps, then copies each result to a passive 2D canvas. The shader uses GGX direct specular, Schlick Fresnel and an analytic studio environment. Artwork, resin badges, matte copy and polished borders have separate responses. `CARD_MATERIALS` in `cardMaterialRenderer.ts` defines metalness, roughness, transmission, emission and IOR by finish and region; badge bounds and the copy boundary are measured from the actual face layout rather than assumed percentages. Measurements use untransformed layout coordinates, and all hosts share a canonical print aspect, so deck rotation and canvas zoom cannot change the print. World cards mask the material before the title and keep preview content above decoration.

Foil has a brushed normal field and silver strip reflections. Holo (the persisted `rainbow` value) uses irregular triangular facets with separate normals and optical film thickness. Starlight combines fine embedded flakes with angle-dependent emissive starbursts. Laser uses a continuous engraved normal field and directional spectral reflection. All patterns stay fixed in card UV space. Transmission approximates the procedural studio environment; it does **not** refract HTML text, external artwork or the page behind the card. Image faces use a lighter material overlay to preserve their artwork.

GPU work is event-driven: visible entry, measured layout changes and the existing coalesced pointer callback. Offscreen faces do not draw; rest rasters use a bounded 20-entry cache. Standard/showcase rasters are capped at 640 × 800 and 1.5× device scale; thumbnails use the same shader with up to 2× sampling capped at 256 × 320. There is no animation loop and quality changes never substitute a different illustration. Text-heavy expanded world surfaces restrict the finish to their headers. Shared CSS/SVG plates remain a fallback for missing WebGL or shader initialization failure. Observers and pending callbacks are released on unmount; development hot replacement also releases GPU resources.

Workspace, inspector, container and Legion titlebars/tabs explicitly select `surface="chrome"`. This crops the same procedural stock at a fixed 320 × 400 layout-pixel scale instead of squeezing the entire portrait into a shallow bar. The shader print transform is independent of the badge and text masks, and belongs to the rest-raster cache key. Chrome has a separate 1536 × 160 raster cap (up to 1.5× device scale), avoiding the few blurry scanlines produced by a 256-pixel thumbnail cap on a wide window. Its reflection, rim and CSS fallback are subdued around the title; ordinary card and hand rendering retain their existing intensity and projection.

The regional controls follow the same separation as [physical material channels](https://threejs.org/docs/pages/MeshPhysicalMaterial.html), but this lightweight compositor is an approximation for DOM cards, not a full 3D scene renderer.

`useCardFinish` measures once on pointer entry, normalizes local coordinates and coalesces CSS-variable updates into one requested frame. The studio light stays fixed at the upper left; surface orientation changes the broad reflection. Collectible `CardStock` faces opt into gentle perspective rotation, including ordinary and thumbnail cards. Canvas nodes keep their existing geometry. The hook never updates React state on pointer movement or writes the host transform, and cancels pending work on leave, scroll, resize, drag, finish change and unmount. Existing pack and deck movement is preserved.

Thumbnail fallback plates omit the extra grain element. Idle cards have no JavaScript animation loop or permanent GPU promotion; only interaction or layout/visibility changes queue a paint. Shared SVG fallback print plates are generated once at module initialization. Pack illumination mounts after the face emerges and runs once. Touch input and reduced motion retain a static material and static revealed faces. Blend/mask fallbacks reduce decoration while retaining readable artwork.

The shared collectible face uses five minimal layouts, muted colours and a clear title sheet. See [Card design language](card-design.md) for presets, artwork and plugin integration. Library collection and detail panes scroll independently inside the bounded dialog. The showcase card scales to available pane height; narrow screens place the selected detail above the collection.

## Development preview

Run the frontend dev server and open `/?card-finishes`, or open **DEV · F3 → Card finish preview** in a development profile. The standalone gallery works without a backend. It includes every finish, dark/bright stock, a one-shot reveal and a 200-thumbnail mode. It changes no collection data or probabilities. The route and tools are excluded from production builds.

## Validation

- `backend/tests/test_card_finishes.py`: deterministic weight boundaries, invalid distributions, exactly one sample per roll, new entry assignment, overlap/reopen/stale requests, legacy defaults, SQLite restart, deck moves, placement, duplication, legion capture, restore and deployed workspace persistence.
- `frontend/src/cards/cardFinish.test.ts`, `CardFinishLayer.test.tsx`: compatibility, stable rendering, pointer coalescing, cleanup, interaction pass-through, reduced motion and 200 passive materials.
- `frontend/src/api/client.test.ts`: API load/create/restore preservation.
- Library, pack, world card and `NodeWorkspace.finish.test.tsx` component tests cover stored finish propagation, stale parent snapshots, reveal timing and legacy cards.
- `ContainerFinishes.test.tsx` covers expanded containers, equipment and shadow collections. The Library browser scenario checks placement, undo/redo and reload against the saved finish, plus three viewport sizes.
- `frontend/e2e/card-finishes.spec.ts`: real browser light/dark inspection, actual shader pixel response and rest restoration, regional alpha, a single shared GPU context, WebGL fallback, reduced motion and idle performance for 200 cards. Holo pixel samples are compared across the showcase, rotated hand thumbnail and production React Flow world card; dragging and semantic zoom retain the finish. Screenshots are diagnostic artifacts, not pixel assertions.
- The same browser suite mounts real workspace and inspector surfaces to check chrome raster resolution, two-axis texture detail, title readability in both themes, and narrow-window resizing.
- `frontend/e2e/library-preview-layout.spec.ts`: showcase/thumbnail tilt, full preview visibility and independent scrolling at desktop, narrow and mobile sizes, including long descriptions and a full collection.

Run `npm.cmd test -- --maxWorkers=2 --minWorkers=1` and `npm.cmd run build` in `frontend`, and `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_card_finishes.py` from the repository root. The repository currently has no configured formatter; retain surrounding conventions and check whitespace with `git diff --check`.

For browser material checks, start Vite, set `OAW_E2E_BASE_URL` to its local URL and run `node node_modules/@playwright/test/cli.js test e2e/card-finishes.spec.ts`. The existing `run-e2e.mjs` harness starts an isolated backend for Library integration checks.

## Main implementation files

Backend: `card_finishes.py`, `card_library.py`, `api/card_library.py`, `world/models.py`, `world/store.py`, `persistence/database.py`, `legions/models.py`, `services.py`, `deployment_workspace.py`.

Frontend: `cards/cardFinish.ts`, `cards/CardFinishLayer.tsx`, `cards/cardFinish.css`, `cards/useCardFinish.ts`, `components/CardFace.tsx`, `shell/LibraryCard.tsx`, `shell/CardLibrary.tsx`, `shell/LibraryPack.tsx`, `shell/libraryCatalog.ts`, `palette/ComponentPalette.tsx`, `cards/CardFrame.tsx`, `cards/NodeWorkspace.tsx`, `legions/LegionWorkspace.tsx`, `state/cardLibrary.ts`, `types/world.ts`, `api/client.ts`, `debug/FinishPreview.tsx`, `debug/DevelopmentPanel.tsx`, `main.tsx`, associated surface CSS and finish translations in `i18n/messages.json`.

Custom card integration: `cards/ContainerFrame.tsx`, `cards/Equipment.tsx`, `cards/ShadowCollection.tsx`, `cards/equipment.css`, `cards/shadowCollection.css`, `legions/legionWorkspace.css` and `theme.css`.
