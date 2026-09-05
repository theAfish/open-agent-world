# Open Agent World

Open Agent World is a spatial environment for agents, managed resources, and isolated workplaces. Cards are persisted world objects. Edges are live permissions: changing the graph changes what an Agent or Sandbox can do.

The application uses React, TypeScript, Vite, React Flow, Zustand, FastAPI, SQLite and Google ADK. Sandbox execution supports Windows AppContainer/Job Objects, native Linux isolation, and the same Linux isolation inside an existing WSL2 distribution. Docker and VM images are not required.

## Run locally

Requirements:

- Windows 10/11 or Linux (including WSL2); macOS can run the application but has no local Sandbox runtime yet
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer

Set up and run the Google ADK agent runtime:

```powershell
./scripts/setup.ps1
$env:GOOGLE_API_KEY = "your-key"
./scripts/dev.ps1
```

On Linux or inside WSL2:

```bash
bash scripts/setup.sh
export GOOGLE_API_KEY="your-key"
python3 scripts/dev.py
```

The Python launcher also works on Windows. Use `--agent-runtime core.mock` for local debugging without credentials. It selects available local ports and terminates its child servers on exit. The application and Sandbox dependencies are separate: creating cards does not install tools, download images, or provision a sandbox.


Google ADK is the built-in default Runtime Provider. Native ADK model names such as `gemini-3.7-flash` use ADK's built-in model support, while provider-qualified names such as `openai/...` or `anthropic/...` are resolved internally by ADK's LiteLLM adapter. The backend Run layer can also resolve a different registered Runtime Provider per Agent; this provider selection does not add a second user-facing model configuration path.

For an OpenAI-compatible endpoint, open the gear button, enter its Base URL (for example `https://llmapi.paratera.com`) and API key, then include the provider-qualified model names in the model list. These credentials stay in the browser session and backend runtime memory, are restored after a backend restart while the browser session remains open, and are never written to world data. The agent lifecycle, session handling, tools, permissions, and events remain ADK in every case.

For deterministic local debugging without a model credential, explicitly start the mock runtime:

```powershell
./scripts/dev.ps1 -AgentRuntime mock
```

Google Vertex AI credentials supported by ADK can be used instead by setting `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` before starting the application. These values remain in the Agent trust zone and are never inherited by Sandbox processes.

Application data defaults to `%LOCALAPPDATA%/OpenAgentWorld` on Windows, `$XDG_DATA_HOME/open-agent-world` (or `~/.local/share/open-agent-world`) on Linux, and `~/Library/Application Support/OpenAgentWorld` on macOS. Override it with `OPEN_AGENT_WORLD_DATA_ROOT` for a disposable development store.

## Sandbox working folders

Use **Browse…** next to the default Workspace location or a Sandbox's Working folder to open the operating system's folder picker on the backend computer. Selection fills the draft; click Save to apply it. Cancelling preserves the existing path. Desktop browsing is available for local connections; remote/headless deployments can still enter paths manually. Windows uses its built-in folder dialog; Linux/macOS desktop selection requires Python Tk support.

In **Settings → Sandbox**, set a default Workspace location (an existing absolute folder on the backend host, such as `D:\Workspaces`) and a default runtime. New Sandboxes receive separate subfolders there; existing cards keep their settings. These defaults are saved in the backend database and survive restarts. Clear the location to restore system-managed workspaces for new cards. Folders created under a custom location are retained when their Sandbox cards are deleted.

Open a Sandbox inspector, choose its runtime, enter an existing absolute **Working folder** path, select read/write or read-only access, and save before starting. An empty path uses a managed workspace. For a Windows-hosted server with a WSL runtime, enter a Windows path such as `D:\Projects\demo`; the bridge translates it for Linux. Paths always refer to the backend host, not the browser's machine.

Writes in the selected folder change real files immediately. Stop revokes active execution/access; deleting a card removes sandbox-owned storage and permissions, never the selected folder. Working folders can be changed while stopped. The chosen execution runtime is pinned on first start, so automatic discovery cannot silently move an existing card to a different filesystem. Use a new card to change runtime.

Automatic selection prefers a usable existing WSL2 environment on Windows, otherwise native Windows; native Linux uses its own kernel. Linux/WSL requires Bubblewrap, libseccomp, Python 3.10+ for the small trusted worker, and a systemd user manager supporting scopes with cgroup-v2 memory and process limits. The backend itself still requires Python 3.11+. Runtime discovery reports missing prerequisites; **Refresh** explicitly checks again after environment changes. No ordinary host-process execution is substituted when isolation is unavailable. See [the sandbox contract](docs/security.md).

Connected agents receive both inspect and execute tools. Inspection reports the actual OS, shell, working directory, resource paths and access mode. Attached Text/Image resources remain separate from the real working folder and retain their graph-defined permissions. Host folder bindings are excluded from captured Legion templates.

## What is implemented

- Infinite pan/zoom surface with dot and contour terrain, semantic edges, drag/drop palette, in-place card expansion, selection, and light/dark tokens.
- Agent, Text, Image, and Sandbox card systems with persisted position, size, configuration, resources, and relationships.
- Descriptor-scoped plugins with owned node types, relationships, lifecycle transactions, scoped capability handlers, and a backend-authoritative canvas catalog. See [plugin development](docs/plugins.md).
- A runnable, installable [Greeter plugin example](examples/plugins/greeter/README.md) plus temporary `-PluginPath` mounting for plugin development.
- Backend-authoritative edge validation and capability derivation with immediate permission revocation.
- Managed UTF-8 text read/replace/patch operations and image import/inspection; resources never retain arbitrary host paths.
- Scoped Google ADK tools rebuilt for every run, with authorization checked again at tool invocation.
- Durable, provider-neutral Runs with explicit lifecycle transitions, nested lineage, per-Agent concurrency policy, and cancellation by Run ID. See [Runs and runtime providers](docs/runs.md).
- A per-card runtime registry with lazy provisioning, live host folders, native Windows and Linux isolation, a WSL2 bridge, minimal environments, network denial and process-tree limits behind `SandboxBackend`.
- Typed runtime activity over WebSocket without exposing hidden model reasoning.
- 2048-unit chunk indexing, viewport prefetch, distant-card unloading, and a developer stress generator.

## Verify

```powershell
./scripts/verify.ps1
```

The verification script runs backend invariant tests, a native disposable AppContainer smoke test, frontend state/relationship tests, and the production frontend build. The native pass verifies read-only mount enforcement, unrelated-host-file isolation, default network denial, timeout, and child-process cleanup. It fails explicitly if the host cannot establish the required boundary; the application never falls back to an ordinary subprocess. Code-only CI on a non-Windows host can opt out with `./scripts/verify.ps1 -SkipNativeSandbox`.

Linux/WSL development checks:

```bash
uv run --project backend pytest tests backend/tests
npm --prefix frontend test
npm --prefix frontend run build
OAW_TEST_SANDBOX_RUNTIME=linux uv run --project backend pytest backend/tests/test_sandbox_system.py
```

The last test uses a disposable real folder and exercises the HTTP card lifecycle through the native boundary. On Windows, set `OAW_TEST_SANDBOX_RUNTIME` to `windows` or `wsl:Ubuntu` (using your actual distro name). The broader WSL isolation/timeout/cancellation test is enabled by `OAW_TEST_WSL_DISTRO=Ubuntu` when running `backend/tests/test_linux_sandbox.py` from the repository root. Native checks are opt-in; ordinary unit tests do not claim an OS boundary was exercised.

With the development server running, the public-API acceptance scenario can also be replayed from a second terminal:

```powershell
uv run --project backend python -m backend.scripts.http_acceptance_smoke
```

It creates and cleans up a four-card world, exercises direct Text/Image capabilities, runs the mock Agent boundary, edits a mounted Text resource inside a real AppContainer, and verifies edge revocation.

For a real-browser canvas interaction check, run:

```powershell
npm --prefix frontend run test:e2e
```

The Playwright check uses the installed Chrome, starts isolated mock-runtime backend and frontend servers, creates disposable Agent cards, drags a relationship from one card boundary to another, and verifies the persisted curve and both visible boundary endpoints. Stable `data-card-*`, `data-connection-side`, and `data-edge-*` attributes are also available to local accessibility and automation tools.

## Project map

```text
frontend/
  src/api/          API and event-stream boundary
  src/canvas/       spatial surface and chunk behavior
  src/cards/        the four world object renderers
  src/edges/        semantic relationship rendering
  src/palette/      creation controls
  src/state/        Zustand world state and invariants

backend/
  api/              FastAPI routes and application composition
  world/            authoritative cards, edges, and chunk index
  capabilities/     permission derivation and guarded operations
  resources/        managed text/image storage
  agents/           RuntimeProvider contract and built-in ADK/mock adapters
  runs/             durable Run records, InvocationContext, and RunManager
  sandbox/          SandboxBackend and Windows native boundary
  events/           typed WebSocket activity
  persistence/      SQLite schema and transaction boundary
```

See [architecture](docs/architecture.md) and [sandbox security contract](docs/security.md) for the key invariants and trust boundaries.

## POC boundary

This is a local experimental environment, not a production multi-user service. It includes a trusted backend plugin registration foundation, but intentionally omits accounts, a plugin marketplace or isolation boundary for plugin code, cloud execution, multiplayer collaboration, and arbitrary host filesystem access. Sandbox security is fail-closed, but the code should still be reviewed before using it with hostile workloads.
