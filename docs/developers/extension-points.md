# Choose an extension point

Start with the smallest contribution that fits your feature. The public Python API is `open_agent_world.plugin_api`; frontend contracts come from `@oaw/plugin-api`.

| You want to add… | Start with | Example or contract |
| --- | --- | --- |
| A configurable card | `NodeTypeDefinition`, `PackDefinition` | [First plugin](first-plugin.md) |
| An Agent tool for a connected target | Capability and relationship definitions | [Greeter](agent-tools.md) |
| Structured editable data | `NodeDocumentDefinition` and document actions | [Documents](../plugins.md#node-documents-and-actions-plugin-api-12) |
| A database or other native file | Resource actions and lifecycle-managed storage | [SQLite](../../plugins/sqlite/README.md) |
| A custom card or workspace UI | Frontend views and the host SDK | [Custom interface](frontend.md) |
| A connected file preview | `core.file-viewer` and `useFileViewer` | [File viewers](../plugins.md#connected-file-viewers) |
| Reusable teams | `LegionPresetDefinition` | [MatCreator](../../plugins/matcreator/README.md) |
| Task readiness and completion | `NodeExecutionDefinition` | [Work-source execution](../execution.md) |
| An alternative Agent runtime | Runtime provider registration | [Runtime providers](../plugins.md#runtime-providers) |
| Shared or session state | Declarative state policy | [Card state](../card-state.md) |
| A view in an end-user deployment | `NodeDeploymentDefinition` | [Plugin deployment](../plugin-deployment.md) |

## Responsibilities

The host owns persistence, registration, live permission checks, lifecycle transactions, and shared UI boundaries. Your plugin owns its domain schema, behavior, views, and declared resources. Use the existing host contracts to connect them.

If your feature requires a new host contract, contribute that contract explicitly and keep its authority clear. Avoid implementing a parallel data store, permission shortcut, or hidden route from a view to internal host state.

Full details: [Plugin API](../plugins.md) and [Architecture](../architecture.md).
