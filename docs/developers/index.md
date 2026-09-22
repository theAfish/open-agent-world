# Build a plugin

A plugin is a Python package that registers cards and other contributions with OAW. You can start with a card and an editable field; add Agent tools or React views when your idea needs them.

## Follow the learning path

| Step | You will finish with |
| --- | --- |
| 1. [Set up development](setup.md) | OAW running in an isolated development profile |
| 2. [Create your first plugin](first-plugin.md) | A discoverable package, a pack, and a persistent card |
| 3. [Give an Agent a tool](agent-tools.md) | A connection that authorizes a working tool |
| 4. [Add a custom interface](frontend.md) | A view using the public frontend SDK |
| 5. [Test and distribute](testing.md) | Checks and clear installation instructions for your package |

**Start with Python.** React and TypeScript are only needed for a custom interface. The first example uses OAW's standard card controls and needs no model credentials.

## Find the right example

| Example | Learn from it |
| --- | --- |
| [Hello World](../../examples/plugins/hello_world/README.md) | A minimal card and pack |
| [Greeter](../../examples/plugins/greeter/README.md) | Agent tool, relationship, lifecycle, and integration test |
| [Structure Viewer](../../plugins/structure_viewer/README.md) | Connected file viewing |
| [SQLite](../../plugins/sqlite/README.md) | Managed native files and scoped resource actions |
| [Task Board](../../plugins/task_board/README.md) | Documents, actions, and work-source execution |
| [Deployed workspace](../../examples/deployed-workspace/README.md) | A scoped interface for a published workspace |

After the first tutorial, use [Choose an extension point](extension-points.md) to find advanced patterns. [Technical reference](../reference/index.md) contains the full contracts.
