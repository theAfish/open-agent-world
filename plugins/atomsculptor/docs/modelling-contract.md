# AtomSculptor modelling contract

For installation and ordinary use, see the [Pack guide](../README.md). This
reference covers native modelling tools and evidence validation. Agent-generated
results still require scientific review.

## Execution and delivery

The skill package intentionally makes no attempt to create virtual environments,
install dependencies, manipulate a host environment or bypass OAW's Sandbox.
Scientific packages belong in the explicitly selected Sandbox environment.
Modelling and bundled executable Skills use OAW's existing host-prepared shared Sandbox
Python (`legacy-shared` in the Pack declaration). OAW's bootstrap prepares ASE,
NumPy, Matplotlib, pymatgen, pandas, mp-api and RDKit per execution platform in the active profile;
Sandboxes reuse that read-only environment, including when workload networking
is disabled. Preparation still needs a working package source or download cache.
Materials Project and molecular-creation Skills reuse this shared environment.
Materials Project queries still require the configured API credential.
The backend's Conda site-packages are not copied into
workloads. AtomSculptor provides no separate installer or environment cache.
The equipped Toolbox is an editable local copy. Existing copies are not updated
on plugin upgrades. During development, update their contents manually, or equip
a newly created AtomSculptor Skills card and remove the old copy.
Bundled Skill names and IDs use underscores, for example `structure_inspect`
and `nanotube_builder`. Reads also accept OAW's current name aliases, so older
toolboxes with hyphenated names remain readable without replacing local edits.
In active staged probe/build work, brief direct `python3 -c` checks are allowed
only before a saved experiment script exists, within the host-reported limits.
After saving a script, execute or patch that script instead of using inline
Python as its experiment receipt. Other modes follow their exposed tools/policy.
For custom
modelling and verification, save the complete Python source with the linked
Sandbox's `write_sandbox_text_file` tool, check its returned byte count, and
run that `.py` file in a separate direct `python3` command. The file remains
in the Sandbox workspace for inspection and reruns. Avoid shell heredocs on
macOS Seatbelt; if a shell must invoke Python indirectly, select
`python_environment=managed`. Check stderr and generated files as well as the
command's exit code. A generated extxyz file must still pass the linked structure_inspect Skill,
then be converted by that Skill and imported with the current Atom Structure revision. Run packaged Python entries through
`run_skill_script`; the host selects their declared shared or private environment.
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
For custom modelling in team mode, Planner normally supplies `build` (construct
and independently inspect the candidate) and `deliver` (requested file/document
delivery). An initial `probe` is optional, only for a missing input that prevents
construction. The run-local
`advance_modelling_stage` tool checks observed script versions and successful
execution receipts. Build needs a fresh independent validation receipt for
structure outputs; document delivery needs import and matching read-back.
After each saved script finishes, Builder assesses it either in the next
`run_modelling_step.previous_assessment` or via `advance_modelling_stage(stage=observe)`,
with concise `hypothesis`, `measurement`,
`outcome` (`continue`, `ready`, `needs_plan_review`, or `blocked`) and `next_step`.
Omitted receipt fields, including `sandbox` and `script_path`, are filled from
the latest finished execution; explicit values must match that execution.
The host requires this receipt-linked assessment before new writes, execution
or advancing the stage. Authorized independent validation can collect the
candidate's receipt before the assessment. It is a Builder assessment, not independent
geometry acceptance. Builder chooses whether a contradiction needs Planner review.
After review, existing valid measurements can be reassessed under the new plan
revision without replaying commands. Numerical iterations should persist their
best state and stop/revert worsening or stalled updates as well as having a finite bound.
The host snapshot supplies remaining Run time and a closeout reserve (default
180 seconds, capped at 20% of the Run budget); this guides validation/delivery
or an honest unfinished report, without changing the selected output allowance.
Planner selects document, files, or report delivery from the original request;
analysis and file-only tasks do not require an unrequested document write.
Staged helper tools and their instructions are supplied only when the modelling
workflow is required. Typed operations use their native tool paths; direct
Builder mode returns results to the user without specialist completion tools.
The helper contract derives public signatures from the actual bundled functions
and supplies a runnable serialization example: `write_extxyz(path=..., atoms=...)`
returns the saved path, atom count and round-trip receipt. That receipt checks
serialization, not independent geometry acceptance.
The writer also returns its actual `layout`; reuse it in `artifact(...,
layout=write_receipt['layout'])` instead of inventing file/dataset names.
For costly calculations, `emit_measurements(..., report_path='measurements.json')`
optionally persists the full data as an ordinary Sandbox JSON file. A reporting
branch in the same saved script can load its `oaw_measurements` object, repair
only the envelope and re-emit it without recomputing the scientific result.
The host returns `saved_report_file` with malformed-envelope feedback when the
record supplies that reference. This does not validate or repair failed facts.
These checkpoints establish execution and delivery evidence, not physical validity
of a custom junction. Planner order is enforced before task dispatch because
ADK task-mode delegation can bypass normal tool callbacks. Explicit direct Builder
mode retains its single-agent flow and does not instantiate Planner.
Each probe/build plan includes a script artifact, a single question, an executable
experiment and a finite stop condition. Its `experiment.scope` is `input_measurement`
for probe and `candidate_work` for build. Probe addresses a missing blocking input;
these labels describe the checkpoint purpose rather than prescribing an algorithm.
Build can use several small saved/executed scripts or reuse intermediate arrays
and modules. The first build script need not finish the candidate; actual outputs
guide the next step. If subsequent design depends on an unknown quantity,
the first experiment computes it and saves a useful intermediate; later choices
follow that output. A concrete finite construction may still finish in one step.
Pending commands, unexecuted versions and execution errors
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
The step's question/expected_output/stop_condition can be inherited from the
current pending action or its latest completed step. Inheritance is restricted
to the same plan revision, stage, Sandbox and target; the HOST preview and SDK
required-field projection use the same full source. Different targets or stale
plans require their own fields, rather than copying a truncated preview.

Builder handoffs and host snapshots focus the current stage; acceptance checks from
all stages and the exact original request stay available. Planner/coordinator retain
the full plan. Unsigned delegation advice is normalized at the accepted-call boundary;
signed provider parts remain intact. The current phase narrows tools and assessment
schemas: required assessment fields are genuinely required, and rejection diagnostics
identify each missing/oversized field. Independent reads may be parallel, but batches
containing state-changing or unknown calls return a structured denial for those
call IDs, while independently authorized reads may execute. The model response,
including signed parts, is retained rather than regenerated wholesale. Unknown
tool names use the SDK error callback and native invocation archive. Writes still
require a single-call response or the existing dependent-step composites. A new failed execution or first assessment is decision progress;
repeated reads, identical-source/outcome reexecution and the same assessment do not
refresh that budget. Execution progress still does not prove task acceptance.

Algorithm assumptions are separate from user constraints. All original acceptance
checks remain in build/deliver.
Planner's `finish_task.result` is exposed as an object and encoded once for SDK
completion; legacy JSON strings remain accepted. Optional `finish_task.result.assumptions` are
exploratory hypotheses. Builder can return `status=needs_plan_review`, `reason`,
`sandbox` and `script_path` when actual execution contradicts the plan. The host
supplies its native command ID, exit status and observations to the coordinator, which
invokes Planner and resumes Builder. Each new review needs a new finished execution receipt; reviews
can revise current/remaining stages but cannot change completed stages or delivery;
the original request and existing receipts remain authoritative. Planner can read
authorized Skill guidance but cannot run generators or mutate structures.
Plan size limits are published in the host snapshot: up to 12 checks per stage,
prose targets of 400 characters per goal/check and 300 per experiment field,
with an independent 12,000-character total result bound. Invalid submissions
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
Tool declarations publish that hard bound. Exact edits share OAW's native schema
and executor (1–20 edits, nonempty old text, exact single matches); no second edit
policy is maintained. Omitted or empty input_artifacts both mean no inputs.
Assessment argument errors identify missing/invalid fields and their
previous_assessment paths before any saved source is replaced or executed.
Before saved source exists, advance_modelling_stage(stage='refine', reason=...,
next_experiment=...) can narrow the current action. It reuses the existing action
and dependency validator, preserves stage goals/checks and plan revision, and
records no execution, assessment or completion. Pending/unknown effects, review
and existing saved work must be resolved through their ordinary receipt paths.
Continuation uses the same authorized Skill and save/execute routes after native
Sandbox readiness; it does not impose a separate tool subset solely because an
action was restored. Inline previews carry received-result character counts and
truncation flags plus a native command result reference for argv/stdout/stderr.
Legacy completeness is unknown. Historical reads do not create new measurements
or confer validation, and unavailable native receipts must not be reconstructed.
The assessment's `command_id`/`sandbox`/`script_path` receipt fields are optional:
the host fills them from the latest finished execution and rejects only a
contradictory value, so an entire step (including its source) is no longer
discarded for echoing the wrong identifier. `advance_modelling_stage(stage=observe)`
fills omitted receipt fields the same way. Assessments retain the model's outcome,
next step and proposed experiment. Repeated evidence is a separate host advisory;
it neither rewrites a ready/continue judgment nor forces a Planner delegation.
Checkpoint validation still rejects failed checks, reported contradictions,
unverified files and missing read-back. Known question/expected_output/stop_condition
fields can be omitted only for the matching current subtask; the host fills them.
Once an assessment is recorded, step declarations omit its nested schema.
During probe/build, finish_task publishes status-dependent result shapes.
subtask_complete requires next_experiment and accepts only the optional native
receipt IDs; measurements and saved paths are host-filled. Review uses reason;
blocked/failed can carry limitations. Final-delivery instructions apply after
the requested delivery rather than to these local returns. Planner receives
the native Task Board operation names only when graph-authorized; Builder
does not inherit its task editing/control operations.
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
saved source with required full `source` and an optional reason; `rerun_modelling_step`
reuses saved bytes. Sample/search records the actual execution scope; a prior sample
is recommended, not required. A scope label alone changes no inputs or evidence.
Code must be in actual tool arguments, never only in reasoning text. Argument errors
return precise missing/invalid fields and focus the next tool declaration on that
contract. Repeated argument failures terminate with an unfinished native checkpoint;
they do not trigger scientific replanning or pretend Python executed. Missing patches, identical rewrites and cancelling edits never start code.
An unchanged failed source with unchanged arguments and observed environment
cannot silently rerun. Retired incremental_edit_hint settings are discarded
when loading old cards; they do not appear in new configuration schemas.
For document delivery, `run_delivery_step` runs validate → convert → import →
read-back as one action over the same native capabilities. With no new validation
parameters, it reuses build's independent receipt for the saved source path.
Identical explicit parameters also reuse the receipt; changed parameters require
new validation. The SDK tool name matches the instructions. Pending operations without
a waiter and unknown imports are retained without replay. It checks each receipt
before the next step and stops at the exact failed step without dispatching
later ones.
`advance_modelling_stage(stage='build')` completes the current build checkpoint;
it does not mean "enter build". SDK options are narrowed to current operations.
After independent validation, a `checkpoint_action` can identify the exact
completion call when existing execution/assessment/requirement gates pass. It
does not mutate the assessment, advance automatically, or satisfy failed checks.
The explicit structure path in an assessment and a unique current validation
binding identify the candidate even when the execution also emits labels or
reports. `next_action` and `checkpoint_action` reuse the same completion gate;
multiple independently validated candidates still require an explicit choice.
Completing build opens delivery tools. Staged helper guidance is only supplied
when those tools are registered; direct Builder keeps its native tool flow.
Its result includes the native import/read-back summary, bounded structure
overview and the actual confirmation scope. Reuse that read-back instead of
inspecting again for the same facts. Missing summary fields remain unknown;
file validation does not imply scientific revalidation of the read-back document.
Delivery options and SDK acceptance fields derive from the trusted bundled Skill
function signatures, the same metadata used by its CLI parser. Both validation
and conversion receive `--folder .` and the workspace-relative `--file-name`;
booleans receive explicit `true`/`false` values. Unknown keys, incorrect types and
conflicting input paths are rejected before dispatch with precise field errors.
Finite choices use the validator's shared `Literal` type: `distance_mode` is
`auto`, `finite` or `periodic` in the host schema, pre-dispatch checks and Skill
CLI. The host reads trusted type declarations without importing scientific
dependencies. Unknown values are invocation errors, not failed geometry checks.
CLI parse failures (`exit_code=2`) are interface blockers, distinct from scientific
validation failures, and route to an accurate unfinished report.
An input/dependency/unsupported error without a scientific verdict is reported
separately from measured violations; inspect the cause before changing the method.

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
in parallel. `validate_modelling_artifact` starts native Sandbox work and updates
a validation receipt, so it must be the only call in its response even though it
preserves the candidate file. Assessing an existing result requires no unrelated helper script.

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
prefix. The parser finds the last record across the complete native-bounded
stdout, even with trailing multi-line diagnostics. The last record is authoritative: malformed or duplicate fields
produce a repair diagnostic instead of silently reusing an older metric. Modern
`kind=candidate` records require a stable `measurement_revision` and nonempty
`constraints=[{name,passed,reason?}]`, plus `coverage={region:sample_count}`. These
counts describe tested populations, not defect counts. An empty measured region
cannot prove zero defects for a check that references it even when that check
says passed. An unused optional zero count does not fail unrelated checks.
Failed coverage or hard constraints prevent
an objective from counting as improvement or advancing build; passing self-reported
checks still does not replace independent validation. A changed measurement
definition starts a separate comparison series. Legacy objective-only records
remain readable.

Intermediate coordinate files can report `artifacts=[{path,units,frame,components,layout}]`
inside `oaw_measurements`. `frame` contains `origin` and three `axes` expressed in
the saved coordinate system, after translations; `components` contains stable
component names and nonnegative counts, with identities also persisted in the data.
Zero represents a genuinely absent optional component; negative counts remain invalid.
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
Completed native Skill JSON reports also register their workspace-relative output
paths and command IDs as inputs, including after native wait collection. These
receipts do not invent units/frames, count as a custom script execution or grant
candidate eligibility. Consumers still use the existing Sandbox file inspection;
missing or escaped files fail before the modelling command.

Executed `assumption_invalid=true` with a concise `contradiction`, repeated failed
constraints and unchanged diagnostic evidence generate host advisories. Builder
chooses continue, ready, review or blocked. Reported contradictions and failed
checks still prevent checkpoint completion; no heuristic changes the model's
recorded decision or selects its next experiment. The former
`diagnostic_limit` setting is discarded on loading. Accepted
Planner revisions preserve completed stages and requested
delivery. The original user request remains authoritative; review instructions
require keeping its acceptance requirements; reviews have no global count cutoff.

`working_decision` retains the last action, invalid candidate checks and rejected
methods in the existing Run evidence/checkpoint. For a justified next code action,
combine a brief `previous_assessment` with that action rather than drafting future
source in thought and then submitting only `observe`. Separate observation remains
available for stage decisions, review and independent validation. Host facts and
advisories inform the model; native archives retain the original exchanges.

The native-export comparator merges Sandbox history, SDK execution results and
capability receipt hints by command ID. Later running/redacted observations never
erase a completed result. It reports retained history coverage, command exit codes
and durations, returned-model timings by role/stage, interruption time and retry
waits separately. Missing usage or stages remain unavailable rather than inferred.

A small sample before an expensive finite search is recommended. Both new and
saved scripts may run a search directly with their declared finite stopping rule
and native command timeout. Reruns execute the saved path without rewriting it.
Native identity covers actual source version, argv, inputs and environment; a
scope label is not a new execution. Unchanged calls reuse the confirmed receipt;
a nonempty repeat_purpose explicitly requests a fresh stochastic/reproducibility
trial. Persist search outputs separately from postprocessing. Empty candidate sets
are measured results rather than successful structures.

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
unit) from actual output, or `assumption_invalid=true`. Native objective values
are kept separately from model-reported values. Stagnation hints never grant
acceptance or force review. An explicit Planner revision resets search tracking
without replaying experiments.

Builder and coordinator reuse OAW's native RequestWindow/ManagedContext services
for context reduction. Complete call/response exchanges, unresolved operations
and provider-signed history retain their protocol guarantees. Native invocation
archives keep full inputs, outputs and usage; the plugin does not add another
history compressor.

The default total model-request limit is null. Existing cards' exact former
32 default migrates to null; other explicit finite values remain enforced. The
cumulative output budget is disabled. Near a finite configured request budget the next action is a real
unfinished closeout. If a budget or a terminal harness guard is exhausted, the runtime emits a failed outcome
with native Run `execution_checkpoint` state (saved scripts, actual receipts,
validation/import state and unknown executions). This provides continuation
evidence; it does not automatically replay commands or restore an entire SDK
session. The exporter includes `execution checkpoint.json` when this state exists.
For unfinished probe/build work, continuation rechecks the existing command receipt
and reads the current saved source through authorized native tools. This includes
exit-zero commands whose diagnostic or candidate work was interrupted afterward.
The source becomes editable; historical measurements, acceptance and validations
are not promoted. An unchanged confirmed nonzero exit can retain its failed-command
identity for repair. Changed or legacy source remains editable without attributing
an old result to its current bytes. A further interruption retains the saved work.
Unknown effects, missing files, mismatched receipts and revoked access prevent
restoration; no command is replayed automatically. `read_modelling_source` is
projected only when a current native-confirmed source cache exists.

Common Builder guidance covers supplied-structure inspection and exact file
staging in direct and staged modes. Self-constructed inputs are identified as
such; preservation claims refer to that stated reference. Missing test inputs do
not add a mandatory probe or a material-specific construction rule.

During probe/build, actions are narrowed to the current readiness, experiment,
assessment or waiting phase. Builder retains relevant authorized inspection,
Skill-reading, execution and repair tools; unrelated delivery/team operations wait
for their phase. Stage/import guards and execution budgets still apply. Before a saved probe
exists, it also permits a short direct `python3 -c`
measurement. The defaults of two attempts and 1600 source characters are
advisory thresholds for saving reusable code, not execution cutoffs. Inline
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
The additional Builder request wall-clock ceiling is disabled by default:
`model_action_seconds=null`. Native OAW stream watchdogs and request retries
still handle silence and disconnects; active reasoning is not cancelled just
because it exceeds a fixed action duration. Total Run wall-clock limits are
also disabled by default: harness
`max_run_seconds=null` and the native OAW Agent setting
`run_execution_deadline_seconds=0`. Existing cards carrying the former
1200-second harness default migrate to null during validation; their missing
native setting uses the node default of zero. Other Agents keep the server
deadline. A finite custom harness limit remains opt-in. An explicitly configured
request ceiling does not establish whether reasoning is complete. After that
ceiling, one recovery is allowed for the current response, with at least the
ordinary request allowance; `action_recovery_seconds=null` inherits it. Recovery
applies only to that request; the next action uses the normal configured ceiling.
Hard-ceiling metadata records
whether content arrived and its idle duration without saving thought text.
Existing cards carrying the exact legacy 60/30 or 600/600-second default pair
migrate to null/null on config validation without direct database edits. Other custom
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
`script_execution_seconds`; HOST labels it default_script_timeout_seconds,
separately from hard request/Run limits. An explicit command deadline, including
one larger than this default, is preserved within the tool's declared bounds. Builder
instructions require finite iterations, advancing state and nonconvergence
output; a small candidate trial before a large scan is advisory. A longer, bounded experiment
can request a larger command deadline; extending it does not extend the Run.
Configure model action limits with `model_action_seconds`,
`action_recovery_seconds` and `model_requests_without_progress` in `harness`.
These action controls apply to staged team modelling; typed panel operations and
explicit direct Builder mode keep their existing behaviour. A model that still
cannot act ends with an explicit failure rather than an unbounded reasoning loop.
Output-limit recovery permits one report/action retry. Truncated tool calls never
dispatch. The selected OAW model sets the per-request output limit; reasoning may
consume that allowance on compatible providers. `specialist_output_tokens` and
`recovered_action_output_tokens` default to null, inheriting that limit. Explicit
role caps can lower it. There is no cumulative Run output-token budget, and token
usage does not shrink the next request's allowance.

Retired `harness.max_output_tokens` and `closeout_reserve_tokens` fields are dropped
when old cards load. The exact former 16384 specialist / 65536 cumulative / absent
or 4096 recovery default policy becomes inherited per-request limits. Customized
role caps remain supported. Request deadlines and the stream watchdog apply
independently of token limits.

Recovery replaces the prior host evidence snapshot while retaining user input,
tool calls, signatures and results. Identical unsigned `start_sandbox` calls in
one complete accepted response are coalesced before dispatch; other calls,
distinct arguments and later retries are retained.


## Measurement and task evidence


The Sandbox stdout contract is generated from the same Pydantic types used to
parse it. HOST measurement_contract retains compact diagnostic/candidate examples
and essential semantics. Its complete schema is generated into the bundled
structure_inspect Skill's references/measurement-contract.json, read through the
existing authorized Skill file interface. If a report graph has no Skill reader
or the equipped reference cannot be read, the host keeps the schema inline.
No second schema or storage system is maintained.
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
Requirements are labelled as Planner interpretations of the original request,
not user quotations. Optional stage method_checks hold method/checker diagnostics
and do not generate requirement IDs. Existing plans retain their checks, and any
reported failed measurement constraints remain failures; nothing is silently
reclassified to complete a candidate.
Every constraint with nonempty check_ids requires nonempty regions naming its
actual tested coverage populations; the generated schema descriptions and
compact contract both state this dependency.
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

The structure_inspect validator can independently check coordination in a
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

The bundled Skills share one maintained `skill_cli.py` source, injected as
`scripts/oaw_runtime.py` during SkillPackage assembly. Each materialized Skill
remains standalone. Structure-inspect provides optional topology, local-geometry,
symmetry and explicit-calculator instruments. Update external Skill
cards manually when using them instead of the built-in package. Interface
catalogue entries are unverified drafts; selecting one must validate and convert
the saved file before importing it.
