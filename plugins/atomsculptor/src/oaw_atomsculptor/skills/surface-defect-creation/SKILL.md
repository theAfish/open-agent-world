---
name: surface-defect-creation
description: Plan and validate vacancies, substitutions, interstitials and other defects in an existing atomic structure.
when_to_use: Use when the user asks to introduce defects or dopants into a bulk, surface or nanotube structure.
---

# Defect creation

This is guidance, not an executable defect builder. No `defect_builder` script
or tool is bundled with this OAW Skill package. Do not convert to CIF merely
to call that nonexistent tool. Prefer ExtXYZ when stable IDs, layers, cell or
PBC must survive a Sandbox round trip.

1. Inspect the connected Atom Structure and its revision. Resolve user-selected
   atoms by stable atom ID, not list index. If the selected-ID snapshot is
   truncated, obtain a complete authorized selection before changing atoms.
2. Establish defect type, exact target sites or reproducible selection rule,
   count/concentration, seed for random choices, and whether relaxation is
   requested. Do not treat a random site as an experimentally known defect.
3. For a small explicit edit, produce one complete validated replacement
   document and pass `expected_revision` to `replace_atom_structure`. Preserve
   stable IDs for all retained atoms. For a larger edit, stage the document
   into an authorized Sandbox, run user-permitted code, convert its output and
   import with the latest revision and structure digest.
4. Verify the changed atom count and composition, defect locations, nearest
   distances, local coordination and any periodic-image interactions from the
   resulting structure. A successful file write alone is not a card update.

If the current graph lacks the needed Structure or Sandbox authorization,
report that constraint; do not use host files or unconnected tools to bypass it.
