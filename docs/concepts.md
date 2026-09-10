# Core concepts

[Documentation](README.md)

## Cards and the world

The world is a persistent canvas. Each card represents an object with its own configuration and identity: an Agent, a document, a Sandbox, or a plugin-defined resource. Moving or resizing a card changes its presentation, not its permissions.

Pan and zoom to navigate, place cards from the bottom deck, and open a card's expanded surface or inspector to work with it. Text and Image cards hold managed resources; a Sandbox provides a separate execution workspace.

## Connections and capabilities

A connection describes an allowed interaction. Connect card boundaries and choose a supported relationship. The backend validates the combination; visual proximity alone grants nothing.

| Connection | What it allows |
| --- | --- |
| Agent -> Text | Read, or read and edit |
| Agent -> Image | View the image |
| Agent -> Agent | Send a message and request another Agent's response; direction is configurable |
| Agent -> Sandbox | Execute and inspect; **Execute + Start/Stop** additionally grants lifecycle management |
| Text / Image -> Sandbox | Attach a resource with its allowed mount access; images are read-only |

Plugins add further relationships. A **capability** is the operation an Agent receives from an authorized relationship, such as reading a particular document. Tools check current permissions when invoked. Removing or changing an edge revokes the corresponding future access; it does not undo completed work.

Direct document access does not require a Sandbox. Conversely, connecting an Agent to a Sandbox does not grant arbitrary host filesystem access. See [Sandbox workspace](sandbox-workspace.md).

## Agents and Runs

An Agent has instructions, a selected model, and access to connected resources and tools. A **Run** is one execution attempt by that Agent, with its own status and results. The Agent persists across Runs. Watch activity for tool calls, outputs, and errors; activity does not expose hidden model reasoning.

[Configuration](configuration.md) covers model connections. [Runs](runs.md) explains concurrency, cancellation, sessions, and runtime providers.

## Teams and reusable arrangements

A **Legion** groups ordinary cards into a team space with shared instructions, optional model settings, and shared state. Existing connections can cross its boundary. Group membership alone is not a replacement for resource permissions.

Save reusable formations as Legion templates, then instantiate them when needed. Templates describe reusable setup; they do not carry private credential bindings or external host folder bindings. See [Legion team spaces](legions.md).

For task coordination, the bundled [Task Board](../plugins/task_board/README.md) supports shared tasks, dependencies, and optional Agent execution bindings. [Skill Toolboxes](../plugins/skill_packages/README.md) collect reusable instructions and skills.

## Packs, the Library, and Decks

A **plugin** supplies functionality. A **pack** groups its card definitions. Opening an installed pack adds reusable cards to your **collection**; a **deck** chooses which collected cards and saved Legions appear in the bottom tray. Placing a card creates a world instance.

Deck membership is a convenience, not an execution permission. A fresh installation starts with unopened packs and an empty deck. See [Packs, Card Library, and Decks](card-library.md) for collection, updates, and availability rules.

## Current scope

OAW is a local experimental environment. Accounts, multiplayer collaboration, an online plugin marketplace, and a general cloud execution service are not implemented. Plugins run as trusted backend code. A Compute Target describes a destination; it does not itself provide a remote scheduler or connection. See [execution configuration](execution-configuration.md) and [security](security.md).
