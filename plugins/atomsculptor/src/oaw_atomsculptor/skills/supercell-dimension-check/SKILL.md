---
name: supercell-dimension-check
description: Size periodic supercells for defects, adsorbates and molecular guests before building them.
when_to_use: Read before enlarging a periodic structure that must host a defect, molecule or adsorbate.
---

# Supercell dimension check

This is an instruction-only OAW Skill. The old `structure_tools.py` command is
not bundled. For a connected Atom Structure and integer repetitions, prefer
the deterministic `build_atom_supercell` capability after inspecting the
document and passing its current `expected_revision`. For general matrix
transformations of a Sandbox file, use the separately connected
`supercell-builder` Skill through `run_skill_script` and an authorized Sandbox.

1. Inspect the starting structure's atom count, three cell vectors and PBC.
   Distinguish primitive and conventional settings; do not assume one from
   an atom count or a file format alone.
2. Calculate proposed output atom count as input count times the absolute
   determinant of the integer supercell transform. Check the resulting cell
   vectors, not only their scalar lengths, especially for non-orthogonal cells.
3. Choose image separation, defect concentration and computational cost for
   the user's intended method. For a molecule or adsorbate, measure its extent
   and evaluate the closest periodic-image separation in each periodic
   direction. A suggested numerical clearance is a design input, not a
   universal validity threshold.
4. Build once, then verify atom count, PBC, cell, stable atom identities and
   relevant interatomic distances from the resulting document or file.

If the operation is only to repeat the connected Structure, do not use a
Sandbox Skill just to duplicate what `build_atom_supercell` already does.
