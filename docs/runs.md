# Runs and runtime providers

Open Agent World keeps four execution concepts separate:

- **Agent** is the persistent actor and capability-bearing world object.
- **Work item** is a plugin-owned unit of work, such as a Task. One item may have several Run attempts; its acceptance rules belong to its plugin.
- **Run** is one durable execution attempt by one Agent.
- **External Job** is a future long-running operation started during a Run.

The persisted `RunRecord` is authoritative. Provider events are live output,
not lifecycle storage. A provider turn ending also does not imply that the Run
succeeded: a provider must explicitly emit a terminal `run_status`. If its
event stream ends without one, `RunManager` leaves the Run in `waiting`.

Run working data is deliberately separate from `RunRecord`. Every new Run owns
a fresh durable `run:<run_id>` state scope for its input, progress, scratch data,
intermediate results, and result. The provider receives a typed `StateContext`
whose Run scope is most local; see [Runtime state](state.md).

The host also persists the latest provider text as `output_text`, so execution
result handoff survives restart. Plugin-dispatched Runs use `caller_kind="work"`,
the batch ID as `caller_id`, and `<source_node_id>:<item_id>` as `task_id`.
See [plugin work-source execution](execution.md) for scheduling and recovery.

## Lifecycle

Valid transitions are centralized in `RunManager`:

```text
created -> running
created -> cancelled
running -> waiting | succeeded | failed | cancelled
waiting -> running | succeeded | failed | cancelled
```

`succeeded`, `failed`, `cancelled`, and `interrupted` are terminal. On backend
startup, persisted `created`, `running`, or `waiting` records from the prior
process are marked `interrupted`; they are never silently reported as success.

Agent card status remains operational. A failed Run returns the Agent to
`idle`; it does not set the Agent to `error`. Run status and execution
occupancy are independent: changing a Run to `waiting` retains its Agent slot
by default. `max_concurrent_runs` is an explicit per-Agent configuration policy
(default `1`) and counts Runs holding execution capacity, regardless of their
Run status.

`TOOL_STARTED` and `TOOL_COMPLETED` are activity events only. A short tool call
does not change Run status or release capacity. Long-running work must make an
explicit suspension decision:

```python
await run_manager.suspend_run(
    run_id,
    reason="external_job",
    release_agent_slot=True,
)
```

A waiting Run may instead retain its slot by leaving `release_agent_slot`
false. Resuming a released Run through `waiting -> running` reacquires a slot
and is subject to the same concurrency policy as a newly started Run.

Execution-turn synchronization is also separate from durable completion:

- `wait_execution(run_id)` returns when the current provider coroutine ends,
  even if the Run remains waiting.
- `wait_terminal(run_id)` returns only for `succeeded`, `failed`, `cancelled`,
  or `interrupted`.

## Provider event-stream inactivity policy

`RunManager` currently applies a provider event-stream inactivity policy. If a
stream produces no events for the configured window, the Run is transitioned
to `failed` with an explicit error instead of stalling silently, and
`stop(run_id)` is called on the provider. Stream silence is an operational
heuristic, not proof that the provider's underlying task failed. This temporary
policy should be replaced once providers expose a formal liveness/heartbeat
contract. The default window is
300 seconds; it can be changed globally with
`OPEN_AGENT_WORLD_RUN_INACTIVITY_TIMEOUT` (non-positive disables it) or
per Agent with the `run_inactivity_timeout_seconds` card configuration key
(non-positive disables it for that Agent). Each provider event resets the
window, so long multi-step runs are unaffected as long as they keep reporting
activity through the normalized event stream.

## Conversation outcomes

Every Run started from a conversation ends with a durable transcript entry.
A successful Run with text persists a normal agent message; failures,
cancellations, interruptions, suspensions, and empty responses persist a
`system` message describing the outcome. Synchronous delegated turns
(conversation handoffs and agent-to-agent communication) never block on a
suspended Run: if the delegated provider turn ends without a terminal status,
the delegated Run is cancelled and a clear error is returned to the caller.

## Lineage and cancellation

A root Run points `root_run_id` to itself. A child stores its immediate
`parent_run_id` and inherits the root, task, and context correlation when those
values are not overridden. Invocation code running inside a provider receives
an immutable `InvocationContext`, so later delegate/spawn/controller features
can create descendants without passing application services or provider SDK
objects through the boundary.

Cancellation addresses a `run_id`. By default, cancelling a parent recursively
cancels every non-terminal descendant. A nested caller can explicitly start a
detached root Run when that propagation is not desired. `POST /api/agents/{agent_id}/stop`
remains a convenience that cancels all non-terminal Runs for that Agent.

## Runtime providers

Providers are registered on `PluginRegistry` under namespaced IDs such as
`google.adk` and `core.mock`. `RunManager` resolves the Agent card's optional
`runtime_provider_id`, falling back to the configured application default.
Provider instances are cached per provider ID, not globally assumed to be the
only runtime in a world. Provider SDK sessions, runners, and events remain
inside their provider implementation.

Run inspection and cancellation are available at:

```text
GET  /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/children
POST /api/runs/{run_id}/cancel
```


## Conversation timeline and sessions

A Conversation contains named groups, each with independent sessions. A session
retains its participant roster and its own runtime context. Creating a new
session in the UI copies the current roster; it does not change historical
session membership or graph authorization. New sessions start as `New session`;
the first user message supplies a whitespace-normalized title of up to 60
characters. `PATCH /api/conversations/{id}/sessions/{session_id}` accepts a
manual `title` (up to 200 characters). Renaming does not change group identity
or the default session's deletion protection.

Provider-visible text and tool start/completion records in Conversation runs
are committed to `conversation_messages` before notification. Tool argument and
response details are retained; provider-private reasoning is not projected.
Each record has an immutable ID and a per-session increasing `sequence`.
Final replies reference the originating text record through the Run's
`output_message_id`; finalization marks that record instead of duplicating or
matching its content. Live graph and session participation checks also apply
to intermediate records, including delegated runs.

`GET /api/conversations/{id}/sessions/{session_id}/timeline` returns `items`,
`has_before`, `has_after`, and `active_agent_ids`. Pass either `before` or `after`
with a sequence cursor; `limit` defaults to 50 and is capped at 100. With no
cursor it returns the newest page in ascending order. The existing `/messages`
endpoint remains the final-message transcript for runtime context and existing
clients; it does not become a dump of tool activity.

The UI keeps a contiguous window of at most 150 records, loads 50 at a time in
both directions, preserves a visible message anchor when shifting windows,
and offers Jump to latest. Discarding a UI page never deletes database history.
WebSocket events invalidate the durable view; periodic tail reads and reconnect
reads repair missed notifications. Session/request generation checks reject
late responses belonging to another selection. These display limits are
separate from provider context-window policies.

Schema migration runs transactionally, maps each legacy session to its own
group, and keeps existing session IDs, message IDs, timestamps and Run scopes.
It can retain previously saved messages, but cannot reconstruct intermediate
events that older versions only broadcast and never stored.


Outgoing user messages appear immediately with a pending delivery indicator.
The optional UUID `message_id` on POST is preserved as the durable record ID,
so a WebSocket/REST update arriving before the POST response reconciles the
same bubble. An existing ID is rejected with 409 rather than overwriting a
record or starting another turn. This is identity correlation, not an automatic
retry protocol. Unconfirmed sends remain visible with their text; responses do
not clear a subsequent draft. Title updates do not gate rendering. Timeline
invalidations arriving during a fetch are coalesced and drained after that fetch
instead of being dropped until the next polling interval.
