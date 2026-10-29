# AtomSculptor for Open Agent World

Build and edit atomistic structures with an **Atom Structure** card and an
**AtomSculptor Agent**. The Pack also supplies modelling Skills. Structures are
revisioned documents with stable atom IDs, cells, periodic boundaries and layers.

## Install

Install the `.oawpack` through **Library → Packs → Install Pack from File...**
and follow its dependency/restart instructions. This Pack declares Plugin API
1.35; use a compatible host. See [the source manifest](oaw.pack.toml) for its
managed environments and feature prerequisites.

From a source checkout, run OAW setup with `--plugin-path plugins/atomsculptor`
and the needed `--runtime adk --runtime litellm` options (PowerShell also accepts
these portable flags). Use the same development profile at setup and launch.
[Source setup](../../docs/developers/setup.md) explains the platform commands.
The modelling Python environment is prepared for the selected Sandbox, separately
from the host adapter. Discovery alone does not install dependencies.

## Use

1. Open the AtomSculptor Pack, add its cards to a deck and place a Structure,
   Agent and the Skills you need.
2. Select a configured model for the Agent. Start a Sandbox when your operation
   needs scripts, and grant the Agent the required Sandbox and Skill connections.
3. Connect the Agent to Structure with **Inspect structure** or **Modify structure**
   according to the task. The panel's selected Agent and Sandbox are only choices;
   they do not replace graph permissions.
4. Import a supported structure or describe a modelling task. Review Run activity
   and inspect the resulting file/document and measurements.
5. Save or export the structure. Publish files as Artifacts only through the
   required connected resources.

The editor supports selection, layer visibility, copy/cut/paste and undo/redo.
Supported import/export formats include JSON, XYZ, ExtXYZ, LXYZ, CIF, POSCAR, PDB,
SDF and MOL2. **Follow opened files** can connect a Sandbox file source to the
Structure card; importing into its document is explicit.

## Boundaries

The diagonal supercell action is deterministic. Surface, interface, SMILES and
other modelling requests use Agent orchestration; typed parameters do not make
them fixed scientific procedures. Inspect all generated structures and evidence.
Materials Project access needs its configured credential. Visual observation
requires a Vision-enabled model, structure access and an open Structure workspace.

Skills execute only through an authorized Sandbox; some are guidance without an
executable builder. Structure/file transfers recheck both Structure and Sandbox
grants. Removing a connection revokes subsequent access.

## Further reading

- [Modelling tools, measurement and delivery contract](docs/modelling-contract.md).
- [Run diagnostics and exports](docs/diagnostics.md).
- [Skills](src/oaw_atomsculptor/skills) and [implementation](src/oaw_atomsculptor).
