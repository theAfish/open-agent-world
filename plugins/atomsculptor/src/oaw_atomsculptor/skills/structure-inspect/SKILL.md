---
name: structure-inspect
description: Read and validate atomic structures — summarize a file, dump raw text, measure inter-atomic distances, detect overlapping atoms.
when_to_use: Use to confirm what is in a structure file before / after another step (atom count, formula, cell, PBC), to measure a specific bond distance, or to flag unrealistic close contacts.
entry: scripts/structure_inspect.py
---

# Structure Inspect

Six CLI sub-commands for *reading* and *checking* structure files, plus two
deterministic converters for the AtomSculptor document:

- `read_structure`         — formula, num atoms, cell, PBC, full atom list if ≤ 10 atoms.
- `read_structures_in_text`— raw file contents (text formats only).
- `calculate_distance`     — distance between two atom indices.
- `check_close_atoms`      — pairs whose distance is below `covalent_radii_sum + tolerance`.
- `to_atomsculptor_document` — convert a structure file to the canonical
  StructureDocument JSON (stable atom IDs preserved when present).
- `from_atomsculptor_document` — write a StructureDocument (inline JSON or a
  workspace JSON path) to a structure file, keeping atom IDs and layers as
  per-atom columns.

```bash
python3 scripts/structure_inspect.py read_structure --folder . --file-name Fe_bcc.extxyz
python3 scripts/structure_inspect.py calculate_distance --folder . --file-name slab.extxyz --index1 0 --index2 5
python3 scripts/structure_inspect.py check_close_atoms --folder . --file-name slab.extxyz --tolerance -0.3
python3 scripts/structure_inspect.py to_atomsculptor_document --folder . --file-name slab.extxyz
python3 scripts/structure_inspect.py from_atomsculptor_document --document structure.json --output-name bulk.extxyz
```

`folder` may be `"."` to use the sandbox root. Large CIF files (> 1 MB) are
parsed via `pymatgen.io.cif.CifParser` to avoid ASE's slow path.

Use the two converters whenever a structure must move between an OAW Atom
Structure card and a Sandbox file. Never retype or summarize atom lists: pass
the `document` JSON from `to_atomsculptor_document` unchanged to
`replace_atom_structure`, and give `from_atomsculptor_document` the inspected
document so generated files keep stable atom identities.
