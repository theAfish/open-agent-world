# Task Board plugin

A shared work board for human and Agent task planning. This checkout bundles the
plugin in **Fields > Task Board**; it can also be built and installed as the
independent `oaw-task-board` Python distribution. An installed entry-point version
takes precedence over the bundled source. Version 0.2 requires host Plugin API 1.3.

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
| Control execution | Read tasks; read/start/stop execution, without plan editing |

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

## Optional Agent execution

Connect **Task Board -> Agent** using **Execute with**, then open **Agent execution**
and choose the default executor. Each task can override it. The other two settings
are parallelism (default one task at a time) and pause on failure (default on).
Executor bindings grant dispatch only; Agents keep their existing tools, resource
permissions, sandbox connections, model and Legion settings.

**Run ready work** runs available tasks and continues through newly unlocked
dependencies. A row's **Run** or **Retry** runs only that task. Retry creates a new
Run and preserves completed prerequisites. After retry, use Run ready work to
continue the plan. Prerequisite summaries and Run references enter each prompt;
results are bounded to keep inputs manageable. Task notes hold up to 4000 characters
of returned text; full provider text remains in Run state.

Run success completes a task by default. Failure/cancellation/interruption blocks
it with a retry option. Stop cancels active Runs and prevents further admission;
completed tasks remain complete. With pause on failure, already running peers
finish while new tasks wait. A missing executor connection or unavailable Agent
pauses the batch with an error. Reconnect/reconfigure and start explicitly.

While a batch runs, document edits and source deletion are locked. Stop before
replanning. Execution history records each attempt separately. Backend restart
reconciles unfinished attempts and never automatically dispatches work.

Agent-to-board read/edit permissions never imply dispatch permission. A controller
Agent needs **Control execution**, checked again before each dispatch. Its batch
runs independently of the controller's Run; stopping the controller does not stop
the batch. Stop it through the board or the execution tool.

## Persistence and reuse

Task data lives in host StateStore (`node_document:<board_id>`), independently of
card config. Reloads and backend restarts preserve it. Deleting a board removes its
document; canvas undo restores its exact saved progress and notes.

Saving a Legion captures task structure/descriptions/dependencies and resets task
statuses to `todo` and progress notes to empty in the preset. Each deployment gets
its own document. Execution settings and internal Agent bindings are remapped to
the new instance. External executor references are cleared. Run IDs and execution
history are not part of the preset. Task IDs are local to their board. Canvas
undo restores the document including result notes, but not host execution history.

Without execution configuration, the board remains a manual/shared todo list.
It uses the host's generic [work-source contract](../../docs/execution.md); external
job monitoring and richer acceptance policies can be supplied by other plugins.

## Development

```powershell
./scripts/dev.ps1 -AgentRuntime mock -PluginPath ./plugins/task_board
uv build ./plugins/task_board --wheel
```

The plugin imports only `open_agent_world.plugin_api` and Pydantic. It contributes
one document-backed node, five relationships, and five scoped capability handlers.
The host supplies the reviewed `ui.task-board.v1` renderer; no arbitrary plugin
browser code is executed. DAG validation and mutation policies live in this package.

Host integration tests: `backend/tests/test_task_board.py`.
Execution and non-DAG contract tests: `backend/tests/test_node_execution.py`.
Browser workflow: `frontend/e2e/task-board.spec.ts`.
