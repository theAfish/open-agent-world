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

## Automatic event discovery

Registered tools are automatically available in the state-machine event picker through the same live capability projection as the Agent's tool list. There is no separate plugin event API, frontend adapter, or capability-name allowlist to implement. Existing inline `CapabilityGrantDefinition` registrations are normalized and work too. A friendly `CapabilityDefinition.label` is optional; otherwise the host derives a display name from `tool_name`.

The host's authorized invocation boundary emits `capability.started` followed by one of `capability.succeeded`, `capability.failed`, `capability.cancelled`, or `capability.timed_out`. A returned mapping with `ok: false` is a failure. Invocation ID, Agent, actual capability kind, target, Run, and session identify the call; arguments and result contents are excluded. Authorization rejection does not emit a tool-start event. Returning a handle for asynchronous work means the call returned, not that the work finished.

Plugins already using `NodeExecutionDefinition` also expose assigned-work lifecycle events automatically. The host reads `executor_relationship` to discover work-source → Agent bindings independently of Agent → tool permissions, and emits execution outcomes after the work-source document is updated. No Task Board-specific event registration is needed.

The host persists operation events alongside local business mutations where possible. Explicitly enabled, version-bound state-machine instances consume this journal independently of the editor or a provider turn. Rules atomically update user-defined states and queue action intents; dispatch reuses these same capability boundaries and never blindly retries an uncertain external outcome. Saving a definition alone does not enable it, and legacy previews remain disabled. See [State machines](../state-machines.md).

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

Continue with [Add a custom interface](frontend.md), or [Test and distribute](testing.md) for a Python-only plugin.
