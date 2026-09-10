# Getting started

[Documentation](README.md) / [Configuration](configuration.md)

## Requirements and installation

Run all commands from the repository root after cloning it.

- Python 3.11 or newer, uv, and Node.js 20 or newer.
- Windows 10/11 or Linux, including WSL2, for local Sandbox execution.
- macOS can run the application, but has no local Sandbox runtime.

Windows PowerShell:

```powershell
./scripts/setup.ps1
./scripts/dev.ps1
```

Linux, WSL2, or macOS:

```bash
bash scripts/setup.sh
python3 scripts/dev.py
```

Setup installs the backend development dependencies, Google ADK and LiteLLM adapters, and frontend dependencies. The launcher selects available local ports and prints the application URL; use that URL rather than assuming a fixed port. Ctrl+C stops the launcher.

Application setup and Sandbox provisioning are separate. Placing a card does not provision an execution environment. See [Sandbox workspace](sandbox-workspace.md) for prerequisites and starting a Sandbox.

## First launch

1. Open the printed URL and choose **Settings > Models**. Add a connection, enter its API key if required, add the service model ID, select a default model, and save. See [model configuration](configuration.md#models-and-connections).
2. Open **Pack & Card Library**, open an installed pack, and add collected cards to your active deck. A fresh installation starts with unopened packs and an empty deck.
3. Place an Agent from the bottom tray, set its instructions and model, and connect the resources you want it to use. [Core concepts](concepts.md) explains the available relationships.

## Try without model credentials

For deterministic local debugging, use the mock runtime:

```powershell
./scripts/dev.ps1 -AgentRuntime mock
```

Or with the portable launcher:

```bash
python3 scripts/dev.py --agent-runtime core.mock
```

The mock runtime is a debugging substitute, not a language model. Real Agent responses require a configured model service. Existing Agents with an explicitly selected runtime retain that selection.

## Startup and troubleshooting

- Missing dependencies: run setup before launching. The portable launcher checks for the backend virtual environment, Vite, and Node.
- Model authentication or routing failures: check the connection's enabled state, credential source, endpoint, and exact model ID in [Configuration](configuration.md).
- Empty tray: collect cards and select deck contents in [Card Library](card-library.md).
- Unavailable Sandbox: inspect its runtime diagnostics, install the reported prerequisites, and refresh discovery. OAW does not substitute unisolated execution.
- For a disposable development store, set `OPEN_AGENT_WORLD_DATA_ROOT` before launch. See [storage](configuration.md#application-storage).
- Windows `scripts/dev.ps1` waits for backend readiness without a fixed deadline and reports migration progress. Use `-StartupTimeoutSeconds 300` for an explicit deadline. The portable `scripts/dev.py` has a bounded readiness wait, so a large storage migration can exceed it.

## Development and verification

Keep changes focused and run checks appropriate to the affected behavior. The stack and ownership boundaries are described in [Architecture](architecture.md); plugin contributors should start with [Plugins](plugins.md).

Windows:

```powershell
./scripts/verify.ps1
```

This runs backend tests, a native disposable Windows Sandbox smoke test, frontend tests, and the production build. `-SkipNativeSandbox` skips that native smoke step; it does not establish OS isolation coverage.

Linux/WSL code checks:

```bash
uv run --project backend pytest tests backend/tests
npm --prefix frontend test
npm --prefix frontend run build
```

Native Linux HTTP lifecycle acceptance:

```bash
OAW_TEST_SANDBOX_RUNTIME=linux uv run --project backend pytest backend/tests/test_sandbox_system.py
```

On Windows, set `OAW_TEST_SANDBOX_RUNTIME` to `windows` or `wsl:<installed distribution>` before running that test. The broader WSL checks in `backend/tests/test_linux_sandbox.py` use `OAW_TEST_WSL_DISTRO`. Native tests require a usable isolation runtime; ordinary tests and skipped native checks are not evidence of OS isolation. See [Sandbox networking](sandbox-networking.md#real-runtime-acceptance) for separate network acceptance.

With the development server running, the Windows public-API scenario is:

```powershell
uv run --project backend python -m backend.scripts.http_acceptance_smoke
```

It creates and cleans up a four-card world, exercises resources and mock Agent tools, and checks AppContainer execution and edge revocation.

Browser interaction checks:

```bash
npm --prefix frontend run test:e2e
```

Playwright uses installed Chrome and isolated test servers. Browser checks verify UI behavior; they do not establish native Sandbox isolation.
