# Default Agent context management

The OAW-owned `google.adk` runtime automatically manages context without exposing
compaction controls. Model window/output limits live in Settings > Models. A provider ID, registry ownership and the installed runtime
implementation select this path; the `core.agent` trait does not. Other runtime
providers retain their existing continuation behavior.

`agent_contexts` in the existing SQLite database stores private checkpoints by
`(agent_id, context_id)`. Conversation context IDs are session IDs. Each checkpoint
contains a rolling summary, a recent structured content tail (including tools),
the canonical conversation cursor, usage calibration and compaction count.
Foreign keys clean up deleted Agents and sessions. An additive table creation
upgrades existing databases. None of this is Agent Card configuration or exposed
through the state tools. ConversationStore remains the canonical message history.

Before **every model call**, including calls within a long tool loop, the runtime
renders the checkpoint, unseen conversation messages, current task and fresh
instructions/tools. It no longer appends a repeated 40-message transcript. ADK's
transient event log is reset between invocations while retaining ADK session state.
One Agent/session serializes checkpoint mutations; different pairs are independent.
ADK 2 can assemble a later model request before its public tool events are consumed.
The model hook reconciles that actual request with observed events, retaining each
part once even after compaction. Usage is calibrated in the model response callback.

Each configured model stores `context_window` (default 128,000) and
`max_output_tokens` (default 8,192) in the existing versioned model catalog. The
fields appear under the model's collapsible **Context & output limits** section.
They are positive integer settings; the window is at least 1,024 and output must
be smaller than the window. Old catalog rows receive these defaults when read,
and saving persists the values alongside the model. No new database table or
destructive migration is needed. Values are scoped to the connection's stable
model reference, so identical model IDs on different connections remain separate.
The configured default model resolves through the same reference.

Configured limits take precedence without model-name matching or provider
discovery. New runs read the latest saved values; in-flight runs retain their
budget. The output value is sent as the model generation cap and reserved when
calculating input space. Safe input is `floor((context_window - max_output_tokens)
* 0.85)`, with a one-token lower bound. Thus even an unusually large output
reservation cannot make input plus output exceed the configured window. Settings
cannot expand an endpoint's actual capacity. Other plugin runtimes retain ownership
of their own generation/continuation settings.

For legacy raw runtime model strings with no catalog reference, the previous
installed LiteLLM metadata fallback still supplies the model input limit and output cap.
Resolution preserves exact provider entries, then matches compatible adapter
prefixes and model-name casing. A small verified fallback covers newly released
DeepSeek V4.1 Flash names missing from the installed map; its published window is
[1M tokens](https://api-docs.deepseek.com/quick_start/pricing/). It does not change
the model ID sent to the configured endpoint. Unknown/private model names use a
32,768-token rolling compaction target, not a claimed provider limit: the API's
`context_limit` is zero and fixed request overhead cannot cause a local rejection
against this provisional target. The provider still enforces its own limits.
In that legacy path only, output reserve is the smaller of the model output cap,
8,192 and one eighth of the input limit. A further 15% of the remaining input
space leaves tool/runtime growth and safety headroom. Pressure is
rendered input divided by this safe input budget, clamped to 0–1. Provider prompt
usage takes precedence, with estimated deltas for subsequent growth; otherwise a
conservative UTF-8 estimate includes instructions and tool schemas. This is
**context pressure**, not an exact context-window utilization percentage.

At 85% of the safe input budget the state becomes `high`. Compaction uses a
single window-relative allocation: subtract the rendered current task and fixed
instructions/tool schemas from safe input to obtain available history space.
Trigger at fixed input plus 85% of that space; target fixed input plus 50% after
compaction. Allocate at most 20% to the checkpoint and 30% to the recent tail.
These are OAW policy ratios, not claimed Codex/Copilot implementation constants.
For unknown model aliases, the provisional safe-input amount is a rolling history
allocation added beside fixed instructions, not a hard cap on those instructions.
A generic compaction pass folds older contents and the prior snapshot using the same
configured ADK model adapter and connection, without tools. It preserves goals,
facts, decisions, uncertainties, execution outcomes and artifact/resource paths.
A token-sized recent tail is retained, after allowing for the fixed instructions
and tool schemas, with tool call/result groups kept together.
Oversized histories/tool bodies and even an oversized prior checkpoint are folded
in chunks sized against the complete serialized summary request, including JSON
escaping, instruction, previous fold and output reservation. Summary text has no
fixed 2K/4K ceiling: acceptance uses the window-relative checkpoint allocation.
The prose target is half that allocation or half the configured generation budget,
whichever is smaller. Generation tokens also pay for reasoning, so they are not
treated as a checkpoint-length measure. Checkpoints commit only after reduction
and verification that the complete rendered continuation fits the target.

`MAX_TOKENS` (including ADK's error-code representation) and empty output can
retry with a doubled generation allowance within the provider output cap and
remaining window. Empty output can retry at the cap; oversized prose retries with
a concision instruction and the original source. All retries are bounded to three
attempts and fit the configured window. Failure distinguishes empty, oversized,
rejected and truncated results, includes applicable budget sizes, and preserves
the entire old checkpoint. No agent tools are replayed. A later turn can retry.
Interrupted tool calls get an explicit unknown-outcome result so
continuation cannot assume either success or permission to repeat a side effect.

This adopts the lifecycle described in the
[OpenAI compaction guide](https://developers.openai.com/api/docs/guides/compaction):
grow, measure, compact, continue, repeat. It uses a **generic rolling snapshot**,
not OpenAI encrypted/native compaction or a separate Responses API transport.
The [Codex configuration reference](https://developers.openai.com/codex/config-reference/)
exposes model-specific automatic compaction thresholds; the
[Copilot CLI context guide](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/context-management)
describes background compaction near 80% and waiting near 95%. OAW compacts
synchronously at its serialized model-call boundary, preserving its existing
per-session ownership and cancellation contract rather than adding a background
writer. Explicit connection limits remain authoritative; this change does not
increase a proxy's configured window based only on its model name.

REST conversation summaries expose only session/Agent `ContextStatus` projections.
The existing event hub publishes `context_status` invalidations on 5% pressure
buckets and state/count changes. The Conversation participant avatar reads the
selected session's projection; message avatars have no rings. Hover gives pressure
and count, with no token counters, settings or compaction toast. Reconnect reloads
the authoritative snapshot; reduced-motion preferences disable animation.

Limitations: summarization is lossy and adds model calls/latency. Scripted-model
tests exercise the real ADK runner and tools, not live-model factual retention.
Metadata can be missing or inaccurate for a proxy/private alias, and an endpoint
may impose a smaller window than its upstream model. For a known window, a current message
or tool schema larger than the remaining safe budget fails explicitly instead of
silently truncating it. Provider-native hidden continuation, ADK process-local
session state and interrupted Run resumption remain subject to existing runtime
limitations; OAW checkpoints and raw tails survive restart. Existing execution
deadlines and inactivity policies still apply to compaction calls.

Validation: 15 new backend tests cover the real ADK runner with a scripted model,
repeated in-turn tool compaction, continuation, history retention, independent
scope/status, restart, failure/cancellation, usage/event buckets, plugin exclusion,
session deletion, interrupted tools, peer-stream updates, scope locks and oversized
current input, ADK event ordering and Stop/continue cleanup. The final related
Conversation/Run/streaming/persistence group passes 78 tests. The broader backend
suite (before the last two added cases) reports 863 passed, 26 skipped and three recursive
summoning result-text failures; all three also reproduce with the changed backend
modules loaded from unmodified HEAD `756dc6d`. The frontend suite passes 451 tests
(before the added ring test), and the final focused UI/store/timeline/i18n group
passes 69. Production build and final TypeScript checks pass. An isolated Chrome
test verifies ring geometry, session switching, pressure reduction, reduced motion
and missing-status fallback; its REST pressure projection is controlled test data.

Follow-up regression coverage adds compatible model-name resolution, exact
provider limit precedence, a fresh Conversation with a large ADK tool schema and
no inherited checkpoint, provisional unknown-model budgets, reasoning-exhausted
summary retries, output-cap enforcement and retained checkpoints on exhaustion
or rejection. Read-only inspection of the reported MatCreator failures confirmed
separate contexts: the fresh Conversation retained one message, while the older
one retained 384. Both were mistakenly budgeted at 8K under
`openai/DeepSeek-V4.1-Flash`. Live checks through the configured adapter used only
synthetic input: a 1,024-token summary budget produced `MAX_TOKENS`, no text and
1,024 reasoning tokens; an 8,192-token budget returned a complete text summary.
No real conversation was replayed and no agent tools were executed by the check.
The final related backend regression group passes 85 tests. Loading this fix in
an already running backend requires restarting that process; persisted histories
and checkpoints need no reset or manual migration.

Configured-limit validation: 110 related backend tests pass, including catalog
defaults, old JSON compatibility, persistence/restart, invalid values, connection
isolation and a real ADK runner applying changed limits on the next invocation.
All 454 frontend tests pass with two workers, and the production build passes.
The isolated Chrome model-settings test verifies default values, editing, saving,
reload, connection isolation, Chinese labels and narrow layouts; rendered screenshots
were inspected. This is isolated browser coverage, not a live-model conversation
or embedded-browser check. Configured model limits require no metadata requests.

Window-relative budget verification (2026-09-20): 90 related backend tests cover
the runtime, model catalog and conversation paths, including complete summaries
above the old 4K ceiling, empty/oversized recovery, bounded escaped Unicode input,
smaller-window checkpoint folding, early compaction and unknown-model history
rolling. With explicit user authorization, a read-only copy of the failed
MatCreator checkpoint was summarized through its configured adapter. At the
currently configured 1M window, a forced pass produced a 4,168-token estimated
summary and reduced total estimated input from 427,035 to 265,697. At the original
128K window, five summary calls reduced the same estimated input to 39,028, below
the 58,875 continuation target, in 166 seconds. These conservative local estimates
are not provider token counts. The probes did not execute tools or write the
canonical database; they verify budget recovery, not comprehensive factual
retention or a resumed live Agent run. Restart an already running backend to load
the implementation; no checkpoint reset or data migration is required.
