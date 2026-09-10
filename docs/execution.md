# Plugin work-source execution (API 1.3)

[Documentation](README.md)

OAW is a plugin + Agent platform. Task boards, approval queues, staged research,
and domain workflows should share execution infrastructure without sharing one
business model. The Task Board plugin is one adapter for this infrastructure.

## Responsibilities

| Layer | Owns |
| --- | --- |
| Host Run manager | Runtime provider selection, Agent capacity, Run lifecycle, cancellation, durable output |
| Host node execution | Admission, live connection checks, one batch per source, bounded scheduling, attempts, stop and recovery |
| Plugin | Work identities, readiness, prompts, executor selection, result interpretation and acceptance |
| Legion | Membership, shared settings/state and portable composition; not a second scheduler |
| Frontend | Common controls/history and optional specialized document editors |

A plugin's `NodeTypeDefinition.execution` accepts a public
`NodeExecutionDefinition`. It requires a `document` and three pure callbacks:

```python
from open_agent_world.plugin_api import (
    ExecutionPolicy, NodeExecutionDefinition, WorkItem,
)

def items(value):
    # This is a review queue, with no DAG or Task Board schema.
    return [WorkItem(
        id=entry["id"], prompt=entry["request"], agent_id=value["reviewer"],
        ready=entry["stage"] == "queued",
        retryable=entry["stage"] == "failed",
    ) for entry in value["entries"]]

def apply_outcome(value, outcome):
    # Return a new JSON document. The model validates it before persistence.
    stage = {"running": "working", "succeeded": "awaiting_acceptance"}.get(
        outcome.status, "failed")
    return {**value, "entries": [
        {**entry, "stage": stage, "run_id": outcome.run_id,
         "result": outcome.text, "error": outcome.error}
        if entry["id"] == outcome.item_id else entry
        for entry in value["entries"]
    ]}

execution = NodeExecutionDefinition(
    items=items,
    apply_outcome=apply_outcome,
    policy=lambda value: ExecutionPolicy(max_parallel=1, pause_on_failure=True),
    executor_relationship="example.review.executor",
    control_capability_kind="example.review.control",  # optional
)
```

Register the named relationship in the same plugin with the work-source node as
source and a `core.agent` target. This is dispatch authority, independent from
any Agent-to-document access edges. If using Agent controllers, register the
control capability handler and grant it separately. Its handler calls
`context.node_execution_action(capability, action, arguments)` with `read`, `start`
or `stop`. Plugins do not receive the database, service container, or RunManager.

`WorkOutcome` carries item ID, Run ID (possibly absent after interrupted admission),
status, text and error. Callbacks must be deterministic and idempotent for the same
outcome: recovery may redeliver an outcome after persistence was interrupted.
Persist a processed Run ID in the document when acceptance includes accumulating
or incrementing values. Run success does not force plugin acceptance. The example
enters `awaiting_acceptance`; the bundled Task Board instead enters `done`.

## Host APIs and execution behavior

```text
GET  /api/nodes/{id}/execution
POST /api/nodes/{id}/execution/start {expected_revision, item_id?}
POST /api/nodes/{id}/execution/stop
```

The snapshot contains batch status, active flag, authorized executor choices,
work readiness metadata (without prompts), and recent attempts. Start requires
the current document revision. Omit `item_id` to repeatedly dispatch ready work;
provide it to execute/retry only that item. A batch dispatches each ID at most once.
The plugin may expose newly generated work after processing outcomes.

The host allows 1–8 concurrent Runs per source and serializes items assigned to
the same Agent. Agent admission limits still apply across sources. At most 1000
items may be returned and 1000 attempts dispatched per batch. Starting another
batch retains the previous 200 attempts; the current batch's attempts remain
visible, and durable Run records are kept separately. The built-in history UI
shows the latest 30 entries.

The host records `caller_kind="work"`, a unique batch caller ID, and a namespaced
`task_id=<source_node_id>:<item_id>` on each Run. Before awaiting Run admission it
persists an attempt reservation. Restart can therefore recover a Run even if the
process stopped before linking its ID to the attempt. Terminal outcomes that were
not applied to the document are reconciled. Remaining incomplete work is marked
interrupted; nothing automatically restarts.

Execution ownership is explicit: a controller starts a detached batch and can
query/stop it through its granted capability. Its own Run ending does not cancel
the batch. Permission revocation prevents new dispatch; a dispatch error stops
the batch and settles its in-flight Runs. Normal work failure with
`pause_on_failure=True` lets in-flight peers finish but admits no new work.

For this first version, active execution locks document edits and source deletion.
Stop before changing the plan. Agent unavailability is surfaced as a batch error,
not silently retried. Retry and restart require an explicit request. External job
polling, persisted timers, automatic retry/backoff, live replanning, and distributed
workers are intentionally not implemented by this local execution service.

## Reuse and user experience

Simple use needs only an executor, concurrency choice, and failure behavior. A
plugin without execution remains fully usable as a document resource. The host
catalog exposes `has_execution`; executable plugins without a specialized view
receive a generic workspace with run/stop/retry and history controls. Specialized
views can reuse `useNodeExecution` and `NodeExecutionControls`.

Legion templates capture plugin documents via `capture` and remap internal node
references via `remap_references(value, ids)`. Template capture uses live IDs to
template keys; instantiation uses template keys to new IDs. External references
must be cleared. Execution history and Run IDs should not be copied into presets.
No material-science-specific state, tool name or dependency rule is built into
the host contract.
