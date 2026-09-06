# Task Board plugin

A shared work board for human and Agent task planning. This checkout bundles the
plugin in **Fields > Task Board**; it can also be built and installed as the
independent `oaw-task-board` Python distribution. An installed entry-point version
takes precedence over the bundled source. Requires host Plugin API 1.2.

## Use

1. Place a Task Board from Fields. Open its inspector or workspace.
2. Add task titles. Task details provide a description, status, progress note,
   and optional prerequisite checkboxes.
3. Connect an Agent to the board and choose one access mode:

| Connection | Agent tools |
| --- | --- |
| Read tasks | Read the board and ready task IDs |
| Update progress | Read; change task status and note |
| Manage tasks | Read; create/replace tasks; edit dependencies; delete tasks; update progress |

All access is scoped to the connected board and rechecked at invocation time.
Status updates cannot alter titles, descriptions, or dependencies. Human controls
have full editing access. The dependency graph is separate from canvas permission
edges: dependencies remain within a board and do not grant resource permissions.

The default list is also a todo list when no dependencies are set. The Dependencies
view lays out prerequisites left-to-right; select a node to edit it. Filter the
list to ready or completed tasks. A task is ready when it is `todo` and all its
prerequisites are `done`. `blocked` is an explicit blocker with an optional note;
waiting for a prerequisite is derived, not stored as a second status.

Tasks must have unique stable IDs. The plugin rejects cycles, missing dependencies,
and starting/completing tasks before prerequisites finish. Reopen active/completed
dependents before reopening a prerequisite. Remove references to a task before
removing it. Limit: 100 tasks per board; 256 KiB per document.

Edits use compare-and-set revisions. Concurrent changes never silently overwrite
one another. A failed edit retains the draft; Reload discards it and reads the
latest board. Browser reconnects and state events refresh the view.

## Persistence and reuse

Task data lives in host StateStore (`node_document:<board_id>`), independently of
card config. Reloads and backend restarts preserve it. Deleting a board removes its
document; canvas undo restores its exact saved progress and notes.

Saving a Legion captures task structure/descriptions/dependencies and resets task
statuses to `todo` and progress notes to empty in the preset. Each deployment gets
its own document. External Agent IDs and machine-specific paths are not bindings
in the task model. Task IDs are local to their board.

The board provides planning/readiness tools. It does not automatically dispatch
Agent Runs, monitor external Jobs, or claim task work was executed. A future
controller can consume ready IDs and publish progress through these contracts.

## Development

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./plugins/task_board
uv build ./plugins/task_board --wheel
```

The plugin imports only `open_agent_world.plugin_api` and Pydantic. It contributes
one document-backed node, three relationships, and four scoped capability handlers.
The host supplies the reviewed `ui.task-board.v1` renderer; no arbitrary plugin
browser code is executed. DAG validation and mutation policies live in this package.

Host integration tests: `backend/tests/test_task_board.py`.
Browser workflow: `frontend/e2e/task-board.spec.ts`.
