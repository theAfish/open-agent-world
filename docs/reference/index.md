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

## Application and runtime

Start with [core concepts](../concepts.md), then look up [configuration](../configuration.md),
[cards and decks](../card-library.md), [Legions](../legions.md), [runs](../runs.md),
or [runtime state](../state.md). For execution environments, follow
[Sandbox workspace](../sandbox-workspace.md) → [execution configuration](../execution-configuration.md)
→ [networking](../sandbox-networking.md).

## Canvas and rendering

[Canvas control](../canvas-control.md) defines interaction ownership;
[placement](../layout.md) defines geometry. Continue with
[canvas performance](../canvas-performance.md), [terrain rendering](../terrain-webgl.md),
and [card finishes](../developers/card-finishes.md) for rendering details.

## Host implementation

[Architecture](../architecture.md), [security](../security.md), [runs](../runs.md), and [runtime state](../state.md) explain how the host enforces these contracts. [Canvas control](../canvas-control.md) and [placement](../layout.md) cover host interactions.

Use [Contribute to OAW](../contributing/index.md) for the host development and release
workflow. [Maintainer records](../internal/README.md) contain proposals and dated
validation evidence on GitHub; they are excluded from the site and offline manual.
