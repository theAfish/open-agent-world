# Share a Legion as a content Pack

You can create and share a useful formation without writing a plugin or signing
in to a store. A content Pack contains saved Legion templates, usage instructions
and references to the original Packs that supply its cards and tools.

## Create a Pack

1. Build and configure a Legion on the canvas, then save it to the Library.
2. In **Library → Cards**, select your saved Legion and choose **Make a Pack…**.
3. Enter a name, version, description and optional author. Use **First-use guide**
   for preparation steps, an example task and its expected result.
4. Choose **Check Pack**. Dependencies are derived from the template and pinned to
   the currently loaded plugin versions. Install missing dependencies and restart
   OAW before preparing a release.
5. Initial documents, resources and shared state are omitted by default. Select
   individual cards under **Include initial content** if their captured content is
   meant to be shared, then check again. A card whose template handler cannot
   represent empty state must be explicitly included or removed from the formation.
6. Resolve blocking findings and review the warnings. Choose **Export .oawpack**.

The UI exports one saved Legion per file in this first version. The Legion keeps
its member sizes, connections, layout, state scopes and supported portable
payloads. The original saved Legion is not changed by export. Agent model choices
and Legion model overrides return to recipient defaults; Sandbox runtime selection
returns to auto. Existing card template projections remove their machine bindings.

Check instructions and selected resources yourself before sharing. The checks
identify common structured credential fields and highlight local paths and service
addresses; they cannot identify every secret in prose, binary resources or
arbitrary plugin data. Conversation histories and run logs are not serialized by
the standard Legion capture path. Selected documents use each card's capture
contract, including its rules for resetting task progress.

## Install and use

Share the exported file directly, or attach it to a release in your own repository.
Recipients use **Library → Packs → Install Pack from File…**. The review displays
the description, self-declared author and first-use guide.

Install and activate the required Packs first. Missing or incompatible
dependencies are reported with their IDs and version requirements. Content Packs
use the existing installation, immutable-version and restart workflow; there is
no automatic downloading of dependencies. After restarting OAW, open the new Pack
in the Library. Its Legion appears under that Pack, before ordinary cards. Drag
it into a deck or onto the canvas when you want to use it. Installation does not
add entries to your decks.

Configure the recipient's model, tools and local environment before running the
example. The first-use guide also remains available in **Manage installed Packs**
and in the artifact's `README.md`.

## Release an update

Keep the **Pack ID** and increase the version, for example from `0.1.0` to `0.1.1`.
The ID initially uses a local namespace derived from the saved Legion. The author
field is descriptive and does not claim a verified publisher identity.

Installation rejects replacement of an existing ID/version with different
content, including after removing that version. Updating a Pack changes the
available template. Previously instantiated Legions and their user edits remain
independent copies. Retained versions can be selected through the existing Pack
management controls.

## Check with a clean profile

Before sharing, install the file in a separate OAW data directory and follow the
recipient steps. On systems where you launch OAW from source, select a temporary
`OPEN_AGENT_WORLD_DATA_ROOT` for this check. Use a fresh directory; do not delete
your working profile. Test the example with recipient-owned model settings.

This catches hidden configuration dependencies. A separate data directory is not
a security sandbox for plugin code. Content Pack archives contain no executable
entrypoints, but their templates refer to installed tools and instructions that
can perform actions when a user runs the formation.

## Artifact contract

Content Packs extend `.oawpack` with `schema_version: 2` and `kind: "content"`.
Version 1 code Packs keep their existing contract. An example content manifest is:

```json
{
  "schema_version": 2,
  "kind": "content",
  "id": "local.research",
  "name": "Research assistant",
  "version": "0.1.0",
  "compatibility": {"oaw": ">=0.1.0,<1", "plugin_api": "1.23", "frontend_api": 1},
  "dependencies": {"packs": [{"id": "open-agent-world.core.default", "version": "==0.1.0"}]},
  "content": {"legions": ["content/legion.json"]},
  "creator": {"description": "A research formation", "author": "Example author"}
}
```

Use the dependency IDs emitted by your installed OAW rather than copying the
example. Each declared JSON file contains a namespaced ID, name, description,
revision and the existing `LegionBlueprint` data. A content archive accepts only
`manifest.json`, `checksums.json`, optional `README.md` and its declared template
files. It rejects backend wheels, frontend modules, undeclared files and runtime
package installation requirements. Existing archive size, path and checksum
checks apply. Installing also validates template compatibility and declared owners
against loaded dependencies before selecting the version.

The host registers templates and an empty-card Pack wrapper through its own
registry adapter. No Python module or JavaScript bundle comes from the content
archive. Existing `python -m open_agent_world.pack build/inspect/install` commands
can process these artifacts as well.

Future creator work can add multi-Legion composition, cover assets, card presets,
Deck recipes and a guided temporary-profile preview. These are not part of the
current editor.
