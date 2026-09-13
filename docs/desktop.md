# Desktop installation and development

Open Agent World has three entry points sharing the same application code:

| Mode | Entry | Frontend | Data | F3 |
| --- | --- | --- | --- | --- |
| Installed Windows app | Start menu / desktop shortcut | Built assets | Formal user directory | Absent |
| Formal use from source | `scripts/start.ps1` / `python3 scripts/start.py` | Built assets, served by Python | Formal user directory | Absent |
| Development | `scripts/dev.ps1` / `python3 scripts/dev.py` | Vite with hot updates | Checkout-owned development profile | Debug panel |
| Release preview | `scripts/start.ps1 -Preview` / `python3 scripts/start.py --mode preview` | Built assets | Checkout-owned preview profile | Absent |

## Daily use

On Windows, install the generated `Open Agent World_*_x64-setup.exe`, then open **Open Agent World** from the Start menu. The per-user NSIS installer does not require a developer environment. It includes a complete Python interpreter, locked backend dependencies, bundled plugins, frontend assets, and uv for managed Sandbox Python provisioning. Its WebView2 bootstrapper downloads the runtime if needed.

The desktop shell shows startup progress, waits for backend readiness, and opens the world in a separate window. Opening the app again focuses its existing window. Closing the final window asks the backend to shut down; a Windows Job Object also owns the backend process tree for crash/forced-exit cleanup. Backend logs rotate at 5 MB. Formal launch logs use `%LOCALAPPDATA%\OpenAgentWorld.logs\launcher.log`, outside the relocatable data store; development and explicitly configured stores use `logs/launcher.log` inside their profile. The startup window reports the log path.

Application installation and Sandbox provisioning remain separate. Scientific plugin dependencies and platform-specific execution prerequisites may still require a first-use download or OS configuration. macOS still has no local Sandbox runtime. The current installer pipeline targets **Windows x64**; the source launcher also supports Linux/macOS.

Formal data keeps its existing default location (`%LOCALAPPDATA%\OpenAgentWorld` on Windows), including any relocation already selected in Settings. An application upgrade does not replace this directory. Uninstalling the application leaves user data available for a later reinstall. Source launches continue to support `OPEN_AGENT_WORLD_DATA_ROOT`; the installed desktop uses the formal user location and its storage settings.

From a source checkout:

```powershell
./scripts/setup.ps1
./scripts/start.ps1
```

Setup now also builds the frontend. After later frontend edits, run `npm --prefix frontend run build` again. Starting an already built source checkout needs Python and its installed backend dependencies; it does not run Vite or require Node at runtime.

## Development and F3

```powershell
./scripts/dev.ps1
./scripts/dev.ps1 -AgentRuntime mock -Profile tutorial
```

Portable equivalents:

```bash
python3 scripts/dev.py
python3 scripts/dev.py --agent-runtime core.mock --profile tutorial
```

Development data is always below `.open-agent-world/development/profiles/<profile>`. The launcher does not adopt the previous formal store or a directory passed in `OPEN_AGENT_WORLD_DATA_ROOT`. Existing formal data is left in place; use `start` to reopen it. New development profiles begin empty and require their own model configuration.

Press **F3** or click **DEV · F3** to toggle the panel. It includes reset presets, scope selection, a review of the actual profile path and world card count, and a separate **Confirm and restart** action. Selecting another scope invalidates that confirmation. The panel supports English and Chinese. Virtual stress cards can be generated and cleared without adding persistent world cards.

| Reset scope | Effect | Retained |
| --- | --- | --- |
| Workspace | All world cards, relationships, conversations, runs and scoped state; managed world files are archived; tutorial and canvas references reset | Collection, decks, saved Legion templates, models, shared Python installation |
| Decks | One empty default deck | Collection and pack progress |
| Packs and collection | Packs become unopened, collected cards clear, node entries are removed from decks | World cards, saved Legion entries, plugin installation/enabled state |
| Tutorial | Tutorial progress and session references reset | Placed cards; use the first-launch preset to also empty the world |
| Interface | Viewport, surfaces, sticking layout, language, theme, library preferences | Tutorial progress unless separately selected, model preferences |
| Models and credentials | Model connections and their saved API keys clear | World and other settings; existing Agent model references may need reconfiguration |
| Sandbox runtime | Shared Python and plugin environment preparation state archived and rebuilt | External workspaces and world cards |
| Complete reset | All scopes, saved Legions and remaining application settings reset | Profile identity, logs, recovery backups, external workspaces |

**Test first launch** combines workspace, decks, packs and interface resets while retaining model connections. Complete reset also removes those connections. The reset path explicitly disables legacy collection migration, so an existing database cannot accidentally unlock packs again.

UI preferences are now stored with the backend profile. The app hydrates them before creating its stores, so changing a port or switching from a browser to the desktop does not change the tutorial/viewport profile. The first formal browser launch imports available legacy browser preferences once; development never imports them. Browser preferences from another origin that has not opened the updated app are not automatically migrated.

## Reset lifecycle and recovery

Reset endpoints are registered only by the development launcher. The production frontend excludes the debug module, and the packaged backend omits `development.py`. A profile marker, checkout path validation, generation checks, and the storage lock restrict resetting to the current development profile.

The backend rejects new writes after accepting a reset, drains HTTP/WebSocket connections and application tasks, closes SQLite, then applies the reset and starts a new server. Active plugin dependency installation may delay shutdown until its current operation finishes. Failed shutdown prevents the reset. Connected windows reload when they observe the new profile generation; old preference writes are rejected.

Backups are placed in:

```text
.open-agent-world/development/backups/<profile>/<timestamp-id>/
  world.sqlite3
  reset.json
  assets/             # if reset
  projects/           # if reset
  sandboxes/          # if reset
  sandbox-bindings/   # if reset
  sandbox-runtimes/   # if reset
  runtime/            # if reset
  secrets/            # complete reset
```

Only managed directories themselves are archived. A user-selected external Sandbox workspace is never a reset target. An interrupted filesystem move is recovered from the reset journal on the next development launch.

For manual recovery, stop the development launcher first. Keep the current profile as a separate backup, restore `world.sqlite3` to `database/world.sqlite3` with no stale `-wal`/`-shm` files, and restore the archived directories to the same original profile path. For partial resets, keep the original `secrets/settings.key`; complete-reset backups include that directory. Backups may contain saved credentials and are not included in desktop packages. They remain until you remove them.

## Build a Windows installer

Build on Windows x64 with a full 64-bit CPython installation, Node, uv, Rust with the MSVC toolchain, and Visual Studio C++ build tools / Windows SDK available.

```powershell
./scripts/setup.ps1
./scripts/build-desktop.ps1
```

The build script builds the frontend, creates and self-tests `.open-agent-world/desktop-payload`, installs the locked desktop CLI, and invokes Tauri. Output:

```text
desktop/src-tauri/target/release/bundle/nsis/
```

The payload starts with the interpreter and standard library from the build machine, excluding its global site-packages. Backend packages are installed from `backend/uv.lock` with wheel hashes enforced. The payload self-test imports Google ADK/LiteLLM, discovers the bundled plugins, and creates a separate Sandbox Python environment. The backend remains ordinary Python so plugin discovery, file-based WSL helpers, and `sys.base_prefix` continue to work after installation.

The build retains previous payloads under `.open-agent-world/desktop-previous-*` until manually removed. `-SkipFrontend` and `-SkipPayload` are available when rebuilding only the desktop shell; use them only when those inputs have not changed. The initial package is unsigned; no certificate, publishing destination, or automatic update service is configured.

## Verification

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_application_launch.py
npm --prefix frontend test
npm --prefix frontend run build
node frontend/scripts/test-application-launch.mjs
```

The browser acceptance script launches isolated profiles and checks F3, confirmed reset/restart, empty first-launch collection/decks, persistence after a port change, and the absence of debug controls/API in the built app. It uses Chrome by default; set `PLAYWRIGHT_CHANNEL=msedge` or `PLAYWRIGHT_EXECUTABLE_PATH` for another installed Chromium browser. Screenshots and process logs are saved below `.tmp/application-acceptance`.

Tauri configuration and entry point: `desktop/src-tauri`. Backend server supervisor: `backend/launcher.py`. Reset implementation: `backend/development.py`. Profile preferences: `backend/application.py` and `frontend/src/state/profileStorage.ts`.
