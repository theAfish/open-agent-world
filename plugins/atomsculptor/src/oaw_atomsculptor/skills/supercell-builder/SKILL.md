---
name: supercell-builder
description: Build supercells from a bulk/slab structure by integer or 3×3-matrix multiplication.
when_to_use: Use to enlarge a unit cell before introducing defects, adsorbates, or molecules. Pair with `supercell-dimension-check` to size correctly.
entry: scripts/supercell_builder.py
---

# Supercell Builder

For simple integer repetitions of the connected Atom Structure, prefer OAW's
deterministic `build_atom_supercell` after inspecting its revision. Use this
Skill only for a file-based workflow or general 3×3 transformation. Read the
Skill from an authorized card, then run the script through `run_skill_script`
and an independently authorized Sandbox. The output file does not update an
Atom Structure card until converted and imported with a fresh revision.

```bash
# diagonal expansion (n1, n2, n3)
python3 scripts/supercell_builder.py build_supercell \
  --folder . --file-name Fe.extxyz --repetitions '[4,4,4]'

# general 3x3 transformation matrix
python3 scripts/supercell_builder.py build_supercell \
  --folder . --file-name Fe.extxyz --repetitions '[[2,0,0],[0,2,0],[0,0,1]]' \
  --output-name Fe_supercell.extxyz
```

Default output format: `extxyz`. If `output-name` is omitted the file is
written as `supercell_<input>.extxyz`.
