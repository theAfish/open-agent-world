# Tool failures and Run control

A tool invocation may fail without failing its calling Agent Run. A tool result
is an observation for the model, not a Run lifecycle command. RunManager remains
the sole authority for Run transitions. Do not weaken its handling of provider
crashes, protocol errors, missing terminal events, or explicit cancellation.

## Shared model-facing boundary

`backend.agents.tool_execution.execute_tool` owns invocation and result encoding
and returns a `ToolOutcome` (`succeeded`, `failed`, `cancelled`, or `timed_out`).
ADK's generated functions and Codex's dynamic-tool bridge use this same policy.
Plugin adapters can import it from
`open_agent_world.plugin_api.tool_execution`. This is the required pattern for
new model-facing adapters, rather than a new provider-specific exception list:

```python
outcome = await execute_tool(
    lambda: capabilities.invoke_tool(agent_id, capability_id, arguments),
    serialize=provider_tool_encoder,
)
# Return outcome.response through the SDK's tool-response channel.
# For SDKs with a success flag, use outcome.ok, not "no exception was raised".
```

Keep authorization and dispatch inside the operation. The broker still performs
live permission checks; it is not bypassed, cached, or moved into the model.
Low-level capability APIs used by host services retain their exception contract.
A deterministic workflow controller may explicitly stop when a mandatory step
has no valid result. That policy is distinct from accidentally unwinding a
model's reasoning loop because a plugin raised TypeError.

Successful payloads retain their existing format, including image content.
Explicit `ok: false` feedback remains failure feedback. DomainError retains its
public code, type, and message. Unexpected ordinary exceptions and invalid
plugin outputs become sanitized error envelopes with an error_id; the complete
traceback is logged for operators under that ID, not sent to the model or UI.
ADK validates ordinary JSON payloads and visual metadata before returning to its
runner, so serialization failures are contained before session/model encoding.
Failure to encode even the safe envelope remains an adapter/protocol fault.

No automatic retry is introduced. Failed, cancelled, or timed-out operations may
have performed external side effects; inspect state before repeating a write.
An invocation timeout does not certify that an external process has stopped.
The helper does not override existing per-tool deadlines or the Run deadline.

## Cancellation ownership

The operation runs in a separately owned, awaited asyncio Task. A child-only
CancelledError becomes tool-cancelled feedback. Cancellation of the calling
runtime task remains CancelledError and propagates down to the operation. The
caller checks its cancellation state even if a plugin suppresses cancellation
or raises while unwinding. This avoids treating Stop as a retryable tool error.

There is no shielded/detached task or background queue. The operation inherits
the caller's context variables for authorization and tracing; its local context
changes do not leak back. Parallel tool failures do not cancel healthy siblings.
Process-control BaseExceptions are not converted to successful tool responses.
This is cooperative fault containment, not isolation from native crashes,
blocking code, or plugins that deliberately refuse cancellation. Existing Run
cleanup/deadline supervision still applies; process sandboxing is separate.

## Regression contract

Tests cover unexpected exceptions, public domain errors, invalid output,
multimodal encoding, returned failure envelopes, parallel calls, tool-local
cancellation, caller cancellation, cleanup errors, and both timeout scopes.
ADK adapter tests actually invoke a failing generated function and a subsequent
successful function before producing the final answer. Codex tests exercise its
subprocess bridge. RunManager tests assert the Run stays running and retains its
slot after failed-tool feedback, then reaches success only on an explicit
provider terminal event. Tests requiring plugin bugs to fail the Run are not a
valid regression contract and must not be reintroduced.
