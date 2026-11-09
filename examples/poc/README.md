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
python scripts/setup.py --profile poc --plugin-path plugins/knowledge_base --runtime adk --skip-build
python scripts/poc.py init --template examples/poc/legion.json
python scripts/poc.py start
```

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

1. Edit cards, connections, instructions and the Workspace layout in OAW. Put all
   connected dependencies inside the Legion, including nested groups/equipment.
2. Finish active tasks and stop the development launcher with Ctrl+C.
3. Save the current Legion as a readable definition:

```sh
python scripts/poc.py list
python scripts/poc.py save examples/poc/legion.json --legion "Lab Manager"
```

Names work when unique; `list` also prints IDs if two instances have the same name.
Saving retains the definition ID, increments its revision only when it changes,
and writes atomically. Repeated saves without edits produce no Git diff. This uses
the existing `LegionBlueprintPreset` contract, capture/remapping and content-export
checks; there is no separate POC Pack format or installation step per edit.

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

## Add DSH, XRD or publish later

Use the existing Pack installation process for each developer's profile, then
edit the Legion in OAW and save it again. Document the required Pack versions and
their installation sources with the scenario. External source Packs also need
`OPEN_AGENT_WORLD_PLUGIN_DIRS` set consistently for setup/edit/start.

DSH, XRD and Alibaba bindings are deliberately not prerequisites of this initial
knowledge scenario. Local development tests the graph, tools and Workspace;
Alibaba Sandbox/AgenticFS behavior still needs cloud integration acceptance.
When a scenario is ready for users, use OAW's existing **Publish application** or
content Pack export and the reviewed deployment process. These commands neither
deploy to ECS nor enable automatic releases.
