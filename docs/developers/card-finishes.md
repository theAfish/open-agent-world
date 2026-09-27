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

`useCardFinish` measures once on pointer entry, normalizes local coordinates and coalesces CSS-variable updates into one requested frame. The studio light stays fixed at the upper left; surface orientation changes the broad reflection. Collectible `CardStock` faces opt into gentle perspective rotation, including ordinary and thumbnail cards. Canvas nodes keep their existing geometry. The hook never updates React state on pointer movement or writes the host transform, and cancels pending work on leave, scroll, resize, drag, finish change and unmount. Existing pack and deck movement is preserved.

Thumbnail materials omit grain. Idle cards have no JavaScript animation loop or permanent GPU promotion; only the hovered collectible queues a paint. Shared SVG print plates are generated once at module initialization. Higher quality materials activate their stronger reflection only during interaction. Pack illumination mounts after the face emerges and runs once. Touch input and reduced motion retain a static material and static revealed faces. Blend/mask fallbacks reduce decoration while retaining readable artwork.

The shared collectible face uses an engraved crest, a framed emblem, double printed borders and a separate paper title plaque. Library collection and detail panes scroll independently inside the bounded dialog. The showcase card scales to available pane height; narrow screens place the selected detail above the collection.

## Development preview

Run the frontend dev server and open `/?card-finishes`, or open **DEV · F3 → Card finish preview** in a development profile. The standalone gallery works without a backend. It includes every finish, dark/bright stock, a one-shot reveal and a 200-thumbnail mode. It changes no collection data or probabilities. The route and tools are excluded from production builds.

## Validation

- `backend/tests/test_card_finishes.py`: deterministic weight boundaries, invalid distributions, exactly one sample per roll, new entry assignment, overlap/reopen/stale requests, legacy defaults, SQLite restart, deck moves, placement, duplication, legion capture, restore and deployed workspace persistence.
- `frontend/src/cards/cardFinish.test.ts`, `CardFinishLayer.test.tsx`: compatibility, stable rendering, pointer coalescing, cleanup, interaction pass-through, reduced motion and 200 passive materials.
- `frontend/src/api/client.test.ts`: API load/create/restore preservation.
- Library, pack, world card and `NodeWorkspace.finish.test.tsx` component tests cover stored finish propagation, stale parent snapshots, reveal timing and legacy cards.
- `ContainerFinishes.test.tsx` covers expanded containers, equipment and shadow collections. The Library browser scenario checks placement, undo/redo and reload against the saved finish, plus three viewport sizes.
- `frontend/e2e/card-finishes.spec.ts`: real browser light/dark inspection, pointer response, reduced motion and idle performance for 200 cards. Screenshots are diagnostic artifacts, not pixel assertions.
- `frontend/e2e/library-preview-layout.spec.ts`: showcase/thumbnail tilt, full preview visibility and independent scrolling at desktop, narrow and mobile sizes, including long descriptions and a full collection.

Run `npm.cmd test -- --maxWorkers=2 --minWorkers=1` and `npm.cmd run build` in `frontend`, and `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_card_finishes.py` from the repository root. The repository currently has no configured formatter; retain surrounding conventions and check whitespace with `git diff --check`.

For browser material checks, start Vite, set `OAW_E2E_BASE_URL` to its local URL and run `node node_modules/@playwright/test/cli.js test e2e/card-finishes.spec.ts`. The existing `run-e2e.mjs` harness starts an isolated backend for Library integration checks.

## Main implementation files

Backend: `card_finishes.py`, `card_library.py`, `api/card_library.py`, `world/models.py`, `world/store.py`, `persistence/database.py`, `legions/models.py`, `services.py`, `deployment_workspace.py`.

Frontend: `cards/cardFinish.ts`, `cards/CardFinishLayer.tsx`, `cards/cardFinish.css`, `cards/useCardFinish.ts`, `components/CardFace.tsx`, `shell/LibraryCard.tsx`, `shell/CardLibrary.tsx`, `shell/LibraryPack.tsx`, `shell/libraryCatalog.ts`, `palette/ComponentPalette.tsx`, `cards/CardFrame.tsx`, `cards/NodeWorkspace.tsx`, `legions/LegionWorkspace.tsx`, `state/cardLibrary.ts`, `types/world.ts`, `api/client.ts`, `debug/FinishPreview.tsx`, `debug/DevelopmentPanel.tsx`, `main.tsx`, associated surface CSS and finish translations in `i18n/messages.json`.

Custom card integration: `cards/ContainerFrame.tsx`, `cards/Equipment.tsx`, `cards/ShadowCollection.tsx`, `cards/equipment.css`, `cards/shadowCollection.css`, `legions/legionWorkspace.css` and `theme.css`.
