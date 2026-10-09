# Packs, Card Library and Decks

[Documentation](README.md)

A Plugin installs trusted runtime code. A Pack distributes references to that
plugin's canonical card definitions. Opening a Pack collects those cards. A Deck
selects collected cards for the bottom tray. World instances remain separate
objects with their existing lifecycle, relationships and permissions.

## Using the Library

Open **Pack & Card Library** from the world controls or the tray's Library button.
The **Packs** tab shows the wrappers themselves. Click a sealed wrapper to collect
its contents; click an empty, opened wrapper to browse that pack's cards. Opening
does not fill a deck. In **Cards**, search or filter by the existing catalog category,
inspect a card and add it to the selected deck. **Decks** supports creating,
renaming, deleting and switching named decks, and removing individual entries.
The tray tabs switch the persistent active deck; clicking or dragging a card uses
the existing world placement flow. Removing a deck entry preserves the collection
and all existing world instances.

Drag a card from the bottom hand onto another deck's tab to move that entry and
switch to the destination. The source removal and target addition share one
revisioned Library transaction, so failures cannot leave the card between decks.
An existing target entry is kept once; membership in other decks is unchanged.
Dragging a saved formation from the virtual Legions tab adds it to the chosen
deck while preserving the saved formation.

The **Store** tab is a placeholder. Installation remains repository-folder or
Python-entry-point discovery followed by restarting the backend. No online
acquisition, payments, random drops, duplicates, rarity or trading are implemented.

## Plugin manifests

Use the existing Python registration mechanism (Plugin API **1.14**):

```python
from open_agent_world.plugin_api import PackDefinition

def register(self, registration):
    registration.register_node_type(tool_card_definition())
    registration.register_pack(PackDefinition(
        id="acme.tools.default",
        name="Acme Tools",
        description="Tools for a research workflow.",
        cards=("acme.tool",),
    ))
```

Register more than one pack when a plugin has multiple distribution bundles. Pack
IDs are stable, globally unique registry identifiers; use your plugin namespace.
Only that plugin's card IDs are valid references, and every registered type must
appear in at least one pack. A card may appear in multiple packs. Opening either
collects one reusable card; collection provenance records all opened source packs.
Single-card plugins follow the same contract. For an all-content pack,
`cards=tuple(registration.nodes)` avoids repeating the node list; call it after
registering all node types. Bundled plugins use explicit pack definitions.

Older plugins without manifests receive a compatibility pack named
`<plugin-id>.default`. There is no new YAML parser or separate metadata registry.
PluginRegistry stages and validates packs atomically with other contributions;
`GET /api/catalog` includes their manifests. Existing `deck_id`, `deck_label` and
`deck_icon` metadata now describe catalog categories and legacy migration, rather
than ongoing deck assignment. The frontend still reads `deck_revision` during
the one-time import of old browser folders.

Managed types also belong to packs. They can be inspected in the collection but
cannot be added to a deck when `user_creatable=False`; their existing container or
domain action creates them. User-saved Legion formations retain their LegionStore
identity and blueprint dependencies. They appear alongside collected cards under
**Saved Legions** and may be explicitly selected for decks without a synthetic
plugin or pack. Pack-provided Legion capabilities can use ordinary registered
card definitions in the future.

## Pack appearance

Plugin API **1.15** supports optional `artwork_asset` and `accent_color` on
`PackDefinition`. Register an image with the existing `PluginAsset` contract,
then reference its plugin-local ID:

```python
from importlib.resources import files
from open_agent_world.plugin_api import PackDefinition, PluginAsset

registration.register_asset(PluginAsset(
    id="pack-cover",
    content=files(__package__).joinpath("assets/pack-cover.svg").read_bytes(),
    media_type="image/svg+xml",
))
registration.register_pack(PackDefinition(
    id="acme.tools.default",
    name="Acme Tools",
    cards=("acme.tool",),
    artwork_asset="pack-cover",
    accent_color="#527b70",
))
```

Artwork uses the existing SVG/PNG/JPEG/WebP/GIF asset endpoint and 5 MiB limit.
An unregistered or another plugin's asset is rejected atomically. The catalog
publishes `artwork_url`; clients do not resolve files or accept executable views
for the cover. Use a portrait composition (roughly 3:4) with room for the host's
name, count and foil treatment. Artwork is cropped to fill the printed panel.
`accent_color` accepts a six-digit hex color. Without artwork (or if it fails to
load), the host prints a line pattern, pack name, plugin name, description and
card count, using the pack accent or the packaging preset's default color.

Plugin API **1.25** adds `packaging` to `PackDefinition`:

| Value | Form | Default material |
| --- | --- | --- |
| `standard` (default) | Soft pouch, crimped seams and tear notches | Mist blue, soft-touch matte |
| `premium` | Pearl pouch with a narrow holographic seal | Warm ivory, satin foil |
| `paper` | Reusable envelope with an arched flap and leaf seal | Sage green, fibrous paper |
| `collector` | Rigid box with a visible spine and lifting lid | Pale blue, layered landscape |

For example, add `packaging="paper"` to the registration above and declare
`plugin_api_version="1.25"` in the plugin descriptor (and `compatibility.plugin_api`
in its distribution manifest). Existing registrations default to `standard`.
All four presets support `artwork_asset` and `accent_color` independently.
The artwork replaces the decorative pattern/landscape but keeps the host's
brand, icon, title, description and count. Content Pack authors can select the
same four options in **Make a Pack → Pack packaging**; exports store it under
`creator.packaging`, and it survives installation and restart.

Bundled defaults: **Core essentials → collector**, **Codex Agent → premium**,
and **Research Library → paper**. Registry reconciliation refreshes their
appearance in existing libraries without resetting opened packs or collected cards.

During frontend development, open **`/?pack-design`** or the F3 panel's
**Pack design presets** link for the live Concept C design sheet. It uses the
production `PackSurface`, includes opening interactions, Library-size and
dark-background previews, and copies the selected Python registration. Inspect
front, side, back and top views, rotate the light, or switch to clay/wireframe.
The opening study scrubs the selected preset through its actual 3D animation.
These demo openings are local and never modify the Card Library.

The Library renders packages with the directly pinned `three` dependency,
loaded lazily by `PackSurface`. `shell/pack3d` owns procedural geometry, print
textures, materials and rendering. Soft pouches have continuous inflated meshes,
modeled shoulder wrinkles, notches and crimped seals. The envelope has solid
folds and a curved, hinged flap. The collector has continuous inner/outer walls,
a base and a separate fitted lid; these retain their volume when rotated.

Pouch tessellation follows the shape: dense crimp rows, intermediate shoulder
rows and a sparse belly are stitched without T-junctions. Explicit columns keep
the premium foil's printed boundaries intact. Including shell, lining and tear
strip, the standard/premium meshes contain 16,660/16,836 triangles, down from
56,640 each. A sealed pouch skips its occluded lining, drawing 8,748/8,836
triangles before shadow passes; opening reveals the same deforming interior.

Each region has its own material: matte film, pearl laminate, fibrous paper,
uncoated board, interior lining and metallic foil. Printed color, roughness and
emboss maps are generated from the current pack metadata. The premium strip
uses its own mesh material group with thin-film iridescence, anisotropy and a
view-dependent diffraction accent shader. The rainbow is driven by viewing and
lighting angles. Room-based environment lighting, clearcoat micro-normals and
blurred contact shadows provide consistent illumination across presets.

All visible packs share **one WebGL context**. Each render is copied into its
own DOM canvas so scrolling, dialogs and clipping remain native. Rendering is
on demand, at the screen's pixel density capped at 2× and a 1024px buffer.
Unchanged React updates do not redraw. Visible views are processed in rotating
batches with a 6ms CPU budget and at most one new model per frame (an individual
model/render can exceed that budget). Hidden card contents are built only during
a reveal. Offscreen views and hidden tabs stop drawing; views do not load the
3D module or create GPU resources until they approach the viewport.

After 1.5 seconds offscreen, including when the Library dialog is closed,
models/textures are released. The shared renderer is released 1.2 seconds after
the last view detaches. Brief scrolling retains the models. A session-local LRU
cache of up to 24 default-angle 3D bitmaps (12 MiB of decoded pixels) displays
previously seen covers immediately on remount while interactive rendering starts.
Its keys include printed metadata, packaging, opening state and view settings;
animated or pointer-tilted frames are not cached. A failed or lost GPU
context shows the CSS cover; restored contexts resume rendering. During initial
loading without a cached preview, the slot reserves its space and fades in the first 3D frame, without
showing the legacy cover. Hover smoothly tilts towards the pointer (up to 14°
horizontally and 11° vertically); shadows project away from the key light. Drag to rotate,
use arrow keys to inspect or Home to reset. Click/Enter still opens or browses;
dragging does not trigger opening. Reduced motion uses a stable reveal pose.

A successful `open_pack` transaction tears the pouch's top seal, folds back the
paper flap, or lifts the box lid, then draws up to three actual card previews
completely out. Pouches collapse in depth; the rigid box keeps its
shape and the paper sleeve keeps its folded-back flap. All contents
are collected by that transaction. Failures leave the sealed, full wrapper intact.
`opened` determines its persistent empty appearance after reload or plugin disable.
There are no buttons or details below a pack. Click the empty wrapper to browse
its cards; source pack controls on that page handle plugin availability and explicit
collection of new cards added by plugin updates. Unavailable wrappers also lead
there so an installed, disabled plugin can be re-enabled. Motion is decorative and
honors reduced-motion preferences; keyboard and touch use the wrapper button.
Collection cards use paper borders, thickness shadows and a lighter surface sheen.

`shell/pack3d/motion.ts` defines `PACK_OPENING_PRESETS`: soft pouches open their
mouth before a straight upward pull; paper folds its flap behind the mouth and
pulls the centred stack upward; the collector lifts/parks its lid and moves cards
forward through the front opening before raising them. Each preset controls
release, clearance, presentation timing, height and spread. Cards fan only after
clearing the rim and fade about their own centres. Animation framing includes
the lid and cards when inspecting any side of the package.

## Persistent model and API

`CardLibraryStore` stores one schema-versioned, revision-checked aggregate in the
existing world SQLite database's `application_settings` table, key
`card_library.v1`. The scope is the local application's data root, consistent with
world and application settings; there is no account system in this MVP.

| Layer | Authority / persisted fields |
| --- | --- |
| Plugin definitions | PluginRegistry, descriptor and contributed runtime code |
| Install state | Last observed descriptor/version, installed, enabled |
| Pack definitions | Registry-owned PackDefinition references |
| Pack state | Pack ID, owning plugin, owned, opened, opened_at |
| Collection | Card ID, owning plugin, unlocked, unlocked_at, source_pack_ids |
| Decks | Stable ID, name, icon, ordered node/Legion references |
| Active deck | active_deck_id |
| Instances | Existing WorldStore / LegionStore and lifecycle services |

Last observed public metadata is retained to label unavailable cards and packs.
Installed metadata is reconciled from the registry, and availability is always
derived from current registration, ownership and enabled state. These metadata
snapshots cannot supply executable definitions. The separate plugin environment
bootstrap database continues to track dependency preparation, not ownership or
deck state.

`GET /api/card-library` returns the snapshot and current available IDs.
`POST /api/card-library/actions` accepts `expected_revision` and one of
`open_pack`, `create_deck`, `update_deck`, `delete_deck`, `activate_deck`,
`import_legacy`, or `set_plugin_enabled`. A stale write returns HTTP 409 and the
frontend refreshes before retry. Writes use the shared SQLite transaction boundary
and publish `card_library_updated` on the existing event stream. Other windows
refresh from the backend; localStorage is not an ownership or deck authority.

Tray placement uses `POST /api/card-library/nodes`, which validates collection and
availability under the existing node mutation lock before lifecycle creation.
Generic world creation, file import, restoration and plugin-generated objects keep
their existing contracts: collection is a user acquisition/selection workflow,
not a security or licensing boundary. Every path still rejects unavailable plugin
contributions. Deck membership controls convenience, not execution permission.

## Migration and updates

The database records whether the world schema existed before its first Library
initialization. For a pre-Pack database (even an empty old world), the one-time
migration opens the currently registered packs, unlocks their cards and creates
initial decks matching the old catalog folders. The former Legion tray becomes a
saved-formations deck. Existing world data is untouched.

On first browser connection, the server-authorized pending migration imports the
old `open-agent-world.decks.v2` or `open-agent-world.custom-decks.v1` folders, using
the existing legacy normalization rules. The first completed import or user edit
closes that migration window. Another browser cannot overwrite later choices.
With no old browser folders, the catalog-based initial decks remain equivalent.

A fresh database begins with owned, unopened installed packs, no collected cards,
and one empty named deck. Later plugin installs add unopened packs to either kind
of installation. Reconciliation never adds cards to an existing deck. Updates that
add cards to an opened pack show **Collect new cards**, requiring another explicit
opening action. Existing collection timestamps and deck references survive.

## Availability and remaining boundaries

Disable is permitted only after plugin-owned world nodes, relationships, dependent
Agent runtimes and pending cleanup are removed. The core plugin stays enabled.
The registry gates node definitions, relationships, capability handlers and
runtime providers, including already cached providers. Disable preserves all
collection/deck references and the UI marks unavailable entries. Re-enable restores
them. Environment bootstrap skips disabled plugins and is queued on re-enable.
This does not unload trusted Python modules or cancel a dependency installer that
was already running; full hot-unload requires a separate runtime lifecycle design.

For installed `.oawpack` files, local Pack management selects uninstall for the
next restart and blocks new world admissions through the existing enable gate.
Source-development packages can still be removed from their development path.
The existing fail-closed
startup rule still applies if plugin-owned world objects remain. With those objects
removed, the app retains absent pack/card/deck metadata and restores availability
on reinstall. A different plugin cannot claim a previously collected card or pack
ID. Saved Legion compatibility continues through its existing dependency checks.

Future store work can replace the current installation-to-ownership acquisition
rule without changing deck references. Account ownership, download/install
transport, active-instance suspension and server-side
catalog paging remain separate future work. Current card browsing pages rendered
results (30 per page) while loading catalog/collection metadata as one snapshot.

Local archive validation, immutable version installation, upgrade/rollback,
runtime frontend loading and shared Python requirement resolution are implemented
by the [Pack distribution host](pack-distribution.md).
