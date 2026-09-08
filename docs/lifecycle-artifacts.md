# Execution lifecycle and durable outputs

## Gap map (September 2026)

Existing contracts already separate Agent lifetime from Runs and Sandbox lifetime
from commands. Private equipment is explicit; container membership and ordinary
connections do not transfer ownership. Node deletion has a durable, ordered journal
and post-commit finalizers. Run attempts survive restart as interrupted attempts.

The gaps are cancellation cleanup without a durable outcome, silence-based tool
timeouts, summoning stop intent cleared on failure, and `artifacts` values that are
only metadata. Files currently belong to Text/Image cards or mutable workspaces;
neither is an immutable publication. File transfer is bounded to small JSON copies.

## Separate relationships

| Relationship | Contract |
| --- | --- |
| Capability/access | Current graph grants authorize each operation; IDs are locators. |
| Container membership | Organization/display only. |
| Lifecycle ownership | Private equipment and explicitly instantiated roots determine cleanup. |
| Execution lineage | Dependent child Runs inherit cancellation; detached Runs are owned by their Agent and retain their own cancellation endpoint. |
| Provenance | Historical producer and input descriptors, never an access or ownership grant. |
| Artifact retention | Explicit user retention independent of producer and display references. |

Stopping admission is reversible and prevents new work. Cancelling a Run requests
provider termination and settles the logical attempt; cleanup is tracked separately.
Completing a Run leaves its Agent alive. Stopping a Sandbox terminates its runtime,
preserving the workspace; completing a command also preserves it. Deleting a node
commits the graph mutation before irreversible cleanup. A cleanup failure is debt,
not a rollback of cancellation. Browser lifetime never owns execution.

Provider coroutines cannot be resumed after restart. Original attempts remain
interrupted; external effects whose termination cannot be verified are uncertain.
A new attempt may consume retained output, but never silently repeats an old one.

## Implemented lifecycle behavior

Run records carry a separate persisted `lifecycle` object: responsible Agent,
cancellation policy, local execution state, current wait/tool signal, capacity,
cancellation request, cleanup outcome and reason. Dependent children receive
cancellation; detached attempts have their own Agent owner and cancellation URL.
An already cancelled/failed/interrupted parent cannot admit dependent work.
Successful historical parents can still identify a later child attempt.

Cancellation first records intent and settles the logical Run. Provider cleanup
has a bounded join. A pending join remains tracked and a retry joins the same
operation. Failures retain their reason and block fresh Agent admission until
resolved. `complete` describes provider-owned cleanup, not proof that arbitrary
remote side effects have stopped. On restart, unavailable original sessions are
marked uncertain even if a new provider object is created. Cancellation never
changes the original attempt into a new execution.

Sandbox command receipts include their Run and stable history identity, so
command completion can be recorded after node deletion. Native stop/cancel intent
and failures persist in the existing command history and cannot age out while
unresolved. Failed termination closes command admission. Startup retries only
idempotent native termination using the backend's existing ownership manifests.
It never replays command argv.

Summoning persists stop/reclaim intent, the cleanup node set and error. It closes
Run, command and transfer admission for that family. Repeated reclaim is harmless;
interrupted reclamation is retried after node deletion recovery. Private equipment
is traversed by ownership; user objects placed in an instance are detached, and
shared connected resources survive. The existing ordered node deletion journal
remains the authority for physical deletion debt after graph removal.

Registered tool execution and explicit Run suspension bypass chat inactivity
failure, but retain an absolute execution deadline. Tool signals describe activity,
not proof of useful progress. Silence without a tool/wait signal is reported as
unknown liveness. Browser heartbeat remains transport information only.

| Setting | Default |
| --- | --- |
| `OPEN_AGENT_WORLD_RUN_INACTIVITY_TIMEOUT` | 300 seconds; nonpositive disables silence timeout |
| `OPEN_AGENT_WORLD_RUN_DEADLINE` | 3600 seconds per provider attempt |
| `OPEN_AGENT_WORLD_RUN_CLEANUP_TIMEOUT` | 10 seconds per cancellation join |

`GET /api/lifecycle` returns Run, command, instance, transfer and cleanup snapshots.
The activity panel and Agent workspace reload these on reconnection, and poll
while work is active. `POST /api/lifecycle/retry-cleanup` retries resource debt;
`POST /api/runs/{id}/cancel` retries Run cleanup. Provider events are refresh signals,
not the only copy of execution or output state.

## Published versions

`ManagedResourceStore.artifacts` extends the existing SQLite and `assets` storage.
`artifact_id` identifies an output across publications; `version_id` identifies one
immutable content version. A version contains a bounded manifest of file paths,
directory entries, sizes and SHA-256 checksums, creation timestamps, producer
names/IDs, Run ID, selected source paths and optional explicitly declared input
versions. Input version provenance captures the content checksum and is historical;
it does not retain or authorize those inputs. Producer names and IDs remain
interpretable after reclamation. Existing arbitrary `core.run.artifacts` values
remain metadata; they are not silently imported or made retention references.
Lifecycle snapshots derive managed published Run references from committed records.
Summoning results, work-source attempt snapshots and `WorkOutcome.artifacts` use
the same durable references, including after producer deletion.

Publication reserves the source Sandbox under the existing graph mutation barrier,
then releases that barrier for I/O. Commands, reconfiguration, stop and deletion
cannot race this reservation. Source access and destination publication authority
are independent live checks. Source reads use the existing pinned, no-follow
filesystem boundary, including the WSL worker path. Neither a host path nor a
hardlink becomes a published version.

The caller must explicitly declare inputs finalized and pause external writers.
The host records identities, sizes and change timestamps, checks every chunk, and
re-enumerates selected content before commit. Detected changes fail publication.
This is **not** a transactional snapshot of arbitrary concurrently mutating
directories: a finalized-input contract is required. Captured bytes are streamed
into a staging directory, flushed, checksummed and renamed before the ready record
commits. Restart never promotes an incomplete staging intent; it records failure
and retries staging removal. Startup leaves ready versions unchanged and does not
open or hash their content. The local control plane can explicitly run full verification
with `POST /api/artifact-collections/{collection_id}/versions/{version_id}/verify`.
It holds a consumption lease and reports `verified`, `corrupt` (size/checksum mismatch),
or `unavailable` (filesystem error). Diagnostic results do not rewrite lifecycle state
or remove bytes; temporary storage failures are never persisted as corruption.
Full streamed reads and materialization retain their existing checksum checks.

| Limit | Default |
| --- | --- |
| Internal transfer chunk | 256 KiB |
| Text preview | 64 KiB |
| Selected roots | 100 |
| Manifest entries | 10,000 |
| `OPEN_AGENT_WORLD_ARTIFACT_MAX_BYTES` | 1 GiB per version |
| `OPEN_AGENT_WORLD_ARTIFACT_STORAGE_BYTES` | 10 GiB reserved/stored content |

The internal WSL transport encodes individual bounded chunks; file bytes never
enter card documents, Run snapshots or WebSocket events. HTTP content downloads
stream to the browser's download handler. No whole-file Blob/base64 browser copy
is required for published downloads.

Idempotency keys are scoped to the publishing principal (`user` or Agent ID).
Reusing a key requires the exact same request, including destination and paths.
A live duplicate reports in-progress; a settled duplicate returns the original
record. Incompatible reuse is rejected. Failed or intentionally changed inputs
require a new key. Workspace edits do not change an existing version. Supplying
an accessible `artifact_id` publishes a new version of that identity.

## Access, retention and plugin API 1.12

A `core.artifact-collection` card holds **references**, not bytes or ownership.
Create one explicitly and grant `artifact.read`, `artifact.publish`, or
`artifact.manage` connections. The normal selector projection exposes one
`inspect_artifacts`, `publish_artifact`, `materialize_artifact`, and
`release_artifact` operation regardless of artifact count. Publication and
materialization independently require a Sandbox execute selector. The manage grant
allows collection reference changes through `manage_artifact_references`, never global
release. Adding a reference also requires current read access through a source collection;
knowing a version ID alone is insufficient. Only the trusted local user control plane
can release user-owned retention.
Inspection and consumption require a current reference in an
authorized collection. Guessing a version ID grants nothing.

Trusted plugins use `CapabilityContext.artifact_action` and the public
`ArtifactPublish` / `ArtifactMaterialize` request contracts. They receive references
and bounded previews, without database connections or unrestricted filesystem paths.
Optional `inputs` descriptors must identify versions the publisher can currently
read, and are rechecked at publication commit. These are provenance, not new grants.

Publishing establishes user retention outside the temporary producer. Removing a
collection or reference never deletes retained bytes. The local user's retained
listing can restore references into another collection; Agents cannot use that
control-plane listing to bypass graph grants. Release explicitly drops retention,
records deletion intent, prevents new reads, and removes bytes idempotently. Active
read/copy leases cause release to return a retryable conflict. No garbage collector
or canonical writable Sandbox mount is introduced.

`GET /api/artifacts/retained` lists only ready versions with active retention, including
those with no collection references. `GET /api/artifacts/history` provides all historical
records, including staging, failed, released and deleted versions. No migration or
reference-count-based retention rule is introduced.

Materialization creates a new destination directory exclusively and streams a
working copy into it. It never overwrites an existing directory or maps canonical
storage as writable content. Revocation is checked at each chunk and admission/
commit boundaries; it aborts further authorized transfer. It cannot erase bytes
already legitimately downloaded or copied. Interrupted copies may leave an
incomplete working directory; inspect/remove it explicitly and use a fresh
destination. Only publication is an immutable atomic result operation.

The Sandbox file tree supports explicit file/directory selection and publication.
The collection workspace shows state, content, checksums, provenance and retention,
with separate remove-reference and release-content actions. No per-file cards are
created. Ready means captured bytes are complete and accessible under authorization;
domain validity and scientific verification belong to plugins.

## Migration and verification boundaries

Startup adds `runs.lifecycle_json` with an empty default for old records and creates
`artifact_versions` / `artifact_references` if absent. Existing Run statuses,
Text/Image resources, workspace files and arbitrary artifact metadata remain usable.
There is no destructive data rewrite. Plugin API advances from 1.11 to 1.12 with
additive request/context contracts. Existing compatible plugins remain supported.

Tests include an actual killed backend process at publication, committed output,
deletion and reclamation boundaries; combined summoned-Run publication and private
workspace reclamation; large binary/bundle integrity; mutable source isolation;
concurrent requests, transfer/deletion exclusion, permission revocation, path and
hardlink attacks; cleanup failure/retry; quiet tools, deadlines and detached Runs.
The Playwright flow publishes through the file view and restores retained state
after producer deletion and browser reload. A separately enabled real Windows
AppContainer test generates, publishes, reclaims and consumes an output.

No arbitrary Python coroutine/provider session resumption, external-job reattachment,
remote-side-effect cancellation guarantee, automatic GC, writable canonical mount,
or concurrent-writer filesystem snapshot is promised. Native Linux/WSL isolation
requires its separate opt-in acceptance environment; mocked runtime tests do not
prove an OS security boundary.

Verified on 2026-09-08:

- Complete backend suite: **503 passed, 19 skipped**; after result-reference
  integration, artifact/work-source/task-board/summoning regression: **41 passed,
  1 native test skipped**.
- Frontend suite: **190 passed**; TypeScript and Vite production build passed.
  The existing bundle-size advisory remains.
- `frontend/e2e/artifacts.spec.ts`: **1 passed**, with rendered screenshot review.
- Explicit `OAW_TEST_SANDBOX_RUNTIME=windows` acceptance: **1 passed** under the
  normal host account. The restricted test account could not create an AppContainer;
  that preflight failure was not counted as native validation.
- Additive old-database migration, five killed-process restart boundaries,
  adversarial transfers and cleanup retry tests passed. `git diff --check` passed.
- Native Linux/WSL acceptance was not run; the suite's opt-in/platform skips remain
  separate from the successful Windows native flow.
