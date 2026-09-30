# MatCreator on OAW

This plugin publishes a **MatCreator research** Legion preset, a research task
board, four portable Toolsets and an evolving Know-Do Graph.

## Research workspace preset

Restart the OAW backend and rebuild/reload the frontend after updating. Open the
MatCreator Pack in the Library and add **MatCreator research** to a deck, then
place it from that deck. Confirm opening any remaining owned dependency Packs.
Open **Workspace mode**
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
it does not launch the upstream ADK server. Revision 7 includes equipped Summoning,
a Research Executors Barracks and an Executor blueprint sharing the research
Sandbox and Know-Do Graph, which owns all skills from the four scientific packages. OAW supplies asynchronous Run
dispatch and collection; the coordinator chooses tasks and verifies results.
Remote-job status checks run through the coordinator's authorized tools when a
scheduled continuation wakes it; OAW does not infer external job completion.

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

The coordinator first inspects and starts the shared Sandbox. `task_board_execute`
with `action=collect` returns `document.revision`, runnable item IDs and authorized
Executor targets including `library_id` and `agent_id`. `delegate` requires
`item_id`, `expected_revision` and a unique `request_id`. Target IDs or unambiguous
names are optional when exactly one authorized Executor is available.

Tool responses default to summaries. Pass `since` with the last returned `cursor`
to omit unchanged state, use `inspect` with an `instance_id` for one complete
report, and request `detail=full` only when the whole board is needed. The human
board API retains its full document response.

Delegation persists its attempt before admission and supplies verified upstream
inputs, acceptance criteria and a separate `research/<board>/<task>/<attempt>/`
output directory. These directories share one Sandbox; they are not filesystem
isolation. Reuse a request ID only to recover the same invocation. A deliberate
retry needs a new request ID.

Executors call `report_delegated_task` before ending, with `outcome` set to
`complete`, `partial`, `blocked` or `waiting`, a summary, evidence, output paths
and the next action. Complete is an executor claim, not acceptance. The coordinator
verifies files and scientific assumptions before marking **Done**. Partial or
blocked reports return the task to **Blocked**. Runs with no structured report
still enter **Awaiting review** and never imply scientific completion.

Short waits use `wait` with instance IDs and a timeout of 0-60 seconds. When the
coordinator finishes its turn with unobserved child results, OAW reconciles those
results and starts a continuation in the original context/session. UI polling
does not consume notifications. Explicit coordinator collection does. Continuations
retain the original Run lineage and summon budget, recheck live board/Barracks and
conversation permissions, yield to active/user-queued turns, and never restart
stopped, failed or interrupted parents. Each root permits at most 64 automatic
continuation turns; reaching the limit requires explicit continuation.

For external jobs, an Executor's `waiting` report must include `external_jobs`
and `check_after_seconds` (1-86400). This schedules a coordinator check. A coordinator
can also use `defer` with `request_id`, `delay_seconds`, `reason` and optional
`external_jobs`, then finish its turn. Check the real remote job at wake-up;
never infer completion from elapsed time or resubmit an existing job. Pending
checks appear on the task board and can be cancelled there or with `cancel_defer`.
Stopping a task/board/coordinator suppresses its pending continuations. Checks
from successfully finished turns survive ordinary restart; admitted continuations
are deduplicated and interrupted provider executions are not automatically replayed.

Executors retain private contexts and do not join the main Conversation. Revision 7
adds a shared **Research artifacts** collection with publish access for Executors
and manage access for the coordinator. The coordinator can attach published file
versions to the main conversation. Executors have command execution access, while
the coordinator owns shared Sandbox start/stop. Normal graph revocation still applies.

Restart the backend/frontend and deploy **MatCreator research** revision 7 for the
new instructions, artifact connections and Executor permissions. Existing placed
Legions retain their configured Agents, graph and knowledge snapshots; they are not
silently replaced. Existing boards gain the new tools after a backend restart, and
new delegations can auto-continue. Historical attempts are reconciled without
retroactively enabling automatic execution.

Maintained compatibility notes accompany the affected scientific packages (0.1.1).
They preserve upstream provenance, record the OAW adaptation revision, correct the
Bohrium npm installation and project-list examples, document workflow-specific
image/machine variables with explicit common fallbacks, and require installed CLI
help and model-format probes. See the [official installation guide](https://bohrium-doc.dp.tech/docs/bohrctl/install/).
Documentation review is not scientific runtime verification. Existing imported
Know-Do Graph snapshots remain immutable; deploy or explicitly assimilate the new
package to use the updated notes.

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
