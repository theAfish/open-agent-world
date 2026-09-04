# Runs and runtime providers

Open Agent World keeps four execution concepts separate:

- **Agent** is the persistent actor and capability-bearing world object.
- **Task** is a future work contract. A Task may have several Run attempts.
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
