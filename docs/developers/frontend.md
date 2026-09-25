# Add a custom interface

This guide uses the existing bundled/source development workflow. Independent
Packs use the same `FrontendPlugin` / `PluginViewProps` interface with the
[external Pack build contract](../pack-distribution.md#frontend-contract-and-shared-react)
and do not require rebuilding the host frontend after installation.

**Goal:** replace the first plugin's settings with a small React form. Complete [Your first plugin](first-plugin.md) first. Standard schema controls remain a good choice when you do not need a custom view.

## 1. Add frontend files

Your local package should now contain:

```text
plugins/hello_world/
  frontend/
    plugin.json
    index.tsx
  pyproject.toml
  src/oaw_hello/__init__.py
```

Set `frontend/plugin.json` to:

```json
{ "pluginId": "community.hello", "apiVersion": 1 }
```

The ID must match the Python descriptor. The frontend API version is separate from the Python plugin API version.

## 2. Export a settings view

Put this in `frontend/index.tsx`:

```tsx
import { useEffect, useState } from "react";
import type { FrontendPlugin, PluginViewProps } from "@oaw/plugin-api";

function Settings({ card, host }: PluginViewProps) {
  const saved = String(card.config.message ?? "");
  const [message, setMessage] = useState(saved);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { setMessage(saved); }, [card.id, saved]);

  async function save() {
    setSaving(true);
    setError("");
    try {
      await host.updateConfig({ message });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <label>Message
      <input value={message} maxLength={200} required disabled={saving}
        onChange={(event) => setMessage(event.target.value)} />
    </label>
    <button type="submit" disabled={saving}>
      {saving ? "Saving…" : "Save"}
    </button>
    {error && <p role="alert">{error}</p>}
  </form>;
}

export default {
  apiVersion: 1,
  views: { settings: Settings },
} satisfies FrontendPlugin;
```

The form edits the configuration through the card-scoped `host` adapter. The backend still validates the message. React is shared with OAW; use `@oaw/plugin-api` for host contracts.

### Transient drafts and surface visibility

Canvas culling may unmount any view. An optional `host.draft` stores small JSON UI
drafts outside the React mount: `get()` returns the draft or `undefined`, and
`set(value)` replaces it. Optional `subscribe(listener)` returns an unsubscribe
function and notifies views of changes, including completion of a save from a
previous mount. Call `set(undefined)` after save or cancel, comparing the latest
draft with the submitted draft so newer edits survive an in-flight save. The host
owns the card, state-scope and session namespace, so views must not invent or
pass session IDs. Drafts are shared across that card's surfaces, survive leaving
and returning to an area, and are cleared on confirmed card deletion or page
reload (including profile changes). They are not durable document state and are
not written to preferences. Old hosts may omit this additive capability; use
optional chaining. Never put full query results or rendered assets in a draft.

Use the existing `level` prop to pause polling, layout and heavy child rendering
outside `inspector`/`workspace`. Preview views should request a summary rather
than an editor document. Retaining draft data does not require retaining the
editor DOM. Existing third-party views with only local React state remain
responsible for adopting this capability before their drafts can survive culling.

## 3. Select the view in Python

Add this argument to your `NodeTypeDefinition(...)`:

```python
frontend={"settings": "settings"},
```

The key is the host slot; the value names your exported view. Supported slots are `preview`, `body`, `settings`, and `workspace`. A workspace also needs to be enabled by the node's presentation definition; see [presentation and views](../plugins.md#local-frontend-extensions-and-public-assets).

## 4. Restart and check

Restart both development servers after adding the package or manifest. Reopen the card's settings, save a message, and verify it after reload. Existing frontend source changes support Vite hot reload.

For a production build:

```sh
npm --prefix frontend run build
```

OAW discovers frontend entries in immediate `plugins/*/frontend` directories at build time. Installing a Python wheel by itself does not add UI to an existing desktop app; distribute compatible frontend source and rebuild, or include it in a compatible OAW build.

## Grow the interface

Use [workspace sections](../plugins.md#composable-workspace-sections) for rearrangeable panes and [file viewer contracts](../plugins.md#connected-file-viewers) for connected files. Keep UI state inside the plugin and use the public SDK instead of importing host stores or internal components.
