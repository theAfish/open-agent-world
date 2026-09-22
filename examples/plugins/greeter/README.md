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

In **Pack & Card Library**, open the **Greeter** pack, collect its card, and add
it to your active deck. Place it, then connect an Agent with **Greet with**.
The edge authorizes that Greeter as a target of `greet(target, name)`. Multiple
Greeters share one tool, with readable target aliases. Removing the edge or
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

Start with the [Agent tool walkthrough](../../../docs/developers/agent-tools.md).
See [`docs/plugins.md`](../../../docs/plugins.md) for the complete contract,
additional extension patterns, packaging, validation, and security guidance.
