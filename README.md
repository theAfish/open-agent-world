# Open Agent World

Open Agent World is a Windows-first spatial environment for agents, managed resources, and isolated workplaces. Cards are persisted world objects. Edges are live permissions: changing the graph changes what an Agent or Sandbox can do.

This repository is a proof of concept built with React, TypeScript, Vite, React Flow, Zustand, FastAPI, Pydantic, SQLite, WebSockets, Google ADK, and Windows-native AppContainer/Job Object isolation. It does not use Docker or WSL.

## Run locally

Requirements:

- Windows 10/11 x64
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer

Set up the deterministic local runtime:

```powershell
./scripts/setup.ps1
./scripts/dev.ps1
```

Then open <http://127.0.0.1:5173/>. The API listens on <http://127.0.0.1:8000/>.

The mock Agent runtime is selected explicitly by `dev.ps1`; it is useful for testing the real capability broker without a model credential. To run actual Google ADK agents:

```powershell
./scripts/setup.ps1 -WithAdk
$env:GOOGLE_API_KEY = "your-key"
./scripts/dev.ps1 -AgentRuntime google-adk
```

Google Vertex AI credentials supported by ADK can be used instead by setting `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, and `GOOGLE_CLOUD_LOCATION` before starting the application. These values remain in the Agent trust zone and are never inherited by Sandbox processes.

Application data defaults to `%LOCALAPPDATA%/OpenAgentWorld`. Override it before startup with `OPEN_AGENT_WORLD_DATA_ROOT` when a disposable development store is useful.

## What is implemented

- Infinite pan/zoom surface with dot and contour terrain, semantic edges, drag/drop palette, in-place card expansion, selection, and light/dark tokens.
- Agent, Text, Image, and Sandbox card systems with persisted position, size, configuration, resources, and relationships.
- Backend-authoritative edge validation and capability derivation with immediate permission revocation.
- Managed UTF-8 text read/replace/patch operations and image import/inspection; resources never retain arbitrary host paths.
- Scoped Google ADK tools rebuilt for every run, with authorization checked again at tool invocation.
- Windows AppContainer process identity, NTFS ACL grants, a minimal environment, network-denied capability set, and Job Object containment behind `SandboxBackend`.
- Typed runtime activity over WebSocket without exposing hidden model reasoning.
- 2048-unit chunk indexing, viewport prefetch, distant-card unloading, and a developer stress generator.

## Verify

```powershell
./scripts/verify.ps1
```

The verification script runs backend invariant tests, a native disposable AppContainer smoke test, frontend state/relationship tests, and the production frontend build. The native pass verifies read-only mount enforcement, unrelated-host-file isolation, default network denial, timeout, and child-process cleanup. It fails explicitly if the host cannot establish the required boundary; the application never falls back to an ordinary subprocess. Code-only CI on a non-Windows host can opt out with `./scripts/verify.ps1 -SkipNativeSandbox`.

With the development server running, the public-API acceptance scenario can also be replayed from a second terminal:

```powershell
uv run --project backend python -m backend.scripts.http_acceptance_smoke
```

It creates and cleans up a four-card world, exercises direct Text/Image capabilities, runs the mock Agent boundary, edits a mounted Text resource inside a real AppContainer, and verifies edge revocation.

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
  agents/           AgentRuntime and Google ADK adapter
  sandbox/          SandboxBackend and Windows native boundary
  events/           typed WebSocket activity
  persistence/      SQLite schema and transaction boundary
```

See [architecture](docs/architecture.md) and [sandbox security contract](docs/security.md) for the key invariants and trust boundaries.

## POC boundary

This is a local experimental environment, not a production multi-user service. It intentionally omits accounts, cloud execution, third-party plugin cards, multiplayer collaboration, and arbitrary host filesystem access. Sandbox security is fail-closed, but the code should still be reviewed before using it with hostile workloads.
