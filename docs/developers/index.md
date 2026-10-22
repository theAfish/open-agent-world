# Build a plugin

**English** | [简体中文](index.zh-CN.md)

A Pack can contribute cards without a Python plugin. Start with
[Create cards and connect them](card-contract.md) to choose between a saved
Legion, declarative cards, Card Factory and Python. Add Agent tools or React
views when your idea needs them.

## Follow the learning path

| Step | You will finish with |
| --- | --- |
| 1. [Set up development](setup.md) | OAW running in an isolated development profile |
| 2. [Create your first plugin](first-plugin.md) | A discoverable package, a pack, and a persistent card |
| 3. [Give an Agent a tool](agent-tools.md) | A connection that authorizes a working tool |
| 4. [Add a custom interface](frontend.md) | A view using the public frontend SDK |
| 5. [Test and distribute](testing.md) | Checks and clear installation instructions for your package |

For a form card with saved values and passive connections, start with
[declarative cards](declarative-packs.md). The learning path above covers the
Python API for runtime behavior. React and TypeScript are only needed for a
custom view; both paths can use OAW's standard controls without model credentials.

For distribution, add `oaw.pack.toml` and keep Python dependencies in
`pyproject.toml` and frontend dependencies in the Pack's `frontend/package.json`.
Use the public Python and frontend SDKs; avoid imports from private host source
directories. [The unified author/build/install guide](../pack-distribution.md)
covers shared adapter wheels, isolated workers, explicit Sandbox dependency
groups, independent frontend builds and the same lifecycle for checkout Packs
and `.oawpack` releases.

## Find the right example

| Example | Learn from it |
| --- | --- |
| [Connected Notes](https://github.com/theAfish/open-agent-world/tree/dev/examples/packs/connected-notes) | Two persistent cards and a passive association, without Python or JavaScript |
| [Hello World](../../examples/plugins/hello_world/README.md) | A minimal card and pack |
| [Greeter](../../examples/plugins/greeter/README.md) | Agent tool, relationship, lifecycle, and integration test |
| [Structure Viewer](../../plugins/structure_viewer/README.md) | Connected file viewing |
| [SQLite](../../plugins/sqlite/README.md) | Managed native files and scoped resource actions |
| [Task Board](../../plugins/task_board/README.md) | Documents, actions, and work-source execution |
| [Deployed workspace](../../examples/deployed-workspace/README.md) | A scoped interface for a published workspace |

After the first tutorial, use [Choose an extension point](extension-points.md) to find advanced patterns. [Technical reference](../reference/index.md) contains the full contracts.
