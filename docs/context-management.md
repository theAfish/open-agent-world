# Default Agent context management

OAW manages working context for its `google.adk` runtime. Other runtime providers
retain ownership of their continuation behavior. The default runtime and all
AtomSculptor roles use the same final-request accounting and compaction machinery.

## Window and token accounting

Settings > Models stores each connection/model's `context_window` and
`max_output_tokens` in the existing versioned catalog. Saved values take precedence
without provider discovery. New runs read current settings; running invocations
retain their configuration. The actual requested output cap is reserved:

```
input capacity = configured context window - requested max output tokens
```

There is no percentage reserve, early percentage trigger, checkpoint percentage,
recent-tail percentage, or fixed prose-length quota. Configuring a larger window
does not expand the provider's real capacity. Normal task generation keeps the
selected output cap.

Local accounting uses `cl100k_base` BPE data already bundled with LiteLLM, without
runtime tokenizer downloads. It tokenizes rendered contents and instruction/tool
configuration, rather than dividing UTF-8 bytes by a guessed constant. This is a
**reference tokenizer count**, not the exact count for an arbitrary compatible
endpoint. Native Run records label it `local_cl100k_base_reference`, with
`tokenizer_matches_provider=false`, and store provider usage separately. Returned
prompt usage calibrates subsequent deltas. Checkpoints from the former estimator
retain their history but discard incompatible calibration anchors.

Legacy raw model strings can resolve limits from installed LiteLLM metadata.
Unknown/private aliases have no fabricated hard limit or rolling quota: their
reported `context_limit` and pressure are zero until real limits are configured.
The provider enforces its actual limits. Client compaction requires a configured
or known window to size its own requests safely.

## Request preparation and compaction

Before dispatch, normalize unsigned text-only streamed thought chunks and project
large archived tool outputs into bounded previews. Neither operation changes the
original Run archives. A preview requires a uniquely matched complete native tool
invocation. Call IDs and names remain intact; `read_tool_result` retrieves needed
slices with offsets and JSON pointers. Access is restricted to references
issued into the current Run/role/delegation. Missing archives cannot produce an
invented receipt. Signed prefixes bypass client normalization and projection.

Previews retain a JSON structure rather than cutting serialized JSON. Omitted
subtrees supply a `read_pointer` rooted at the original archived result;
`preview`, `text_preview`, `items_preview` and `fields_preview` are working-view
wrappers and are not added to the archive. Character paging applies directly to
the original string. Invalid paths return bounded information about the nearest
accessible parent so the Agent can correct the read. Unknown references reveal
no result structure. Reads use the existing tool failure/cancellation boundary:
bad arguments are tool feedback, while stopping the Run still cancels the read.

The completed request includes current instructions, late HOST frames, selected
tools, arguments/results and the actual output reservation. When it no longer
fits, an explicitly supplied adapter-native reducer takes precedence. Current
compatible Chat Completions adapters do not implement OpenAI Responses encrypted
compaction or Claude server-side compaction. OAW uses a client summary fallback
for unsigned history, through the same model adapter and connection.

The fallback summarizes settled history and the previous checkpoint. It keeps the
latest complete exchange where it fits, and never splits a tool-call/result group.
An oversized settled exchange can be folded; a currently open exchange stays
intact. The current user task and protected HOST facts remain verbatim. Summaries
retain goals, confirmed facts, decisions, uncertainties, execution outcomes and
artifact/resource paths. Reported evidence does not become an acceptance receipt.
Acceptance requires an actual reduction and a complete continuation fitting the
input capacity, rather than separate allocations for summary and recent history.

Oversized transcripts are folded in bounded fragments. Each summary request
includes its instructions, preceding fold and output reservation in accounting.
Reasoning shares the generation cap with prose. Empty, oversized or truncated
summary responses use bounded read-only retries. When a larger generation cap or
correction instruction requires more room, the transcript fragment is repacked;
the unconsumed suffix remains for subsequent folds. No transcript suffix is
silently discarded. A failed or cancelled pass preserves the previous checkpoint
and request; original messages and tools are never replayed by compaction.

Because a reference tokenizer may underestimate a gateway's usage, an explicit
provider/SDK context-overflow rejection can trigger **one** compaction and retry
of the unpublished model request. It has a separate allowance from network
recovery. A generic HTTP 400, invalid schema or authentication error is not a
compaction signal. Repeated overflow stops explicitly. Agent execution and
completed tools are not restarted. Without suitable reduction support, the error
asks for actual endpoint limits or adapter-native compaction.

Signed reasoning requires an explicitly supplied native reducer or a fresh
assignment from saved facts. Provider-native opaque compaction state must be
handled by its adapter; model-name matching never implies support.

The public references informing this lifecycle are the
[OpenAI compaction guide](https://developers.openai.com/api/docs/guides/compaction),
[Claude compaction documentation](https://platform.claude.com/docs/en/build-with-claude/compaction)
and [Claude Code context lifecycle](https://code.claude.com/docs/en/how-claude-code-works).
OAW implements the applicable client fallback, not either product's undisclosed
internals or unsupported provider interfaces.

## Persistence and ownership

The existing SQLite `agent_contexts` table stores private checkpoints keyed by
`(agent_id, context_id)`: summary, structured tail, conversation cursor, usage
anchor, estimator identity and compaction count. ConversationStore remains the
canonical message history; native Run invocation records retain full model
requests, reasoning, tool calls/results and provider usage. Working projections
and compaction do not overwrite those records or create another database.

Transient visual tool observations are an explicit exception to full-body
persistence: live requests receive the pixels; Run logs and private checkpoints
retain observation metadata and omission markers. User-supplied images retain
their ordinary persistence behavior. An exact recovery checkpoint is unavailable
if redaction or image omission changes its request. When restoring a signed
prefix whose transient images were omitted, OAW starts from its public tool/text
facts as historical data rather than replaying altered provider signatures.
The Agent can re-observe when current pixels are needed.

ADK model hooks reconcile assembled requests with public events without retaining
parts twice. Interrupted tool calls receive an explicit unknown-outcome result,
so continuation must inspect state before repeating a side effect. ADK transient
events reset between invocations while session state and OAW checkpoints remain.
Scope locks serialize one Agent/session without blocking unrelated sessions.

AtomSculptor specialist scopes follow the Run and ADK delegation branch. Exact
current HOST frames stay pinned; no previous specialist transcript enters a new
assignment. Coordinator checkpoint scope and Conversation attribution remain
separate. Summary calls use ordinary native invocation records with phase
`context_compaction`, usage accounting and request-level network recovery.

The host and opt-in plugin request compactor share
`open_agent_world.context.adk.AdkContextCompactor` for transcript chunking,
summary generation and candidate validation. The host adapter supplies its token
estimator, configured input capacity, private checkpoint store and traced model
calls. Plugins may keep their own persistence and the public default allocation;
these adapters do not maintain separate summary executors.

Builder HOST plans reference existing requirement IDs; the complete acceptance
text appears once in the stable frame's `requirements`. Current plans and stage
indexes resolve against that same mapping. This changes only the working view,
not committed plans, acceptance checks or receipts. Identical source head/error
excerpts are also included once. A stable frame is one copy in working history;
it still contributes to every API request's input, even when prefix caching helps.

Conversation context status exposes pressure, state and compaction count.
Pressure uses the input capacity; `high` means that capacity is reached.
Invalidations are coalesced into UI buckets, which are not execution budgets.
Checkpoint persistence and original archives survive restart. Summarization is
lossy and adds model calls; local reference counts are not provider guarantees.
