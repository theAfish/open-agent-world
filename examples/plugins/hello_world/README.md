# Hello World plugin

The smallest card used in [Your first plugin](../../../docs/developers/first-plugin.md).
The host supplies configuration editing and persistence. There are no Agent tools,
custom UI, external resources, or lifecycle callbacks in this example.

From the repository root, copy this package into an immediate child of `plugins/`:

```powershell
Copy-Item -Recurse examples/plugins/hello_world plugins/hello_world
./scripts/dev.ps1 -AgentRuntime mock -Profile plugin-tutorial
```

On Linux/macOS: `cp -R examples/plugins/hello_world plugins/hello_world`, then
`python3 scripts/dev.py --agent-runtime core.mock --profile plugin-tutorial`.

Open **Pack & Card Library**, open **Hello World**, add **Hello card** to your
active deck, place it, and edit its `message` in the standard configuration UI.
Restarting with the same development profile preserves the saved configuration.

Run the package's integration test without copying it:

```sh
uv run --project backend --with-editable ./examples/plugins/hello_world python -m pytest -p no:cacheprovider examples/plugins/hello_world/tests
```

Delete placed Hello cards before removing the package. If making your own plugin,
rename the Python package, distribution, entry point, descriptor and contribution
IDs together; do not load two different packages with the same identifiers.
