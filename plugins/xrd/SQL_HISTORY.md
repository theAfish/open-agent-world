# XRD SQL run archive (schema v1)

Connect the XRD match card to one existing `data.sqlite` card with
`xrd.history-store`. The relationship grants no arbitrary SQL capability.
The existing SQL card owns `database.sqlite3`; XRD never recreates a missing file.
Multiple bindings are rejected when accessed. Removing the edge revokes SQL
archive access; unbound workbenches retain the legacy filesystem history.

## Tables and views

- `xrd_schema`: independent XRD schema version (not SQLite's DDL schema counter).
- `xrd_runs`: `(owner_id, run_id)` primary key; stage, status, timestamps, complete
  provenance manifest. Latest status is updated atomically with records/files.
- `xrd_records`: content-addressed, append-only versioned records: status,
  progress state, Jev decision reservation, measured evaluation, joint review,
  complete result snapshot, and validated Agent report. Reimport is idempotent.
  `actor_id` comes from the host/harness, never a model-provided identity.
  BO evaluations are explicitly attributed to `BO_baseline`.
- `xrd_artifacts`: exact original file bytes, relative name, SHA256 and length,
  keyed by owner/run/name. Working files are still used by numerical workers;
  once bound, history reads its files and run metadata from SQL. Review retries
  also have immutable `review-history/<started_at_ns>/...` artifact names.
- `xrd_evaluations`, `xrd_decisions`, `xrd_agent_reports`: queryable typed columns
  over the versioned records. Rwp/Rp columns explicitly use percent units.

Example (use column selection, not `SELECT *` on binary artifacts):

```sql
SELECT run_id, trial_id, candidate_ids, score, rwp_percent, converged
FROM xrd_evaluations WHERE owner_id = ? ORDER BY recorded_at_ns DESC;
```

Snapshots retain all result values. Large curves and CIFs are omitted only from
history UI record summaries, and remain available in archived files. Scientific
nonconvergence and uncertainty are never converted to success claims.

## Agent/Jev contract

Jev retains its validated TypeSafe legal-action/selection-token/construction-path
contract. The harness, not the model, attaches run ownership and records the
actual decisions and evaluations using schema v1. No SQL-generation prompt is
used and BO evidence is not exposed to Jev during its search.

Ordinary analysts receive the `xrd.results-report` relationship, which grants
both reading and validated report submission. Existing `xrd.results-read` edges
are upgraded in place because OAW permits only one edge per source/target pair. They call
`xrd_read_results`, then `xrd_submit_report` before their final factual summary.
The read tool returns the schema and owned run IDs / archived evidence filenames.
The reporting tool validates this strict object (extra fields are forbidden):

```json
{
  "schema_version": 1,
  "run_id": "owned-run-id",
  "summary": "Evidence-based analysis",
  "evidence": ["result.json"],
  "limitations": ["Fit residual does not establish phase identity"],
  "conclusion": "inconclusive"
}
```

`conclusion` is `supported`, `inconclusive`, or `failed`. Evidence references must
exist in this run's SQL archive. The host checks the current capability grant and
assigns actor/record IDs; repeat identical submissions are idempotent. Validation
cannot prove a model's scientific claim: the evidence and limitations stay visible.
Natural-language conversation remains in OAW conversations, while the structured
analysis report is the XRD SQL output. A model which omits the tool has **not**
archived a report; prompts must not claim otherwise.

## Existing data and recovery

After backup, call the match card's `history_migrate` resource action with `{}`.
It imports only owned run directories, skips foreign records, and commits one
run at a time. Cancellation/failure leaves earlier commits usable; retry safely.
It does not run scientific workers or synthesize old Agent reports. Do not delete
working directories: current workflow controls still use them. Missing SQL files
and incompatible schema versions fail explicitly. The history UI labels running
records without a live worker as interrupted without altering the raw evidence.

`history_list`, `history_inspect`, `history_records`, and `history_file` power the
copper/green history dialog. Record and run lists are paged. File previews and
downloads have a 30 MiB API limit; larger files remain stored in SQLite.

This connection configures a specific Legion. SQLite database contents are not
bundled in reusable XRD templates; each instantiated workbench binds its own card.

### Starting a new workflow

Dropping or selecting a spectrum on the workspace canvas opens a theme-aware confirmation dialog. Confirming imports the validated spectrum, preserves library/analysis settings, and persists a new workflow time boundary on the match card. Current search, single-phase frames, multiphase selection, and results-discussion evidence are scoped to that boundary; previous run files and SQL history remain available. Cancel leaves the current input untouched. Active calculations block replacement. A backend capability check prevents a newer frontend from silently resetting against an older backend. Reusable templates omit the workflow boundary.

The XRD workspace follows the OAW light/dark theme, including parameter panels, spectrum canvases, popups, history, and PNG background/line colors.
