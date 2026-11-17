# Work with OAW cards in your terminal

Install the command once, then use `oaw` from any directory. The CLI and OAW's interface share cards, connections and conversation history.

## Install once

From a Windows source checkout:

```powershell
.\scripts\install-cli.ps1
oaw --help
```

This installs the command for your user and updates PATH. New Windows desktop installers do this automatically.

On Linux or macOS:

```sh
bash scripts/install-cli.sh
oaw --help
```

The command goes into `~/.local/bin`. If it is missing from PATH, follow the single command printed by the installer.

Open your normal OAW application. You can also run `oaw start`, then use another terminal to work with its cards.

## Find and create cards

```sh
oaw cards
oaw show agent
oaw create agent Helper
oaw create text Notes --text "Review this project"
oaw show Notes
```

The list puts each card's **name** first, followed by type, status and a short ID. Use a name or short ID in later commands. A type such as `agent` also works when exactly one card matches it.

Omitting a name creates names such as `agent` and `agent-2`. If names repeat, use a short ID.

`oaw types` shows the basic types and card groups. Expand a group with `oaw types GROUP`, or show everything with `oaw types --all`.

## Get an Agent's reply

Run `oaw models` first. If there is no default model, add a connection and choose a default in **OAW Settings → Models**.

```sh
oaw chat Helper "Hello. What can you help me with?"
```

For an ongoing chat, run `oaw chat Helper` and type messages. Enter `/exit` to leave.

## Save a shared conversation

A Conversation card stores chat history that both the interface and CLI can read:

```sh
oaw create conversation Discussion
oaw connect Helper Discussion
oaw chat Discussion "Please suggest a plan"
oaw chat Discussion --history
```

With multiple participants, choose an Agent with `--agent Helper`. Use `--new` for a new session, or `--session` to select an existing one.

## Connect tools and check tasks

```sh
oaw connect Helper Notes read
oaw create sandbox Workbench
oaw connect Helper Workbench execute_manage
oaw connections
```

If several relationships are possible, an interactive terminal offers a choice. `execute_manage` grants command execution and Sandbox start/stop.

For longer work, submit a task and inspect it later:

```sh
oaw run Helper "Review the project and suggest improvements"
oaw runs
oaw result RUN_ID
```

Failed runs show their reason. Use `oaw wait RUN_ID` to wait, or `oaw cancel RUN_ID` to cancel.

## For external agents

```sh
oaw cards --json
oaw chat Discussion --message-file task.txt --json
oaw create text --input card.json --json
```

`--json` produces plain JSON, including errors, with nonzero exit codes on failure. It never opens interactive choices. Input files use UTF-8; `-` reads stdin.

After a timeout or lost connection, query the returned Run or session before submitting again. Use `oaw COMMAND --help` for detailed options.
