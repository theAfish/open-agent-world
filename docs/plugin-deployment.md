# Plugin deployment

**English** | [简体中文](plugin-deployment.zh-CN.md)

Use this contract to expose an existing plugin view in a
[published application](deployment.md). Backend-only plugins keep their nodes,
connections, configuration, and runtime in the copied profile. A public custom
view opts in with `NodeDeploymentDefinition`, available since **Plugin API 1.21**.
The editor and deployed application load the same `frontend.workspace` or
`frontend.body` component; the publisher does not maintain a plugin-name allowlist.

An undeclared custom view fails publication. Its plugin can still operate as a
hidden backend dependency.

## Minimal integration

Import from `open_agent_world.plugin_api` and add the declaration to the existing
`NodeTypeDefinition`:

```python
from open_agent_world.plugin_api import DeploymentSurface, NodeDeploymentDefinition

# NodeTypeDefinition(..., frontend={"workspace": "notes"}, document=existing_document,
deployment = NodeDeploymentDefinition(
    surface=DeploymentSurface(
        config_fields={"heading"},
        document_fields={"text"},
        document_actions={"save"},
        downloads={"text"},
    ),
)
# ... deployment=deployment)
```

Register `save` and `text` in the existing `NodeDocumentDefinition.actions` and
`downloads`. The declaration selects access; calls still use the original handlers,
revision conflict checks, and persistence. Set the plugin descriptor's
`plugin_api_version` to `"1.21"` or higher.

The existing React component receives `host.deployment` in deployment mode. It is
`undefined` in the editor:

```tsx
const canSave = !host.deployment || host.deployment.document_actions.includes("save");
// Show Save when canSave; show editor settings only outside deployment.
const snapshot = await host.readDocument();
await host.documentAction("save", { text: "Hello" }, snapshot.revision);
```

See the runnable [Python declaration and handlers](../examples/deployed-workspace/plugins/demo/oaw_deployment_demo.py)
and [shared React view](../examples/deployed-workspace/plugins/demo/frontend/index.tsx).
Start the [deployment example](../examples/deployed-workspace/README.md), open its
plugin notes view, save text, and download it to verify the full path.

## Public surface

| Declaration | Behavior |
| --- | --- |
| `config_fields` | Read-only configuration such as a heading; undeclared connection and credential fields are withheld |
| `document_fields` / `summary_fields` | Only these top-level fields appear in reads and action responses |
| `document_actions` | Allows registered business operations; generic document replacement is not automatically exposed |
| `downloads` | Allows registered exporters, which must emit content suitable for public access |
| `resource_actions={"query": {"rows"}}` | Calls the original resource handler and returns only declared top-level fields |
| `execution=True` | Explicitly allows the node's existing execution status, start, and stop interfaces, including their requests and responses |

Collections default to empty and execution defaults to disabled. Projection is
**top-level**: a selected nested object is exposed in full. Do not expose objects
containing passwords, internal node IDs, or host paths. Handlers still validate
arguments. Allowing an action authorizes its business effects, so generic management
actions that change internal configuration or read arbitrary paths are unsuitable.
Authors must also review exports, execution details, and errors for public exposure.
Plugins remain trusted host code; deployment permissions do not sandbox malicious plugins.

In deployment mode, `host.listCards()` returns published nodes only; cross-node
`readDocument(id)` can access only exposed documents. Configuration updates, Agent
internals, document conversion, arbitrary file reads, and management APIs are outside
this contract and unavailable on the server. Adapt older views to document/resource
business actions; hiding settings buttons alone is insufficient.

## Existing workspace sections

Declare existing `WorkspaceSection` IDs through `sections` without adding a new layout:

```python
deployment = NodeDeploymentDefinition(
    surface=DeploymentSurface(config_fields={"heading"}),
    sections={
        "results": DeploymentSurface(document_fields={"results"}),
        "input": DeploymentSurface(document_fields={"input"}, document_actions={"submit"}),
    },
)
```

Publishing a whole card combines `surface` with its visible sections. Publishing
one section grants that section's permissions. Hidden sections grant none; extracted
sections grant access from their own layout position. `surface=None` allows only
declared sections, not whole-card publication. Permissions from visible sections
combine per node; sections are not isolation boundaries between users.

Unauthorized `WorkspaceSection` children are not mounted in deployment mode, avoiding
requests from hidden controls. Parent loading logic must still check `host.deployment`.
Keep card-wide reads/actions separate from access intended for a private section.

## Install, build, and upgrade

1. Follow [plugin installation](plugins.md#package-structure-and-discovery), declare
   `requires_plugins`, install dependencies, and verify the original workspace in the editor.
2. For source-checkout plugins with a frontend, retain `plugin.json` and `index.tsx`
   under `plugins/<package>/frontend/` and run `npm --prefix frontend run build`.
   A Python wheel alone does not install browser components. The builder also discovers
   example frontends; their cards do not appear without the corresponding backend plugin.
3. Save the Legion layout, follow the [deployment procedure](deployment.md), and copy
   to a new directory. The copy contains data; distribute matching source, frontend
   build, Python dependencies, and external plugin resources as well.
4. Publication fixes versions and permissions. After a plugin upgrade or surface
   change, update the version, verify in the editor, and publish a new copy.
   Do not hot-install plugins into a running deployment; startup rejects version mismatches.

Continue with the [deployment example](../examples/deployed-workspace/README.md)
for verification, or [Plugin API](plugins.md) for the complete extension contracts.
