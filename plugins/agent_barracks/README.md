# Agent Barracks

Configured Agents in a Barracks are live summon blueprints. Drag an Agent into
the Barracks as an ordinary container move. Its private equipment is cloned with
each instance; authorized templateable external connections stay shared.

Equip **Summoning** on the calling Agent and connect it to the Barracks using
**Summon agents**. The scoped tool can `list`, `summon`, `inspect`, `message`,
`wait`, `stop` and `reclaim`. Private equipment follows the instance's lifecycle;
reclaiming an instance never deletes its shared Sandbox or knowledge graph.

Use `summon` with `agent_id`, a specific task `prompt` and `wait=false` to start
independent work and receive an instance handle immediately. `wait` accepts
`instance_ids`, `wait_mode=any|all` and `timeout_seconds` from 0 to 60. Timeout
does not stop the work. An Agent's legacy default `wait=true` waits for the
provider turn; explicit `wait` joins the durable Run's terminal state.

New instances default to `context_mode=task`: they have a private context that
is retained for follow-up turns. Supply objectives, inputs, output paths and
acceptance criteria in the prompt. `context_mode=inherit` explicitly uses the
calling context instead. `message` starts a follow-up turn on an available
instance; it does not inject text into a busy provider.

All recursive instances share root depth, concurrency and total-instance limits.
Parent Run cancellation propagates to dependent children. Every invocation
rechecks graph authorization, including after a bounded wait.

MatCreator adds task-ledger dispatch through the host's existing work-source
contract. See [MatCreator](../matcreator/README.md#delegating-research).
The plugin requires Plugin API 1.22. The host owns Run lifecycle, resource
restoration, capability checks and durable instance records.
