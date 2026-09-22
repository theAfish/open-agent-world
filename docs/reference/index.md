# Technical reference

Use this section to look up a contract or investigate host behavior. For a guided introduction, start with the [user guide](../user-guide/index.md) or [plugin developer guide](../developers/index.md).

## Plugin contracts

| Concern | Reference |
| --- | --- |
| Package discovery, identity, registration, configuration | [Plugin API](../plugins.md) |
| Persistent card state and state namespaces | [Card state lifecycle](../card-state.md) |
| Reversible resource creation and cleanup | [Lifecycle transactions](../plugins.md#lifecycle-transactions) |
| Agent tools and authorization | [Relationships, traits, and capabilities](../plugins.md#relationships-traits-and-capabilities) |
| Task readiness, outcomes, and execution | [Work-source execution](../execution.md) |
| Durable outputs | [Lifecycle and artifacts](../lifecycle-artifacts.md) |
| Custom views in published workspaces | [Plugin deployment](../plugin-deployment.md) |

## Host implementation

[Architecture](../architecture.md), [security](../security.md), [runs](../runs.md), and [runtime state](../state.md) explain how the host enforces these contracts. [Canvas control](../canvas-control.md) and [placement](../layout.md) cover host interactions.

Implementation notes and advanced behavior live here so everyday user instructions can stay focused on actions and results. Design drafts remain in the repository and are not included in the published navigation or search.
