# Card Factory (basic edition)

[Chinese guide](card-factory.zh-CN.md)

Card Factory is a bundled Pack with ID `oaw.factory`. After restarting OAW,
open it in **Library → Packs** and place its workbench on the canvas. The
workbench contains five connected devices; you can also place and connect
individual devices.

## Design and print

1. **Pack Designer** sets the Pack ID, name, version, author, description,
   instructions, packaging style and theme colour. Click the preview to open
   or close the packaging.
2. **Face Designer** selects a layout, style, semantic content and finish.
   Advanced mode exposes layers, shapes, constraints and style tokens.
3. **Function Designer** defines text, number and switch fields with defaults.
   Basic operations support `{{name}}` templates, sum, product and ordered
   concatenation. Test the operation on its test bench. A connected Face Designer
   exposes saved buttons from enabled views; new workbenches include this link.
   Custom button logic bindings are reserved metadata and do not execute yet.
4. **Card Printer** connects one face designer and one function designer.
   Save the designs and choose **Print Card** to create an independent card.
   Open the printed card to fill its form and run it. Later designer edits
   do not change cards already printed.
5. **Packer** connects a pack designer and accepts cards or Legion references
   dragged from the canvas or Library. Export uses their current configuration;
   removing a reference does not delete the original.

Common parameters match field identifiers and override custom-card defaults,
including cards inside Legions. Values must match field types. Original cards
and arbitrary configuration from other plugins are preserved.

## Export and install

Choose **Check Pack** to inspect dependencies and release findings, then
**Export .oawpack** to check again and create an installable file. **Share File**
uses the operating system's sharing interface when available; otherwise the
browser downloads it. This edition does not publish to an online store.

Recipients install the file through **Library → Packs → Install from file**
and restart OAW. Packs containing only printed cards need no author-side
factory devices or code. Cards and Legions from other plugins retain their
original Pack dependencies, which recipients must install first.

## Design controls and limits

Face Designer supports eight layouts, six styles, image and semantic content
slots, finishes, undo/redo, deterministic polishing and separate node, card,
detail and workspace modes. Advanced controls include shapes, free layers,
constraints, snapping and embedded PNG backgrounds or silhouettes. Saved
designs and images travel inside the Pack. See the
[Chinese guide](card-factory.zh-CN.md) for detailed controls.

Each card supports at most 20 form fields; each Pack supports 30 common
parameters and 50 source items. Each Legion supports 101 nodes. Each display
mode supports 24 shapes and 32 content layers, with 24 vertices per polygon.
PNG images are limited to 1 MiB and 2048 × 2048 pixels. A recipe is limited
to 8 MiB. Printed cards retain their design dimensions; resize the design
and print again to change them.

Initial documents, resources and shared state are excluded by default.
Include them explicitly when needed. Model and machine bindings follow
existing Legion portability rules. Keep the Pack ID and increase its version
for updates; installed content at the same ID and version cannot be overwritten,
and existing canvas cards are not rewritten by updates.

## Developer contract

`plugins/card_factory` supplies the devices and workbench. The host's
`backend/packs/factory.py` reuses capture locks, template projection, dependency
inference and the installer. Content Pack schema 2 supports optional
`content.cards`, a list of declarative card JSON paths. `content.legions` may be
empty, but a Pack must contain at least one card or Legion template.

Recipes contain namespaced IDs, face designs and declarative function settings.
They do not execute custom Python or JavaScript, install hooks or make network
requests. Complex workflows can be shared through configured Legions.
Optional `face.studio` v1 stores display modes, semantic designs, shapes and
layers. Legacy recipes without `studio` retain their original configuration.

Run the factory, content-Pack and installation backend tests, the frontend
build, and `e2e/card-factory.spec.ts` when changing this workflow.

Card recipes may include `tutorials` for [progressive tutorials and offline
documentation](developers/progressive-tutorials.md). The host registers node types
and renders forms; archives still cannot contain Python wheels, JavaScript,
undeclared files or install hooks. Factory devices can be saved in Legions;
the Packer's external source references are cleared when captured.
