# Runs and runtime providers

[Documentation](README.md)

Open Agent World keeps four execution concepts separate:

- **Agent** is the persistent actor and capability-bearing world object.
- **Work item** is a plugin-owned unit of work, such as a Task. One item may have several Run attempts; its acceptance rules belong to its plugin.
- **Run** is one durable execution attempt by one Agent.
- **External Job** is a future long-running operation started during a Run.

The persisted `RunRecord` is authoritative. Provider events are live output,
not lifecycle storage. A provider turn ending also does not imply that the Run
succeeded. The provider contract is explicit: a provider turn must end with
either a terminal `run_status` event or an explicitly registered
suspension (`suspend_run`). If the event stream ends with neither,
`RunManager` fails the Run with a provider protocol error. `waiting` therefore
always means a deliberate, explainable wait with a recorded reason and a
resume/ownership mechanism — never "the provider stopped producing events
and we don't know why".

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
explicit suspension decision before the provider turn ends:

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

`RunManager` applies two distinct liveness rules:

- **Exhausted stream**: a provider turn that ends without a terminal
  `run_status` or an explicit suspension fails immediately with a provider
  protocol error (see above). This is a contract violation, not a timeout.
- **Silent open stream**: if a still-open stream produces no events for the
  configured inactivity window, the Run is transitioned to `failed` with an
  explicit error instead of stalling silently, and `stop(run_id)` is called on
  the provider. Stream silence is an operational heuristic, not proof that the
  provider's underlying task failed. This temporary policy should be replaced
  once providers expose a formal liveness/heartbeat contract.

The default inactivity window is
300 seconds; it can be changed globally with
`OPEN_AGENT_WORLD_RUN_INACTIVITY_TIMEOUT` (non-positive disables it) or
per Agent with the `run_inactivity_timeout_seconds` card configuration key
(non-positive disables it for that Agent). Each provider event resets the
window, so long multi-step runs are unaffected as long as they keep reporting
activity through the normalized event stream. Active tool execution and
explicitly suspended Runs are exempt from the inactivity window.

## Model streams and recovery

The built-in ADK runtime and ADK-based plugins can use OAW's shared model
observation layer. It emits model-request status and, only when the provider
actually supplies them, bounded live `model_reasoning` previews. An interrupted
preview is marked incomplete, not presented as a final answer. OAW does not
expose reasoning that the model provider keeps private.

ADK model requests retry a transient failure once inside the same role and
delegation, with the same prepared input and tool results. Partial responses go
only to the preview; ADK receives the aggregated response after clean stream EOF.
Thus a failed attempt cannot dispatch its tool calls. Previously completed tools
are not re-executed, even when earlier requests wrote files. This regenerates the
current response; it does not resume provider-hidden thinking or restart the
coordinator. The LiteLLM adapter's additional transport retries are disabled
under this boundary so budgets do not multiply. Output token settings are unchanged.

The idle watchdog allows 300 seconds before first content, or 90 seconds since
the last real content (empty keepalives do not count). This is not a total thinking
deadline. Recovery events expose role, request, attempt, exception type, HTTP status
when available, local-watchdog versus provider origin, content count and idle time,
without logging arbitrary exception bodies or credentials. A second failure stops
instead of restarting the entire Agent. Manual Stop cancels the active request.

For errors outside that request boundary, the conservative capability-receipt
reconciliation remains: unknown or completed writes are never blindly replayed.
Sandbox command IDs, workspace hashes and document revisions provide evidence
for a user-reviewed continuation, not permission to repeat side effects. Persisted
checkpoints are diagnostic context, not process-restart recovery of an active stream.

The TypeSafe Jev adapter is a non-streaming, structured decision API. Its
decision-only HTTP request gets bounded transient retries, but it does not
produce model-reasoning events or use the ADK stream restart path.

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

Provider-visible text in Conversation runs is committed to
`conversation_messages` before notification. Successive snapshots with the same
`provider_message_id` update that message in place. Distinct messages remain in
the timeline, including text emitted before a follow-up from the user. Tool
start/completion details remain in the Run trace; provider-private reasoning is
not projected.
Conversation uses one chronological projection for live and restored activity.
Consecutive content from one recorded Agent shares a single speaker header.
Each message or public progress entry retains its own chat bubble; execution
groups and Run controls sit between or after those bubbles. A different speaker
starts a new turn; display names alone never establish shared identity.
Adjacent tool calls in the
same Run form an execution group; messages and other Runs separate groups.
Results update the original tool row. A running group has a bounded scrolling
viewport that follows activity until the reader scrolls away. The latest group
stays open while the Agent narrates or awaits its next model response. Groups
collapse by default when a newer execution group begins or the Run terminates,
and can be reopened. Unfinished calls remain visible across user interruptions. Run termination
never relocates activity underneath the final reply.

Successful Runs expose `final_message_id` in the timeline summary, recorded when
the final response is persisted. `is_final` only seals an individual streamed
message; it does not identify the final response. Once that response is visible,
the preceding process is collapsed as a single work log before the response.
User messages, speaker changes, and other Runs remain chronological boundaries.
Paged-out final replies retain the visible process with older activity available
on request. Failed/interrupted or legacy history without this explicit relation
uses the recorded Run start and does not treat the last narration as a final reply.

Tool activity retains separate start and completion timestamps when merging
events, including completion-before-start repair from REST. Each tool displays
its own elapsed time; a group displays its wall-clock span (parallel calls are
not added together). Missing timestamp pairs do not invent durations. Automatic
live previews stay compact and scroll, while manually opened groups, archived
groups, and open tool details use the full transcript height for reading.

All tool rows display only the resolved tool name, status and elapsed time.
Wrapper names, capability IDs and argument previews are omitted from the UI.
Available input, output and errors use continuous hierarchy tables. Parent fields
span their descendant rows, each nesting level gets a column, and values share
the final column. Arrays use numbered rows in the same table. There are no inner
disclosures or collection-type groups. Narrow panes show each full field path
above its value to avoid squeezing deep hierarchies into tiny columns.
Empty sections are omitted; calls without details have no disclosure.
There is no raw JSON view or placeholder for unrecorded data.
The dependency-free `open_agent_world.tool_activity` projection
bounds and masks tool evidence in isolated workers and again before host
publication. MatCreator resource tools include their results; internal executor
reports retain only an outcome summary, and private reasoning is excluded.
System notices have a separate label and body. Background monitoring handoff
notices record request keys, providers, external IDs and OAW job IDs so users can
locate the work in Jobs; these identities describe the handoff, not live status.

The bounded Run checkpoint (`tool_trace`, retained for API compatibility) stores
the latest 50 tool/public-progress events, including `status`, `plan`, and
`reasoning_summary`. `tool_trace_truncated` explicitly marks omitted earlier
events. The display cache retains up to 200 merged rows. These are activity
previews, independent of the durable `run_activity` directory. Every public
tool call/visible progress event is retained there; native tools and provider-exposed
thinking are indexed by their existing Run archive identities. Conversation
timeline responses omit tool bodies. Opening **Work log** reads one metadata
page (`GET /api/runs/{run_id}/activity`, default 50 rows), and **Load more activity**
requests the next page. Only opening a specific tool/thinking row reads its body.
Native invocation/thinking bodies are selected individually from Run state;
public-only calls retain their sanitized payloads, including existing payload
truncation markers. Body and directory writes for native records are atomic.

Existing native archives and retained public checkpoints are indexed on first
access without altering the original records. Older checkpoints cannot restore
events that were never persisted; the directory reports incomplete coverage when
the recorded call count exceeds available history. A successful final reply has
a collapsed Work log before it even when the browser has no live activity cache.
Messages and live activity are ordered first. At the recorded final reply,
contiguous preceding narration is folded in place with the saved work log.
Folding never re-sorts the timeline or crosses another actor's live activity.
A paged-out final reply does not fold the visible process.
Failed/interrupted Runs also retain a Work log. Active Runs repair their latest
50 directory entries, including thinking, without fetching historical pages.
Active Runs use the existing tool groups, with no additional Work log or refresh
control. When the live cache omits earlier rows, the first tool group's disclosure
offers paged loading into the same chronological transcript. Runtime ticks with
no display text are excluded before paging and rendering, including old indexed
ticks, so they neither create empty bubbles nor split adjacent tool calls.
Streaming thinking opens automatically and respects manual collapse; completed
thinking bodies in history load only on disclosure. Codex forwards the app-server's
readable reasoning summaries, including section boundaries and final snapshots;
it does not forward raw reasoning blocks. No content is invented when a provider
does not expose thinking. Grouping does not create runtime phases or
infer dependencies. Historical message windows retain their associated Run
summaries and restrict activity to the visible time range.
Each record has an immutable ID and a per-session increasing `sequence`.
Final replies reuse the Run's `output_message_id` when it already contains that
reply; a separate completion reply gets its own row. Live graph and session participation checks also apply
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

Addressed follow-ups are durable immediately. During an active Run in the same
session, ADK consumes them just before its next model call, after available tool
results. Codex uses `turn/steer`; MatCreator's native planner and executors receive
updates in their model callbacks. These paths continue the current Run. A new
Run in the same session is not admitted just because the Agent has spare capacity.
Inputs arriving after the last model boundary, or targeting a provider without
live-input support, remain queued for a subsequent Run.

Runtime adapters read `pending_conversation_input(agent_id, run_id)` from their
`AgentCapabilityProvider` and call `acknowledge_conversation_input(...)` with
message IDs only after including or accepting the input. Reads and acknowledgements
check the current Run, session membership and graph connection again. Inputs for
other sessions or Agents are never returned. Optional `RuntimeProvider.notify_input`
wakes event-driven adapters; it is not a delivery acknowledgement.
