# Card design language

The Library, active hand and selected-card preview share `CardFace` and `CardStock`. The design uses a single rounded silhouette, sans-serif titles, muted colours and a softly raised icon tile. There are no crests, engraved borders or repeated corner icons. Thumbnail cards show only the symbol or artwork and title; descriptions remain available in the Library detail pane.

## Presets

| Variant | Composition | Typical use |
| --- | --- | --- |
| `icon` | Raised icon tile, left-aligned title and short description | Agents, skills, tools |
| `image` | Full-width image above an overlapping title sheet | Images, datasets, collections |
| `text` | Small symbol, more space for a short excerpt | Notes, conversations, documents |
| `compact` | Unframed centred symbol and title | Utilities, small sets |
| `dark` | Deep slate stock with light text and a subtle tile | Consoles, media, featured tools |

Tones: `midnight`, `sage`, `sand`, `sky`, `rose`, `stone`. Choose a layout for its content, not its material. `dark` supplies its own ink treatment. Other layouts adapt to the application's dark theme. Images use `object-fit: cover`; avoid putting text inside the artwork.

Keep titles short (at most two visible lines), descriptions to one or two sentences, and optional badges to one meaningful value. Do not add decorative action arrows or fake status information. The whole card's existing interaction remains the action.

## Python plugin API (1.24)

Set the optional `card_face` on `NodeTypeDefinition`. Existing plugins work without changes; the frontend derives a default layout from the type and traits.

```python
from open_agent_world.plugin_api import CardFaceSpec, PluginAsset

# During registration, publish the image bytes as an asset owned by this plugin.
registration.register_asset(PluginAsset("landscape", image_bytes, "image/webp"))

# Add to your NodeTypeDefinition(...):
card_face=CardFaceSpec(variant="image", tone="sky", image_asset="landscape")
```

For an icon card, use `CardFaceSpec(variant="icon", tone="sand")`; its symbol uses the existing `icon` / `icon_asset` fields. Image assets must be registered by the same plugin with an image media type. The host publishes the resolved URL in `card_face.image_url`. Missing or failed images fall back to the card icon.

## Frontend components

The same components and types are exported by `@oaw/plugin-api` for custom views:

```tsx
import { CardFace, CardStock } from '@oaw/plugin-api';

<CardStock size="standard" finish={card.finish} quality="standard"
  style={{ width: 200, height: 250 }}>
  <CardFace variant="icon" tone="sand" icon={<MyIcon />}
    label="Python Console" description="Run code and explore data." />
</CardStock>
```

Alternatively, pass `definition` to `CardFace` to use catalog settings. Explicit component props override the catalog. Use `imageUrl` for artwork, `imageAlt` only when it adds information beyond the title, and `badge` for one short value. `CardStock` defaults to `size="compact"`; full previews use `size="standard"`. The caller supplies the dimensions and interaction. These components do not add buttons inside existing card buttons.

## Materials and preview

Layout and finish are independent. All five existing finishes (`normal`, `foil`, `rainbow`, `starlight`, `laser`) use `CardFinishLayer`; ownership, persistence, probabilities, reduced-motion handling and pointer behaviour are unchanged. Title sheets, symbols and badges sit above the material plates to preserve contrast. Normal cards add no material DOM.

Run the frontend and open `/?card-design` for the live design reference. It includes all layouts, six tones, material switching, dark theme and actual hand-size thumbnails. `/?card-finishes` retains the material and 200-card performance study. Both standalone routes are development-only and do not change collection data.

Canvas workspaces and plugin-owned content keep their existing layouts. Custom plugin previews can opt into the shared components without replacing their editor or workspace.
