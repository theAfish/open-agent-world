---
name: mof-structure-creation
description: Planning and validation guidance for metal-organic frameworks; no MOF builder is bundled.
when_to_use: Use when the user requests a MOF or another metal-node and organic-linker framework.
---

# MOF structure creation

This is an instruction-only OAW Skill. The old `mof_builder.py`, MOF-5 and
Cu-BTC template commands are **not** bundled in this Toolbox. Do not claim
that a MOF builder tool is available. Use only a connected, authorized source
of structural data or user-permitted code in an authorized Sandbox.

1. Establish the exact framework, composition, linker, metal node, topology,
   cell setting and whether an experimental structure is required. Ask for
   clarification when these choices materially change the result.
2. If using Materials Project or another database, obtain the structure
   through an authorized networked resource; distinguish a fetched structure
   from a manually constructed idealization. Never present remembered lattice
   parameters as a live lookup.
3. If constructing from components, verify atom connectivity across periodic
   boundaries, metal coordination, linker geometry, stoichiometry, pore
   openings and overlapping atoms with executed code. State clearly when the
   geometry has not been relaxed.
4. Save a cell-aware file such as ExtXYZ or CIF in the Sandbox. To update the
   connected Atom Structure, convert through `structure-inspect` and use the
   authorized file-backed import with a fresh inspected revision.

Any reported surface area, pore size or space group must come from a
calculation or cited source for the actual structure, not from a generic MOF
example. If no authorized data source or construction path exists, report the
missing prerequisite instead of inventing coordinates.
