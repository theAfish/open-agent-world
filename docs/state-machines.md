# State machines

[简体中文](state-machines.zh-CN.md)

Each Agent has one orchestration space containing **system-owned** runtime projections and **user-owned** states. Execution is a set of SYSTEM-owned states inside that Agent, not a second state machine. They share references, events, conditions, visualization and evaluator inputs, but have different write authority. A system state can be observed and reacted to; a user rule can never assign it. An edge toward a system state requests an existing authorized operation.

Ownership upgrades recognize the unchanged old Agent lifecycle and promote it to SYSTEM rather than copying it to `legacy_status`. Previously duplicated defaults are consolidated on initialization, including their primary display, runtime values and read references. The five redundant built-in rules are retired; historical versions remain available. Custom states, modified rules and externally assigned user groups are preserved.

## Node support and defaults

| Node | Default graph | Initial state | Editor |
| --- | --- | --- | --- |
| Agent, including plugin nodes with `core.agent` | Execution · SYSTEM: idle, running, waiting, error | idle | Yes |
| Legion | available, without invented business phases | available | Yes |
| Sandbox | No editable graph; its resource runtime owns lifecycle | stopped | No |
| Built-in text, image, Conversation | No default graph | Type-defined | No |

Plugins optionally declare groups in the existing `state_machine=...` registration. Each group has `ownership: "user" | "system"` (default `user`). System groups declare their subsystem `owner`, stable state IDs, canonical `projection` facts, and legal `commands`. Plugins without an authoritative lifecycle need no system group or extra interface. A `core.agent` type always inherits RunManager's Execution contract; plugin customizations cannot override it. Other plugins can opt into blank authoring with `state_machine_editor=True`.

Editor support is a type capability. `has_state_machine` reports definition availability. Legacy stored graphs do not grant Sandbox an editor. There is no universal Planning / Awaiting review template.

## Default Agent transitions

Creation persists the type graph and initializes one node instance. Execution retains the stable group ID `status`. Its five canonical projections are read-only:

| Fact | Default destination |
| --- | --- |
| `agent.ready`: runtime initialization completed | idle |
| `agent.work_started`: at least one Run is running | running |
| `agent.work_waiting`: unfinished Runs exist, none running | waiting |
| `agent.work_finished`: all Runs ended, the latest ending was not failure | idle |
| `agent.runtime_failed`: all Runs ended, the latest ending was failed | error |

Facts aggregate all Runs belonging to the Agent. Finishing one concurrent Run cannot mark a still-running Agent idle. A suspended Run remains waiting even when it releases execution capacity. A later start or runtime-ready fact can recover error through the declared graph.

These mappings are owned by RunManager, outside the user rule list. No business group is created automatically. To react to Running, create a user group and connect the Execution / Running reference to its Researching state. The editor supplies `state.entered` and the stable state ID; the user need not select `agent.work_started`. Exit anchors use `state.exited`. `state.current` samples the current state on relevant operation observations; it is not a timer or polling loop.

## Editing and applying

The editor opens one canvas for each Agent/object, containing all its user groups and immutable SYSTEM states. SYSTEM states use a distinct fill, double border and badge; Execution has no separate canvas or frame. A new Agent initially shows only its four system states; Add state creates a user group lazily, even when a system state is selected. Group selection chooses where new states belong without hiding other local groups. All states of the current object, expanded members and referenced objects appear in the window automatically; there is no per-state visibility picker.

All local states share the owner's coordinate space. Older group-local layouts are merged without overlapping groups and saved as presentation metadata. Connections reuse the main canvas's boundary geometry, following the circular state boundary as nodes move. Return and parallel edges use separate lanes; self-loops use distinct start/end anchors and curve outside the state.

The graph contains states and edges, with no event blocks or ANY-state nodes. Canonical transitions are faint, read-only background edges and can be collapsed. Runtime facts, wildcard matching, conditions, actions and diagnostics appear only in transition details or explicitly opened panels. System states remain movable but have no rename/delete controls. Save creates an immutable object-local draft; layout-only changes update separate presentation data.

**Apply saved version** replaces the node instance's bound version. Valid current state IDs survive; removed states fall back to the new group's initial state. Signal memory and reference bindings are rebuilt, and undispatched old-version actions are cancelled. Already dispatched external work is not undone. Failed activation rolls back the version, state and bindings.

Primary node state has one `default` instance. Disabling pauses user rules and pending dispatch, but system projections continue following authoritative facts. Broken user references also disable orchestration without freezing Execution. Clearing an Agent/Legion definition restores its type graph. Deleting IDs used by another saved/applied definition is rejected before Apply.

Auxiliary workflows without `status_entity_id` do not project card status and may use separate scopes. They retain explicit immutable version binding: editing never silently rebinds a scope. The built-in Agent no longer creates a second independent business-state machine.

## Ownership and execution boundaries

| Data | Authority |
| --- | --- |
| System vocabulary, canonical projections, command declarations | Owning runtime / node-type registration |
| User vocabulary, rules, conditions, actions and references | Immutable per-object definition |
| Current node states, signal memory, journal cursor | Node instance in the existing StateStore |
| Card.status, AgentInfo.status, operational_status | System operational state when declared |
| primary_state, status_label | Group selected by `status_entity_id` for presentation |
| state_groups | Current IDs and ownership of all local groups |
| Positions, viewport and expansion | Separate presentation |
| Per-attempt created/running/waiting/succeeded/failed/cancelled/interrupted | RunStore / RunManager |
| Capacity, cancellation, timeouts and permissions | Existing execution/security owners |
| Documents, shared values and session data | Document services and scoped StateStore |

A Run describes a real task attempt, not an alternative Agent status. Changing a displayed state to idle does not terminate a Run or free capacity. Run/stop controls consume active Run and occupied-slot counts. Ordinary card patches cannot override machine-owned status, which is also hidden from editable Agent/Legion config. Legacy database status fields are compatibility storage, not runtime authority.

## Events, evaluation and actions

`GET /api/state-machines/events?card_id=…` derives its catalog from Agent activity, Run lifecycle, live capability projection, registered document/resource actions, work execution, state entry and stored-value changes. The frontend contains no tool-name-to-event mapping.

SQLite `operation_events` is the durable input. The synchronous Agent observation path drains its node instance in journal order before a Run API returns. A background consumer handles remaining events and dispatches actions. Both use the same evaluator, cursor and deduplication receipts. Simulation uses that evaluator with synthetic observations and no real side effects.

All user guards see the same snapshot: observed system facts and the pre-transition user states. Definition order selects at most one matching rule **per affected group**. Independent groups can change on the same event; a rule with additional effects claims all its affected groups together or yields to an earlier matching rule. A command-only rule claims its source group, not the system target. Projected facts, user changes, receipts, condition memory, cursor and outbox intents commit in one transaction before dispatch. Duplicate events cannot repeat actions. Enter/exit observations identify the authoritative instance/group; cascades stop at depth 32.

Actions use existing authorized capability calls, document/resource actions or `RunManager.start_run()`. Dispatch rechecks availability, permission and capacity. Caller/target selectors can be current, specific, associated or produced objects. Invocation associations carry explicit identities and lineage; an API return does not imply associated work completed. External effects are outside the SQLite transaction. Outcomes not provable after a crash are marked uncertain and are not automatically replayed.

System command declarations contain an operation identity, input schema, authorization metadata and possible outcomes. A rule's `command` stores `{entity_id, state_id, command_id, arguments}`. It resolves to the existing action/outbox contract, never a system state effect. Execution currently exposes Start work (`host:run`); additional commands require existing registered operations. Accepted commands are diagnosed as COMMAND / accepted, separately from later SYSTEM transitions. Run completion, timeouts, capacity and cancellation remain RunManager's responsibility. Capability authorization stays in the broker. Container Run requests are limited to current descendants; Agent-to-Agent requests retain the normal communication grant and are rechecked at admission.

## Legion, templates and migration

A Legion owns local groups and references member groups, never copied definitions. Identity is `(card_id, state_group_id, state_id)`; `definition_version` is debug metadata, not a long-lived pin. References follow their bound owner's applied definition when IDs survive. Apply checks dependent states and commands before accepting removed IDs. A Legion may observe a member's Execution and request member work, but cannot assign it. Dynamic targets use the existing invocation associations; there is no Summoning-specific state-machine branch.

Templates contain user groups, user rules (including system anchors and command requests), stable references, presentation and activation intent. `system_groups` lists required group IDs; system definitions are resolved from the target type during deployment. Missing groups/states/commands report incompatibility during deployment/Apply. Active templates deploy active; drafts deploy as drafts. Current state, journal cursors, condition memory, receipts, outbox work and Run IDs are never captured.

Legacy `config.state_machine` imports as a disabled draft, with system declarations supplied by the target type. Old editable groups colliding with system IDs are preserved under `legacy_…` user IDs, with their local rule references remapped. Existing instances migrate to immutable ownership-aware versions while preserving valid user progress. Contract changes validate dependencies and cancel undispatched requests; repeated initialization is idempotent.

## Implementation map

- `backend/agent_state_machine.py`: activity vocabulary and default graphs.
- `backend/plugins/registry.py`: declarations, inheritance, validation and editor capability.
- `backend/state_machine.py`: graph schema and primary-group binding.
- `backend/state_machine_store.py`: versions, node instances, status projection, references and migration.
- `backend/state_machine_runtime.py`, `state_machine_preview.py`: shared evaluation, durable processing, dispatch and simulation.
- `backend/runs/manager.py`: Run authority, aggregate facts and capacity; no graph destination decisions.
- `backend/world/store.py`, `services.py`: public projections and lifecycle wiring.
- `frontend/src/stateMachines/`: graph editing, event selection, simulation, save/apply and diagnostics.
- `frontend/src/state/worldStore.ts`, `cards/AgentCard.tsx`: authoritative status consumption and execution-count controls.
