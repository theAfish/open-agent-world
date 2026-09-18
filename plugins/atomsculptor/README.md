# AtomSculptor for Open Agent World

This plugin migrates AtomSculptor onto the Open Agent World (OAW) contracts. It
does not start the former Starlette service, browser WebSocket protocol, custom
Sandbox, package installer or command allow-list.

## What it contributes

- **Atom Structure** is a revisioned OAW document with validated atoms, unit
  cell, periodic boundary conditions, layers, and stable atom IDs. Its workspace
  supports selection, layer visibility, copy, cut, paste, undo, redo, and
  JSON/XYZ/ExtXYZ/LXYZ/CIF/POSCAR/PDB/SDF/MOL2 import/export through document
  actions. The 3D display is the plugin's own Three.js editor
  (`frontend/legacy/StructureViewport.tsx`), not MatterViz and not the retired
  Starlette application. The card is also a `core.file-viewer`: connect it to a
  Sandbox with **Follow opened files**, open a structure file in the Sandbox's
  native file tree, and import it into the document from the workspace panel.
- **AtomSculptor Agent** retains the Planner, Structure Builder and Materials
  Project specialist roles. It accesses only graph-authorized OAW tools.
- **AtomSculptor Skills** packages the 18 retained modelling skills as an OAW
  Skill Toolbox. A runnable script must be explicitly paired with an OAW
  Sandbox. The bundled `structure-inspect` converters move structures between
  Sandbox files and the canonical document without LLM guessing.
- **Modelling panel** sends structured, declarative requests (surface slab,
  supercell, interface, SMILES molecule) to the linked Agent. The Agent alone
  executes: it materializes the current structure into its authorized Sandbox,
  runs the selected Skill through `run_skill_script`, converts the output with
  the deterministic converter, and writes the document back with
  `expected_revision`. Interface candidates are written as separate Structure
  cards for the chooser, and exports can be published as OAW Artifacts the same
  way. Progress, dependency problems and output files stay in OAW's native
  Sandbox UI.
- A managed model is resolved from **Settings → Models** at provider invocation.
  The credential does not enter card state, events, the browser or a Sandbox.

## Setup

The backend runtime uses OAW's optional Google ADK and LiteLLM dependencies.
From the OAW repository root, install them before starting the backend:

```sh
uv sync --project backend --extra adk --extra litellm
```

Install the frontend dependencies from `frontend/` after copying or updating the
plugin (the OAW frontend now declares Three.js directly):

```sh
npm install
```

Then restart both OAW processes so the local Python entry point and the local
frontend plugin entry are discovered. Create an AtomSculptor Agent, an Atom
Structure and an AtomSculptor Skills card. Connect the Agent to the resources it
needs using the displayed relationships. To execute a modelling script, also
connect the Agent to a properly configured OAW Sandbox and, for Materials
Project work, an Environment Profile containing the required secret.

The skill package intentionally makes no attempt to create virtual environments,
install dependencies, manipulate a host environment or bypass OAW's Sandbox.
Scientific packages belong in the explicitly selected Sandbox environment.

## Migration boundary

The retained source-level changes are the removal of CodeGraphRAG/Memgraph,
the AtomSculptor specialist workflow and the structure-editor interaction model.
The old application-specific Sandbox implementation is intentionally excluded.
Further UI migration should use Atom Structure document actions and OAW Artifacts,
not restore the old `/api/*` routes or WebSocket events.
