# Start materials research with MatCreator

MatCreator brings a research Agent, conversation, computing environment, knowledge
and artifacts into one workspace. Start with a small task: check the environment,
generate a structure file and inspect the result.

## Prepare the workspace

1. Install MatCreator from Library, along with the dependencies in its installation plan. Wait for preparation to finish and restart if prompted.
2. Open the MatCreator Pack, add **MatCreator research** to a Deck and place it on the canvas.
3. Open the Legion's **Workspace mode**.
4. Configure a model in **Settings → Models** and select it in the MatCreator Agent. This Agent needs an ADK/LiteLLM model connection; a connection available only through the TypeSafe adapter cannot be used.
5. Open the connected Sandbox, choose a runtime, check its diagnostics and start it.

The Pack prepares the Agent's runtime dependencies. Scientific software, model
weights, materials databases, licenses and remote computing accounts still need
to be configured in your chosen computing environment. Installing the Pack does
not prepare those resources.

Keep model keys in OAW's model settings and computing credentials in the relevant
environment settings. Do not put them in task instructions or templates.

## Complete a small task

Select the MatCreator Agent in Conversation and send:

> First check the available scientific computing environment. Plan a small copper supercell, generate its structure, check the atom count and publish the structure file. Tell me first if any required software is missing.

The default **normal** mode proposes a plan before execution. Check its scale,
resources and outputs before approving. **flash** mode delegates work directly.

During execution, use **Agent graph** for task history and Conversation for messages
and tool activity. Click a record to expand its details in place; click again or
press Escape to collapse it. Search finds tasks, and references in the details
link to related records. Positions in the graph do not represent fixed Agents
or dependency relationships.

| Area | Purpose |
| --- | --- |
| Conversation | Discuss the task, approve plans and read responses |
| Skills & knowledge | Inspect available knowledge and skills |
| Sessions | Create or switch sessions |
| Files / File preview | Browse and preview Sandbox files |
| Structure viewer | Inspect supported structure files |
| Research artifacts | View published artifacts and their versions |

Clicking a file opens its preview. When a connected Structure viewer successfully
parses it, the workspace switches to the structure view. A **Pinned** viewer does
not follow new file selections. Invalid or unsupported structures stay in the
ordinary preview.

Check atom counts, units, structures and computational assumptions before using
the artifacts. A successful run, low error or confident Agent response does not
replace scientific review.

## Continue or troubleshoot

- Switching Sessions restores that session's conversation and execution history. Opening details or selecting records does not change scheduling.
- **Jobs → Agent** shows tracked jobs in the current session. Pausing monitoring does not stop remote computation; cancellation requires confirmation from the computing service.
- If the Agent cannot see a resource, check connections and grants. Membership in the same Legion does not grant access.
- If runtime preparation fails, inspect the reason in Library and fix it there. Installing dependencies into a different Python environment will not help.
- Existing workspaces retain their layouts. Place a new research workspace or use **Edit layout** to change one.

To extend or maintain this Pack, see its [README](../../plugins/matcreator/README.md)
and [runtime contract](../../plugins/matcreator/docs/runtime.md).
