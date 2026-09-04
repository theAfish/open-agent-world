# Greeter plugin example

This installable example exercises the canonical plugin path:

```text
entry-point factory -> plugin descriptor -> owned registry contributions
                    -> catalog/node/lifecycle -> relationship
                    -> scoped Agent tool -> instance-owned runtime
```

From the repository root, start the application with the example attached:

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./examples/plugins/greeter
```

In the UI, the **Community examples** deck and **Greeter** card are supplied
entirely by this package. Connect an Agent to a Greeter with **Greet with**.
The edge grants that Agent a scoped `greet_with_*` tool. Removing the edge or
Greeter revokes the tool immediately.

Both contributions explicitly opt into Legion portability. Select an Agent and
Greeter together to collect and redeploy the complete configured formation; the
plugin lifecycle reconstructs each new Greeter runtime from its copied config.

Before unplugging, delete every Greeter card from the world so no persisted object
still records the plugin as its owner. Then restart without `-PluginPath`.

Run its integration test through an editable install. The test inspects the real
entry-point metadata and loads the plugin through host discovery:

```powershell
uv run --project backend --with-editable ./examples/plugins/greeter `
  python -m pytest -p no:cacheprovider examples/plugins/greeter/tests
```

See [`docs/plugins.md`](../../../docs/plugins.md) for the complete contract,
additional extension patterns, packaging, validation, and security guidance.
