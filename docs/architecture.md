# Open Agent World architecture

Open Agent World is a spatial capability system. The graph is not a workflow diagram: a card represents a persisted world object, and a semantic edge grants one precise interaction between two objects.

## Runtime boundaries

```text
React work surface
  |  REST catalog/snapshots/mutations + typed WebSocket events
  v
FastAPI application
  |-- Plugin registry (nodes, relationships, capabilities, runtime providers)
  |-- World repository (SQLite is authoritative)
  |-- Capability broker (derives permissions from current edges)
  |-- Managed resources (never arbitrary host paths)
  |-- RunManager + durable RunStore
  |    `-- RuntimeProvider (Google ADK built-in; plugins may add others)
  `-- SandboxBackend (Windows security boundary)
```

The frontend may request a mutation, but it cannot grant a capability. Every protected operation asks the capability broker to resolve the current graph inside the backend request. No capability is cached across an operation boundary, so changing or deleting an edge takes effect immediately.

## World model

A card stores identity, namespaced type, world position, size, expansion state, configuration, timestamps, and a revision. An edge stores a source, target, and one registered semantic relationship. Built-in definitions are registered through the same API exposed to trusted Python plugins.

| Source | Target | Relationships |
| --- | --- | --- |
| Agent | Agent | `communicate` |
| Agent | Text | `read`, `read_edit` |
| Agent | Image | `view` |
| Agent | Sandbox | `execute` |
| Text | Sandbox | `mount_read_only`, `mount_read_write` |
| Image | Sandbox | `mount_read_only` |

`PluginRegistry` is the single rule authority. It publishes serializable UI metadata at `GET /api/catalog`, while configuration models and executable capability handlers stay in the trusted backend. Endpoint rules can match exact node types and/or declared traits. A connection gesture is unordered: when only its reverse orientation matches, the frontend and backend normalize it to the relationship's canonical source and target.

The backend rejects unsupported, duplicate, and self-referential edges. Scoped capabilities are generated from registered capability grants; there is no global “resource by ID” tool exposed to an agent.

An Agent-to-Agent `communicate` edge has a persisted direction. A `forward` edge exposes one target-scoped messaging tool to the source Agent, while a `bidirectional` edge exposes the corresponding scoped tool to both Agents. Invoking either tool starts the other Agent with the message and returns its final response. The permission and direction are re-checked at invocation time like every other graph-derived capability.

## Direct interaction flow

1. The user connects an Agent to a Text card.
2. The backend validates and persists the semantic edge.
3. When the Agent runs, the capability broker resolves that Agent's current edges.
4. `RunManager` creates a durable Run, resolves the Agent's registered `RuntimeProvider`, and supplies resource-scoped read/edit tools only for the resolved resources.
5. A tool call checks the broker again, modifies the managed resource, records history, and publishes an operational event.
6. Deleting the edge makes the next check fail without restarting the Agent service.

Direct resource tools never route through a Sandbox.

## Sandbox interaction flow

1. Resource-to-Sandbox edges define mounts and their access mode.
2. Agent-to-Sandbox `execute` defines who may invoke that workplace.
3. `SandboxBackend` materializes only the current attachments in the Sandbox workspace.
4. A command is launched with an AppContainer/LPAC identity, explicit ACL grants, a scrubbed environment, no network capability, and Job Object containment.
5. stdout, stderr, lifecycle changes, and resource changes are published as typed events.
6. Attachment removal revokes the ACL/materialization before another command can run.

If any required Windows security primitive cannot be established, creation or execution fails. There is no normal-subprocess fallback.

## Canvas scaling

World coordinates are indexed into 2048-by-2048 logical chunks. The client derives the visible chunk rectangle from the React Flow viewport, requests that rectangle plus a one-chunk prefetch ring, and keeps only nearby cards as full React components. React Flow's own visible-element optimization is a second rendering layer, not the primary data strategy.

Only edges whose two endpoints are loaded are returned, so React Flow never receives an edge pointing to an absent node. The one-chunk prefetch ring keeps nearby cross-boundary topology visible. Distant persisted cards remain in SQLite and are fetched again as the viewport moves.

## Events

The WebSocket carries operational facts, never hidden reasoning. Event types cover Run, Agent, and Sandbox lifecycle, tool start/completion, stdout/stderr, command completion, resource modification, permission changes, and runtime errors. A reconnect triggers a fresh world snapshot; the event stream is not treated as the persistence source of truth. Run history is independently authoritative in SQLite.

## Storage

All application-owned data lives below a single managed root. Imported resources are copied into that root and addressed by opaque IDs. Resolved paths are verified to remain below their expected managed directory before any file operation.

The default Windows layout is:

```text
%LOCALAPPDATA%/OpenAgentWorld/
  projects/
  assets/
  sandboxes/
  database/
  logs/
```
