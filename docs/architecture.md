# Open Agent World architecture

Open Agent World is a spatial capability system. The graph is not a workflow diagram: a card represents a persisted world object, and a semantic edge grants one precise interaction between two objects.

[Documentation](README.md) / [Core concepts](concepts.md)

The application uses React, TypeScript, Vite, React Flow, and Zustand in the frontend; FastAPI, SQLite, and registered Agent runtime providers in the backend. Google ADK is the default provider.

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
  |-- StateStore + StateContext (scoped runtime state and inherited reads)
  |-- RunManager + durable RunStore
  |    `-- RuntimeProvider (Google ADK built-in; plugins may add others)
  `-- SandboxBackend (Windows, Linux, or WSL2 isolation)
```

The frontend may request a mutation, but it cannot grant a capability. Every protected operation asks the capability broker to resolve the current graph inside the backend request. No capability is cached across an operation boundary, so changing or deleting an edge takes effect immediately.

## World model

A card stores identity, namespaced type, owning plugin ID, world position, size, expansion state, configuration, timestamps, and a revision. An edge stores a source, target, owning plugin ID, and one registered semantic relationship. Built-in definitions are installed through the same descriptor-scoped registration mechanism as trusted Python plugins.

| Source | Target | Relationships |
| --- | --- | --- |
| Agent | Agent | `communicate` |
| Agent | Text | `read`, `read_edit` |
| Agent | Image | `view` |
| Agent | Sandbox | `execute`, `execute_manage` |
| Text | Sandbox | `mount_read_only`, `mount_read_write` |
| Image | Sandbox | `mount_read_only` |

`PluginRegistry` is the single rule authority. Entry points create application-scoped plugin instances with versioned descriptors; registration is staged atomically and every contribution retains its owner. The registry publishes plugin descriptors and serializable UI metadata at `GET /api/catalog`, while configuration models and executable capability handlers stay in the trusted backend. Endpoint rules can match exact node types and/or declared traits. A connection gesture is unordered: when only its reverse orientation matches, the frontend and backend normalize it to the relationship's canonical source and target.

The backend rejects unsupported, duplicate, and self-referential edges. Scoped capabilities are generated from registered capability grants; there is no global “resource by ID” tool exposed to an agent.

An Agent-to-Agent `communicate` edge has a persisted direction. A `forward` edge exposes one target-scoped messaging tool to the source Agent, while a `bidirectional` edge exposes the corresponding scoped tool to both Agents. Invoking either tool starts the other Agent with the message and returns its final response. The permission and direction are re-checked at invocation time like every other graph-derived capability.

## Direct interaction flow

1. The user connects an Agent to a Text card.
2. The backend validates and persists the semantic edge.
3. When the Agent runs, the capability broker resolves that Agent's current edges.
4. `RunManager` creates a durable Run and its independent Run state scope, builds
   the invocation's world/agent/session/run `StateContext`, resolves the Agent's
   registered `RuntimeProvider`, and supplies resource-scoped read/edit tools only
   for the resolved resources.
5. A tool call checks the broker again, modifies the managed resource, records history, and publishes an operational event.
6. Deleting the edge makes the next check fail without restarting the Agent service.

All graph-derived capability calls share one error boundary. A node operation
failure is returned to the calling Agent as structured tool data with an error
code, type, and message, allowing the Agent to retry, choose another action, or
explain the failure. Such failures do not terminate the calling Run. Runtime
cancellation remains control flow and is propagated immediately; a Run fails
only when its runtime/provider itself cannot continue.

Direct resource tools never route through a Sandbox.

## Sandbox interaction flow

1. Resource-to-Sandbox edges define mounts and their access mode.
2. Agent-to-Sandbox `execute` grants inspection and execution; `execute_manage` also grants Start/Stop. Both use live capability checks.
3. The runtime exposes the configured workspace and currently authorized attachments.
4. Commands run with a minimal environment and process-tree limits inside the selected boundary: AppContainer/Job Objects on Windows, or Bubblewrap, seccomp, and cgroup v2 on Linux and WSL2.
5. Networking is disabled by default. Explicit enabled networking requires the platform's separate policy components.
6. stdout, stderr, lifecycle changes, and resource changes are published as typed events. Revocation prevents subsequent unauthorized access.

Missing isolation prerequisites fail closed; there is no ordinary host-process fallback. See [Sandbox workspace](sandbox-workspace.md), [networking](sandbox-networking.md), and [security](security.md) for platform-specific contracts.

## Canvas scaling

World coordinates are indexed into 2048-by-2048 logical chunks. The client derives the visible chunk rectangle from the React Flow viewport, requests that rectangle plus a one-chunk prefetch ring, and keeps only nearby cards as full React components. React Flow's own visible-element optimization is a second rendering layer, not the primary data strategy.

Only edges whose two endpoints are loaded are returned, so React Flow never receives an edge pointing to an absent node. The one-chunk prefetch ring keeps nearby cross-boundary topology visible. Distant persisted cards remain in SQLite and are fetched again as the viewport moves.

## Events

The WebSocket carries operational facts, never hidden reasoning. Event types cover Run, Agent, State, and Sandbox lifecycle, tool start/completion, stdout/stderr, command completion, resource modification, permission changes, and runtime errors. A reconnect triggers a fresh world snapshot; the event stream is not treated as the persistence source of truth. Run history and state are independently authoritative in SQLite.

## Storage and lifecycle

Application-owned data lives below the configured managed root. Imported resources use managed IDs and validated paths. User-selected external Sandbox workspaces remain external and are not deleted with the card. [Configuration](configuration.md#application-storage) describes locations, backups, and relocation.

[Runs](runs.md) defines durable execution records and runtime-provider behavior. [Runtime state](state.md) explains scope and inheritance. [Execution lifecycle and durable outputs](lifecycle-artifacts.md) describes recovery, cleanup, published versions, and retention; provider output and transient events are not persistence authority.

## Code navigation

| Area | Responsibility |
| --- | --- |
| `frontend/src/api`, `frontend/src/state` | API/event boundary and client state |
| `frontend/src/canvas`, `frontend/src/cards`, `frontend/src/edges` | Canvas, card surfaces, and connections |
| `backend/world`, `backend/persistence` | Authoritative graph and SQLite transactions |
| `backend/capabilities`, `backend/resources` | Permission checks and managed resources |
| `backend/agents`, `backend/runs` | Runtime adapters and durable execution |
| `backend/sandbox` | Platform isolation and workspace execution |
| `open_agent_world`, `plugins` | Public plugin API and bundled extensions |

Plugin ownership and registration contracts are in [Plugins](plugins.md).
