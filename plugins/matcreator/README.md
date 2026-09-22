# MatCreator on OAW

This plugin publishes a **MatCreator research** Legion preset, a research task
board, four portable Toolsets and an evolving Know-Do Graph.

## Research workspace preset

Restart the OAW backend and rebuild/reload the frontend after updating. Open the
bottom **Legions** deck and place **MatCreator research**. Open **Workspace mode**
in its header. The preset is available without running a demo script or adding
cards to the current world beforehand.

The layout provides Sessions and Files on the left, Conversation in the middle,
and Research tasks / File preview / Research knowledge / Participants
as tabs on the right. The lower-right tabs contain Sandbox controls/settings/terminal
and **Structure viewer**. The viewer follows structure files opened from either
the Conversation or Sandbox through their `core.file-preview` connections.
All scientific skills are stored directly inside Research knowledge, including their
scripts, reference files and source snapshots. No external Toolset cards are deployed.
The MatCreator Agent stays available in the workspace's bottom bar. Edit layout with the standard Legion controls;
saving to the library preserves its arrangement and remaps owners on deployment.

The plugin requires the bundled `science.structure-viewer` plugin (loaded first
through its declared plugin dependency). New deployments include this viewer;
existing canvas formations keep their user-edited layout and connections.

The preset connects one native OAW Agent to a Conversation, a stopped Sandbox,
the task board, and the prepopulated Know-Do Graph with **Use and learn**. It uses the user's default model. Configure that model and the Sandbox
runtime/environment before a real computation. No host path, credentials, package
installation or automatic Sandbox startup is embedded in the preset. Files use
the ordinary Sandbox workspace; the Agent is instructed to use a separate output
directory for each plan/session.

The research loop follows [AI4MS/MatCreator devel's planning/execution
orchestrator](https://github.com/AI4MS/MatCreator/blob/75c705c3c2bae7f2392c2d4bc9e90ca618a37f4a/src/matcreator/agents/orchestrator/agent.py)
(inspected commit `75c705c3c2bae7f2392c2d4bc9e90ca618a37f4a`): clarify the goal,
plan dependent steps, execute, verify results, revise blocked work and record
experience. OAW owns model calls, tools, Runs, cancellation, conversations and
files. This preset uses a scientific system instruction on the existing Agent;
it does not launch the upstream ADK server. Revision 4 includes equipped Summoning,
a Research Executors Barracks and an Executor blueprint sharing the research
Sandbox and Know-Do Graph, which owns all skills from the four scientific packages. OAW supplies asynchronous Run
dispatch and collection; the coordinator chooses tasks and verifies results.
Remote-job reconciliation remains outside this workflow.

### Research tasks

The board supports multiple named plans within each conversation session, task
dependencies, **To do / In progress / Awaiting review / Blocked / Done**, acceptance criteria, result notes and output
paths. OAW automatically binds plans, progress and execution attempts to the
active conversation session: a new session starts with an empty board, and
returning to an earlier session restores its plans. The card, its configuration
and connections stay the same. No session field or persistence setting is needed.
**Research plans** chooses among plans in the current session. Output paths are
recorded references; inspect files in the Files and File preview sections.
Sandbox files and the Know-Do Graph retain their shared behavior.

Existing board documents and execution attempts are lazily retained in the
workspace's default conversation session. Old plan `session_id` labels remain
readable for compatibility but are not namespace keys or displayed in the UI;
new Agent tool schemas do not request them. The host handles namespace creation,
deletion and recovery. Switching sessions never redirects pending results, and
live delegated work blocks deleting its card or originating session.

Both human edits and `task_board_*` Agent tools use the same revision-checked
document. Live updates are polled every three seconds. A concurrent edit retains
the local draft and offers an explicit reload. Unknown/cyclic dependencies and
starting a task before its prerequisites finish are rejected. **Done** requires
a result note; the Agent/user remains responsible for verifying that evidence.
Changing a status never starts or cancels a Run. **Read tasks** grants inspection
only; **Manage tasks** grants editing, and removing the connection revokes access
immediately. Saving a reusable Legion keeps task structure and descriptions, but
clears session references, results and output references and resets all tasks in
the copy. The new Sandbox does not contain the source project's computed files.
Ordinary reloads and backend restarts preserve the live board's progress.

### Delegating research

The coordinator discovers Executor IDs using Summoning `list`, then uses
`task_board_execute` with `action=collect` to read runnable work IDs and attempts.
`delegate` takes `item_id`, `library_id`, `agent_id`, `expected_revision` and a
unique `request_id`. Admission persists a stable attempt and supplies the Executor
with task inputs, verified dependency results, acceptance criteria and its own
`research/<board>/<task>/<attempt>/` output folder. Repeat the same request ID
only to recover an uncertain dispatch response; a deliberate retry gets a new ID.

Launch independent tasks, then use `wait` with `instance_ids`, `wait_mode=any|all`
and a bounded `timeout_seconds` (0–60). A timeout leaves work running. Continue
waiting or doing useful independent work until the results arrive. `collect`
reconciles terminal Runs and retains failures. Success enters **Awaiting review**;
the coordinator must inspect actual files, record evidence/output paths and mark
**Done**. Return inadequate results to **Blocked** before retrying. The task detail
shows each attempt, report, output directory and a targeted **Stop task** button.
Stopping the coordinator propagates through dependent child Runs. Collection
never restarts cancelled work. Restart recovery reconciles existing attempts,
including admissions interrupted before their handles reached the board.

Each summoned Executor has a private retained context. It does not join the main
Conversation or receive its whole history; supply necessary scientific details in
the task. Its shared connections retain normal live graph authorization. Bare
Executor snapshots do not wait for unrelated shared Sandbox commands to finish.

Restart the backend/frontend and place **MatCreator research** from the Legions
deck to use revision 3. Existing placed Legions retain their configured Agents and
layout; they are not silently replaced. The updated task tools also work on old
boards when their coordinator has Summoning connected to a suitable Barracks and
uses the new coordination instructions.

This release keeps the coordinator active through explicit bounded waits. It does
not yet inject messages into running providers or automatically reopen a finished
coordinator turn. Remote calculations must still be checked through their actual
job tools. Suggested acceptance request: “Build 2×2×2 and 3×3×3 copper supercells,
verify the atom counts independently, then compare the results.”

Host integration tests: `backend/tests/test_matcreator_workspace.py`;
delegation tests: `plugins/matcreator/tests/test_delegation.py`;
rendered workspace tests: `frontend/e2e/matcreator-workspace.spec.ts`.

Example request: “Plan a copper supercell study. Inspect the available scientific
environment, record the steps in the task board, generate the structure when
ready, verify its atom count and publish the output paths.” Actual scientific
execution requires the relevant Sandbox dependencies and an available model.

## Skill package provenance

MatCreator source: `theAfish/MatCreator`, `devel` commit
`a1a57688cdb7fc476498476cc388f932b6e83d6a`.

The package loader uses OAW SkillPackage directly. Materials Core contains
structure manipulation/conversion, plotting and supporting guides; Atomistic
Simulation contains ASE, DFT/MD, phonons and equations of state; Materials AI
contains ML potentials, training and the complete MatterGen family; Research and
Remote Compute contains database/search and remote-provider skills, including the
complete Tavily family. Nested SKILL.md files, scripts, references, binary assets,
frontmatter and source hashes are retained. OAW's internal Skill resource members
remain inside their Toolset; there is no separate published card type per skill.

## Start the demo

From the OAW repository:

```powershell
backend/.venv/Scripts/python.exe plugins/matcreator/create_demo.py
$env:OPEN_AGENT_WORLD_DATA_ROOT = (Resolve-Path .open-agent-world/matcreator-demo).Path
./scripts/dev.ps1
```

Restart the frontend and backend after adding the plugin. Local plugin discovery
loads it automatically; no MatCreator runtime, ADK tools, graph database or remote
provider is installed by importing the plugin. The demo creation command creates
new cards each time; use a fresh data root for an isolated repeat.

The wheel contains the backend and all four packages. The frontend extension is
included in the source distribution; keep its `frontend/` folder under the local
plugin directory and rebuild OAW, as with other OAW frontend plugins.

Configure an actual Agent model in OAW before asking it to work. The creation
script uses the mock provider only to create the world without calling an LLM;
the acceptance test invokes the real Agent capability provider directly and is
not a claim of autonomous LLM task completion.

Choose Ubuntu WSL for the first scientific demo. Bind a dedicated existing
Windows folder as the Sandbox's read/write workspace. Provision Python/ASE
explicitly. The validated test copies already installed Ubuntu packages into
`python-libs` inside that folder using `provision_science.py`; it does not install
dependencies or grant access to the host home. For example, when Ubuntu already
has ASE, NumPy and SciPy:

```powershell
wsl -d Ubuntu -- python3 /mnt/d/AI/open-agent-world/plugins/matcreator/provision_science.py --destination /mnt/d/your-demo-workspace/python-libs
```

Select the connected Local ASE Environment when executing. It sets
`OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`. This profile is an explicit
invocation choice. Start the Sandbox after configuring its runtime and folder.

1. Connect Materials Agent to Materials Core using **Use skills**, to KDG using
   **Use and learn**, and to Sandbox using **Execute**. The creation script sets
   these links and the Environment link.
2. Ask: “Use Local structure demo to build a 2×2×2 copper supercell. Run its
   `scripts/build_structure.py` through the Sandbox with interpreter `python3`,
   the Local ASE Environment and argv `['--repeat','2','--output','copper2',
   '--library-path','python-libs']`. Verify the report and publish all three
   output files as an Artifact.” Expected atom count: **32**.
3. Open the KDG's **Open workspace** Window. This switches the container itself
   to an inline circular relationship graph that moves and scales with the world
   canvas. **Show member cards** restores its spatial Skill members. Drag a Toolset
   directly from the Tools deck into the KDG body to preview and confirm importing
   its package; no temporary world Toolbox is created. Alternatively use **Assimilate a Toolset**, choose
   Materials Core, preview and confirm. Alternatively drag the Toolset header
   into the KDG body, away from its connection boundary, and confirm. Ordinary
   connections never assimilate anything.
4. Search `copper` or `structure`; click an entry to inspect it and double-click
   to expand its neighborhood. The consumed Toolset and its old relationship
   are removed after commit. No manual source deletion is necessary.
5. Ask for the related **3×3×3** task. The Agent uses `knowledge_search` and
   `knowledge_inspect`, then passes the returned `skill_node_id` to OAW's normal
   `run_skill_script`. Expected atom count: **108**. Publish CIF, extxyz and JSON.
6. **Use and learn** permits recording experience. **Curate knowledge** is a
   separate explicit relationship granting durable edits and distillation. The
   Window's Review mode also supports explicit memory promotion with evidence.

The native acceptance test verified both structure roundtrips, real Artifact
publication, and denial after revoking KDG access. The Windows AppContainer
baseline test also passed, but this ASE distribution's ctypes/SciPy dependency
did not load under its existing isolation mitigations. The scientific demo is
therefore validated on WSL; Windows isolation settings were not weakened.

## Knowledge and resource lifecycle

Publisher knowledge is immutable. Edit mode can save a user-owned copy, create
new entries and deliberately create/remove typed relationships. Memories remain
pending until reviewed into a Heuristic or Procedure. Promotion records evidence
and `derived_from` relationships; it never marks knowledge scientifically tested
without an explicit editor decision. Agent inspections increment usage metadata.

Assimilation is a pure plugin conversion plus a host transaction. The target
document, recreated Skill resource members and source consumption commit together.
The host refuses non-document sources or sources with external lifecycle effects,
checks both document revisions, and buffers mutation events until commit. Scripts
remain Skill files, not graph-node text. Meaningful procedure references become
Procedure nodes; other support files remain addressable resources.

Palette drops use the same transformation endpoint with `source_type` instead of
`source_id` and `source_revision`. The host accepts only user-creatable inert
document types with declared initial content. Preview is read-only; confirmation
checks the destination revision and writes the graph and its members atomically.
Palette provenance records the type and plugin, with no source node ID.

Each imported package has an immutable SHA-256-keyed source snapshot, including
complete original adapted package bytes and source provenance. Download snapshots
from the Window. A snapshot's `package` value is an ordinary `SkillPackage` and can
be exported using `open_agent_world.skill_packages.export_plugin`. This is recovery
of the consumed source package; publishing selected evolved graph knowledge is
not implemented in this milestone.

Search/expansion responses are bounded (default 60, maximum 200 plus explicitly
requested roots), with type, relationship, source, trust and memory filters.
Full bodies and resources load only on inspection. Backend queries currently scan
the host-owned document; they do not require a second persistence database.
The Window renders the progressive working subgraph using OAW's existing React
Flow, preserves node positions while expanding and culls offscreen nodes. A
1,200-entry contract fixture verifies bounded paging; it is not a performance
benchmark of all possible graph shapes.

## Verification commands

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_matcreator.py
$env:OAW_MATCREATOR_PYTHON = 'python3'
$env:OAW_TEST_SANDBOX_RUNTIME = 'wsl:Ubuntu'
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_matcreator.py::test_native_ase_before_after_assimilation_and_artifact
```

The native test needs a real desktop account with WSL access and provisioned
Ubuntu packages. It creates a bound temporary workspace, copies those packages,
uses normal capability calls, publishes actual artifacts and destroys its Sandbox
after successful validation. It never calls an LLM or sends data to a remote
compute/search provider. UI acceptance: `frontend/e2e/matcreator.spec.ts` against
isolated OAW backend/frontend servers on ports 8017/5177.

Regenerate bundled source packages explicitly with:

```powershell
backend/.venv/Scripts/python.exe plugins/matcreator/build_packages.py path/to/MatCreator
```

The maintainer importer requires PyYAML. Check out the pinned revision first;
normal plugin startup only reads the generated JSON packages and requires no YAML
loader, network, scientific runtime or MatCreator installation.
