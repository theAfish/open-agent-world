---
name: nanostructure-creation
description: Design and validate nanoparticles, carbon nanotubes, shells and filled nanostructures.
when_to_use: Read for a nanostructure task when geometry, chirality, PBC or filling needs an explicit design decision.
---

# Nanostructure creation

This is guidance, not an executable builder. Read it from an authorized OAW
Skill card or Toolbox. Use the packaged `nanotube-builder` only if the user
permits `ase.build.nanotube`; otherwise write and run authorized Sandbox code
that meets the user's restrictions. No `cylinder_filler`, `molecule_filler`,
`atomic_text` or generic nanoparticle builder is shipped in this Toolbox.
Do not attempt to call those names as OAW tools.

## Carbon nanotubes

- State chirality `(n,m)`, bond length, number of translational repeats, atom
  count and whether the requested tube is finite or periodic before building.
- For graphene lattice constant `a ≈ 2.46 Å`, the ideal diameter is
  `d = a / pi * sqrt(n² + n*m + m²)`. Thus `(n,n)` has `d ≈ 1.356*n Å`,
  while `(n,0)` has `d ≈ 0.783*n Å`. Verify the built radii numerically.
- A **finite** tube has `PBC=(False, False, False)` and vacuum at both axial
  ends as well as around its wall. A **periodic** tube has axial PBC only if
  its cell equals the true translational repeat length. The occupied atom span
  is not necessarily that cell length.
- For double-wall tubes, calculate each wall's mean radius from its atoms,
  then compare the difference with the requested inter-wall spacing. Check
  chirality, atom counts, nearest-neighbour C–C distances, end clearance and
  any cross-wall close contacts from the executed file, not mental estimates.
- If the user prohibits a generator or library, that prohibition also applies
  to nested Skills. Do not substitute a packaged generator silently.

## Other finite or filled systems

Build only with tools and libraries authorized by the live OAW graph and user
request. For filling, choose a reproducible placement method and seed, check
all guest–guest and guest–host contacts, and distinguish a plausible initial
geometry from an optimized structure. For periodic hosts, use minimum-image
distances and verify periodic-image separation. For nonperiodic systems, do
not enable PBC merely because a builder's default does so.

Save a cell-aware format such as ExtXYZ when cell or PBC matters. If the
result should replace the connected Atom Structure, use the authorized
file-backed conversion/import path with a fresh inspected revision. A file
written in Sandbox alone does not update an OAW document.
