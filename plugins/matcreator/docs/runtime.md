# MatCreator runtime and extension contract

[Pack guide](../README.md)

## Native MatCreator Agent

Use **MatCreator research** for the Pack-owned planner, execution
orchestrator and recursive step executors. Their maintained Python source lives
in `oaw_matcreator_engine/` and is included in the Pack wheel alongside the
`oaw_matcreator/` host adapter. Editing that source takes effect in the next
worker; no upstream checkout, MatCreator PyPI/Git installation, source fingerprint
update or separate service is required.

Context compression uses OAW's [shared budget policy](../../../docs/developers/context-compaction.md).
The planner, execution orchestrator and each recursive executor check their own
assembled request before calling the model. Catalog context/output limits take
priority; compression triggers on budget pressure, not every three turns.
Checkpoints are private to each native session/branch/agent and preserve the
original event history. A normal final reply does not start a post-turn summary.

Engine attribution and adaptation boundaries are recorded in
[UPSTREAM.md](../oaw_matcreator_engine/UPSTREAM.md). The current Pack and required
API versions are declared in [pyproject.toml](../pyproject.toml) and
[oaw.pack.toml](../oaw.pack.toml).

Source setup, desktop packaging and schema 3 `.oawpack` installation use the
same declared environment and host-owned preparation provider. In
[`oaw.pack.toml`](../oaw.pack.toml), `native` is an install-time Python worker
environment; its Google ADK, LiteLLM, JSON Repair and tokenizer requirements
come from `oaw_matcreator/native-requirements.txt`. OAW installs and verifies
these libraries under the active application's data root, separately from host
site packages. Desktop builds can supply the same verified environment as an
offline runtime bundle. The legacy setup entry point delegates to this provider.

Preparation records durable operation receipts, serializes concurrent installs
and publishes verified environment generations. A compatible interpreter is
prepared when needed. Workers use `-I -S` and explicitly load Pack source plus
the selected private libraries; worker startup never downloads dependencies.
Engine source edits alone do not invalidate the dependency environment. Valid
pre-migration environments remain readable during migration. Managed dependency
cleanup follows Pack references and active workers; session data is kept.

Existing `matcreator.agent` cards, the `matcreator.native` provider ID, session
storage paths and checkpoint format are retained. Obsolete `python_executable`
and `source_directory` config fields are discarded on validation; external
engine/interpreter override paths are no longer exposed or executed. Existing
saved workspace layouts remain user-owned.

On Windows, `scripts/dev.ps1` prepares and launches the persistent
`backend/.venv/Scripts/python.exe`, matching normal setup. Runtime selection uses
the active OAW data profile. Missing-runtime errors report the backend interpreter
and expected directory; Agent details expose the managed environment's availability.

Only orchestration dependencies are installed in this worker environment.
Executable Skills explicitly select Sandbox profiles for ASE, SSH support,
scientific input preparation and the Bohrium `bohr` CLI. OAW prepares their Python
libraries or private npm prefix within the authorized Sandbox command, subject
to its network policy and deadline. Node/npm must already be available in a
Sandbox that uses the Bohr profile. Model connections, cloud credentials,
project IDs, actual LAMMPS/DeepMD/VASP calculators, model weights and licensed
pseudopotentials remain explicit prerequisites on the selected compute target;
installing preparation libraries does not supply those resources. The native
runtime supports ADK/LiteLLM model connections
(OpenAI, Anthropic, Gemini and legacy); the host's TypeSafe-only adapter is not
supported by this ADK execution engine.

In **normal** mode, MatCreator first presents its plan and waits for approval in
Conversation. **flash** mode delegates directly without a DAG. The Legion
connects the Agent to Conversation, Sandbox, Research knowledge, Research
artifacts. The separate **Agent graph** observes the Agent through a read-only
connection. Structure viewer follows files from
Conversation and Sandbox. No OAW Barracks or second task scheduler is deployed.

The planner owns user communication in both normal and Flash modes. Execution
orchestrators and recursive step executors return internal reports and artifact
references to their caller. Their text remains in native checkpoints for review;
only planner text becomes Conversation replies, while tool activity stays visible.
Provider-exposed thinking from the planner, orchestrator and child executors is
published as `model_reasoning` activity using native event/part identities. OAW
owns its Run history and loads each full text on expansion. Native model calls
are non-streaming: thinking appears when each model response arrives, before its
tool calls, without becoming a Conversation reply.
`to_planner` ends the execution stream after its tool response, before another
executor model round. The planner then reviews the report and, when needed,
presents a revised plan for confirmation. Internal executors cannot send
Conversation messages or request another Conversation turn through the resource
bridge, including capability IDs remembered in older plans or checkpoints.
Model error events, including `MAX_TOKENS` with only reasoning and no visible
answer, fail the Run with a specific diagnosis. Ending an ADK stream does not
make an unsuccessful model response a successful Run; earlier tools are not
automatically replayed. Each new worker uses the selected connection's saved
output token limit, which includes reasoning tokens on compatible providers.

The Pack owns its orchestration tools, graph validation, recursive runner,
result schemas and recovery ledger. Planner and child agents are constructed
with the same explicit OAW resource bridge; they never construct standalone
Shell/Python, remote-provider or knowledge-store tools:

| Resource | Connected OAW tool/view |
| --- | --- |
| Shell/Python and files | Sandbox inspection, execution, operation waiting and file browser |
| Skills and memory | Know-Do Graph search/inspection, skill resources/scripts, save memory |
| Output files | Publish/inspect immutable OAW artifact versions; Structure viewer |
| Plans and subagents | Host Agent graph through the public execution-source contract |
| Conversation and stop | OAW Conversation and Run controls |

Tools are discovered through `oaw_list_tools` and invoked through
`oaw_invoke_tool`; each call rechecks current graph authorization. Removing an
edge revokes the tool for all internal subagents. Native host shell tools, native
knowledge databases, native remote-job submission tools and configured remote
A2A agents are not exposed. Existing scientific commands may be run through an
authorized Sandbox; remote job monitoring must use those connected tools and
retain operation/job IDs, rather than assuming the upstream web server's job
monitor is running.

Unknown function names return tool feedback in the same model loop. Planning,
execution orchestration and every recursive Step agent share this behavior.
Native function arguments are validated before invocation; execution, result
encoding and tool-local cancellation failures are contained per call. One bad
call does not cancel unrelated calls in the same batch. Failed calls are never
automatically replayed: an operation may already have produced side effects.
Run cancellation still cancels and joins the active tool work.

Each card/conversation pair has its own private worker state and atomic session
checkpoint under the host data root. New turns load that checkpoint in a fresh
worker; stopping a Run cancels/join its OAW tool calls and closes the worker.
Interrupted work never restarts automatically. Pack attempt recovery runs
on the next requested turn. Private runtime paths, credentials, checkpoint data
and execution snapshots are excluded from saved Legion templates. The execution
view is a projection; an unavailable view does not abort scientific work.
On restore, unanswered tool calls receive matching responses that explicitly
report an unknown outcome. Existing results are retained and no operation is
replayed by this repair. Model/transport failures outside tool dispatch and
worker/persistence faults remain Run failures.

Artifacts are verified through the current OAW artifact read capability, using
`oaw:artifact:<collection>:<version>` references. Sandbox paths are never treated
as host files. Native `success` and OAW Run success remain execution claims, not
proof of scientific correctness.

## Execution history and dependency evidence

The Pack records immutable Step attempts,
plan identities, timestamps, retry/replan links and versioned artifact producers.
The host stores a full read-only projection with each Run and exposes authorized,
revision-consistent pages. A completed Conversation turn does not turn unfinished
Steps into successes. New turns retain the previous graph until their first
projection arrives; the stable history ID preserves selection across these turns.

The host renders attempts and inline details through the shared
[execution history viewer](../../../docs/developers/execution-graph.md#built-in-graph-presentation).
The Pack does not own a separate dependency-lens or Step-graph interface.

`consumed_inputs` explicitly names a successful `step_id`, a same-plan predecessor
`node_id`, or a versioned `artifact_ref`. A data edge is recorded when the result
is delivered, not merely when the plan declares it. Successful artifact inspection
records access evidence; it does not prove that the model used the data in its
reasoning. Free-form contextual references are not reconstructed. Sources outside
this private Agent/session history have no fabricated producer node. Older records
lack evidence that was never recorded; migration preserves known identities and
explicit control dependencies without inventing historical provenance.

The host accepts up to 10,000 nodes, 50,000 edges and 8 MiB per projection. The Pack
preserves complete history on disk and declares presentation limits when necessary.
History folding does not aggregate or rewrite real edges. Runtime scheduling,
resource access and scientific execution remain Pack-owned.


## Long computations

Native steps can return `waiting` with host `job_ids`. Submission identities
are recorded before the remote call, and the host saves the provider ID before
returning it to the model. A waiting workflow checkpoints and releases its worker.
When external work settles, the host starts a bounded continuation that restores
the same approved plan, inspects the existing jobs, and collects outputs. A changed
plan still needs approval. Known jobs are supplied to the resumed step explicitly.

Discover `matcreator.bohrium-batch` with `inspect_background_jobs`. Its declared
`bohr` profile prepares a private npm package in the connected Sandbox; submission,
monitoring and cancellation resolve that package's CLI directly, including after
the submitting Run ends. No global `bohr` installation is needed. It performs a
dry run, requires explicit project/image/machine and compute budget, and records
a searchable `oaw-<host-job-id>` in the remote job name. A lost submission response
requires inspecting existing provider jobs and attaching the matching ID; it is
never automatically retried. Stage input files before submission; download results
into the Sandbox and publish artifacts after completion. Existing deployed skills
retain their saved content; reimport the updated package to receive runtime notes.

Missing `submit_step_result` triggers one report-only repair invocation, limited to
two model calls and 60 seconds. It cannot rerun resource or child-agent tools.
Persistent failure is classified as `result_missing`, separately from an executor
timeout or exception. Partial diagnostic events remain available. Provider success
does not prove trajectory quality or scientific acceptance.

See the host's [waiting and recovery contract](../../../docs/execution.md#long-computations-and-durable-waiting-api-133)
for Stop, cancellation, resource deletion and restart behavior.


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
not supported.

Search/expansion responses are bounded (default 60, maximum 200 plus explicitly
requested roots), with type, relationship, source, trust and memory filters.
Full bodies and resources load only on inspection. Backend queries currently scan
the host-owned document; they do not require a second persistence database.
The Window renders the working subgraph through the host's shared NetworkMap,
with details loaded on inspection.


## Verification commands

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_matcreator.py backend/tests/test_matcreator_native.py backend/tests/test_matcreator_workspace.py plugins/matcreator/tests
$env:OAW_MATCREATOR_MANAGED = '1'
backend/.venv/Scripts/python.exe -m pytest plugins/matcreator/tests/test_native_runtime.py plugins/matcreator/tests/test_owned_engine.py
cd frontend
npx vitest run src/cards/ExecutionGraph.test.tsx src/plugins/StructureViewer.test.tsx src/legions/LegionWorkspace.test.tsx
node scripts/run-e2e.mjs --builtin-ui e2e/matcreator-native.spec.ts
```

The native runtime test uses the Pack engine/real ADK with a deterministic local
model endpoint. The browser test uses a fixture execution snapshot and real
Conversation attachment/Structure viewer APIs. These are not real-model or
remote-science acceptance tests. Generic host delegation and continuation
regressions live in `backend/tests/test_node_delegation.py` and
`backend/tests/test_node_continuations.py`, with a test-only task plugin.

Regenerate bundled source packages explicitly with:

```powershell
backend/.venv/Scripts/python.exe plugins/matcreator/build_packages.py path/to/MatCreator
```

The maintainer importer requires PyYAML. Check out the skill-package source
revision first; normal startup reads generated JSON. Migrated executable Skills
prepare their declared dependencies when run in an authorized Sandbox.
`provision_science.py` remains an optional helper for older manually configured
Sandbox workspaces; it is not required for the declared ASE profile.
