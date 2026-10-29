# Plugin deployment

Existing plugin views and registered business operations can be used directly in a
[published application](deployment.md). The project owner chooses the workspaces
and reviews a reminder about their data access and computation. Plugins do not need
an additional publication declaration. Backend dependencies keep their nodes,
connections, configuration, and runtime in the copied profile.
The editor and deployed application load the same `frontend.workspace` or
`frontend.body` component; the publisher does not maintain a plugin-name allowlist.

The runtime API is scoped to the selected workspace graph. Credentials and private
instructions remain server-side, and global management APIs are unavailable.
`NodeDeploymentDefinition` is an optional restriction on exposed fields or actions.

## Document-backed cards (Plugin API 1.36)

Implement the ordinary card once: `frontend.workspace` / `frontend.body`,
`NodeDocumentDefinition`, and the existing `host.readDocument`,
`host.documentAction`, and `host.documentDownloadUrl` calls. If its complete
document, action responses and downloads are user-facing, add one declaration:

```python
deployment=NodeDeploymentDefinition.from_document()
# Or: NodeDeploymentDefinition.from_document(read_only=True)
```

There is no second component, HTTP endpoint, or Workspace adapter. The publisher
freezes the registered fields, actions and downloads into the release. Read-only
mode selects only actions marked `read_only`; edit mode selects registered actions.
The host's generic `replace` operation is not added. Search/detail actions retain
their original response shapes, revision checks and persistence.

Undeclared cards default to their registered document actions, downloads, native
business operations and computation. This shorthand is useful for explicitly
restricting a view, especially with `read_only=True`. For mixed public/private data,
use the precise declaration below. Project ownership decides publication; the
runtime enforces workspace membership and protects host management and credentials.

The shorthand sets `shared_sections=True`: ordinary `WorkspaceSection` IDs can be
composed without repeating permission declarations. All such sections share the
card's business API; hiding a section changes presentation, not data access.
Use explicit `sections` when different sections need different permissions.

The administrator composes the existing cards into a Legion, saves its layout and
opens **Publish application**. A read-only preflight lists all incompatible panes
before creating a release. Publication checks the layout again; it does not alter
the engineering profile or a running release.

## Selective integration

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
| `document="read"` / `"edit"` | Opts into the whole existing document API; expands registered reads or all actions plus downloads at publication |
| `config_fields` | Read-only configuration such as a heading; undeclared connection and credential fields are withheld |
| `document_fields` / `summary_fields` | Only these top-level fields appear in reads and action responses |
| `document_actions` | Allows registered business operations; generic document replacement is not automatically exposed |
| `downloads` | Allows registered exporters, which must emit content suitable for public access |
| `resource_actions={"query": {"rows"}}` | Calls the original resource handler and returns only declared top-level fields |
| `execution=True` | Explicitly allows the node's existing execution status, start, and stop interfaces, including their requests and responses |
| `file_preview=True` | Reuses `host.readFile` for a `core.file-viewer`; both the viewer and source must be published and connected by the existing preview grant |

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

Agent Graph and Jobs use their existing connected-source APIs, selected-session
checks and job controls. Publishing these views exposes the connected sources'
business projections, not their configuration. Artifact collections provide list,
preview and download access to referenced versions; retention administration,
global artifact browsing and copying into another Sandbox remain private.
File viewers follow only published Conversation / Sandbox sources; Sandbox reads
are limited to the `workspace` root. The unchanged MatCreator research layout uses
these host capabilities and the document shorthand for its knowledge graph.

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
2. For distributed plugins with a frontend, build the Pack's UI independently and
   include its compiled assets in a [`.oawpack`](pack-distribution.md). Install it
   in the editor profile before publication; no host frontend rebuild is required.
   A Python wheel alone does not install browser components. When developing the
   OAW source checkout itself, `npm --prefix frontend run build` also prepares its
   source-Pack frontends.
3. Save the Legion layout, follow the [deployment procedure](deployment.md), and copy
   to a new directory. The copy contains data; distribute matching source, frontend
   build, Python dependencies, and external plugin resources as well.
4. Publication fixes versions and permissions. After a plugin upgrade or surface
   change, update the version, verify in the editor, and publish a new copy.
   Do not hot-install plugins into a running deployment; startup rejects version mismatches.

Continue with the [deployment example](../examples/deployed-workspace/README.md)
for verification, or [Plugin API](plugins.md) for the complete extension contracts.
