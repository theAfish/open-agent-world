# Design composable workspace content

OAW uses one semantic design system for canvas cards and application content. Printed
cards own their stock, ink and finish; workspace panes use quiet surfaces and
readable content. Keep a card's execution and state ownership independent of
where its content is displayed.

## Shared tokens and controls

Use the host tokens instead of fixed light-theme colors:

| Purpose | Tokens |
| --- | --- |
| Surfaces | `--surface-solid`, `--surface-soft`, `--surface-raised` |
| Reading text | `--ink`, `--ink-soft` |
| Dividers and emphasis | `--line`, `--line-strong`, `--accent`, `--accent-soft` |
| Feedback | `--danger`, `--warning`, `--success`, `--info` |
| Type | `--font-ui`, `--font-mono`, `--font-size-body` (13px), `--font-size-small` (12px), `--font-size-caption` (11px), `--font-size-title` (16px) |
| Rhythm | `--space-1` through `--space-6` (4, 8, 12, 16, 20, 24px) |
| Shape and motion | `--radius-control`, `--radius-surface`, `--shadow-surface`, `--motion-fast`, `--motion-normal`, `--motion-ease` |

Use secondary ink for explanatory text; faint ink is for decorative marks.
Reduced-motion preferences disable token-based transitions. Global light/dark
selection remains the theme authority. `ApplicationSurface appearance="paper"`
offers a quieter raised-paper variant in either theme; `warm` is the default.
Override semantic variables on your own root to customize a view, not on `:root`.
Keep established Pack accent colors: Knowledge Base uses purple in both themes,
including detached sections. Shared spacing and controls do not require every Pack
to adopt the global terracotta accent.

Cards whose content explains their purpose can opt into `ui.content-surface.v1`
in their node-type traits. For expanded inspectors and standalone workspaces, the
host replaces its title/footer bars with a 32 px drag edge. Card details, rename,
status, finish and removal live in its accessible disclosure; expand/close remain
direct buttons. The content owns its top/bottom edges and scrolling. Task boards
and Structure Viewer use this mode. Collapsed collectible faces and card finishes
are unchanged, and docked content continues to use the existing pane host. This
trait does not change presentation states, persisted sizes or Composer layout.

Reuse native buttons with `primary-button` or `secondary-button`, native inputs
with `oaw-input`, and labels with `field-label`. `SchemaFields`, `IconButton`,
and the existing focus-managed `Modal` are public SDK exports. Use `Tooltip` for
control hints and always give icon-only buttons an accessible label.
Use `oaw-table` on a semantic `<table>` and `oaw-list` on a `<ul>`. Put wide tables
inside a scrollable content region. Existing workspace tabs and splitters retain
keyboard access; these content helpers introduce no second tab or layout engine.

### Tooltips and popovers (Plugin API 1.41)

Import `Tooltip` and `Popover` from `@oaw/plugin-api`. They inherit the trigger's
theme tokens, use the browser top layer to escape clipping and Canvas transforms,
follow moving anchors, and flip/clamp their position at the viewport edges.
Both accept `placement="top" | "bottom" | "left" | "right"` and `className`.
They add no trigger wrapper, preserving flex/grid layout and existing refs/handlers.
The single trigger must be a focusable native element or a component that forwards
DOM events and ARIA attributes. Omit native `title` to avoid duplicate hints.

```tsx
import { Tooltip, Popover } from '@oaw/plugin-api';

<Tooltip content="Drop a spectrum here, or click to choose a file">
  <button type="button" onClick={chooseFile}>Import spectrum</button>
</Tooltip>

<Popover label="Display options" trigger={<button type="button">Options</button>}>
  {close => <><label><input type="checkbox" /> Show reference peaks</label>
    <button type="button" onClick={close}>Done</button></>}
</Popover>
```

`Tooltip` is for noninteractive explanations. It opens after a 350 ms pointer
hover (`delay` can override this), opens immediately on keyboard focus, associates
its content through `aria-describedby`, and stays open while the pointer is over
the explanation. Escape or activating the trigger dismisses it without changing
the trigger's action. Interactive content belongs in `Popover`.

`Popover` manages its trigger's expanded state and opens a labelled nonmodal
dialog. Focus enters its content; Escape, outside click, focus leaving, or the
optional render callback's `close()` dismisses it. Closing from inside returns
focus to the trigger; Tab can leave naturally. Use `Modal` for blocking dialogs.
Declare `plugin_api_version="1.41"` and matching manifest compatibility when using
these components. Existing card-specific content and accents remain under Pack ownership.

## A reusable content template

```tsx
import {
  ApplicationSurface, WorkspaceSection, WorkspacePanel,
  WorkspaceToolbar, WorkspaceEmptyState,
} from '@oaw/plugin-api';

function DocumentView({ content, onChoose }: {
  content: string; onChoose: () => void;
}) {
  return <WorkspaceSection id="document" title="Document"
    layout={{ min_width: 280, min_height: 240, ideal_width: 720, ideal_height: 560 }}>
    <ApplicationSurface>
      <WorkspacePanel>
        <WorkspaceToolbar label="Document actions">
          <button type="button" className="secondary-button" onClick={onChoose}>Choose document</button>
        </WorkspaceToolbar>
        {content ? <article className="my-document">{content}</article>
          : <WorkspaceEmptyState title="Choose a document"
              description="Open a source to start reading."
              actions={<button type="button" className="primary-button" onClick={onChoose}>Browse sources</button>} />}
      </WorkspacePanel>
    </ApplicationSurface>
  </WorkspaceSection>;
}
```

`WorkspacePanel` has optional `title` and `actions`; omit the title when the host
already names the pane. `WorkspaceEmptyState busy` supplies loading status.
Translate visible strings with the existing `t`/`useLocale` API in production.
The helpers are optional: custom markup and styles continue to work. New SDK
exports and Python metadata require a host with Plugin API **1.28**; declare that
minimum in a Pack that uses them. Existing Packs need no migration.

Official source examples are the Conversation sections, Sandbox file browser
and terminal, Knowledge document reader, and shared `NetworkMap`. These retain
their domain-specific controls while using the same palette and spacing. A
dashboard can combine `WorkspacePanel` regions in CSS Grid; no Conversation is
required.

## Respond to the pane, not the desktop

Give a sized root `container-type: inline-size`, `min-width: 0` and `min-height: 0`.
Use container queries on descendants to stack columns when the *pane* narrows.
For detachable content, its container must travel with the Section's DOM host,
not depend on an ancestor left in the original card. `WorkspaceSection` keeps
one React owner when moved; do not render another copy in the destination.
For the same reason, put `ApplicationSurface` and custom theme overrides inside
the Section, as above, when those styles should travel with detached content.

If a Pack has its own tabs, call `useWorkspaceSections()` once at the component's
top level and keep a section mounted when `activeTab === id || isDetached(id)`.
The returned `isInline(id)` and
`isDetached(id)` both respect published section permissions; hidden sections are
neither inline nor detached. Keep the same `WorkspaceSection` at the same React
position so its portal, input drafts, and reader state survive navigation.

Allow toolbars to wrap, make long names ellipsize or wrap, and keep actions
reachable. Assign scrolling to the content region and use
`overscroll-behavior: contain`; avoid several full-height scroll containers.
Do not conceal a section to make a narrow pane look cleaner. The official
Conversation stacks its content; Files stacks its tree and preview. Charts and
maps retain their existing renderers and resize handling.

## Activate content after an operation

Workspace surfaces receive the optional `host.workspace.activate(view?)` API.
Use it after a user operation makes content available:

```tsx
const result = await host.documentAction('inspect', { entry_id });
setResult(result.value);
host.workspace?.activate({ section_id: 'details' });
// Omit the argument to reveal this card, or pass card_id for another member.
```

It returns whether the current workspace can reveal the target. Placed sections
activate their existing tab; inline sections reveal the owning card; unplaced
cards open their drawer. It does not create tabs, recompose/save geometry, open
another workspace or grant resource access. Unknown/hidden sections, nonmembers,
unavailable deployed sections and requests during layout editing are rejected.
Use `useWorkspaceNavigation()` for the same contract in built-in React content.
Existing saved layouts remain authoritative; activation is transient browser UI.

Call it for an explicit operation, not from background polling, mount or every
state update. Keep the card's original React owner and `WorkspaceSection` host.
Guard asynchronous results against a newer selection or a revoked connection.

Sandbox selection reveals its `preview` section. Structure Viewer requests its
card only after a newly opened file parses successfully. `host.openFile` accepts
optional `{ reveal: false }` for background refresh, and `onReveal` to dismiss a
temporary preview after a connected viewer successfully reveals its workspace
tab. The shared `useFileViewer` returns this browser-local intent with the file;
read authorization remains enforced by `host.readFile` and the backend.

## Optional sizing metadata

Python declares whole-card content needs independently of canvas `sizes`:

```python
from open_agent_world.plugin_api import NodePresentation, WorkspaceLayoutMetadata

presentation = NodePresentation(
    states=("node", "preview", "workspace"), initial="preview", open="workspace",
    workspace_layout=WorkspaceLayoutMetadata(
        min_width=280, min_height=240, ideal_width=720, ideal_height=480,
    ),
)
```

All fields are optional; supplied dimensions must be finite, 96–8192px, with
ideal size at least the declared minimum. Section `layout` uses the same keys.
Absent metadata falls back to minimum 240×200 and ideal 480×360. Tab groups use
the largest minimum of their views, so changing tabs does not resize the layout.
Minimum sizes constrain the renderable content; ideal sizes guide initial
composition only. No role, card type or runtime status gives a card priority.
Adding a larger minimum can increase the rendered size of an existing undersized
pane without modifying its saved split ratio. Temporarily unavailable sections
retain their last declared minimum while the workspace is open.

## Compose once, then let the user edit

`composeWorkspace` produces the existing version-2 layout value:

```ts
import { composeWorkspace } from '@oaw/plugin-api';

const layout = composeWorkspace([
  { view: { card_id: graphId }, layout: { ideal_width: 800, ideal_height: 600 } },
  { view: { card_id: sandboxId, section_id: 'files' }, layout: { ideal_width: 320, ideal_height: 400 } },
  { view: { card_id: sandboxId, section_id: 'terminal' } },
], { recipe: 'balanced', width: 1440, height: 900 });
```

The function returns data; it does not save, publish or mutate card state. Pass
it through your existing authored-layout/preset flow. The editor offers
**Compose all cards**, **Columns** and **Rows** only for an empty layout. A
composition remains a draft until **Save layout**; cancel restores the baseline.

| Recipe | Useful starting points |
| --- | --- |
| `balanced` | Dashboard mosaic or mixed graph/files/terminal tools |
| `columns` | Explorer + detail, canvas + inspector, conversation + context |
| `rows` | Document + tools, chart + log |
| `focus` | First explicitly supplied view gets emphasis; remaining views stay visible |

Nest returned roots to author more elaborate compositions. A tabbed workbench
uses existing `{kind: 'tabs', views, active_view}` nodes or explicit editor
docking. Recipes never introduce tabs, remove views or hide sections. Up to
100 unique views and the existing 16-level tree bound apply.

Saved/manual/authored layouts take precedence. Content refresh, task activity
and window resizing never call the composer. The existing split tree stretches
within available space; if minima exceed the window, its stage scrolls. This is
deliberately different from automatically rearranging an application mid-task.

## Verify a template

Use the existing layout editor as the live preview: resize and move real panes,
then switch to runtime, save, close and reopen. Check light/dark, wide/narrow,
empty/populated data, single/paired/multiple cards, and a composition containing
no Conversation. Verify inputs, file selection, terminal state and document
selection survive section moves. Installed Packs must export through the host
SDK build contract; do not bundle a second React or import host stores.
