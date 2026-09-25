# Give an Agent a tool

**Goal:** understand a working tool from package discovery to live authorization. Use the included Greeter example after completing [Your first plugin](first-plugin.md).

## Run the example

Copy the example into the local plugin directory, then restart OAW with the same development profile:

Windows:

```powershell
Copy-Item -Recurse examples/plugins/greeter plugins/greeter
./scripts/dev.ps1 -AgentRuntime mock -Profile plugin-tutorial
```

Linux/macOS:

```sh
cp -R examples/plugins/greeter plugins/greeter
bash scripts/dev.sh --agent-runtime core.mock --profile plugin-tutorial
```

Do not copy over an existing plugin directory. Alternatively, Windows supports attaching the original package with `-PluginPath ./examples/plugins/greeter`; that attaches its backend package only.

Open **Pack & Card Library**, open **Greeter**, collect its card, and add it to your active deck. Place an Agent and a Greeter. Connect them with **Greet with**.

## Read the implementation in this order

Open [the Greeter source](../../examples/plugins/greeter/src/oaw_greeter_plugin/__init__.py).

| Part | Purpose |
| --- | --- |
| `GreeterConfig` | Validates the greeting, punctuation, and uppercase option |
| `GreeterPlugin.register` | Registers the node, tool, relationship, and pack |
| `CapabilityDefinition` | Names `greet` and describes its `name` argument |
| `RelationshipDefinition` | Matches an Agent to a greeting target and grants the capability |
| `GreeterPlugin.greet` | Validates input and uses the authorized `capability.target_id` |
| `GreeterLifecycle` / `GreeterMutation` | Reconstructs and updates instance-owned runtime state |

The user-created connection grants access to a specific Greeter. Multiple connected Greeters share a tool with selectable targets; the plugin does not invent a second permission system.

The handler returns a result such as `{"text": "Welcome, Ada!", "greeter_id": "..."}`. Invalid input raises `ResourceValidationError`, allowing the Agent to correct its request.

## Verify it without a model

Run the integration test from the repository root:

```sh
uv run --project backend --with-editable ./examples/plugins/greeter python -m pytest -p no:cacheprovider examples/plugins/greeter/tests
```

It verifies real installed entry-point discovery, catalog ownership, a connection-derived tool call, configuration changes, independent plugin instances, Legion cloning, and revocation after deletion. This exercises the tool directly through the host; it does not prove that a real model chooses it correctly.

To try a natural-language request, start with your configured model runtime and select a real model for the Agent. Ask it to greet Ada using the connected Greeter. The mock runtime is for deterministic tests and is not an autonomous language model.

## Adapt the pattern

Define the input schema, return a bounded result, and keep tool handlers behind live host authorization. Add transactional lifecycle handling only when your plugin owns resources that must be created, restored, or cleaned up. File-backed resources should use host-managed storage.

For the exact contracts, see [relationships and capabilities](../plugins.md#relationships-traits-and-capabilities), [lifecycle transactions](../plugins.md#lifecycle-transactions), and [native resources](../plugins.md#native-file-resources).
