# Create cards and connect them

Start with the smallest authoring path that provides the behavior you need.
A Pack distributes card types and saved formations. After opening it in the
Library, collect the cards you want, organize them into your own Decks, and
place instances on the canvas. A Deck is your reusable selection; a Pack is
the versioned file you install or share.

## Choose an authoring path

| What you want to make | Start here | What you author |
| --- | --- | --- |
| A reusable formation of existing cards | [Share a Legion](../creator-packs.md) | Configure, connect and save a Legion, then choose **Make a Pack…** |
| A saved form card, optionally with passive connections | [Declarative cards](declarative-packs.md) | JSON configuration schema and a short `oaw.pack.toml`; no Python required |
| A visual card face with form fields, simple calculations and connections | [Card Factory](../card-factory.md) | Face and function designs; print, check and export through the workbench |
| An Agent tool, data access, execution or custom lifecycle | [Python plugins](first-plugin.md) | A Python registration hook, optionally combined with declarative cards |

Custom React views can be added to declarative or Python cards independently.
You do not need a custom view to create a persistent card or connect it.

For a complete code-free project containing a Note and a Topic, copy
[Connected Notes](https://github.com/theAfish/open-agent-world/tree/dev/examples/packs/connected-notes).
Build and install it, place one of each card, and connect the Note to the Topic
using **About topic**. The connection records that association; it does not copy
the note's text into the topic or run either card.

## The card contract

| Part | Meaning |
| --- | --- |
| Card type | A stable registry ID and its definition; many canvas cards may share it |
| Card instance | A saved canvas object with its own ID, configuration, geometry and connections |
| Configuration | The saved data shape and defaults, defined by a JSON schema or Python model |
| Views | Named frontend components such as `body` or `settings` |
| Traits | Published marker strings used to match compatible card types |
| Association | A named relationship rule selecting source and target card types or traits |
| Capability | Authority granted by an advanced relationship, such as using an Agent tool |

An interface is the combination of configuration, views and connection rules.
Traits are markers, not JSON schemas or permissions. Passive cards declare
traits within their own owner's namespace. Publish their intended
meaning with your Pack. A passive association uses the existing relationship
mechanism and grants no capabilities or data-reading access. It does not provide
typed ports, payload negotiation, routing, cardinality constraints or automatic
execution. Use the [Python relationship contract](../plugins.md#relationships-traits-and-capabilities)
when a connection needs a supported runtime behavior, such as a tool grant
or a data consumer/provider contract.

### Views and presentation levels

Frontend slots (`preview`, `body`, `settings`, `workspace`) select plugin view
components. Presentation levels (`node`, `preview`, `inspector`, `workspace`)
control how a card opens and appears on the canvas. `settings` is a frontend
slot; `inspector` is a presentation level. They are separate contracts and
must not be renamed to substitute for one another. The
[frontend guide](frontend.md) and [card design reference](card-design.md) cover
each in detail.

### Identities and versions

The distribution ID identifies the installed artifact. The runtime owner owns
registrations; a library Pack groups collectible cards; each card type and
relationship has its own ID. Schema 3 can preserve distinct existing IDs.
Changing any persisted type or relationship ID can leave existing cards or
connections missing, so keep IDs stable and increment the distribution version.

Use a dotted, lowercase distribution ID such as `example.connected-notes`.
Runtime IDs support a broader grammar; do not assume every runtime ID is a valid
distribution filename identity. The [manifest reference](../reference/pack-manifest.md)
lists the contracts and the feature-specific Plugin API minimums.

## From a design to a reusable Pack

1. Give each card useful defaults so it can be placed directly from the Library.
2. Add an association only when its source and target mean something to the user.
   Choose an exact type for a specific card, or a documented trait for compatible
   types supplied by different Packs.
3. Declare external Pack dependencies when the example requires those Packs to
   be installed. A trait selector does not install anything or prove that a
   compatible target currently exists.
4. Build and inspect the `.oawpack`, then install it into a separate test profile.
   Place both cards, edit their configuration and try both allowed and rejected
   connections. Restart and check the saved cards and connection.
5. Share the artifact with a short first-use example. New passive Packs can
   activate live; updates to a loaded owner and Packs containing Python require
   the restart shown by the Library.

Schema 2 Factory recipes and Schema 3 contributions intentionally serve different
authoring workflows. Factory cards use the host's form/face renderer and bounded
operations; Schema 3 cards use the supported JSON Schema subset and optional
frontend modules. Their defaults, saved configuration and IDs are not
interchangeable. They share the installer, registry and passive relationship
rules; converting one format to another is not an automatic migration.

## Existing cards and current boundaries

Existing type and relationship IDs, saved cards and manual layouts remain in
place. Factory now makes reusable local types when printing; earlier generic
printed cards still open with their saved designs. Legacy custom-button bindings
remain readable inert metadata and are not interpreted as connections.

New passive traits and associations require API 1.39; older hosts reject those
artifacts through compatibility checks. Existing Packs keep their feature-based
minimum API requirement. `settings` and `inspector` retain their distinct
meanings. There is no automatic migration between authoring formats and no new
port, cardinality or data-routing system.
