# Team POC development

This is a source-development workflow for a small team. The repository holds the
Legion definition; each developer keeps an editable world, settings and data in
their own development profile. Normal startup reuses that world.

The starting [legion.json](legion.json) is the Knowledge Base **Lab Manager**
formation: an Agent, Conversation, Knowledge Base and Sandbox, with a Workspace
layout. It is an editable starting point, not a copy of the deployed Alibaba
environment. It uses the ordinary local model and execution settings.

## First run

Run these commands from the repository root, on Windows PowerShell, Linux or
macOS. Install the prerequisites in [source setup](../../docs/getting-started.md)
first. Keep `--profile poc` consistent when preparing dependencies.

```sh
git clone --branch feat/aliyun-agentcore-poc https://github.com/theAfish/open-agent-world.git
cd open-agent-world
git switch -c feat/my-poc
python scripts/poc.py setup
python scripts/poc.py start
```

`setup` runs the existing source setup recipe for Knowledge Base + ADK, followed
by idempotent initialization from `examples/poc/legion.json`. Run it once per new
checkout/profile and again when dependencies change. It needs no ECS account or
Alibaba credentials. `python scripts/poc.py setup --profile NAME` prepares a
separate profile; the existing setup commands remain supported.

Open the frontend URL printed by the development launcher. Configure your model
once in **Settings**, then edit the canvas or open the Legion's Workspace. Those
settings are shared by the Legions in this profile. For a small manual knowledge
test, create a project and import [sample.md](sample.md); the data is synthetic.
Model-based processing requires your own model connection.

All POC state lives under `.open-agent-world/development/profiles/poc/` in this
checkout, already ignored by Git. Your personal/default profile is separate.
Use `--profile another-name` on every command to keep a second independent POC.

The initializer also accepts installed presets, for example:

```sh
python scripts/poc.py init --preset knowledge.base.research
```

`init` leaves an existing world with cards unchanged. `start` delegates to the
existing development launcher with hot reload; it does not install dependencies,
apply templates, reset data, or build a production release. Rerun setup only when
dependencies change or when preparing a new profile. Stop the launcher with Ctrl+C.

## Edit and share a Legion

The short local loop is **start → edit → Ctrl+C → save → check → preview**.
Code edits use the ordinary source developer workflow; frontend changes hot
reload. Backend changes require restarting the local launcher.

1. Edit cards, connections, instructions and the Workspace layout in OAW. Put all
   connected dependencies inside the Legion, including nested groups/equipment.
2. Finish active tasks and stop the development launcher with Ctrl+C.
3. Save the current Legion as a readable definition:

```sh
python scripts/poc.py list
python scripts/poc.py save examples/poc/legion.json --legion "Lab Manager"
python scripts/poc.py check
```

Names work when unique; `list` also prints IDs if two instances have the same name.
Saving retains the definition ID, increments its revision only when it changes,
and writes atomically. Repeated saves without edits produce no Git diff. This uses
the existing `LegionBlueprintPreset` contract, capture/remapping and content-export
checks; there is no separate POC Pack format or installation step per edit.

Imported cards keep their portable keys when teammates save them again. New cards
receive new keys. This preserves hosted template lineage across different local
profiles; do not rename keys or change the scenario ID by hand to force an update.
`check` instantiates the saved JSON in temporary clean data and validates the real
Workspace publication contract using this profile's installed Packs. It does not
run a model or start a Sandbox. CI checks the committed Knowledge Base starter too.

Save another Legion to another JSON file to maintain several related scenarios.
Use `--id team.other-scenario` when first saving a new file. Files can live in a
private team repository too; `--template` and `save` accept filesystem paths.

Initial document/resource content is omitted by default. Explicitly include a
card's portable content by its unique name or template key:

```sh
python scripts/poc.py save examples/poc/legion.json --legion "Lab Manager" --include-state "Task brief"
```

This only includes content the card already supports capturing. **Knowledge Base
currently creates a fresh, empty database when instantiated. Its project files,
Space definitions, records and results are not portable template content.** Keep
small synthetic inputs alongside the definition and import them locally; this
command does not copy a knowledge database or add a new card-specific export API.

Application settings, credentials, conversations, run history, Sandbox files and
cloud bindings are not part of the definition. Model selections and execution
bindings follow the existing portable-export rules. Review the JSON and any
warnings before committing: structured credential checks cannot detect every
secret pasted into prose or documents.

## Get a teammate's update

Commit the JSON and any intentionally shared sample files on a feature branch;
open a normal GitHub PR. Review the card/config/layout diff, merge, and pull it.
After stopping your local development launcher:

```sh
python scripts/poc.py apply --template examples/poc/legion.json
python scripts/poc.py start
```

`apply` creates a new instance beside your current cards. Existing instances,
documents, sessions and settings stay intact. Compare the two in OAW and remove
the old instance yourself when ready. Imported definitions go through the normal
Legion compatibility and lifecycle checks; missing/incompatible Packs are errors,
not silently substituted cards. `apply --preset PRESET_ID` can also add another
installed formation to the same profile.

The offline commands acquire the launcher's storage lock. They refuse a running
profile, non-development directories, deployed workspaces and unfinished runs.
They do not resume queued jobs, run models, install dependencies, or open a cloud
execution environment.

## Try the published user interface locally

Stop the editor, then run:

```sh
python scripts/poc.py preview --legion "Lab Manager"
```

Open the printed frontend URL and enter the generated local access password.
This uses the real published Workspace UI and restricted runtime APIs, served by
Vite: no production frontend build or ECS release is needed. Model settings and
your test data are copied from your local profile. Authoring controls are absent;
the source canvas, settings and data remain independent of preview writes.
Use `--agent-runtime core.mock` to exercise the shell without a paid model.

Each invocation creates a new snapshot below
`.open-agent-world/development/previews/poc/<release-id>/`. This is private local
data (potentially including your own model credentials), **never a Git artifact**.
Snapshots are not auto-deleted. `--prepare-only` prints the snapshot without
starting servers. To resume one without copying again, use:

```sh
python scripts/dev.py --profile poc --deployment <printed-snapshot-path>
```

The listener remains on `127.0.0.1`. The single local access password tests the
published application boundary, not hosted multi-account login or Alibaba VM
isolation. External filesystem workspaces are rejected by the snapshot command;
use checkout-local test files. A local OS Sandbox still needs the normal platform
prerequisites. Alibaba Sandbox/AgenticFS acceptance remains a separate cloud test.

## PR review and GitHub deployment

```sh
git add examples/poc/legion.json
git commit -m "Update the team research workspace"
git push -u origin feat/my-poc
gh pr create --base feat/aliyun-agentcore-poc --fill
```

Use the existing [Ali PR workflow](../../.github/ALI_PR_GUIDE.md). After review and
merge, CI builds the merged commit. **Once the separately approved controller and
GitHub environment are activated**, a maintainer selects **Review deployments →
Approve and deploy** in that CI run. The trusted ECS controller independently
verifies the approved run and deploys its fixed artifact. No contributor enters a
SHA or runs a server command. The `Ali integration deployment` status, rather than
the approval job's success, confirms actual installation and recovery.

Committed `examples/poc/*.json` definitions appear as **Git: <name>** in the hosted
administrator's existing application deployment selector after code deployment.
They use the same nonroot initialization, Pack approval and compatible update
mechanisms as applications authored online. Choose the target accounts there;
merging a template never silently resets existing user workspaces. Existing
workspaces from a different authoring source, structural changes or Pack-version
changes require an explicit migration/new workspace, as before.

The automatic ECS PR Preview remains Core/mock; use local POC preview to review
the real Knowledge Base scenario. Production activation and exact remaining
operator steps are in [the delivery guide](../../deploy/aliyun/POST_MERGE.md).

## Add DSH, XRD or another Pack

Use the existing Pack installation process for each developer's profile, then
edit the Legion in OAW and save it again. Document the required Pack versions and
their installation sources with the scenario. External source Packs also need
`OPEN_AGENT_WORLD_PLUGIN_DIRS` set consistently for setup/edit/start.

DSH, XRD and Alibaba bindings are deliberately not prerequisites of this initial
knowledge scenario. Local development tests the graph, tools and Workspace;
Alibaba Sandbox/AgenticFS behavior still needs cloud integration acceptance.
Git-managed scenarios use the hosted selector described above. Other ad hoc
applications can still use **Publish application** or content Pack export. Local
POC commands never deploy to ECS or enable automatic releases.
