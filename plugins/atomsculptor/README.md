# AtomSculptor for Open Agent World

This plugin migrates AtomSculptor onto the Open Agent World (OAW) contracts. It
does not start the former Starlette service, browser WebSocket protocol, custom
Sandbox, package installer or command allow-list.

## What it contributes

- **Atom Structure** is a revisioned OAW document with validated atoms, unit
  cell, periodic boundary conditions, layers, and stable atom IDs. Its workspace
  supports selection, layer visibility, copy, cut, paste, undo, redo, and
  JSON/XYZ/ExtXYZ/LXYZ/CIF/POSCAR/PDB/SDF/MOL2 import/export through document
  actions. The 3D display is the plugin's own Three.js editor
  (`frontend/legacy/StructureViewport.tsx`), not MatterViz and not the retired
  Starlette application. The card is also a `core.file-viewer`: connect it to a
  Sandbox with **Follow opened files**, open a structure file in the Sandbox's
  native file tree, and import it into the document from the workspace panel.
- **AtomSculptor Agent** runs one coordinator with three independent ADK
  task-mode specialists: Planner, Structure Builder and Materials Project.
  In team mode, free-form/custom modelling goes through Planner even when
  numeric inputs are complete; typed Structure-panel operations retain their
  direct Builder path. Planner can inspect
  structures and update an explicitly requested Task Board;
  Builder handles structure writes and modelling tools; Materials Project can
  use its authorized Skill and Sandbox but cannot write Atom Structure documents.
  Role tool lists are drawn from graph-authorized OAW capabilities, and OAW
  rechecks authorization when a tool is called. Specialist handoffs, tool
  calls, and model-request start/end timing appear in the normal Run activity
  trace. The new model-boundary events contain sizes, timing and token counts,
  not prompt text, images, credentials or private reasoning.
- **AtomSculptor Skills** packages the 18 retained modelling skills as an OAW
  Skill Toolbox. A runnable script must be explicitly paired with an OAW
  Sandbox. Some Skills are guidance only and do not supply an executable
  builder; their text must not be treated as a tool inventory. The packaged
  nanotube script distinguishes finite tubes with axial vacuum from periodic
  tubes with an axial repeat cell. The bundled `structure-inspect` converters move structures between
  Sandbox files and the canonical document without LLM guessing. For large
  structures, `stage_atom_structure_file` and `import_atom_structure_file`
  transfer exact documents through OAW's pinned Sandbox file boundary; both
  Structure and Sandbox grants are checked at the transfer point.
- **Modelling panel** runs a diagonal supercell operation directly as a
  revisioned document action. Other buttons send validated, typed requests to
  the linked Agent, which can use an authorized Sandbox and Skill. These are
  still Agent-orchestrated workflows, not guaranteed deterministic procedures.
  Interface candidates are stored in the current Structure document and shown
  in its chooser; the selected candidate's Sandbox file is imported later.
  Exports can be published as OAW Artifacts when connected. The panel checks
  the Agent's live OAW graph grants before starting those Agent workflows;
  its Agent/Sandbox/Skill selections are only UI hints.
- A managed model is resolved from **Settings → Models** at provider invocation.
  The credential does not enter card state, events, the browser or a Sandbox.
- **Visual structure observation** is available only when the selected model is
  marked **Vision** in **Settings → Models**, the Agent has an ordinary
  **Inspect structure** or **Modify structure** relationship to the Atom
  Structure, and that structure's workspace is open. The Agent can then call
  `observe_atom_structure` when a current 3D view materially helps. It receives
  one bounded transient PNG plus camera/selection metadata; no screenshot is
  written to the structure document, an Artifact, a Sandbox, or runtime logs.

## Setup

The backend runtime uses OAW's optional Google ADK and LiteLLM dependencies.
From the OAW repository root, install them before starting the backend:

```sh
uv sync --project backend --extra adk --extra litellm
```

Install the frontend dependencies from `frontend/` after copying or updating the
plugin (the OAW frontend now declares Three.js directly):

```sh
npm install
```

The current `matterviz` dependency requires Node.js 24 or newer; use a
matching Node.js installation when building on macOS.

Then restart both OAW processes so the local Python entry point and the local
frontend plugin entry are discovered. Create an AtomSculptor Agent and an Atom
Structure. The Agent automatically carries an AtomSculptor Skills Toolbox in
its backpack, using OAW's default-equipment contract. Existing Agents receive
missing default equipment once on startup. Unequipping or deleting it is retained;
startup does not reinstall a previously initialized slot. Existing external Skills
cards and connections remain usable. The equipped toolbox is the default source;
an explicitly selected external source takes precedence. Connect the Agent to
the other resources it needs using the displayed relationships. To execute a modelling script, also
connect the Agent to a properly configured OAW Sandbox and, for Materials
Project work, an Environment Profile containing the required secret.

The skill package intentionally makes no attempt to create virtual environments,
install dependencies, manipulate a host environment or bypass OAW's Sandbox.
Scientific packages belong in the explicitly selected Sandbox environment.
The equipped Toolbox is an editable local copy. Existing copies are not updated
on plugin upgrades. During development, update their contents manually, or equip
a newly created AtomSculptor Skills card and remove the old copy.
For brief environment probes, use a direct `python3 -c` argv call. For custom
modelling and verification, save the complete Python source with the linked
Sandbox's `write_sandbox_text_file` tool, check its returned byte count, and
run that `.py` file in a separate direct `python3` command. The file remains
in the Sandbox workspace for inspection and reruns. Avoid shell heredocs on
macOS Seatbelt; if a shell must invoke Python indirectly, select
`python_environment=managed`. Check stderr and generated files as well as the
command's exit code. A generated extxyz file must still pass the linked structure-inspect Skill,
then be converted by that Skill and imported with the current Atom Structure revision. Run packaged Python entries through
`run_skill_script`; the host supplies their interpreter and bundled dependencies.
For a small script repair, `write_sandbox_text_file` also accepts `edits` containing
exact `old`/`new` replacements instead of full `content`. Each old fragment must occur
once; an ambiguous match is rejected before any bytes are written.
The repaired version must be executed again.
Full replacements also accept `content` with `overwrite=true`.
AtomSculptor supplies explicit overwrite for files saved by that Run; unknown files
retain OAW's no-overwrite default. No SHA argument or comparison is required.
Sandbox inspection exposes compact managed Python/package versions without importing
packages. Validation receipts follow saved paths and native operations; saving or
executing an unrelated verifier does not discard a validated structure's receipt.
A native write to the structure invalidates its validation and dependent exports.
Import uses OAW's document revision and is confirmed by read-back.
For custom modelling in team mode, Planner supplies three execution checkpoints:
`probe` (compute unknown inputs), `build` (construct and independently inspect the
candidate), and `deliver` (requested file/document delivery). The run-local
`advance_modelling_stage` tool checks observed script versions and successful
execution receipts. Build needs a fresh independent validation receipt for
structure outputs; document delivery needs import and matching read-back.
After each saved script finishes, Builder assesses it either in the next
`run_modelling_step.previous_assessment` or via `advance_modelling_stage(stage=observe)`,
the observed `sandbox`/`script_path`, and concise `hypothesis`, `measurement`,
`outcome` (`continue`, `ready`, `needs_plan_review`, or `blocked`) and `next_step`.
The host requires this receipt-linked assessment before new writes, execution,
validation or advancing the stage. It is a Builder assessment, not independent
geometry acceptance. Contradictions require the existing Planner review path.
After review, existing valid measurements can be reassessed under the new plan
revision without replaying commands. Numerical iterations should persist their
best state and stop/revert worsening or stalled updates as well as having a finite bound.
The host snapshot supplies remaining Run time and a closeout reserve (default
180 seconds, capped at 20% of the Run budget); this guides validation/delivery
or an honest unfinished report, without changing the selected output allowance.
Planner selects document, files, or report delivery from the original request;
analysis and file-only tasks do not require an unrequested document write.
These checkpoints establish execution and delivery evidence, not physical validity
of a custom junction. Planner order is enforced before task dispatch because
ADK task-mode delegation can bypass normal tool callbacks. Explicit direct Builder
mode retains its single-agent flow and does not instantiate Planner.
Each probe/build plan includes a script artifact, a single question, an executable
experiment and a finite stop condition. Its `experiment.scope` is `input_measurement`
for probe and `candidate_work` for build. Probe answers one primitive input question;
fitting, boundary matching and candidate search belong to build using measured inputs.
Build can use several small saved/executed scripts or reuse intermediate arrays
and modules. The first build script need not finish the candidate; actual outputs
guide the next step. Pending commands, unexecuted versions and execution errors
receive concrete next-action guidance in build as well as probe. Final validation
and delivery gates remain unchanged.
probe/build/deliver are acceptance checkpoints, not three monolithic model tasks.
For custom workflows, `run_modelling_step` accepts one question, self-written source,
expected measured output and a finite stop condition. It sequentially calls the
existing authorized Sandbox save and execute operations, collecting a running
command through native wait receipts where available. There is no model request
between save and execution. No source runs in the host process and no database or
logging subsystem is added. Live authorization, compare-and-swap, command timeouts,
Stop, execution receipts and independent validation remain native OAW concerns.
After its result, Builder assesses the actual execution before choosing the next
question. The ordinary tools remain for small repairs and compatibility.

Builder handoffs and host snapshots focus the current stage; acceptance checks from
all stages and the exact original request stay available. Planner/coordinator retain
the full plan. Unsigned delegation advice is normalized at the accepted-call boundary;
signed provider parts remain intact. The current phase narrows tools and assessment
schemas: required assessment fields are genuinely required, and rejection diagnostics
identify each missing/oversized field. Independent reads may be parallel, but batches
containing dependent/state-changing calls are withheld before SDK dispatch and get
the existing single action-recovery opportunity. Never fabricate results for a
withheld call. A new failed execution or first assessment is decision progress;
repeated reads, identical-source/outcome reexecution and the same assessment do not
refresh that budget. Execution progress still does not prove task acceptance.

Algorithm assumptions are separate from user constraints. All original acceptance
checks remain in build/deliver.
Planner's `finish_task.result` is exposed as an object and encoded once for SDK
completion; legacy JSON strings remain accepted. Optional `finish_task.result.assumptions` are
exploratory hypotheses. Builder can return `status=needs_plan_review`, `reason`,
`sandbox` and `script_path` when actual execution contradicts the plan. The host
supplies its native command ID, exit status and observations to the coordinator, which
invokes Planner and resumes Builder. At most two reviews are allowed. Reviews
can revise current/remaining stages but cannot change completed stages or delivery;
the original request and existing receipts remain authoritative. Planner can read
authorized Skill guidance but cannot run generators or mutate structures.
Plan size limits are published in the host snapshot: up to 12 checks per stage,
400 characters per goal/check and 300 per experiment field. Invalid submissions
return the exact field paths, measured sizes and limits; valid checks are retained.
The SDK's string result is JSON-encoded once; valid object submissions are normalized
before SDK completion. Three rejected plan submissions stop with the invalid fields.
Before Builder dispatch, the runtime checks the authorized save/execute, Skill
read/run and requested import/read-back paths. Missing tools stop with connection
guidance; the runtime never grants itself access. Temporarily hiding Builder before
Planner finishes is explicitly described as a phase gate.
`run_modelling_step` accepts the prior command's receipt-linked assessment together
with the next saved script. `complete_stage=true` can advance that assessed stage
before the next action, preserving build's independent validation requirement.
For a small repair, use `patch_modelling_step` with required native `edits`; the
host saves the patch and runs it in the same action. Descriptive
text has concise style targets, with a separate 12,000-character resource bound;
slightly exceeding a style target does not discard an otherwise usable program.
The assessment's `command_id`/`sandbox`/`script_path` receipt fields are optional:
the host fills them from the latest finished execution and rejects only a
contradictory value, so an entire step (including its source) is no longer
discarded for echoing the wrong identifier. `advance_modelling_stage(stage=observe)`
fills omitted receipt fields the same way. A `ready` outcome claimed after three
consecutive measured failures, or after three non-improving measurements of the
same objective, is converted by the host into the measured-review path instead of
being trusted.
Each model request carries two host frames: a stable frame (original request,
authorized tools, turn-start selection, committed plan and assumptions) sent once
per change directly after the opening turn, and a per-turn frame with only current
receipts and guidance. The per-turn evidence also exposes the newest saved
script's version, head and a bounded excerpt around its latest traceback line,
so exact text repairs need not reconstruct the failing code from memory. A specialist's own unsigned reasoning is trimmed to a bounded recent tail
(`harness.retained_thought_chars`, default 4096 characters), limited also to the
latest two model turns. Merged provider Parts are sliced to the same strict bound;
signed Parts and tool exchanges stay intact. This is unsigned working memory,
not verified measurements; 0 restores full omission.
The SDK exposes flat contracts over one native executor: `run_modelling_step`
creates a new artifact with required `source`; `patch_modelling_step` requires
nonempty `edits`; `rewrite_modelling_step` explicitly replaces
saved source with required full `source` and a reason; `rerun_modelling_step` reuses
the saved bytes (`execution_scope=search` requires a successful sample).
Code must be in actual tool arguments, never only in reasoning text. Argument errors
return precise missing/invalid fields and focus the next tool declaration on that
contract. Repeated argument failures terminate with an unfinished native checkpoint;
they do not trigger scientific replanning or pretend Python executed. Missing patches, identical rewrites and cancelling edits never start code.
An unchanged failed source with unchanged arguments and observed environment
cannot silently rerun. Retired incremental_edit_hint settings are discarded
when loading old cards; they do not appear in new configuration schemas.
For document delivery, `run_delivery_step` runs validate → convert → import →
read-back as one action over the same native capabilities. With no new validation
parameters, it reuses build's independent receipt for the saved source path. An explicit validation parameter object requests a new
validation. The SDK tool name matches the instructions. Pending operations without
a waiter and unknown imports are retained without replay. It checks each receipt
before the next step and stops at the exact failed step without dispatching
later ones.
Delivery options and SDK acceptance fields derive from the trusted bundled Skill
function signatures, the same metadata used by its CLI parser. Both validation
and conversion receive `--folder .` and the workspace-relative `--file-name`;
booleans receive explicit `true`/`false` values. Unknown keys, incorrect types and
conflicting input paths are rejected before dispatch with precise field errors.
CLI parse failures (`exit_code=2`) are interface blockers, distinct from scientific
validation failures, and route to an accurate unfinished report.

Document delivery requires `target` in its stage-specific SDK schema. For stored
calls omitting it, the host fills only a unique native import identity and still
requires matching read-back revision with no unknown write. A confirmed import
routes directly to the deliver checkpoint and report; it never repeats conversion
or import to repair a missing stage field. The model-stagnation guard can close a
unique verified delivery checkpoint before final reporting, but cannot close
ambiguous, unconfirmed or unknown outcomes.

Unassessed experiments expose an assessment-only `advance_modelling_stage` action
alongside authorized read dependencies, including Skill guidance and source
previews. Reads do not count as execution or reset the progress guard. Actual
available tools are included in dynamic host evidence; independent reads may run
in parallel. Assessing an existing result requires no unrelated helper script.

`current_experiment` identifies the latest native command, host source version, plan
revision, execution inputs and pending/recorded assessment state. A modelling
action consumes a recorded assessment without recording it again. Omit
`previous_assessment` after a separate observe; a redundant matching receipt is
also accepted without overwriting measurements, objectives or outcomes. Explicit
stale command/source/plan identities reject before dispatch. A newly reported
contradiction follows the existing finish/Planner route; stored review/blocker
outcomes and independent validation gates still prevent new construction.

Host workflow/step-contract rejections carry their origin, command-dispatch state
and a recovery receipt. They do not increment the native dependency-failure
counter or reset the no-progress guard. True native dependency failures retain a
bounded counter scoped to operation, source/receipt and observed environment.
Changed native execution inputs are observable progress even with unchanged
source; identical successful reruns cannot refresh progress indefinitely. Dynamic
evidence and the existing native Run closeout checkpoint retain the correction
feedback, without a separate store or recovery log.

Sandbox measurements accept a pure JSON line or the exact `OAW_MEASUREMENTS `
prefix. The last marked record is authoritative: malformed or duplicate fields
produce a repair diagnostic instead of silently reusing an older metric. Modern
`kind=candidate` records require a stable `measurement_revision` and nonempty
`constraints=[{name,passed,reason?}]`, plus `coverage={region:sample_count}`. These
counts describe tested populations, not defect counts. An empty measured region
cannot prove zero defects even when a reported boolean check says passed.
Failed coverage or hard constraints prevent
an objective from counting as improvement or advancing build; passing self-reported
checks still does not replace independent validation. A changed measurement
definition starts a separate comparison series. Legacy objective-only records
remain readable.

Intermediate coordinate files can report `artifacts=[{path,units,frame,components,layout}]`
inside `oaw_measurements`. `frame` contains `origin` and three `axes` expressed in
the saved coordinate system, after translations; `components` contains stable
component names and positive counts, with identities also persisted in the data.
The host binds the metadata to the native producer command and exposes it as
`artifact_handoffs`. Consumers declare exact `input_artifacts` paths and
a new `input_manifest_path` that their script loads. The composite writes that
manifest, passes its path via the host-owned `--oaw-input-manifest` argument, and
runs a small host-authored file/layout inspection through existing authorized
Sandbox operations before dispatching modelling. Missing or invalid inputs block modelling;
an unconfirmed guard is retained as unknown and must be inspected. The guard
does not execute model code or infer geometry, and ordinary native receipts own
its logging. Frames, units, component counts and producer command IDs are included in native
execution identity: changing those inputs can justify an unchanged-source retry;
renaming a manifest or repeating the same bytes cannot manufacture progress.

Executed `assumption_invalid=true` with a concise `contradiction` routes to Planner
even if the next model claims `continue`. Repeated failed constraints and successful
diagnostics without changed scientific evidence also request review. The former
`diagnostic_limit` setting is discarded on loading. Accepted
Planner revisions preserve completed stages and requested
delivery. The original user request remains authoritative; review instructions
require keeping its acceptance requirements; reviews have no global count cutoff.

`working_decision` retains the last action, invalid candidate checks and rejected
methods in the existing Run evidence/checkpoint. For a justified next code action,
combine a brief `previous_assessment` with that action rather than drafting future
source in thought and then submitting only `observe`. Separate observation remains
available for stage decisions, review and independent validation. Host decisions
guide execution; native archives retain the full original exchanges.

The native-export comparator merges Sandbox history, SDK execution results and
capability receipt hints by command ID. Later running/redacted observations never
erase a completed result. It reports retained history coverage, command exit codes
and durations, returned-model timings by role/stage, interruption time and retry
waits separately. Missing usage or stages remain unavailable rather than inferred.

Scripts intended for a search should accept sample/search arguments. Use
`execution_scope=sample` for one small case; `execution_scope=search` requires a
successful sample of the saved source version. Source changes and repairs
require another sample. Reruns execute the saved path without rewriting it.
Persist search results/best candidates separately from later postprocessing;
empty candidate sets are legitimate measured results, not successful structures.
The host checks execution receipts and source identity, not whether the script's
self-declared sample is scientifically sufficient.

Ordinary probe/input measurements have no optimization direction and need no objective.
Only a build plan that declares `experiment.objective={name,direction,unit?}`
uses the following comparable objective JSON line:
`{"oaw_measurements":{"objective":{"name":"metric","value":1,"direction":"minimize","unit":"unit"}}}`.
The value must be measured by the script, not a placeholder. The host observes
that native stdout and counts non-improvements for the matching declared build
objective (name/direction/unit) even before the next assessment;
an absent metric remains unavailable. This provenance is execution evidence,
not domain acceptance or a scientific oracle.
Assessments may include an `objective` (name, finite value, minimize/maximize,
unit) from actual output, or `assumption_invalid=true`. Three non-improving
measurements of the same objective, three consecutive failed scripts, or an
invalid assumption require the existing Planner review path. Signals never grant
acceptance. Replanning resets search tracking without replaying experiments.

Builder's working history has a rolling character target. Only complete unsigned
exchanges are removed, with a host checkpoint of the current saved source and
recent real evidence. Provider-signed history and unresolved calls remain intact.
Full model/tool archives stay in native Run state; no extra summary-model call is
needed. This bounds working history, not the provider's advertised context window.

The default total model-request limit is now null. Existing cards' exact former
32 default migrates to null; other explicit finite values remain enforced. The
cumulative output budget remains. Near a finite budget the next action is a real
unfinished closeout. If a budget or a terminal harness guard is exhausted, the runtime emits a failed outcome
with native Run `execution_checkpoint` state (saved scripts, actual receipts,
validation/import state and unknown executions). This provides continuation
evidence; it does not automatically replay commands or restore an entire SDK
session. The exporter includes `execution checkpoint.json` when this state exists.

During probe/build, actions are narrowed to the current readiness, experiment,
assessment or waiting phase. Builder retains relevant authorized inspection,
Skill-reading, execution and repair tools; unrelated delivery/team operations wait
for their phase. Stage/import guards and execution budgets still apply. Before a saved probe
exists, it also permits a short direct `python3 -c`
measurement (at most two attempts, 1600 source characters each). A successful
measurement directs the next action to save and execute the probe file; inline
stdout cannot advance a stage or validate a file. Running measurements must be
collected with `wait_sandbox_operation`; failed or duplicate-source measurements
do not refresh progress. The controls are `inline_probe_limit` and
`inline_probe_chars` in `harness`.
An inline exit of zero records observed output; it does not automatically save,
reexecute or promote the source. Builder judges the output and explicitly saves
and executes a probe, or returns measured evidence for plan review. Successful exit
alone never establishes measurement correctness.
It requests a tool action and reuses OAW's model stream watchdog: 300 seconds
without first content, or 90 seconds of silence after real content. Streamed
thoughts, text and tool arguments count as activity; empty keepalives do not.
A separate hard request ceiling defaults to 600 seconds, including while content
is arriving. Total Run wall-clock limits are disabled by default: harness
`max_run_seconds=null` and the native OAW Agent setting
`run_execution_deadline_seconds=0`. Existing cards carrying the former
1200-second harness default migrate to null during validation; their missing
native setting uses the node default of zero. Other Agents keep the server
deadline. A finite custom harness limit remains opt-in. A request ceiling does
not establish whether reasoning is complete. One action recovery is permitted per
Run, with at least the ordinary request allowance. Recovery applies only to that
request; the next action uses the normal ceiling. Hard-ceiling metadata records
whether content arrived and its idle duration without saving thought text.
Existing cards carrying the exact legacy 60/30-second pair are upgraded to
600/600 on config validation without direct database edits. Other custom
ceilings remain configurable, and recovery cannot shorten the ordinary ceiling.
No-progress requests are counted as telemetry. The optional
`model_requests_without_progress` brake defaults to null; the former persisted
default 6 migrates to null, including cards that serialized every default field.
Other custom counts remain opt-in limits. Inspections and repeated reads are
not successful computation. New execution/assessment/validation/read-back
evidence refreshes the decision counter. Pending host operations freeze it
without resetting it or claiming successful progress. For a tracked, saved Python script,
the runtime collects a running command through the already-authorized OAW
`wait_sandbox_operation` capability inside the same tool action; observations
do not require another model request. Cancellation, permission changes and actual
termination receipts remain owned by OAW. Untracked commands and inline probes
keep their ordinary observation path.
Saved custom Python commands without an explicit `timeout_seconds` use a
120-second execution deadline, capped by the remaining Run time, when the
authorized command tool advertises that field. Configure this default with
`script_execution_seconds`; an explicit command deadline is preserved. Builder
instructions require finite iterations, advancing state, nonconvergence output
and a small candidate trial before a large scan. A longer, bounded experiment
can request a larger command deadline; extending it does not extend the Run.
Configure model action limits with `model_action_seconds`,
`action_recovery_seconds` and `model_requests_without_progress` in `harness`.
These action controls apply to staged team modelling; typed panel operations and
explicit direct Builder mode keep their existing behaviour. A model that still
cannot act ends with an explicit failure rather than an unbounded reasoning loop.
Output-limit recovery is separate from action/deadline and transport recovery.
Its single retry requests an executable action, measured plan review or actual
blocker, without automatically reducing OAW's selected output limit. Subsequent
specialist requests also inherit that limit. Reasoning on compatible providers
may consume generation allowance before a tool is emitted; action-focused
instructions are not evidence that a smaller allowance is sufficient. Truncated
tool calls never dispatch. Optional `specialist_output_tokens` and
`recovered_action_output_tokens` are null by default; explicit custom caps and
remaining cumulative budgets still apply. The exact old persisted policy
(16384 specialist / 65536 cumulative / absent or 4096 recovery) migrates to
inherited per-request limits and a 262144 cumulative budget on config validation,
without database edits. Other customized cap combinations are preserved.
The 600-second ceiling and native idle watchdog remain unchanged.
Recovery replaces the prior host evidence snapshot while retaining user input,
tool calls, signatures and results. Identical unsigned `start_sandbox` calls in
one complete accepted response are coalesced before dispatch; other calls,
distinct arguments and later retries are retained.

`scripts/export-atomsculptor-test.py --database PATH --output-root PATH` archives
the latest settled AtomSculptor Run from OAW's existing records using a read-only
SQLite transaction; it creates no database, tables or alternate log store. Use
`--run-id` for one Run and `--label` for its category. Archives contain prompt,
Run activity metadata, Conversation messages, latest role checkpoints, scoped Sandbox/tool receipts and
a provenance/hash manifest. Each Run has its own directory; existing archives
are not overwritten. It refuses to select an older test when the latest is active.
This CLI exports existing data; it does not schedule work or launch a test.
Native tool traces and Sandbox history are bounded, and model checkpoints contain
only each role's latest request. The exporter preserves truncation flags and labels
metadata-derived model timeline entries. Full unretained live model thinking cannot
be recovered; the archive manifest states this limit rather than inventing text.
The Materials Project Skill includes a runnable script but requires `mp-api`,
explicit Sandbox networking and an Environment Profile with `MP_API_KEY`.
Neither the plugin wheel nor the host Python environment needs `mp-api`.
Existing Skill Toolbox cards retain their saved contents after a plugin update;
manually update the equipped/linked package to version 0.1.8 (or create a new
AtomSculptor Skills card and reconnect it) to use revised scripts. Do not delete the
old card until any local edits have been copied over.

Modelling continuations can declare `depends_on` with completed HOST subtask
IDs and `evidence_refs` with assessed `execution:<sequence>` IDs. These use OAW's
existing task graph checks. Produced-file prerequisites use native path and
layout inspection before consumption. The host retains relevant prerequisites and
counterexamples with scoped `read_tool_result` pointers; it does not transfer
an older artifact's verdict to changed bytes.

Scientific recovery compares causes, inputs, environment and measured state.
New failure causes or actual measurements allow another local step; an unchanged
unresolved cause requests Planner review. Changing only source/file names does
not prove progress. Method prose and measurement revision labels cannot hide the
same failed requirement; objectives with different definitions still have separate
comparison series. Reviews require new executed evidence and have no global count
cutoff. The legacy `model_requests_without_progress` brake applies only if
explicitly configured. Repeating an identical rejected action after corrective
feedback, with unchanged host state, is a protocol blocker; pending work is
collected before deciding recovery. Existing native dependency safeguards remain.

New exports include `experiment.json`, with Run-start working-source fingerprints,
model/gateway, actual request settings, input hashes and validator receipts.
Old Runs without these fields remain unknown. For paired experiments, export
each Run with matching `--task-id` and positive `--repeat`, plus `--variant` and
an optional explicit `--acceptance passed|failed|unknown`. The verdict is external
task acceptance, independent of Run status or geometry validation. Existing
archives are immutable; annotations must be supplied on first export.
Compare sets with `compare-atomsculptor-runs.py --current-root PATH --baseline-root PATH`.
The comparison excludes unmatched/duplicate repetitions or changed model/input
conditions and reports task flips, costs among paired successes, execution
recovery, reuse and network waits. Missing usage stays unknown; thinking text
characters are never converted into hidden reasoning tokens or assumed prices.

Copying only `plugins/atomsculptor` into a newer OAW checkout is not a complete
installation: the host must provide plugin API 1.24, including the guarded
Structure↔Sandbox file bridge and the `write_sandbox_text_file` capability,
and the frontend host method
`getAgentCapabilities` for live connection checks; it must discover the Python
entry point, bundle the frontend module, and install the frontend's Three.js
dependencies. Rebuild the frontend and restart the backend after copying.

## Saved model thinking

Provider-exposed model thinking is saved through OAW's shared Run StateStore
(`core.run.model_reasoning`), not a plugin database or Conversation message.
Each role/request/retry has a separate record. Streaming observations are
checkpointed about once per second; completion and interruption flush the latest
received text. Normal Stop preserves the last received tail. After a hard crash,
only committed snapshots survive and unfinished records are marked interrupted.
The host-only field is excluded from runtime StateAPI reads/writes, inherited
resolution and model state snapshots. It does not enter model history.
The full redacted text has no 64,000-character archive cutoff; live events remain
bounded previews. Agent execution details and Conversation Run details restore
the same archive and load full text when a thinking entry is opened. The existing
read-only test exporter includes `model thinking.json` and saved text in both
Agent and Conversation logs, with coverage/interruption metadata. Earlier
unretained text and thoughts not exposed by the provider cannot be recovered.

## Model and tool invocation archives

OAW's shared ADK model boundary also retains each redacted application request
and completed response in `core.run.model_request_logs`. Records contain the
model, system instruction, generation config, declared tools and contents after
context preparation, with separate identities for roles, Run attempts and network
attempts. Full provider usage metadata is retained; missing usage stays unknown.
This is an ADK boundary snapshot, not a raw HTTP capture of adapter transformations.

`core.run.tool_invocation_logs` stores accepted function exchanges plus tool
callbacks, including SDK delegation/finish tools. It retains original/effective
arguments and returned results without the public preview's source/stdout limits.
Host callback denials are preserved, and unsettled calls are marked interrupted
on Stop or startup. Existing Sandbox/tool output bounds still apply before the
ADK boundary; their returned flags and separate native receipts are preserved.

Both fields use the existing StateStore/database, are host-only, and stay outside
model context and Conversation messages. Unsupported SDK values are marked with
paths and incomplete coverage, never represented as complete raw objects. API
lists under `/api/runs/{run_id}/invocations/{models|tools}` are metadata pages;
the `/{identity}` endpoint returns the full redacted record on demand. Agent and
Conversation execution details share these APIs, restore tool history and show
per-attempt input/output token counts and full request details. The existing
read-only exporter includes `model requests.json`, `tool calls.json` and coverage
metadata. Old archives are immutable and missing historical payloads cannot be reconstructed.

## Migration boundary

The retained source-level changes are the removal of CodeGraphRAG/Memgraph,
the AtomSculptor specialist workflow and the structure-editor interaction model.
The old application-specific Sandbox implementation is intentionally excluded.
Further UI migration should use Atom Structure document actions and OAW Artifacts,
not restore the old `/api/*` routes or WebSocket events.

## Remaining integration work

- Model requests emit start, elapsed-time and completion events. Every role
  respects the selected model's configured output limit. The Agent's `harness`
  configuration caps coordinator replies at 4096 tokens; specialists and direct
  Builder inherit OAW's selected model output limit by default. It also bounds total model requests
  (optional; the former default 32-request cutoff is retired), known output tokens
  (262144), and unexecuted writes per Python file
  (2). These defaults are adjustable for complex work. Manual Stop still
  cancels and drains the active stream. Total wall-clock limits are disabled by
  default at both the harness and native Run layers; per-request, stream-health
  and provider limits still apply. Outgoing requests restore unambiguous id-less tool responses
  in place and recover completed specialist results by their exact call ID;
  genuinely unresolved calls fail explicitly instead of receiving invented results.
  OAW `ManagedContext` retains the coordinator's conversation, while ADK task
  scopes isolate specialist delegations. A runtime guard limits planner delegation
  to two attempts. Only unsigned prior reasoning is omitted from specialist
  history; actual script bodies, signed provider thoughts and paired tool
  receipts remain unchanged. All specialists receive the original user input
  independently of the coordinator's delegation. Host evidence records script
  versions and execution outcomes. A Python rewrite loop is corrected
  before another overwrite; persistent refusal to execute stops the run.
  A failed execution permits repair; `wc` and unrelated probes do not count.
  Running commands must be observed before their script can be overwritten.
  The structure-inspect Skill now independently measures saved finite structures
  and verifies unrelaxed CNT lattices. It reports actual cell-face margins,
  per-atom radius/nearest neighbors and nominal versus actual tube lengths.
  Conversion returns the document path; imports carry only a fresh expected
  revision. A run that imported a document cannot finish as
  succeeded without matching read-back confirmation.
  The default `execution_mode=team` preserves specialist coordination. The
  opt-in `execution_mode=builder` directly handles structure-only requests with
  the same authorized tools, budgets and evidence checks; it does not provide
  Planner/Task Board tracking. Compare this mode separately, after verifying
  the default mode, rather than mixing routing and correctness experiments.
  Conversation keeps model thinking collapsed until the user opens it.
  AtomSculptor now uses OAW's shared model observation and recovery layer.
  A transient provider/connection failure retries the prepared model request
  within its current specialist, without redelegating through the coordinator.
  OAW's shared default permits six attempts (the initial request plus five
  retries), with exponential waits of 2/4/8/16/30 seconds, up to 20% positive
  jitter, and a bounded Retry-After. Waiting is visible and cancellable.
  AtomSculptor exposes this same policy through `harness.request_recovery`
  (`max_attempts`, `initial_delay_seconds`, `max_delay_seconds`, `jitter_ratio`);
  no separate retry database or journal is introduced. Authentication and other
  permanent errors are not retried. Partial
  output is preview-only; tool calls are released only after the aggregated
  response and clean EOF. Earlier completed tools remain in context and are not
  replayed. Each attempt has a separate incomplete/complete thinking preview;
  recovery events identify role, request, attempt, exception type and idle time.
  The first-content/stream-idle bounds are 300/90 seconds, not a total request
  deadline. Each transport attempt retains the ordinary model-action deadline;
  backoff does not consume it. Exhausted retries save unfinished execution
  evidence to native Run `execution_checkpoint` and stop; they do not trigger a
  second whole-Agent retry. This regenerates a response, not provider-side resumption of reasoning.
  Errors outside the model request retain OAW's conservative write-reconciliation
  path. Capability receipts and model checkpoints remain privacy-bounded.
  Live ADK verification on macOS is still needed.
- Supercell is deterministic from both the Structure UI and Agent tool.
  Surface, interface, SMILES and remote publish still require Agent
  orchestration; the typed request only validates their
  parameters. They need dedicated deterministic host-side workflows before
  they can be described as fixed procedures.
- The inspect result bounds coordinate windows, selection and metadata, and
  the dual-authorized file bridge avoids relaying thousands of atom records
  through the model. UI selection retries a revision conflict once against
  the latest document using stable atom IDs. Selection still shares the
  structural revision; file imports use the freshly inspected revision.
  Other long-running writes must re-inspect after conflicts; fully separate
  selection state remains future work.
- The frontend checks live Agent capabilities before Agent-run buttons, but a
  different OAW checkout needs the small host SDK method in this branch. A
  clean macOS frontend build and live ADK/Sandbox integration test remain
  necessary after transplanting this plugin.

Native saved-script failures route to Planner after three consecutive executions,
without waiting for the final model assessment or being preempted by the generic
repeated-error hard stop. Other repeated capability errors remain bounded. Native
execution checkpoints include pending Skill operations and unknown imports.
Static context contains the original request, full acceptance checks and the current
stage's method; the dynamic frame and unsigned handoff do not duplicate that plan.
The comparison script reads the existing immutable export to report native command
failures, unchanged source saves, rejection codes, observed stages and interrupted
model time separately. Unknown token usage stays unknown; early failed runs are
not evidence of faster successful builds. No total wall-clock default is restored.


### Measurement and task evidence

The Sandbox stdout contract is generated from the same Pydantic types used to
parse it. HOST measurement_contract includes the versioned schema and an example.
Both `{"oaw_measurements": {...}}` and a bare object after `OAW_MEASUREMENTS `
are accepted. Bounded ordinary numeric data belongs in `values`, `measurements`
or `metadata`; none grants acceptance. `coverage` stays a flat region-to-integer
sample-count map. Errors identify the exact field and type. Conflicting envelope
values, duplicate keys, nonfinite data and malformed safety fields never reuse
an earlier successful record. Repair the producer's record through the existing
Sandbox patch/execute tools; protocol issues do not consume scientific Planner
reviews. Explicit measured scientific contradictions retain their review path.

HOST requirements assigns stable IDs to the planned checks. Native reports map
`constraints.check_ids` to these IDs and `constraints.regions` to measured coverage.
The current execution's requirement ledger distinguishes unknown, reported_pass
and reported_fail. Typed measurement workflows must cover all build checks on a
candidate, independently validate its saved artifact path, and then pass the
existing delivery gates. A fresh diagnostic cannot inherit an older candidate's
passes. Legacy unstructured workflows retain the existing independent validation
contract; once a typed record is used or rejected, omitting it cannot restore
that legacy path. Completed stages retain their evidence references in native
checkpoints. For non-optimization work, a first measured requirement verdict or
newly passing requirement resets the diagnostic counter; repeating a partial
candidate does not. Diagnostic failures do not count as failed candidates.
A reported pass is
still a model-authored claim until independently
verified. Optional checker `controls` record whether known positive/negative
controls gave the expected verdict; a failed control blocks candidate promotion.

The structure-inspect validator can independently check coordination in a
complete, disjoint atom-region partition using `region_checks_file`.
Regions resolve against the actual file being validated, so atom row identities
cannot drift after edits. Nonempty coverage and explicit allowed boundary regions
prevent hiding untested interior atoms. It evaluates declared degree ranges;
it does not infer physical validity or build a task-specific junction.

Builder receives one current_subtask backed by the latest native step and
assessment. Repeated review stdout is excluded from dynamic context; settled
history and full decisions remain in existing OAW invocation records. Execution
step snapshots contain receipt references rather than duplicate questions and
responses. No model-generated code is executed by the host.

The selected OAW model controls per-request output. Retired cumulative
`harness.max_output_tokens` and `closeout_reserve_tokens` settings are discarded
when loading old cards and omitted from new schemas. Usage does not shrink the
next request's limit. The default total wall-clock limit remains disabled;
network recovery uses OAW's native request policy.

The bundled Skills share one maintained `skill_cli.py` source, injected as
`scripts/oaw_runtime.py` during SkillPackage assembly. Each materialized Skill
remains standalone. Version 0.1.9 adds optional topology, local-geometry, symmetry
and explicit-calculator instruments to structure-inspect. Update external Skill
cards manually when using them instead of the built-in package. Interface
catalogue entries are unverified drafts; selecting one must validate and convert
the saved file before importing it.
