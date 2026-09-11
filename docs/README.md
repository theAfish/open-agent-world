# Open Agent World documentation

[Project home](../README.md)

Use these guides to look up a feature or understand how it works. Start with setup if you are new; the other pages can be read independently.

| I want to... | Read |
| --- | --- |
| Install, launch, or contribute | [Getting started](getting-started.md) |
| Understand cards, connections, agents, and capabilities | [Core concepts](concepts.md) |
| Configure models, credentials, or application storage | [Configuration](configuration.md) |
| Collect cards and organize the bottom tray | [Packs, Card Library, and Decks](card-library.md) |
| Organize and reuse a team | [Legion team spaces](legions.md) |
| Use or develop an extension | [Plugins](plugins.md) |
| Work with files and run commands | [Sandbox workspace](sandbox-workspace.md) |
| Configure command variables, secrets, and targets | [Execution configuration](execution-configuration.md) |
| Enable Sandbox networking | [Sandbox networking](sandbox-networking.md) |
| Use the managed Python environment | [Shared Python runtime](shared-python-runtime.md) |

## Technical reference

- [Architecture](architecture.md): authority, persistence, interaction flows, and canvas scaling.
- [Scoped canvas automation](canvas-control.md): host-issued control scopes, field policy, revisions, and synchronization.
- [Runs and runtime providers](runs.md), [runtime state](state.md), and [execution lifecycle and durable outputs](lifecycle-artifacts.md).
- [Plugin work-source execution](execution.md), [node effects](node-effects.md), and [canvas placement](layout.md).
- [Security and isolation contract](security.md): trust zones and platform boundaries.

## Scope and future learning

The interactive tutorial is planned, not implemented. It will introduce basic operations and guide a small working example, linking here for deeper explanations.

This repository documents OAW, core functionality, bundled plugins, and plugin development. Plugin-specific guides can live with their packages; see the [plugin guide directory](plugins.md#bundled-plugin-guides). In-app viewing of plugin documentation and tutorials is a future direction, with no documentation delivery API promised here.

[MatCreator demo plan](matcreator-demo-plan.md) is a design/planning document, not a guide to guaranteed current behavior.
