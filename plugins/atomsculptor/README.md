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
- **AtomSculptor Agent** runs one coordinator with three independent ADK
  task-mode specialists: Planner, Structure Builder and Materials Project.
  Planner is used for ambiguous, multi-stage or Task Board-tracked work, not
  every simple Structure operation. Planner can inspect
  structures and update an explicitly requested Task Board;
  Builder handles structure writes and modelling tools; Materials Project can
  use its authorized Skill and Sandbox but cannot write Atom Structure documents.
  Role tool lists are drawn from graph-authorized OAW capabilities, and OAW
  rechecks authorization when a tool is called. Specialist handoffs, tool
  calls, and model-request start/end timing appear in the normal Run activity
  trace. The new model-boundary events contain sizes, timing and token counts,
  not prompt text, images, credentials or private reasoning.
- **AtomSculptor Skills** packages the 18 retained modelling skills as an OAW
  Skill Toolbox. A runnable script must be explicitly paired with an OAW
  Sandbox. Some Skills are guidance only and do not supply an executable
  builder; their text must not be treated as a tool inventory. The packaged
  nanotube script distinguishes finite tubes with axial vacuum from periodic
  tubes with an axial repeat cell. The bundled `structure-inspect` converters move structures between
  Sandbox files and the canonical document without LLM guessing. For large
  structures, `stage_atom_structure_file` and `import_atom_structure_file`
  transfer exact documents through OAW's pinned Sandbox file boundary; both
  Structure and Sandbox grants are checked at the transfer point.
- **Modelling panel** runs a diagonal supercell operation directly as a
  revisioned document action. Other buttons send validated, typed requests to
  the linked Agent, which can use an authorized Sandbox and Skill. These are
  still Agent-orchestrated workflows, not guaranteed deterministic procedures.
  Interface candidates are stored in the current Structure document and shown
  in its chooser; the selected candidate's Sandbox file is imported later.
  Exports can be published as OAW Artifacts when connected. The panel checks
  the Agent's live OAW graph grants before starting those Agent workflows;
  its Agent/Sandbox/Skill selections are only UI hints.
- A managed model is resolved from **Settings → Models** at provider invocation.
  The credential does not enter card state, events, the browser or a Sandbox.
- **Visual structure observation** is available only when the selected model is
  marked **Vision** in **Settings → Models**, the Agent has an ordinary
  **Inspect structure** or **Modify structure** relationship to the Atom
  Structure, and that structure's workspace is open. The Agent can then call
  `observe_atom_structure` when a current 3D view materially helps. It receives
  one bounded transient PNG plus camera/selection metadata; no screenshot is
  written to the structure document, an Artifact, a Sandbox, or runtime logs.

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

The current `matterviz` dependency requires Node.js 24 or newer; use a
matching Node.js installation when building on macOS.

Then restart both OAW processes so the local Python entry point and the local
frontend plugin entry are discovered. Create an AtomSculptor Agent, an Atom
Structure and an AtomSculptor Skills card. Connect the Agent to the resources it
needs using the displayed relationships. To execute a modelling script, also
connect the Agent to a properly configured OAW Sandbox and, for Materials
Project work, an Environment Profile containing the required secret.

The skill package intentionally makes no attempt to create virtual environments,
install dependencies, manipulate a host environment or bypass OAW's Sandbox.
Scientific packages belong in the explicitly selected Sandbox environment.
For brief environment probes, use a direct `python3 -c` argv call. For custom
modelling and verification, save the complete Python source with the linked
Sandbox's `write_sandbox_text_file` tool, check its returned byte count, and
run that `.py` file in a separate direct `python3` command. The file remains
in the Sandbox workspace for inspection and reruns. Avoid shell heredocs on
macOS Seatbelt; if a shell must invoke Python indirectly, select
`python_environment=managed`. Check stderr and generated files as well as the
command's exit code. A generated extxyz file must still be validated and
imported into the Atom Structure with the current revision.
The Materials Project Skill includes a runnable script but requires `mp-api`,
explicit Sandbox networking and an Environment Profile with `MP_API_KEY`.
Neither the plugin wheel nor the host Python environment needs `mp-api`.
Existing Skill Toolbox cards retain their saved contents after a plugin update;
create a new AtomSculptor Skills card (package version 0.1.2) and reconnect the
Agent to it to use these revised instructions and scripts. Do not delete the
old card until any local edits have been copied over.

Copying only `plugins/atomsculptor` into a newer OAW checkout is not a complete
installation: the host must provide plugin API 1.24, including the guarded
Structure↔Sandbox file bridge and the `write_sandbox_text_file` capability,
and the frontend host method
`getAgentCapabilities` for live connection checks; it must discover the Python
entry point, bundle the frontend module, and install the frontend's Three.js
dependencies. Rebuild the frontend and restart the backend after copying.

## Migration boundary

The retained source-level changes are the removal of CodeGraphRAG/Memgraph,
the AtomSculptor specialist workflow and the structure-editor interaction model.
The old application-specific Sandbox implementation is intentionally excluded.
Further UI migration should use Atom Structure document actions and OAW Artifacts,
not restore the old `/api/*` routes or WebSocket events.

## Remaining integration work

- Model requests emit start, elapsed-time and completion events. AtomSculptor
  does not impose a per-request 240-second deadline or a planner-specific
  output-token cap; live reasoning and waiting updates remain visible, and a
  manual Stop cancels the active stream. OAW's separate Run execution deadline
  and model-provider limits still apply. Outgoing requests restore unambiguous id-less tool responses
  in place and recover completed specialist results by their exact call ID;
  genuinely unresolved calls fail explicitly instead of receiving invented results.
  OAW `ManagedContext` retains the coordinator's conversation, while ADK task
  scopes isolate specialist delegations. A runtime guard limits planner delegation
  to two attempts. Unsigned specialist reasoning and successfully saved script
  bodies are omitted from later model requests; signed provider thoughts,
  tool IDs and tool results are retained.
  AtomSculptor now uses OAW's shared model observation and recovery layer.
  A transient provider failure retries the prepared model request once within
  its current specialist, without redelegating through the coordinator. Partial
  output is preview-only; tool calls are released only after the aggregated
  response and clean EOF. Earlier completed tools remain in context and are not
  replayed. Each attempt has a separate incomplete/complete thinking preview;
  recovery events identify role, request, attempt, exception type and idle time.
  The first-content/stream-idle bounds are 300/90 seconds, not a total request
  deadline. Exhausted retries stop; they do not trigger a second whole-Agent
  retry. This regenerates a response, not provider-side resumption of reasoning.
  Errors outside the model request retain OAW's conservative write-reconciliation
  path. Capability receipts and model checkpoints remain privacy-bounded.
  Live ADK verification on macOS is still needed.
- Supercell is deterministic from both the Structure UI and Agent tool.
  Surface, interface, SMILES and remote publish still require Agent
  orchestration; the typed request only validates their
  parameters. They need dedicated deterministic host-side workflows before
  they can be described as fixed procedures.
- The inspect result bounds coordinate windows, selection and metadata, and
  the dual-authorized file bridge avoids relaying thousands of atom records
  through the model. UI selection retries a revision conflict once against
  the latest document using stable atom IDs. Selection still shares the
  structural revision; file imports can rebase a selection-only change using
  the inspected structure digest while preserving the latest selected IDs.
  Other long-running writes must re-inspect after conflicts; fully separate
  selection state remains future work.
- The frontend checks live Agent capabilities before Agent-run buttons, but a
  different OAW checkout needs the small host SDK method in this branch. A
  clean macOS frontend build and live ADK/Sandbox integration test remain
  necessary after transplanting this plugin.
