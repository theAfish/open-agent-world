# Card design and production

OAW cards are printed collectible objects. Their stock, composition and ink must work without finishing. The surface engine is an optional finishing service within that design, not the source of the card's identity.

## Stock, artwork and ordered process plates

| Layer | Authored data | Rendering responsibility |
| --- | --- | --- |
| 1. Stock / substrate | Cotton, Ivory, Ink or Pearl; grain and optional custom colour | Stable paper colour, small cut radius, visible cut edge |
| 2. Printed artwork | Composition, ink accent, illustration, optional contour/rays/grid print, per-layer opacity and blend | Permanent DOM/SVG/image artwork, present on an uncoated card |
| 3. Ordered processes | Repeatable ink, laminate, foil, emboss and UV plates, each with its own mask | Apply in array order; later plates cover earlier plates |
| 4. Interactive controls | Inputs, action buttons and result panels | Preserve readable, usable controls across process passes |

Foil is a masked metal process. It can coexist with any laminate, including multiple laminate plates before and after it. The neutral `normal` preset is an uncoated print. In the unified ink workflow, removing all layers leaves only the stock. Older recipes keep their original base artwork until the first stack edit. Legacy recipes without an explicit process stack keep their original finishing-then-laminate rendering.

## Shared visual language

`CardFace` retains the collectible compositions used by inventory and deck previews. The factory's `FaceArtwork` is the authored world-card face: its absolute element geometry, paper, print and controls are shared by the designer and actual canvas cards. It uses the continuous `WorldCardPrint` backing without adding edition text, medallions or opaque text panels. A blank print adds no generated motif. Inventory and deck compositions are maintained separately.

Collectible and factory stock stays the same in light and dark application surroundings. `dark` remains an explicitly authored dark card variant. The stock palette supplies readable primary and secondary ink. Titles occupy at most two visible lines in collectible previews; thumbnails retain the symbol/artwork and title. Images should not contain critical text.

| Variant | Composition |
| --- | --- |
| icon | Framed printed pattern, protected icon medallion, title and short copy |
| image | Inset illustration with protected copy below it |
| text | Smaller printed field, more space for an excerpt |
| compact | Compact centred identity |
| dark | The same hierarchy on deep dyed board |

Tones (`midnight / sage / sand / sky / rose / stone`) determine the print accent. Layout, stock, ink and finishing have distinct responsibilities. Canvas preview/node cards use `WorldCardPrint` for a continuous printed board, subdued contour artwork and a selective foil rule. Their title, icon and description are transparent top print, allowing the film to remain continuous around the ink. They retain the canvas theme's stock/ink palette and interaction geometry. Full and simplified LOD views share this layer contract. Canvas workspace chrome and plugin-owned functional layouts keep their existing structure.

## Production schema

`cards/cardProduction.ts` owns the portable, versioned `CardProduction` model:

```ts
{
  version: 1,
  stock: { type: 'ivory', grain: 0.32, color: '#f5f3e9' },
  print: { motif: 'contour', density: 0.38 },
  finishing: {
    spotUV: 0, foil: 0.7, emboss: 0.2, edgeFoil: 0.55,
    target: 'accents', foilTone: 'gold',
  },
  laminate: { type: 'aurora', strength: 0.45, roughness: 0.38 },
}
```

The fields above remain the legacy format. New authoring adds `layers`, an ordered array of at most 24 independent plates:

```ts
layers: [
  {
    id: 'film-first', kind: 'laminate', enabled: true,
    strength: 0.45, roughness: 0.38, color: '#31594b', film: 'holo', relief: 'raised',
    mask: { source: 'all', elementIds: [], preset: 'border', png: '', channel: 'alpha', invert: false },
  },
  {
    id: 'gold-copy', kind: 'foil', enabled: true,
    strength: 0.7, roughness: 0.38, color: '#d6ae61', film: 'holo', relief: 'raised',
    mask: { source: 'text', elementIds: [], preset: 'border', png: '', channel: 'alpha', invert: false },
  },
]
```

Plate kinds are `ink`, `laminate`, `foil`, `emboss` and `uv`; each may occur repeatedly. `film` chooses gloss, holo, aurora, laser or starlight for laminate. Emboss `relief` chooses `raised` or `recessed`. Mask sources include the whole card, text glyphs, shapes, artwork, selected `elementIds`, classic `preset` masks, or an embedded `png`. `accents` and `frame` retain precise legacy region targeting. Presets are `border`, `corners`, `diagonal` and `dots`. PNG masks support alpha or luminance coverage, inversion and optional `fit: 'contain' | 'cover' | 'stretch'` (default `contain`). Images are bounded to 1 MiB and 2048 × 2048; external URLs and SVG/markup are rejected.

An omitted `layers` field uses the original aggregate finishing and laminate fields. An explicit `layers: []` means no process plates, even if legacy fields still contain effects. `productionLayers` supplies a nonmutating adapter for the first stack edit. It preserves the original tooling targets and creates stable legacy plate IDs. Array order is execution order; the editor displays that same order from first to last.

Process strengths, grain and density range from 0 to 1; roughness ranges from 0.06 to 1. Stock `color` is an optional six-digit hex override, independent of its texture. Omitting it restores the stock's original palette. Backend models in `backend/packs/face_design.py` enforce the same ranges and closed enums; executable data and arbitrary shader code are not accepted. These are visual process recipes, not fabrication specifications: physical material compatibility and machine setup are outside the model.

`SurfaceRecipe.production` persists the recipe through editing, printing, legion capture, pack export and clean-profile installation. Existing recipes retain their legacy `material` metadata; `recipeProduction` adapts it read-only until an edit writes the explicit production model. Hand-positioned geometry, inline images and free layers are preserved. Existing inventory finishes and reward probabilities are unchanged.

## Editor

The factory and development studio use the same `FaceDesignerCanvas`:

1. **Stock / 卡纸:** choose paper texture, colour and grain.
2. **Process layers / 工艺层:** individual passes appear in the top production path, between paper and the finished proof. Select, drag to reorder, or add passes there. The sidebar contains only the active pass’s basic settings, expandable text / graphics / image panels, and its owned elements. Layouts, classic process combinations and saved recipes share the toolbar’s “预设” entry. Printed patterns live inside an ink pass’s image panel.
3. **Proof / 成品:** inspect the finished card or individual pass families and compare against ink without coatings. Saving a reusable production preset is optional.

Only the paper step hides all print. The process path scrolls horizontally and supports direct selection, arrow keys and previous/next navigation between individual passes. Content can be dragged directly while editing process layers. Advanced geometry and token overrides remain local to the selected node/card/inspector/workspace view; normal recipe controls synchronize appearance across views. Undo/redo includes production changes and applying presets.

Stored geometry is authoritative: loading, saving, changing copy or paper, releasing preset elements and dragging one element must not reflow neighbouring elements. New elements start as free layers. Reflow is reserved for explicit layout choices or constraint changes; existing free layers retain their coordinates. Dragging automatically releases the selected element. Alt temporarily disables snapping.

Both `FaceShape` and `FaceElement` accept optional `print: { opacity, blend }`, where opacity ranges from 0 to 1 and blend is `normal`, `multiply` or `screen`. Omitted settings mean opaque normal ink. Within each shape/content stack, later array entries print above earlier entries; Shapes remain the base stack below content. Ink opacity never changes the solid cutting mask. With `print.layered: true`, every pass can own `content: { source: 'all' | 'elements', elementIds: string[] }`. Ink renders its DOM/SVG/image content at that process position; non-ink passes use the owned elements’ silhouettes. `all` owns the background/pattern and content not assigned to explicit element plates. Selected IDs remain tied to each view's authored geometry. `strength` and optional `blend` apply to the whole ink plate, and its mask clips the rendered content, including PNG coverage. Ink layers without `content` remain solid spot inks. New ink passes can also own `pattern: { motif, density }`, independently from other ink passes. New passes default to normal compositing; the editor does not expose blend, ink-mode, content-transfer or coverage selectors. Non-ink passes with live owned elements follow those elements automatically. Empty laminate, emboss and UV cover the card; empty foil and ink remain blank. “铺满卡面” adds a full-size editable rectangle to an opaque pass. Semantic slots are unique within each pass; free text and uploaded images can repeat. Duplicating a pass clones its elements and deleting it removes its exclusive elements, with undo support. Legacy explicit masks remain readable. A hidden, inert stencil supplies stable mask geometry independently of which ink passes are visible. Later ink can print above earlier laminate or foil. `printingLayers` adapts the original artwork into a first ink plate on the first edit; simply opening a legacy design does not mutate it.

Legacy factory text and SVG icons use glyph-shaped protection, leaving the film continuous through the empty space around their ink. Explicit process masks can intentionally target that text for foil, ink, emboss or UV. They apply exactly the authored coverage without the legacy protection knockout; full-sheet or selected-control coverage can also coat functional regions. Inventory and ordinary world-card masks retain their existing behaviour.

`FaceFunctionElement` supplies identical field, action and result markup for designer proofs and live cards. Passing `functionDesign` to `FaceArtwork` previews the actual field labels/defaults; a standalone face design uses example fields until a function is attached. Recipe thumbnails use inert visual placeholders, so preset buttons contain no nested interactive controls.

The element palette includes a Buttons panel on ink passes. Repeatable `kind: 'button'` elements carry `button: { action, background?, radius? }`, with closed actions `open`, `collapse`, `surface`, `delete`, `run`, and `custom`. Text and geometry use the existing element fields. The legacy `action` element still runs the configured function. Built-in buttons dispatch through host surface/deletion APIs; run buttons submit the authored form with its field validation. Proofs and thumbnails do not dispatch operations. Printed studio cards no longer render an external toolbar. Proof and printer warnings report missing navigation, deletion, and run controls without blocking saving or printing; disabled ink passes, zero opacity and off-canvas elements do not satisfy these checks. Arbitrary masks and occlusion still require visual inspection.

For future button logic, `FaceButtonEvent` defines the stable `{ mode, element_id }` address and optional `FunctionDesign.button_bindings` stores `{ mode, element_id, logic_id }` records (at most 128, unique by mode/element). Identifiers are bounded to 64 ASCII letters/digits/underscores/hyphens, beginning with a letter. Missing bindings serialize without new legacy fields. `custom` buttons expose the intended binding points but remain disabled in this version; stored bindings are portable metadata and never execute scripts or override built-in actions. The existing `oaw.factory.face-input` relationship also accepts a function designer target, whose `/context` exposes the saved face. The function designer lists enabled-view button targets using the same addresses. New workshop presets include this connection. Buttons and reserved bindings survive printing, Legion capture, export and clean-profile installation.

Presets use the profile preference store (local storage only in the backend-free development route), retain at most 12 named recipes, and replace an existing preset with the same name. They store design intent, accent colour and process masks (including embedded PNG masks), without card titles, descriptions or illustrations. The encoded preset list is limited to 1,000,000 bytes by profile preferences; an oversized save reports an error before replacing existing presets. This preference limit does not change the card's own save or print path.

## Integration

Existing plugins may use `CardFaceSpec` from the Python plugin API or the exported `CardStock` and `CardFace` components from `@oaw/plugin-api`. Existing calls with `finish` remain supported:

```tsx
<CardStock size="standard" finish={card.finish} quality="standard"
  style={{ width: 200, height: 280 }}>
  <CardFace variant="icon" tone="sand" icon={<MyIcon />}
    label="Python Console" description="Run code and explore data." />
</CardStock>
```

Internal integrations may supply `materialOptions={{ production }}`. `stockStyle` supplies stable stock tokens. `compileProduction` resolves the legacy pair; `compileProductionLayer` resolves one plate, with its coverage and order supplied by the process renderer. Direct material overrides are for diagnostics and specialist callers; the ordinary editor exposes production choices.

New artwork declares `data-material-region="artwork|accent|frame"`. Information sheets, icons and controls declare `data-material-layer="protected"`. See [surface engine](card-finishes.md) for masking, light/view and renderer details.

## Development and validation

- `/?card-design`: collectible design reference, all layouts and thumbnail scale.
- `/?card-studio`: actual editor and portable recipe inspection, no backend required.
- `/?card-materials`: production parameters, normal/foil/holo/aurora comparison, masks, separate passes and CPU fallback.
- `/?card-finishes`: saved-finish compatibility and 200-card idle-performance study.

All routes are development-only. Unit tests cover compilation, bounded recipes, zero-strength films, selective mask targeting, undo and preset persistence. Browser tests cover information stability, light/view response, mobile layouts, masks, single-context rendering and idle performance. Backend tests round-trip both legacy and explicit production recipes through printing, export and clean install.
