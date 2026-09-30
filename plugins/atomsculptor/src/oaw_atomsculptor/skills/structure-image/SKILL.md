---
name: structure-image
description: Render a PNG image of an atomic structure (rotation, DPI controllable) using ASE + matplotlib.
when_to_use: Use to write a PNG file of a Sandbox structure when an image artifact is requested. For the live open Structure workspace, use the authorized observe_atom_structure capability instead.
entry: scripts/structure_image.py
---

# Structure Image

This script renders a Sandbox file; it does not capture the live OAW Structure
workspace and does not update an Atom Structure card. Read the Skill from an
authorized card and run `scripts/structure_image.py` with `run_skill_script`
and an independently authorized Sandbox. Its PNG remains a Sandbox file until
an explicitly requested Artifact publish operation succeeds.

```bash
python3 scripts/structure_image.py generate_structure_image \
  --folder . --file-name slab.extxyz \
  --output-image-name slab.png \
  --rotation '10x,20y,30z' --dpi 150
```
