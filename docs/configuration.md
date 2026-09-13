# Configuration

[Documentation](README.md) / [Getting started](getting-started.md)

## Models and connections

Open the gear button and select **Models** in the left sidebar.

1. Add a connection for OpenAI, Anthropic, Gemini, an OpenAI-compatible endpoint, or a local service.
2. Give it a name and the appropriate API address. Enter an API key when the service requires one; local services may not need one.
3. Add models with a friendly display name and the actual service model ID. Select a default for new Agents and save.

Multiple connections can use the same provider with different accounts or endpoints. Agent and Legion selectors group models by connection and support search. Model lists are manual: automatic discovery and capability probing are not implemented.

### Credentials and changes

The API-key field is always visible. Entering a key selects it as the authentication source; leaving it blank preserves the saved key. **Remove saved key** deletes it explicitly. **Advanced connection options** exposes backend-environment authentication for managed deployments. Changing the source does not delete the saved key.

Connections and models are stored on the backend. Disabling one preserves its stable reference but requires affected Agents to select an available model. Changes apply to subsequent Runs; an active Run keeps its resolved connection. Legacy browser model lists are imported into the first settings draft on upgrade; save the draft to complete migration. Existing raw model names retain their routing.

Keys are encrypted on the backend and never returned as plaintext by the settings API or stored in browser storage. Windows protects the encryption key with account-bound DPAPI. Other platforms keep it in `secrets/settings.key` with owner-only permissions. Back up that key with the data directory, and restore Windows data under the same account.

### Runtime providers and environment configuration

Google ADK is the default Agent runtime. Its adapter uses native model support for unqualified names and LiteLLM for provider-qualified names such as `openai/...` and `anthropic/...`. Use a model ID supported by your service rather than assuming a particular example is available.

For native Google credential configuration, `GOOGLE_API_KEY` can be set before launch. ADK's Vertex AI path uses `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` with the required Google credentials. These backend credentials are not inherited by Sandbox commands.

Model connections configure model access; runtime providers control Agent execution. Plugins may register other providers per Agent, so not every Run uses ADK. See [Runs and runtime providers](runs.md) and the [Codex plugin](../plugins/codex/README.md). The [mock launch mode](getting-started.md#try-without-model-credentials) supports credential-free debugging.

## Application storage

Formal application data defaults to `%LOCALAPPDATA%/OpenAgentWorld` on Windows, `$XDG_DATA_HOME/open-agent-world` (or `~/.local/share/open-agent-world`) on Linux, and `~/Library/Application Support/OpenAgentWorld` on macOS. Source launches can override it with `OPEN_AGENT_WORLD_DATA_ROOT`. The `dev` launchers always use a separate checkout-owned profile; choose one with `-Profile` / `--profile`. See [Desktop installation and development](desktop.md).

In **Settings > Storage**, choose a new or empty absolute local folder and save. The running backend keeps using the current location. On its next start, before opening application services, it checkpoints the databases, copies and verifies the data, then switches to the new directory. Conversations, sessions, credentials, artifacts and managed Sandbox files move together. Custom external workspaces stay where they are. The source remains a backup from before the move; subsequent changes go only to the new location. Large stores take longer to start while copying and need enough free space for a full copy. The formal launcher waits for startup without a fixed deadline and records migration progress in its launch log, outside the movable store.

A small startup pointer stays outside the data directory, next to the platform's default folder (on Windows, `%LOCALAPPDATA%/OpenAgentWorld.storage.json`). Keep this file to preserve the selected location. Settings shows the current location, pending move, retained backup and migration errors. You can cancel a scheduled move before restarting. A failed migration keeps the original active and retries on a later start; it never merges into an unrelated nonempty folder. Stop other backend instances before migration. Explicit `OPEN_AGENT_WORLD_DATA_ROOT` overrides bypass this pointer and make the Storage setting read-only. Relocation is within the same host/account; Windows credential protection remains tied to that account.

## Sandbox defaults and execution credentials

In **Settings > Sandbox**, choose a default runtime and an existing absolute Workspace location on the backend host. New Sandboxes get separate subfolders there; existing cards retain their saved configuration. Clear the location to restore managed workspaces for new cards. Custom-location folders survive card deletion.

A Sandbox's own Working folder and access mode are configured before starting it. **Browse** opens a folder picker on the backend computer and fills the draft; save to apply it. Remote/headless deployments can enter paths manually. Windows uses its native dialog; Linux/macOS desktop browsing requires Python Tk support.

See [Sandbox workspace](sandbox-workspace.md) for runtime and folder behavior, and [Execution configuration](execution-configuration.md) for command variables, private credentials, Environment Profiles, and Compute Targets.

## Management access

The management API and development proxy accept local host connections by default. Remote integrations require a configured host-private control-plane credential. Custom ASGI launchers must preserve socket peers with `--no-proxy-headers`. See [trust zones](security.md#trust-zones) before configuring remote access.
