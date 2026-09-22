# Card state lifecycle

[Plugin API](plugins.md) · [Runtime state](state.md)

Plugin API 1.23 adds an opt-in card state policy. The persistent card and graph
remain the same when a conversation changes. Card config belongs to the card;
runtime data belongs to a host-resolved namespace; drafts, hover and selection
remain frontend state. This API exposes only `shared` and `session`, without
inheritance or Agent/Run/project scopes.

## Declaration

Declare `state` on `NodeTypeDefinition`, or on `PluginDescriptor` as the default
for its node types. A node declaration overrides the plugin declaration.

```python
from open_agent_world.plugin_api import ScopedStateSpec, StatelessStateSpec

# Task Board
state = ScopedStateSpec(
    supportedScopes=("shared", "session"),
    defaultScope="session",
    userConfigurable=False,
)

# A renderer may still have persistent card configuration.
state = StatelessStateSpec()  # mode="none"
```

The catalog uses `mode`, `supportedScopes`, `defaultScope`, and
`userConfigurable`. Empty/duplicate scopes and unsupported defaults are rejected
at registration. `none` cannot declare a persistent document or execution ledger.

Card metadata exposes the effective `state_scope`. The database stores only the
explicit `state_scope_override` (in `cards.state_scope`); NULL follows the current
developer default. Setting or resetting an override is allowed only when the
policy is scoped, has multiple scopes and explicitly permits user configuration.
The same check applies to creation, individual edits and batch edits. Switching
the policy does not copy, overwrite or delete either namespace's existing data.

## Plugin access

Python capability handlers and resource actions receive `ctx.state`, bound by the
host to the selected card and the invocation context. It is `None` for a stateless
card. Frontend views receive the optional `host.state` with asynchronous methods.

```python
current = ctx.state.get()  # {"value": {...}, "revision": n}
ctx.state.set({"items": []}, expected_revision=current["revision"])
ctx.state.update({"ready": True})  # atomic shallow merge
ctx.state.delete()                # clear payload; retain revision history
```

Generic state is a JSON object up to 256 KiB. Supplying `expected_revision`
provides compare-and-set protection. Capability handles recheck their live grant.
Typed Node Documents continue using their model, validation, size limits,
read-only actions, capabilities and independent document revision. They share
the namespace adapter with generic data and the host execution ledger, but raw
generic writes cannot bypass document validation or edit execution history.

Plugins do not accept session arguments, build keys, or listen for session
creation/deletion. Existing pure document callbacks stay unchanged. The host
captures a session for requests, mounts a fresh scoped view when it changes, and
keeps delayed responses in their original view. An Agent invocation's context
takes precedence over browser viewing context. Independent execution batches
retain their origin session through dispatch, completion and restart recovery.

Raw generic state and snapshot HTTP routes are desktop-only. Published views
continue using their explicitly published document/resource actions; their
`host.state` is absent. A deployed operator cannot use a supplied namespace to
access an unpublished conversation. Backend plugin actions still receive bound
state after their existing permission checks.

## Persistence and migration

`ScopedStateStore` maps `(card_id, scope_type, scope_id)` to an existing StateStore
scope through `card_state_instances`. Shared uses `*`; session uses the durable
Conversation session ID. Payloads, timestamps, revisions, events and rollback
stay in `state_scopes`/`state_values`; no second JSON storage engine is introduced.
Namespaces are created on first access, using document model defaults until the
first write. Creating a session does not enumerate cards or allocate card state.

The host selects the owning workspace's conversation first. Without a selected
conversation it uses that conversation's General session, or the only
conversation in the world. A standalone card with no unambiguous conversation
uses the host's implicit `default` session. That namespace is retained when other
sessions are used. Session IDs are host transport details, not plugin settings.

An omitted manifest retains the old shared behavior and original
`node_document:<card_id>` address. Existing native resources and lifecycle
handlers are untouched. Merely adding a policy does not relocate native files;
a future Text/Sandbox migration must adapt its actual resource storage too.

Task Board and MatCreator's Research Task Board use session isolation. On first access, their previous
document and execution ledger are atomically associated with the host's default
session, even if the first request comes from another session. Other sessions
start empty. No old values or revisions are discarded. Structure Viewer declares
`none`: binding it does not resolve a session or allocate storage, and direct
persistent-state requests return an explicit capability denial.

Research delegation keeps its attempts and collected results with the originating
board namespace. Summoned Executors retain their private Run contexts. Deletion
checks inspect retained execution ledgers as well as batch workers, so switching
to another session cannot bypass protection for live delegated work. Old research
plan session labels remain compatibility metadata, not routing inputs.

Deleting a session/group deletes only its card namespaces in the same SQLite
transaction; deleting a card removes all its namespaces. Active batches block
deletion of their card or conversation. The host's delete/undo snapshot preserves
every document/generic namespace, omitting execution history. Legion templates
capture the selected document with the plugin's existing reset/remapping rules,
retain explicit scope overrides, and seed the new graph's default namespace.

## UI and remaining boundaries

No scope badge, session label, creation question or settings panel is added.
`host.setDataPersistence` exists only for an eligible policy; plugin authors may
use it inside an existing settings view with behavioral wording. No bundled card
adds this optional control in this iteration.

The audit found older mixed representations outside this migration: operational
`status` still shares `config_json` with configuration for compatibility; Sandbox
config includes its workspace location and operational status; frontend
`normalizeCard` projects resource previews/revisions into `CardConfig`. These are
retained compatibility paths, not new scoped storage. Task Board's document also
contains plan-local executor/parallelism settings, so those follow its plan;
card description/name and graph executor connections remain card-wide.

Host integration coverage lives in `backend/tests/test_card_state.py`,
`frontend/src/state/cardState.test.tsx`, SDK surface tests and
`frontend/e2e/card-state-lifecycle.spec.ts`. The browser test crosses Conversation,
Workspace and Task Board, so it belongs to the host suite. Plugin-owned planning
logic remains in `plugins/task_board`.
