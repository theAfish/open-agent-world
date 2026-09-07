# Codex runtime for Open Agent World

An installable OAW plugin with a distinct **Codex Agent** card
(`openai.codex.agent`) and the `openai.codex` runtime. The card appears in the
Agents library and has its own settings schema. It prefers the desktop App's
native Codex installation, creates a new OAW session using local Codex login and
configuration, and exposes live OAW graph capabilities to that session.

Its settings view lives in `frontend/index.tsx` and uses the public
`@oaw/plugin-api` SDK. Its OpenAI icon is packaged in
`src/oaw_codex/assets/openai.svg` (from the official
[OpenAI brand assets](https://cdn.openai.com/brand/OpenAI-Logos-2025.zip)). The host
loads both through generic extension/resource contracts. Restart Vite after
adding the plugin, or rebuild the frontend for production.

This uses the local Codex harness, including its configured skills and MCP tools;
it does not call a bare model API. It does not attach to or control the desktop
window. Tools supplied only by the desktop UI are not automatically inherited.

## Try it on Windows

Requirements: the repository's backend/frontend dependencies, `uv`, Node.js,
and a working native Codex CLI (`codex.exe`). Run `codex login` if needed.
The plugin uses Codex's own login and configuration; do not paste tokens into
an OAW card. Tested against Codex CLI **0.153.0** and desktop runtime **0.153.4**.

From the repository root:

```powershell
./plugins/codex/try.ps1
```

Open the URL printed by the launcher (normally `http://127.0.0.1:5188`). It creates
a **Codex** Agent, **Talk to Codex** Conversation, and **Codex notes** text card.
Open the Conversation, mention `@Codex`, and send a task. You can also open the
Agent workspace, select **Settings**, and run a prompt directly. Create more
Codex Agent cards from the Agents library; set their Project folder before running.

Try: “Read the connected note, inspect the project README, and describe how this
project works.” Then ask for a small file change or a note edit.

The default coding workspace is this OAW checkout. Select another directory or
a model explicitly:

```powershell
./plugins/codex/try.ps1 -WorkspacePath D:/Projects/example -Model default
```

`default` means the configured Codex model, not the OAW ADK model. The demo keeps
existing cards on subsequent launches, including their original workspace and
model; it prints the actual workspace. Change an existing card's configuration
in its **Settings** tab if you want to retarget it.

The trial uses separate data under `.open-agent-world/codex-card-demo`, finds free
ports, and leaves your regular running OAW instance alone. Ctrl+C stops the trial
servers; cards and Codex session mappings survive restart. No model task runs
until you send one. Normal Codex account usage applies.

You can also stop it from another terminal with `./plugins/codex/try.ps1 -Stop`.

Automatic discovery prefers the running Windows desktop App's native runtime,
then installed desktop runtimes, then PATH. The Settings panel displays the actual
source, executable and version. Discovery means the runtime is available, not that
an existing desktop chat is attached. Select `desktop` to require a desktop
installation, `cli` for PATH, or `manual` and enter a native executable path.

To configure the CLI source through the environment:

```powershell
$env:OAW_CODEX_COMMAND = 'C:/path/to/codex.exe'
./plugins/codex/try.ps1
```

The VS Code Codex extension includes a native binary under its installation's
`bin/windows-x86_64/codex.exe`. Shell wrappers (`.cmd`, `.bat`, `.ps1`) are rejected;
the plugin launches the executable directly without a shell.

## Install into your regular world

The regular backend automatically loads this package from `plugins/codex`.
Restart OAW normally after adding the plugin:

```powershell
./scripts/dev.ps1
```

Create a **Codex Agent** from the **Agents** deck and set its Project folder.
To optionally create the three demo cards in this world, use the **backend API URL
printed by that script** in another terminal:

```powershell
uv run --project backend --with-editable ./plugins/codex python -m oaw_codex setup `
  --api http://127.0.0.1:8000 --workspace D:/AI/open-agent-world
```

Setup is idempotent for the three trial card IDs. Other Agents continue to use
their existing runtime. For your own Agent cards, use this configuration:

```json
{
  "runtime_provider_id": "openai.codex",
  "model": "default",
  "workspace_path": "D:/Projects/example",
  "codex_sandbox": "workspace-write",
  "max_concurrent_runs": 1,
  "inherit_legion_model": false
}
```

The **Codex Agent** settings panel exposes client source, executable, project
folder, model, reasoning effort, session mode, native file access and additional
instructions. Model and effort default to local Codex configuration; explicit
values must be supported by that runtime/model. Connect resources and
Conversations using the normal OAW edges.

## Behavior and boundaries

- Each Run owns a private stdio App Server process; cancellation stops its process
  tree. Codex `thread/resume` restores context on later turns and after restart.
  Each OAW conversation session gets a separate thread. Direct Agent prompts use
  that Agent's own thread. Select `fresh` to start a new thread on every Run.
  Changing workspace or sandbox starts a new thread. Templates preserve portable
  settings but omit local project/executable paths; set a folder after restoring.
- Session mappings live in plugin-owned `sessions.sqlite3`, selected by
  `OAW_CODEX_STATE_DIR`, otherwise `$OPEN_AGENT_WORLD_DATA_ROOT/codex`, otherwise
  `./.open-agent-world/codex`. Keep this directory separate for separate worlds.
  Codex retains its own conversation files in its configured home. Deleting the
  OAW Agent removes its mapping, not those external Codex history files.
- `oaw_list_tools` discovers live capabilities; `oaw_invoke_tool` goes through
  the OAW broker on every invocation. Removing a connection revokes access even
  if the model remembers the old capability ID. Tool results are returned as JSON
  text. This preview does not translate image results into visual input.
- Native project commands/files use **Codex's sandbox**, not an OAW Sandbox card.
  `codex_sandbox` accepts `workspace-write` or `read-only`; approval policy is
  `never`, so operations requiring escalation cannot proceed. This setting governs
  native Codex access; OAW tool permissions still come from graph relationships.
- Interactive approval/question requests fail the Run with an actionable message.
  Ask questions through normal conversation replies. No approval is auto-accepted.
- This is trusted local code, not isolation between hostile Agents. Browser
  interaction and the tools/memory of an existing Codex desktop conversation are
  not automatically inherited. Browser tooling can be connected separately.
- Uses experimental dynamic-tool protocol fields. Unsupported CLI schemas fail
  explicitly; no fallback to another runtime or unrestricted execution.

Before uninstalling, remove or reconfigure Agents and templates that reference
`openai.codex`, then restart OAW without the package.

## Verification

```powershell
uv run --project backend --with-editable ./plugins/codex python -m pytest `
  plugins/codex/tests -p no:cacheprovider --basetemp=.outputs/codex-tests
```

Subprocess fixtures cover stream translation, persisted resume, context separation,
capability revocation, failures, unexpected interactive requests and cancellation.
The HTTP test exercises actual OAW Run state transitions and deletion cleanup.

Opt in to a real account-backed test that creates one file and edits a disposable
OAW text card:

```powershell
$env:OAW_CODEX_LIVE = '1'
uv run --project backend --with-editable ./plugins/codex python -m pytest `
  plugins/codex/tests/test_host.py -k live --basetemp=.outputs/codex-live
Remove-Item Env:OAW_CODEX_LIVE
```

Protocol reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
Use `codex app-server generate-json-schema --experimental --out <directory>` to
inspect the exact installed version; enum spellings may differ from web examples.
