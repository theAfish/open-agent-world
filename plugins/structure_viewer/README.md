# Structure viewer

An OAW plugin using MatterViz 0.7.0 for interactive crystal and molecule views.

Restart the backend and Vite after adding this plugin (rebuild for production).
Open the **Structure viewer** pack in the Card Library, collect the card and add
it to a deck. Place a viewer and connect it to a Sandbox or Conversation using
**Follow opened files**. Open both windows, then select a Sandbox file or click
a Conversation attachment's name. Download remains a separate action.

- Supported inputs: CIF/MCIF, POSCAR/CONTCAR/VASP, XYZ/EXTXYZ and structure JSON.
  JSON must contain structure data, not arbitrary JSON. This is a single-structure
  viewer; trajectories and compressed files are not supported by this adapter.
- **Following** selects the most recently opened file among connected sources.
  Click to pin the current file; click **Pinned** to resume following.
- **Reload** rereads the current file. External file edits are not watched.
- Reads are bounded to 16 MiB and rendering to 20,000 input atoms. Errors stay in
  the viewer. Files are read completely, never from a truncated text preview.
- Opening a file does not execute a Sandbox command or send an Agent message.
  Viewer controls change the visualization in memory; they do not write back.
- Disconnecting clears the rendered source and blocks subsequent/in-flight reads.
  Closing the source window or switching Conversation sessions clears its open
  file context. Selection and pin state are local to this browser and ephemeral.

MatterViz is loaded only when a structure is displayed in an inspector or Window.
The upstream package declares Node >=24. The Svelte compiler is integrated with
Vite; `svelte-widgets` is pinned to 1.6.1 because 1.8.0 rejects MatterViz's control
schema at runtime. Do not remove that override without a browser rendering check.

Vite routes Threlte 8.6's size helper through `threlteMeasure.svelte.ts`. It keeps
canvas, camera, HTML labels and pointer picking in local content coordinates;
upstream screen-bound measurements apply React Flow zoom twice. Preserve this
adapter until upstream supports transformed hosts, and check opening files at
non-unit zoom as well as zooming and resizing an already mounted viewer.

The plugin uses `core.file-viewer`, the shared `core.file-preview` relationship,
the SDK's `useFileViewer` and `host.readFile`. Formats and rendering stay in the
plugin; source references, UI state and read authorization belong to the host.
