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
python3 scripts/structure_inspect.py to_atomsculptor_document --folder . --file-name slab.extxyz --output-name slab.document.json
python3 scripts/structure_inspect.py from_atomsculptor_document --document structure.json --output-name bulk.extxyz
```

`folder` may be `"."` to use the sandbox root. Large CIF files (> 1 MB) are
parsed via `pymatgen.io.cif.CifParser` to avoid ASE's slow path.

Use the two converters whenever a structure must move between an OAW Atom
Structure card and a Sandbox file. Never retype or summarize atom lists. For
small manual workflows, the inline `document` form is supported, but the
file-backed bridge below is the default for Agent workflows.

For large structures, use `stage_atom_structure_file` to copy the exact
revisioned document into the authorized Sandbox without sending it through
the model. Run `from_atomsculptor_document` against that returned JSON path.
After a modelling script, run `to_atomsculptor_document` with `--output-name`
to write a canonical JSON file in the Sandbox, then call
`import_atom_structure_file` with that path and a freshly inspected Structure
revision and `structure_digest`. These two OAW tools check both Structure and Sandbox permissions and
return only file metadata and document summary to the model.
