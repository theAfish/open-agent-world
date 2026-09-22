# Legion groups, team spaces and blueprints

[Documentation](README.md)

A Legion has two representations: a reusable library template and a live `legion`
card in the world. New Legions start as **groups**: a spatial container with no
shared Agent context. The header's **Legion settings** button toggles a settings
sidebar attached to the right side of the Legion. **Enable shared team settings**
turns the group into a team space with a durable `legion:<card_id>` state scope.
The sidebar leaves the canvas interactive. Closing it keeps the canvas clear and preserves unsaved variable drafts. Members remain ordinary world nodes
with a nullable `parent_id`. Existing teams retain their settings and behavior.

Select ungrouped cards and choose **Form Legion**. Select the Legion and additional cards to add those cards using
**Add selected cards**. **Detach** removes membership while retaining the card,
its world position, and its edges. An Agent must finish or stop its active Runs
before changing membership. This version supports one Legion per member, up to
100 members, and no nested Legions.

The container header moves the entire team. Positions remain absolute world
coordinates in storage; React Flow receives relative positions for child nodes.
Batch moves include members exactly once, and viewport loading retains a visible
container and its members together. Resizing establishes the minimum space;
expanded member surfaces can enlarge its rendered bounds. Container removal
requires detaching its members first, or explicitly deleting them in the same
batch. External edges continue to work across the container boundary.

The banner's **Dissolve** action removes the container while keeping member nodes,
positions and their connections. The trash button **Delete Legion and members**
deletes the container and all members as one batch. Both actions support undo/redo,
including restoration of saved shared variables. Library presets are unaffected.

## Workspace mode

A saved workspace can be published as a locked application with **Publish application**.
See [deployment](deployment.md) for the operator surface contract, independent data
copies, access passwords, server hosting, updates and rollback.

Unplaced member cards appear as compact icons in the bottom bar. Click an icon to
open its workspace or inspector in a temporary panel for viewing and configuration;
click again, use the close button, or press Escape to collapse it. Switching or
collapsing the panel preserves page drafts while the workspace stays open. These
actions do not change the saved layout. In edit mode, drag a bottom-bar icon onto a
title strip or region edge to place it. Removing a card from the layout returns it
to the bar; deleting or detaching the member removes its icon and closes its panel.
Pages in the bar are loaded on first use.

Choose **Workspace mode** in a Legion's header to open its members as one modular
window. **Edit layout** lists direct member cards. Use only the cards you need:
drag the first into the empty window, then drop cards on the left, right, top or
bottom edge of a pane. The highlighted half shows the destination. You can also
select a card and click a docking button. Each region's title bar is a tab strip.
Drop a card or an existing tab on that strip to add it to the region; dropping
on a tab inserts before it, while dropping on the trailing space appends. You
can also select a card and choose **Add tab**. Drag a tab to a region edge to
split it back into a separate pane, or remove it without deleting the card.

Click tabs to switch pages in either mode. Left/Right and Home/End navigate a
focused tab strip. In ordinary use the selected tab is saved automatically;
reordering and moving tabs requires edit mode. Inactive pages stay mounted so
unsaved text, terminals and plugin view state survive tab switches and moves.
Tab strips scroll horizontally when their titles exceed the available width.

Regions tile without gaps or overlapping windows. Drag a divider to change its
ratio in both editing and ordinary use; focused dividers also accept arrow keys
and Home/End. In ordinary use, releasing a divider or resize key automatically
saves its ratio, while moving, adding and removing panes requires edit mode.
Editing displays the real card interfaces with a small inset around each region.
**Done editing** or **Save layout** saves the arrangement and removes the editing
insets and controls. Cards use their workspace or inspector surfaces, including
plugin surfaces. The Save button appears only in edit mode. Failed automatic saves retain
the draft and offer a retry. **Cancel layout changes** restores the
saved arrangement. Closing with an unsaved layout offers a discard action.
Small windows scroll once the panes reach their minimum usable sizes.

Cards may expose named functional sections through the plugin SDK's
`WorkspaceSection` component. In edit mode, hover or focus a section to reveal
its arrangement handle and hide control. Drag the handle onto a region edge to
split, or onto a title strip to add a tab; selecting the handle also enables the
docking buttons. The sidebar's **Card sections** list provides the same controls
and **Restore to card** for hidden or extracted sections. Removing a section pane
hides that section; removing a whole-card pane leaves its extracted sections in
place. Both operations preserve the owning card and its data. Missing plugin
sections retain their saved location with an unavailable message.

Sandbox exposes Files, File preview, and Terminal; Conversation exposes Sessions,
Conversation (messages and composer), and Participants. Shared state stays in
the original card, and moving a section preserves its live component and inputs.
Plugins can adopt the same API incrementally; cards without sections remain
usable as whole-card panes. See [plugin section API](plugins.md#composable-workspace-sections).

The window is a presentation of existing members: canvas positions, Glue, team
settings, connections and runtime ownership stay independent. Cards omitted from
the window continue working. Missing or detached members disappear from the
window and adjacent regions expand. Containers without a workspace surface are
not dockable. Plugin containers with a workspace surface (such as scientific
Toolsets and knowledge graphs) can be docked; nested Legions remain excluded.
The window supports splits and tab groups; floating subwindows
are not implemented.

Layouts live in `config.workspace_layout`, with `version: 2`, a nullable `root`,
and `hidden_sections` (an array, defaulting to empty). A view is a reference:
`{card_id: "..."}` for a whole card, or `{card_id: "...", section_id: "files"}`
for a section owned by that card. A leaf is `{kind: "pane", view: ...}`; a tab
group is `{kind: "tabs", views: [...], active_view: ...}`. `views` defines tab
order and `active_view` must match one of its references. A branch is
`{kind: "split", axis: "horizontal" | "vertical", ratio: 0.5, first: ..., second: ...}`.
Horizontal splits place children side by side. Ratios are bounded to 0.15-0.85;
layouts have at most 100 view references (including hidden sections) and 16 levels.

Each `(card_id, section_id)` pair may occur once across all regions and hidden
sections. Whole-card views and their extracted sections may coexist: the card's
presentation omits extracted or hidden sections, while runtime ownership stays
with that card. `hidden_sections` accepts only section references. Hiding a
section does not delete its card, files, connections, or runtime state.

Developers and presets use the same contract via `PATCH /api/nodes/{legion_id}`.
Version-1 panes and tabs are accepted and normalized to version 2 when read or
saved. **Save to library** includes the layout. Capture maps every owner card ID
to a template key; deployment maps it back to a new member ID, preserving section
IDs, order, active views, and hidden sections. Missing owners remove their placed
and hidden references. A tab group retains its active view if possible, otherwise
selects its first remaining view; empty regions disappear and single-tab groups
can collapse to a pane. Templates without a layout open with an empty workspace.
The bundled Coding workspace preset supplies a Conversation/Sandbox split when
deployed with a Legion wrapper; unwrapped deployment has no Legion window.

Plugins can contribute their own presets through Plugin API 1.18. They appear in
the bottom **Legions** deck alongside saved formations and deploy with their
workspace layout. The MatCreator research preset combines Sessions, Files,
Conversation, research tasks, preview, terminal and knowledge tabs. Presets are
owned by the plugin; edit a deployed copy and **Save to library** to keep a custom
version. See [MatCreator usage](../plugins/matcreator/README.md).

## Runtime settings and state

These settings apply in team mode (`config.mode = "team"`). Switching back to
group mode retains the saved settings but disables instruction/model inheritance,
team pause admission, the Legion state scope and state tools for members.
Existing Runs retain their start-time context snapshot; live state-tool access
is revoked immediately. Opening or closing the settings sidebar does not change mode.

- **Team instruction** is appended to each member Agent's own instruction at Run
  start. A member's optional role is included in that context.
- **Team model override** replaces the member model when configured, unless that
  Agent disables **Use team model override**. It never rewrites Agent config.
- **Pause team** prevents new member Runs, including delegated turns. Existing
  Runs continue; resuming the team allows new Runs again.
- **Shared variables** provides named rows with Text, Number, On / off, or JSON
  values. Add and remove rows without editing a whole JSON object; blank names,
  duplicate names, invalid numbers and invalid JSON show validation errors.
  Values are stored as a JSON object in `StateStore`, limited to 64 KiB.
  **Save variables** replaces it using compare-and-set revisions. On a conflict,
  the draft is retained; reload the latest state before reconciling changes.
- Member Agents receive `read_legion_state` and, when enabled,
  `patch_legion_state`. Patches merge top-level keys and require the last read
  revision. Every invocation rechecks membership and read/write mode. Joining a
  Legion grants these state tools only; sandbox, resource and Agent communication
  capabilities continue to require their existing edges.

The Run scope records a snapshot of the effective team context. Runtime Providers
receive it in `InvocationContext.group_context`; the effective instruction and
model are also passed through the provider-neutral `AgentConfig`. The state stack
is `world -> legion -> agent -> session (optional) -> run`. The snapshot is data
at Run start, while the state tools read the live authority. The ADK session is
not a second writable copy of the team state.

## Templates and API

Use **Save to library** in the Legion header, with or without team settings.
Saving to the library also saves any pending
shared-variable draft. It captures the container, settings, current shared variables,
members, internal edges, relative node positions, sizes, display states
(`node`, `preview`, `inspector`, `workspace`, corresponding to levels 1–4), compact
return states, resized workspace dimensions, and portable resource payloads. Membership uses
template-local keys, which are remapped on instantiation. Each deployment receives
an independent copy of the saved variables; changes to the source or another copy
do not alter the preset. Older templates without variable presets start empty.
Display states are stored in the backend template and restored to each new node
ID, including on redo. They are distinct from execution status: restored Agents
start idle and Sandboxes stopped. Run history and host-bound sandbox folders
continue to be excluded.

The frontend deploys older flat templates inside a new Legion. For compatibility,
`POST /api/legions/{template_id}/instances` preserves the original flat behavior
unless `as_group: true` is provided. Use `unwrap: true` to place only the contents
of saved Legion containers while preserving positions and internal connections.
This removes Legion-level settings from the deployed formation; individual node
settings and any other nested containers remain. `unwrap` and `as_group` are
mutually exclusive. Normal library deployment retains Legion membership.

## First-world blueprints

The welcome screen offers **General assistant**, **Coding workspace**, and
**Multi-Agent collaboration**, along with **Start Tutorial** and **Start Empty**.
Users can also choose a saved Legion. Blueprints use the same template deployment
path with `unwrap: true`; no Legion wrapper remains on the canvas. The operation
supports undo/redo, uses the user's default model, and opens model setup when
needed. Creating a formation does not start its Agents or Sandbox.

```text
POST /api/legion-groups                      {name, node_ids}
PATCH /api/nodes/{member_id}                 {parent_id: legion_id | null}
PATCH /api/nodes/{legion_id}                 {config: {...}}
GET /api/legion-groups/{legion_id}/state
PUT /api/legion-groups/{legion_id}/state      {value, expected_revision}
GET /api/legions/presets
POST /api/legions/presets/{id}/instances     {position, unwrap: true}
POST /api/legions                           {name, node_ids, presentation: {node_id: {level, base_level, surface_sizes}}}
POST /api/legions/{id}/instances            {position, as_group | unwrap}
```

Group formation is one database transaction and emits world events only after
commit. Grouping and membership changes participate in frontend undo/redo.

## Extension boundary

This layer provides persistent team identity, membership and context. It does
not yet execute a plan, schedule a DAG, automatically dispatch roles, or recover
external jobs. Those should be separate Controller/Task/Job primitives using the
Legion identity and state, rather than interpreting permission edges as task
dependencies. Role names are descriptive and do not trigger scheduling.
