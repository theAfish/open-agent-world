# Build a Card App

A Card App adds behavior and optionally a custom interface to an OAW card. A Pack
is the installable distribution containing those contributions. Develop in an
external IDE with the public Python and React/TypeScript SDKs; a Coding Agent
can use the same files and commands.

## Build one complete app

Follow [the Greeter Card App tutorial](card-app.md). It takes one project through:

1. A runnable Python card in a separate development profile.
2. Validated settings and lifecycle state.
3. A relationship that grants an Agent a tool, with revocation tests.
4. A React editor using the host SDK.
5. A Schema 3 `.oawpack` containing its wheel and frontend.
6. Installation, restart and behavior checks in a clean host.

The tutorial and APIs here track **dev**. Build against the host version you
intend to support; a minimum Plugin API version is a compatibility requirement,
not proof that the feature has shipped in every desktop release.

## Choose a smaller path when it fits

| Need | Path |
| --- | --- |
| A form with saved fields and no Python behavior | [Declarative Pack quickstart](quickstart-pack.md) |
| A card face and basic form operations without programming | [Card Factory](../card-factory.md) |
| Reuse a configured group of cards | [Content Pack export](../creator-packs.md) |
| Visual source editing inside OAW | [Pack IDE (Experimental)](pack-ide.md) |

Factory remains available for visual card creation. Pack IDE is an experimental
source editor under **Developer tools**; use the external SDK workflow for a
complete Python/React project.

## Give a Coding Agent the right context

Start it with this page and [the complete tutorial](card-app.md), then give it your
host revision, desired card behavior and installation target. Require it to:

- import public Python APIs from `open_agent_world.plugin_api`, frontend contracts
  and helpers from `@oaw/plugin-api`, and React hooks from `react`;
- keep distribution ID, runtime owner, Pack ID and card type ID explicit;
- use host configuration, documents/state and live graph authorization;
- inspect the [API directory](../plugins.md) and [Pack contract](../pack-distribution.md)
  instead of inventing endpoints or importing private host modules;
- build and install the artifact in a disposable profile, and report checks it
  actually ran, including unavailable model/native validation.

For larger apps, continue with [documents and actions](../reference/plugin-documents.md),
[workspace interfaces](workspace-design.md), [data sources](data-sources.md),
[execution sources](execution-graph.md) or [card tutorials](progressive-tutorials.md).
