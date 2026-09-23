# Development setup

**Goal:** run the source checkout with a separate profile for your plugin experiments.

## Prerequisites

Install Git, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 20 or newer. Python is enough for the plugin code; Node builds and serves OAW's frontend.

Windows and Linux/WSL support local Sandbox execution with the required isolation runtime. macOS can run OAW and the first plugin tutorial, but currently has no local Sandbox runtime.

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
./scripts/setup.ps1
./scripts/dev.ps1 -AgentRuntime mock -Profile plugin-tutorial
```

Linux, WSL, or macOS:

```sh
bash scripts/setup.sh
python3 scripts/dev.py --agent-runtime core.mock --profile plugin-tutorial
```

Open the URL printed by the launcher. Ports can change when the usual ones are occupied. Keep the launcher running and stop it with Ctrl+C.

The mock runtime is for deterministic debugging; it is not a language model. The named development profile keeps these experiments separate from your normal app data. Reuse the same profile to keep your test world.

## Locate the public APIs

| Code you write | Import from |
| --- | --- |
| Python contributions | `open_agent_world.plugin_api` |
| React views | `@oaw/plugin-api` |

Application internals are useful for debugging the host, but plugin implementation should use these public imports. Integration tests can use host services to verify discovery and persistence.

Continue with [Your first plugin](first-plugin.md). For host development and broader checks, see [Run from source](../getting-started.md#development-and-verification).
