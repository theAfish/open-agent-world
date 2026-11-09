# Frontend extension contract

[API directory](../plugins.md) · [Card App tutorial](../developers/card-app.md)

## Local frontend extensions and public assets

Plugin API 1.9 adds explicit public image assets and frontend view references.
The backend remains authoritative for each card's owner and selected views.
Register resources from your Python package, so the same code works with local
discovery and installed wheels:

```python
from importlib.resources import files
from open_agent_world.plugin_api import PluginAsset

registration.register_asset(PluginAsset(
    id="logo",
    content=files(__package__).joinpath("assets/logo.svg").read_bytes(),
    media_type="image/svg+xml",
))
# On your NodeTypeDefinition:
# icon="bot", icon_asset="logo", frontend={"settings": "settings"}
```

Only explicitly registered bytes are served at
`/api/plugins/{plugin_id}/assets/{asset_id}`. The host publishes `icon_url` in the
catalog; consumers should use that URL rather than constructing filesystem paths.
Asset names are local to their owning plugin. Registration is atomic, including
asset references. Images can be SVG, PNG, JPEG, WebP or GIF, up to 5 MiB each.
Card icons use their alpha mask with the current text color, so provide a
monochrome mark with a transparent background. The host has no brand-name map.

For a local frontend extension, add:

```text
plugins/my-plugin/
  frontend/
    plugin.json
    index.tsx
  src/my_plugin/assets/logo.svg
```

`frontend/plugin.json` identifies the backend owner:

```json
{ "pluginId": "acme.example", "apiVersion": 1 }
```

`frontend/index.tsx` exports named views:

```tsx
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";

function Settings({ card, host }: PluginViewProps) {
  return <label>Greeting<input defaultValue={String(card.config.greeting ?? "")}
    onBlur={(event) => void host.updateConfig({ greeting: event.target.value })} /></label>;
}

export default {
  apiVersion: 1,
  views: { settings: Settings },
} satisfies FrontendPlugin;
```

The catalog's `frontend` map supports `preview`, `body`, `settings`, and
`workspace`. Each value selects a view exported by that card's owning plugin.
`settings` replaces configuration inside the shared Agent controls; `body`
replaces inspector content, and `workspace` replaces content inside the host
window. The host retains its titlebar, canvas interaction and window controls.
Undeclared slots keep their standard interface. A declared view that cannot load
or render displays a local error instead of silently substituting another view.

Views receive the current card, definition, surface level, and a card-scoped host
adapter with `updateConfig` and `getAgentInfo`. Updates go through existing host
actions and backend validation. The adapter also provides document reads/actions,
transforms, file-viewer access, `openWorkspace(nodeId)`, `runAgent(nodeId, prompt, freshContext?)`
and `onDocumentChange(listener)`. The latter observes future authoritative
`node_document` mutations and returns an unsubscribe function; it must not be
used as a replacement for a plugin's own initial document load. `SchemaFields` is
also exported for scalar schema controls. Import host contracts only from
`@oaw/plugin-api`; React is shared with the host. Do not import host stores or
internal components. UI extensions are trusted application code, not a security
sandbox; error boundaries isolate rendering and module-load errors, not arbitrary
asynchronous side effects.

`updateConfig(patch, nodeId?)` defaults to the current card; an explicit node ID
requires Plugin API 1.50 and uses the same host edit action and backend authorization.
Declare that minimum in the descriptor and manifest when targeting another card:
older hosts can ignore the second argument and update the current card instead.
`getAgentInfo(nodeId?)` also exposes the Agent's existing optional `status` field.
Domain workflows should
live in their Pack and use these generic document, configuration, and navigation
capabilities. Packs can export named `services` beside `views`, consumed through
`loadPackFrontendService(owner, name)` after the host checks that the owner is
loaded. XRD's input interpretation and new-workflow import use this boundary;
the existing XRD host callbacks remain available to installed views.

Vite discovers immediate `plugins/*/frontend` projects at startup/build time.
Each project owns `package.json`, a lockfile and a build script using the public
SDK. Development and production both load that independent build artifact;
source edits rebuild the Pack and reload the page. Restart Vite after adding a
new project. Installed `.oawpack` frontends load from their immutable version
without rebuilding the host. React and the public SDK always use host instances.

See [Codex](../../packs/codex/frontend/index.tsx) for a working SDK-based settings
view and its package-owned icon.

### Skin mods

The skin-mod SDK requires Plugin API 1.49. Packs using these exports declare
`plugin_api_version="1.49"` in their descriptor and
`plugin_api = "1.49"` in their manifest's `[compatibility]` section. JSON imports
through Settings do not require a Pack or a compatibility declaration.

**Settings → Appearance** offers independent color palette, light/dark mode and
canvas background choices. **Import skin mod** installs a JSON file in the active
application profile; **Export skin mod** saves the selected palette and background
as a new portable mod. Users can edit and share these files without building a
Pack. Importing selects the first supplied palette and background. Start with
[Blueprint](../../examples/skins/blueprint.oawskin.json).

```json
{
  "schemaVersion": 1,
  "id": "acme.blueprint",
  "name": "Blueprint",
  "palettes": [{
    "id": "drafting",
    "name": "Drafting",
    "light": { "--accent": "#1766a3", "--accent-strong": "#124f80" },
    "dark": { "--accent": "#80bfff", "--accent-strong": "#add6ff" }
  }],
  "backgrounds": [{ "id": "squared", "name": "Squared paper", "kind": "grid", "spacing": 32 }]
}
```

Palette IDs and background IDs are local to the mod; saved selections use
`acme.blueprint/drafting` and `acme.blueprint/squared`. The `oaw` namespace is
reserved for builtins. Colors override the host's existing theme tokens, with
separate `light` and `dark` maps; omitted tokens retain the host defaults. Use
`--accent`, `--accent-strong` and `--on-accent` together when changing button
contrast. Background kinds are `terrain`, `solid`, `dots`, `grid` and `gradient`;
`spacing` controls dots/grid and `angle` controls gradients. The exported
`SkinMod` type describes the contract, `COLOR_TOKENS` lists allowed tokens, and
`parseSkinMod` validates unknown data. IDs use lowercase letters, numbers, dots,
underscores or hyphens (1–64 characters, starting with a letter); names contain
1–80 characters. Both collections are required, with at least one entry overall.
Each file is limited to 64 KiB and 16 entries per collection; a profile supports
12 imported mods. Dots/grid spacing ranges from 8 to 128, gradient angles from
0 to 360 degrees.

Color values accept hexadecimal RGB/RGBA only (`#rgb`, `#rgba`, `#rrggbb`,
`#rrggbbaa`). The schema admits known color tokens and bounded parameters; it
does not load CSS, image URLs or scripts. Invalid files and duplicate mod IDs are
rejected atomically. Removing a selected mod falls back to Paper/Terrain while
preserving its selection IDs, so registering it again restores the selection.

Trusted frontend code can register the same data for its own lifetime:

```ts
import { parseSkinMod, registerSkinMod } from '@oaw/plugin-api';

const mod = parseSkinMod(JSON.parse(jsonText));
const unregister = registerSkinMod(mod);
// When the owner is disposed:
unregister();
```

Registration updates the Appearance choices immediately and returns an idempotent
cleanup function. Runtime registrations are transient; JSON imports are persisted.
This API does not load Pack modules or introduce an automatic Pack activation
lifecycle. A view-scoped registration exists only while that view owns it; use
the JSON import flow for a skin that should remain available across reloads.

### Human credential configuration

Plugin API 1.42 exposes `host.credentials` on Environment and Sandbox cards in
the builder UI. It is absent from deployed Workspaces and other card types.
Use it from explicit user actions; it is not an Agent tool or a grant.

```tsx
const credentials = host.credentials;
if (!credentials) throw new Error('Credential configuration is unavailable');
const configured = await credentials.status(); // { reference: boolean }, no plaintext
const document = await host.readDocument();
await credentials.set('ACCESS_KEY', enteredValue, document.revision);
// Remove a binding with credentials.set('ACCESS_KEY', null, document.revision).
```

The reference must already be declared by the card's Environment Profile
document. The backend checks the current document revision, reference ownership
and edit permissions, and encrypts the binding through the existing store.
Handle conflicts by reloading the document. Keep entered secrets only in local
form state, clear them after save, and never copy them into card config, documents,
`host.state`, drafts or logs. Status returns configured flags only; this interface
provides no plaintext-read operation. Trusted backend handlers continue using
their existing credential resolver.

### Composable workspace sections

Workspace views can expose semantic panes with `WorkspaceSection` from
`@oaw/plugin-api`. A section should be a useful functional unit, such as a file
browser, terminal, session list, or conversation. Keep individual buttons and
fields inside their pane. Cards without sections continue to work as whole cards.

```tsx
import { useState } from "react";
import { WorkspaceSection, useWorkspaceSections } from "@oaw/plugin-api";

function Workspace() {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const { isInline } = useWorkspaceSections();
  return <div className="project-workspace" style={{
    display: "grid", minHeight: 0,
    gridTemplateColumns: isInline("files") && isInline("preview")
      ? "220px minmax(0, 1fr)" : "minmax(0, 1fr)",
  }}>
    <WorkspaceSection id="files" title="Files">
      <FileBrowser selected={selectedFile} onSelect={setSelectedFile} />
    </WorkspaceSection>
    <WorkspaceSection id="preview" title="Preview">
      <FilePreview file={selectedFile} />
    </WorkspaceSection>
  </div>;
}
```

`FileBrowser` and `FilePreview` above are plugin-owned components. Section IDs
must be stable and unique within their card; they are persisted layout references,
so do not derive them from translated labels, list positions, or random values.
`title` is the user-facing label and may be translated. `className` and `style`
apply to the section's inline shell. Give the content its own sizing, scroll
container, and CSS variables where needed: it can be displayed outside that shell.

The host provides arrangement controls in Legion workspace edit mode. A section
can appear inline, occupy its own workspace pane, or be hidden. These operations
change presentation only: they do not delete data, create cards, grant capabilities,
or transfer ownership. The original card remains responsible for state, effects,
host calls, and cleanup. A hidden section remains mounted, so hiding a terminal,
for example, does not stop its commands.

The host moves one stable DOM container while React content keeps the original
component tree and context. Keep shared selection and other coordinated state in
the original card component, as above; do not create a second renderer for a
detached pane. React events still propagate through that original component tree.
Removing the owning card or unmounting its workspace view still unmounts its panes.

`useWorkspaceSections()` is a read-only presentation hook. `isInline(id)` returns
false for detached and hidden sections, and true in an ordinary card window.
Use it to collapse unused grid tracks and omit adjacent resize dividers. Keep
each `WorkspaceSection` mounted even when `isInline(id)` is false; conditional
unmounting would lose the content and its state. Arrange controls and layout
persistence belong to the host; plugins do not access Legion stores directly.

## Connected file viewers

Nodes with `core.file-viewer` can connect to nodes with `core.file-source` using
`core.file-preview` ("Follow opened files"). This grants only `file.preview`;
it does not grant Sandbox execution or lifecycle management. The capability
broker checks the live direct relationship before and after reads.

The frontend SDK exports `useFileViewer(card.id)` with `file`, `sources`,
`pinned` and `setPinned`. `file` contains a `reference`, display `name` and a
monotonic `sequence`. The hook observes current state, including files opened
before connecting. `host.readFile(reference, signal)` returns complete bounded
base64 bytes through the viewer-scoped backend endpoint. Each viewer owns format
recognition and must cancel stale work and dispose its renderer on source changes.

File source views may call `host.openFile(reference, name)` and
`host.clearOpenedFile()`. The host scopes publication to the source card. Clear
context when the source workspace or session changes or closes. Open-file and
pin state are browser-local and are not stored in card configuration.

Current source references are `{kind: "sandbox", source_id, root, path}` and
`{kind: "conversation", source_id, session_id, version_id, path}`. Sandbox reads
reuse its path/mount boundary; Conversation reads validate the attachment's
session and artifact manifest. A new storage source needs a host read adapter;
declaring a trait alone does not provide filesystem access. See the
[Structure viewer](../../plugins/structure_viewer/README.md) for an implementation.
