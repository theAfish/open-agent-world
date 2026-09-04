# Greeter plugin example

This installable example exercises the complete common plugin path:

```text
Python entry point -> registry -> catalog -> canvas node -> relationship
                   -> scoped Agent tool -> plugin handler/runtime
```

From the repository root, start the application with the example attached:

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./examples/plugins/greeter
```

In the UI, the **Community examples** deck and **Greeter** card are supplied
entirely by this package. Connect an Agent to a Greeter with **Greet with**.
The edge grants that Agent a scoped `greet_with_*` tool.

Before unplugging, delete every Greeter card from the world so no persisted
object still depends on its type. Then restart without `-PluginPath`.

Run its integration test without changing the backend dependency files:

```powershell
uv run --project backend --with-editable ./examples/plugins/greeter `
  python -m pytest -p no:cacheprovider examples/plugins/greeter/tests
```

See [`docs/plugins.md`](../../../docs/plugins.md) for the complete contract,
additional extension patterns, packaging, validation, and security guidance.
