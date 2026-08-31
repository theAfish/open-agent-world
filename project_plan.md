# Open Agent World — POC Project Plan

## 1. Project Purpose

Open Agent World is a Windows-first experimental agent environment built around an infinite spatial canvas.

The core idea is not to create another workflow editor or DAG orchestration interface. Instead, the application should behave more like a construction / management game: users place agents, resources, and workspaces into an open world, then define how they can interact by connecting them.

The fundamental interaction model is:

**Anything can be an object. Connections determine interaction.**

An Agent is an active worker.

A Text File or Image is a resource.

A Sandbox is an open-ended workplace.

An Edge represents an actual interaction relationship or permission, rather than merely a visual connection.

For example:

* `Agent → Text File [read]` means the agent may read that resource directly.
* `Agent → Text File [edit]` means the agent may modify it through a structured backend interface.
* `Agent → Sandbox [execute]` means the agent may perform open-ended computation inside that workspace.
* `Text File → Sandbox [read-only]` means the file becomes available inside that sandbox but cannot be modified there.
* Removing a connection removes the corresponding capability.

The POC should demonstrate that this world model is technically viable, visually intuitive, and capable of supporting future plugin-based expansion.

## 2. What the POC Needs to Prove

The first version should remain deliberately small.

It only needs to prove five things.

### 2.1 Spatial interaction works

Users should be able to create and organize objects on an infinite canvas, move them freely, connect them, and understand their relationships visually.

### 2.2 Connections correspond to real capabilities

The graph must not be decorative.

If an Agent is connected to a Text File with edit permission, the backend should actually expose an editing capability.

If that connection is removed, the capability must disappear.

### 2.3 Structured interaction and open-ended computation can coexist

Simple operations should not require a sandbox.

An Agent connected directly to a Text File should be able to read or modify it through structured tools.

A Sandbox should instead represent an open-ended computational workspace in which the Agent can use shell commands, scripts, and multiple attached resources together.

### 2.4 Agents can operate inside a safe native Windows sandbox

The POC should run directly on Windows without Docker or WSL.

Sandbox processes must be restricted through Windows security mechanisms rather than through prompt instructions or path checks alone.

### 2.5 The canvas architecture can scale beyond the visible viewport

The POC does not need to handle enormous worlds yet, but it should already use chunk-based loading and visible-element virtualization so that future scaling does not require rewriting the canvas architecture.

# 3. Initial World Objects

The POC contains four card types.

All cards share a common visual language and common lifecycle behavior, but each represents a different kind of world object.

## Agent Card

The Agent Card represents an intelligent worker powered by Google ADK.

The compact state should show:

* agent name;
* current status;
* model;
* a subtle activity indicator.

The expanded state should allow the user to configure:

* system instruction;
* model;
* current capabilities;
* prompt/run controls;
* runtime activity;
* stop controls.

Initial runtime states:

* idle;
* running;
* waiting;
* error.

Each Agent Card should represent an independent logical ADK agent/session.

ADK should only handle agent reasoning and tool execution. It must not own the application world model.

## Text File Card

The Text File Card represents a managed text resource.

The compact state should show:

* filename;
* short text preview;
* basic metadata.

The expanded state should provide:

* editable content;
* save state;
* permissions/relationships;
* simple modification history.

Direct Agent relationships should support:

* read;
* edit.

Text resources may also be attached to a Sandbox as:

* read-only;
* read/write.

## Image File Card

The Image File Card represents an image resource.

The compact state should primarily function as a thumbnail card.

The expanded state should show:

* larger preview;
* filename;
* dimensions;
* basic metadata;
* current Agent/Sandbox relationships.

For the POC, image editing is not required.

Images should support read-only attachment to a Sandbox.

If current ADK multimodal APIs integrate cleanly, direct Agent image inspection may also be implemented. This should remain secondary to the core architecture.

## Sandbox Card

The Sandbox Card represents a workplace rather than a simple resource.

It should feel like a place where an Agent can perform open-ended work.

The compact state should show:

* Sandbox identity;
* current state;
* connected Agent count;
* attached resource count;
* current activity.

The expanded state should show:

* terminal/activity output;
* attached resources;
* active command;
* start/stop controls;
* basic security/runtime information.

Sandbox states:

* stopped;
* ready;
* running;
* error.

The implementation should internally treat Sandbox as a generic execution environment so that it could later be renamed or extended into concepts such as Workspace, Lab, or Workbench.

# 4. Interaction and Capability Model

The world graph consists of Cards and semantic Edges.

Edges should have explicit relationship types instead of being generic connections with arbitrary metadata.

For the POC, support the following relationships.

## Agent → Text File

Possible permissions:

* read;
* read + edit.

This exposes structured backend capabilities.

The Agent does not need a Sandbox to perform these operations.

## Agent → Image File

Permission:

* view.

This represents direct access to image content.

## Agent → Sandbox

Permission:

* execute.

This allows the Agent to invoke commands in that Sandbox.

## Text File → Sandbox

Possible relationships:

* mount read-only;
* mount read/write.

## Image File → Sandbox

Relationship:

* mount read-only.

## Capability resolution

The backend world graph is authoritative.

The effective tools available to an Agent should be derived from its current graph relationships.

For example, an Agent connected to:

* `notes.txt [read/edit]`;
* `Sandbox A [execute]`;

may receive tools conceptually equivalent to:

* read notes;
* edit notes;
* execute command in Sandbox A.

Another Agent without those edges should not receive those tools.

Prefer scoped tools tied to concrete resources rather than global tools accepting arbitrary resource IDs.

This keeps the Agent's action space small and makes permission boundaries easier to understand and enforce.

# 5. System Architecture

The application should be divided into four primary conceptual layers.

## World Model

The World Model stores:

* Cards;
* positions;
* sizes;
* expanded/collapsed state;
* Edges;
* permissions;
* object configuration.

This layer describes what exists in the world.

## Capability Broker

The Capability Broker translates graph relationships into actual permissions.

Every privileged operation should be checked here.

This layer answers:

> What is this Agent allowed to do right now?

Authorization should not be scattered across unrelated API handlers.

## Agent Runtime

Google ADK powers Agent reasoning and tool use.

ADK-specific code should live behind an internal `AgentRuntime` boundary.

The rest of the application should not depend directly on ADK concepts.

This allows the Agent implementation to be replaced later without changing the World Model.

## Sandbox Runtime

Sandbox execution should live behind a `SandboxBackend` abstraction.

The initial implementation will be Windows-native.

Conceptually it should support operations such as:

* create;
* start;
* execute;
* terminate;
* attach resource;
* detach resource;
* destroy.

The upper layers should not depend on Windows-specific APIs.

# 6. Technology Stack

## Frontend

Use:

* React;
* TypeScript;
* Vite;
* React Flow / `@xyflow/react`;
* Zustand;
* CSS variables for theme/design tokens.

React Flow should provide:

* pan;
* zoom;
* node positioning;
* edge interaction;
* selection;
* viewport coordinate handling.

The application should own:

* world state;
* card behavior;
* capability semantics;
* persistence;
* chunk loading;
* card rendering;
* visual design.

## Backend

Use:

* Python 3.11+;
* FastAPI;
* Pydantic;
* SQLite for POC persistence;
* WebSocket for runtime events;
* Google ADK.

Use `uv` for the Python environment if practical.

## Suggested repository structure

Frontend:

```
frontend/
    canvas/
    cards/
    edges/
    palette/
    state/
    theme/
```

Backend:

```
backend/
    api/
    world/
    capabilities/
    resources/
    agents/
    sandbox/
    events/
    persistence/
```

Avoid monolithic frontend or backend modules.

# 7. Windows Sandbox Design

The POC should run natively on Windows 10/11 x64.

Do not depend on:

* Docker;
* Docker Desktop;
* WSL;
* the Windows Sandbox desktop feature.

The Sandbox must represent a real security boundary.

For the initial implementation, use Windows-native isolation based on:

* AppContainer or LPAC;
* NTFS ACLs;
* Job Objects.

Keep this implementation behind `SandboxBackend` so that it can later be replaced by newer Windows sandbox APIs or Linux/macOS backends.

## Managed storage

The application should own a controlled data root, for example:

```
%LOCALAPPDATA%/<project-name>/
```

Containing:

```
projects/
assets/
sandboxes/
database/
logs/
```

Files imported into the world should be copied into this managed storage.

Do not let cards silently reference arbitrary host filesystem paths during the POC.

## Sandbox workspace

Each Sandbox gets its own workspace directory:

```
sandboxes/<sandbox-id>/workspace/
```

Resources become accessible inside this workspace only when explicitly connected to the Sandbox.

A Text File mounted read-only should remain readable but not writable.

A Text File mounted read/write should permit modification.

If practical, use NTFS hard links and ACLs to expose managed files without unnecessary copies. Preserve the attachment abstraction so the implementation can change later.

## Process isolation

Use Job Objects to control the Sandbox process tree.

At minimum support:

* killing all child processes when the Sandbox stops;
* command timeout;
* active process limits;
* reasonable memory limits.

Sandbox execution should be intended for headless command-line workloads.

## Network

Sandbox network access should be disabled by default for the POC.

Internet access is not required yet.

## Environment

Do not blindly pass the host process environment into the Sandbox.

Construct a minimal environment and exclude:

* API keys;
* model credentials;
* SSH-related data;
* application secrets;
* cloud credentials.

The Agent runtime and Sandbox runtime should be treated as separate trust zones.

# 8. Frontend and Visual Design

The application should visually feel like an open scientific work surface rather than a conventional dashboard.

The design language is:

**minimal scientific instrument + restrained neo-neumorphism + card-game object system**

## Infinite canvas background

Use two subtle layers.

### Dot matrix

A fine, low-contrast dot grid provides:

* movement reference;
* scale reference;
* orientation.

### Abstract contour lines

Overlay very faint irregular topographic/contour-like curves.

These lines should:

* remain much lower contrast than Cards;
* move with the world;
* feel abstract rather than geographic;
* add spatial character without becoming decoration.

The combination should feel like an abstract scientific terrain.

## Card appearance

Use restrained modern neumorphism.

Cards should have:

* medium/large rounded corners;
* thin border;
* soft short shadow;
* subtle internal highlight;
* restrained surface gradient;
* strong typography hierarchy;
* generous spacing.

Avoid:

* deep embossing;
* large glossy highlights;
* cyberpunk styling;
* strong neon;
* excessive glow.

## Card states

Support visually distinct but restrained states:

* idle;
* hover;
* selected;
* running;
* expanded;
* error.

Selection may increase border clarity, elevation, and introduce a subtle halo.

Running states may use low-frequency breathing or instrument-like activity animation.

## Compact and expanded modes

Cards should expand in place.

The goal is to preserve spatial identity rather than immediately moving configuration into a generic inspector panel.

Compact mode emphasizes world topology.

Expanded mode exposes configuration and detailed content.

Transitions should be short and smooth.

## Edges

Edges should use restrained curved lines.

They should communicate:

* relationship;
* direction;
* permission.

Use small labels/chips where necessary.

Do not make every relationship a different bright color.

Interaction should emphasize an Edge on hover or selection without making the full canvas visually noisy.

## Component palette

Provide a collapsible palette containing:

* Agent;
* Text File;
* Image File;
* Sandbox.

Dragging from the palette onto the canvas creates a new Card instance.

# 9. Infinite Canvas and Chunk Loading

The canvas should be architected for worlds larger than the current viewport.

For the POC, divide world coordinates into logical chunks, for example approximately:

```
2048 × 2048 world units
```

When the viewport changes:

1. determine visible chunks;
2. include a surrounding prefetch ring;
3. load missing chunk data;
4. render nearby cards;
5. unload distant full card representations.

React Flow's visible-element optimization can provide an additional rendering layer.

The POC does not need to support hundreds of thousands of objects.

The goal is simply to avoid an architecture in which every Card must always exist as a live React component.

# 10. Runtime Events

Use WebSocket for live runtime activity.

Define typed events such as:

* agent started;
* agent status changed;
* tool started;
* tool completed;
* Sandbox command started;
* stdout;
* stderr;
* command finished;
* resource modified;
* permission changed;
* runtime error.

Do not expose hidden chain-of-thought.

Expose operational information that helps the user understand what is actually happening in the world.

# 11. Implementation TODO

Implement the POC as vertical slices rather than building all frontend components first and integrating execution later.

## Phase 1 — Application skeleton

Build:

* React/Vite frontend;
* FastAPI backend;
* React Flow canvas;
* SQLite persistence;
* WebSocket connection;
* shared schemas.

## Phase 2 — World model

Implement:

* four Card types;
* drag/drop from palette;
* movement;
* selection;
* expansion;
* persistence;
* semantic Edges;
* permission selection.

## Phase 3 — Direct Text capabilities

Implement managed Text resources and backend operations for:

* read;
* replace;
* patch/edit.

Connect Agent permissions to these operations.

Use a mocked Agent/tool execution path first if useful.

Verify authorization before ADK integration.

## Phase 4 — ADK integration

Implement the first functional Agent Card.

Add:

* Agent runtime lifecycle;
* ADK session management;
* dynamic tool construction;
* activity streaming.

Tools should be generated from current backend capabilities.

## Phase 5 — Windows Sandbox

Implement the Windows Sandbox backend.

Start with:

* sandbox creation;
* workspace creation;
* secure process execution;
* stdout/stderr capture;
* termination.

Then add:

* ACL setup;
* AppContainer/LPAC identity;
* Job Object containment;
* resource attachment;
* resource detachment;
* permission revocation;
* default network denial.

## Phase 6 — Agent/Sandbox integration

Expose Sandbox execution to ADK as a scoped capability.

An Agent should only receive Sandbox execution tools when the corresponding Edge exists.

## Phase 7 — Image resource

Implement:

* image import;
* thumbnail;
* expanded preview;
* metadata;
* read-only Sandbox attachment.

Add direct Agent image viewing only if it fits the architecture cleanly.

## Phase 8 — Visual polish

Implement:

* dot-grid background;
* contour-line layer;
* neumorphic Card styling;
* light/dark design tokens;
* selection;
* hover;
* expansion transitions;
* Edge styling;
* running indicators.

## Phase 9 — Chunk loading

Implement:

* chunk indexing;
* viewport-based loading;
* prefetch ring;
* distant-card unloading;
* developer stress generator.

## Phase 10 — Hardening

Run:

* backend tests;
* Sandbox isolation tests;
* permission revocation tests;
* persistence/restart tests;
* canvas stress tests.

Refactor only after the complete vertical flow works.

# 12. POC Acceptance Scenarios

The POC should demonstrate the following scenarios end-to-end.

## Scenario A — Create a world

The user opens an empty infinite canvas.

The canvas shows:

* dot matrix;
* faint contour lines.

The user creates:

* one Agent;
* one Text File;
* one Image;
* one Sandbox.

## Scenario B — Direct file interaction

The user writes text into the Text File.

Connect:

```
Agent → Text File
```

with:

```
read + edit
```

Ask the Agent to read and rewrite the file.

The Agent should modify it through structured resource capabilities without using the Sandbox.

## Scenario C — Sandbox interaction

Connect:

```
Text File → Sandbox
```

with:

```
read/write
```

and:

```
Agent → Sandbox
```

with:

```
execute
```

Ask the Agent to process the file through a shell command.

The command should execute inside the Windows Sandbox.

stdout/stderr should appear in the Sandbox Card.

The Text File should reflect the resulting modification.

## Scenario D — Permission revocation

Remove:

```
Text File → Sandbox
```

Attempt to access the file from the Sandbox again.

Access should fail.

## Scenario E — Host filesystem isolation

Attempt to access unrelated user/host locations from the Sandbox.

The request must fail.

## Scenario F — Network isolation

Attempt outbound Internet access.

It should fail by default.

## Scenario G — Process containment

Start a long-running process that spawns a child process.

Stop the Sandbox.

The complete process tree should terminate.

## Scenario H — Canvas scale

Generate roughly 1,000–5,000 lightweight development Cards across multiple chunks.

Verify that:

* distant objects are not fully rendered;
* pan remains usable;
* zoom remains usable;
* normal interaction remains responsive.

# 13. Testing

Testing should focus on the architecture's important invariants.

## Backend tests

Cover:

* capability derivation;
* edge permission changes;
* permission revocation;
* unauthorized resource access;
* path traversal;
* Text resource modification;
* Sandbox lifecycle.

## Sandbox integration tests

Cover:

* allowed workspace read;
* allowed workspace write;
* read-only write rejection;
* unrelated host path rejection;
* network rejection;
* process timeout;
* child process cleanup;
* secure Sandbox creation failure.

## Frontend tests

At minimum cover:

* Card creation;
* Card expansion;
* valid relationship creation;
* invalid relationship rejection;
* permission changes;
* graph state synchronization.

# 14. Hard Constraints

The POC should favor one clear implementation path over defensive compatibility layers.

The goal is not to make every possible Windows environment work. The goal is to make the defined architecture work correctly and transparently.

The following constraints are non-negotiable:

* The backend world graph is the source of truth for Cards, Edges, permissions, and capabilities.
* Frontend state must never grant permissions by itself.
* ADK is only the Agent runtime; it must not own the world model.
* Agent tools must be derived from the Agent's current capabilities.
* Direct resource access and Sandbox access must remain separate interaction modes.
* Sandbox commands must only run through the Windows Sandbox backend.
* If secure Sandbox creation fails, execution fails. Never fall back to a normal host subprocess.
* Sandbox processes must not inherit application secrets or unrestricted host environment variables.
* Sandbox network access is denied by default.
* Removing or modifying an Edge must immediately affect the corresponding backend capability.
* Prefer explicit failure over silent degradation.
* Avoid broad exception handlers that hide programming or Win32 failures.
* Do not add fallback implementations, compatibility layers, or temporary bypasses unless the defined POC architecture actually requires them.
* Keep the intentional `AgentRuntime` and `SandboxBackend` abstractions, but avoid speculative abstractions elsewhere.
* When an invariant is violated, surface the failure and fix the root cause rather than compensating elsewhere.

# 15. Explicitly Out of Scope

Do not implement during this POC:

* plugin marketplace;
* arbitrary third-party Cards;
* subagent creation;
* autonomous Card generation;
* persistent long-term Agent memory;
* multiplayer collaboration;
* cloud runtime;
* Docker;
* WSL;
* remote SSH/HPC;
* MCP ecosystem integration;
* browser tools;
* complex workflow/DAG execution;
* CRDT;
* user accounts;
* production authentication;
* production deployment.

The architecture may leave natural extension points for these features, but no implementation work should be spent on them yet.

# 16. Definition of Done

The POC is complete when:

* the application runs locally on Windows;
* the infinite canvas works;
* dot-grid and contour backgrounds are implemented;
* the component palette works;
* all four Card types work;
* Cards can expand in place;
* Card and Edge state persists;
* semantic Edges affect real capabilities;
* an ADK Agent can run;
* tools are dynamically derived from graph permissions;
* direct Text read/edit works without Sandbox;
* Sandbox command execution is Windows-isolated;
* Text/Image resources can be attached to Sandbox;
* read-only vs read/write permissions are enforced;
* removing an Edge revokes access;
* unrelated host files cannot be accessed;
* Sandbox Internet is unavailable by default;
* Sandbox shutdown kills its process tree;
* there is no unsandboxed fallback execution path;
* runtime events appear in the UI;
* chunk-based canvas loading exists;
* the development stress test remains usable;
* architecture and setup are documented;
* critical backend and Sandbox tests pass.

# 17. Final Development Principle

While implementing, preserve the following distinction:

This application is not primarily a node editor.

It is not primarily a workflow engine.

It is not primarily an ADK interface.

It is a spatial world containing agents, resources, and workplaces.

The graph defines what each actor can interact with.

Direct connections expose constrained, structured capabilities.

Sandbox connections expose open-ended computation.

The visual topology should correspond to the real capability topology.
