# Development setup

**Goal:** run the source checkout with a separate profile for your plugin experiments.

## Prerequisites

Install Git, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 22 or newer. Python is enough for the plugin code; Node builds and serves OAW's frontend.

Windows, Linux/WSL and macOS support local Sandbox execution with their required isolation runtime. macOS has a built-in Seatbelt fallback; Apple Container 0.6.0+ on macOS 26+ Apple Silicon provides the preferred VM boundary.

## Clone and install

```sh
git clone https://github.com/theAfish/open-agent-world.git
cd open-agent-world
```

Use the branch matching the documentation you are reading. This site's development guides track `dev`:

```sh
git switch dev
```

Windows PowerShell:

```powershell
./scripts/setup.ps1 -Profile plugin-tutorial -SkipBuild
./scripts/dev.ps1 -AgentRuntime mock -Profile plugin-tutorial
```

Linux, WSL, or macOS:

```sh
bash scripts/setup.sh --profile plugin-tutorial --skip-build
bash scripts/dev.sh --agent-runtime core.mock --profile plugin-tutorial
```

Open the URL printed by the launcher. Ports can change when the usual ones are occupied. Keep the launcher running and stop it with Ctrl+C.

Setup is the installation entrypoint on every platform, backed by `scripts/setup.py`.
Run it when creating a profile or changing dependencies. Default setup prepares
Core and its development tools first, then the optional Agent SDKs and bundled
Packs independently. A failed optional component does not block Core or the
remaining components. `--core-only` / `-CoreOnly` skips optional preparation.
`--skip-build` / `-SkipBuild` prepares dependencies without building production
assets. `--verbose` (PowerShell: `-Verbose`) streams detailed resolver output;
ordinary output retains progress and a bounded error summary.

For normal plugin installation and repair, use the application's Library/Store.
The source CLI is an optional development tool for preparing one or more trusted
projects after setup, using the same Pack dependency and environment managers:

```sh
python scripts/install-plugins.py path/to/first path/to/second --profile plugin-tutorial
```

Run that command with the prepared `backend/.venv` Python. Each selected project
prepares its own backend dependencies and frontend assets; failures do not stop
other selected projects. Add `--skip-build` to prepare dependencies only.
For a production source build, `--frontend-output frontend/dist/assets/packs`
publishes only the selected Pack assets without rebuilding Core.
Existing compatible wheels, environments and build receipts are reused; different
versions or isolated runtimes retain their required separate environments.

Setup also accepts repeatable `--plugin-path` / `-PluginPath` to prepare only
specified projects after Core. Combine it with `--with-plugins` / `-WithPlugins`
to include bundled Packs. `--runtime adk` or `--runtime litellm` selects optional
SDK preparation. Portable Python flags can be passed through the PowerShell
wrapper, for example `./scripts/setup.ps1 --runtime adk --skip-build`.

Ordinary `dev` launches check dependencies and rebuild changed source assets. They
do not install packages. If dependencies are missing or stale, rerun setup for
the same profile. To force an SDK or Pack rebuild without reinstalling, use:

```sh
npm --prefix frontend run build:pack-sdk -- --force
npm --prefix frontend run build:packs -- --force
```

`start.ps1` / `start.sh` serve the existing production build. Setup and dev accept
Node 22+, but the bundled `matterviz` dependency declares Node 24+; use Node 24
when developing those Packs.

Keep `.open-agent-world` for application state: development profiles, their Pack
environments, backups, and launcher logs. It is not a disposable test directory;
partially deleting a profile can remove its identity marker or prepared runtimes.
Use the F3 reset controls to reset development data with a backup.

Put disposable test worlds and diagnostic exports under `.tmp/` or the operating
system's temporary directory. Keep the same named profile when you want to retain
your development world. The mock runtime provides deterministic debugging without
model credentials; it is not a language model.

## Locate the public APIs

| Code you write | Import from |
| --- | --- |
| Python contributions | `open_agent_world.plugin_api` |
| React views | `@oaw/plugin-api` |

Application internals are useful for debugging the host, but plugin implementation should use these public imports. Integration tests can use host services to verify discovery and persistence.

Continue with [the complete Card App tutorial](card-app.md). For host development and broader checks, see [Run from source](../getting-started.md#development-and-verification).

## Setup results

Setup prints `Core ready` before optional work begins. Core-only setup then prints
`Optional components: not requested`. Default setup reports individual Pack
results and a final optional-component ready/failed count, including runtime SDKs.
Backend preparation and frontend build failures appear with their cause and a
targeted retry command. Core preparation remains complete when a Pack fails.

Exit code `0` means the requested setup completed; `3` means optional components
failed while Core remains usable; other nonzero exits indicate setup did not
reach confirmed Core readiness. `install-plugins.py --continue-on-error` also
returns `3` after attempting the remaining Packs when any preparation fails;
source batches continue independently even without that flag. Desktop packaging
retains strict failure handling.
After repairing a Pack, restart OAW to activate it.
