---
name: nanostructure_creation
description: Design and validate nanoparticles, carbon nanotubes, shells and filled nanostructures.
when_to_use: Read for a nanostructure task when geometry, chirality, PBC or filling needs an explicit design decision.
---

# Nanostructure creation

This is guidance, not an executable builder. Read it from an authorized OAW
Skill card or Toolbox. Use the packaged `nanotube_builder` only if the user
permits `ase.build.nanotube`; otherwise write and run authorized Sandbox code
that meets the user's restrictions. No `cylinder_filler`, `molecule_filler`,
`atomic_text` or generic nanoparticle builder is shipped in this Toolbox.
Do not attempt to call those names as OAW tools.

## Choosing the next calculation

Use only the parts needed for the current request; these are method choices,
not extra acceptance stages. Reuse authorized builders and already measured
structures, IDs and labels instead of rebuilding primitives for every experiment.
Read the saved input through HOST's `modelling_io.load_inputs()` when available.
Resolve the uncertainty that would change the construction method before a large
placement search. If a deterministic builder already supplies the requested
geometry, build and inspect it without an unnecessary optimization campaign.

When physical relaxation is needed, use an installed ASE calculator with suitable
elements, interactions and explicit parameter provenance. ASE supplies FIRE and
LBFGS; HOST's optional `relax_structure` helper also preserves existing constraints
and fixes atoms outside caller-supplied `movable_indices`. Choose the moving region,
force tolerance and iteration bound for this experiment. Record actual forces,
convergence and potential/parameter identity. A fitted spring or distance penalty
can prepare geometry but does not demonstrate physical stability. Force convergence
alone also does not validate the requested topology or establish a global minimum.
Reinspect the resulting saved file with the existing `structure_inspect` workflow;
do not reuse pre-relaxation passes for changed coordinates.

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

### When joining covalently bonded tube surfaces

First distinguish welding/reconstruction from contact, nesting or a visual
intersection. For a covalent junction, pairwise rim distance and equal rim atom
counts alone do not establish a seamless network. Diagnose the whole candidate's
coordination, connectivity, close contacts and intended open boundaries using saved
atom identities. If direct pairing is infeasible, test local reconstruction or
atom addition/removal only where the request allows it; otherwise report the
measured incompatibility. Repeatedly scanning rigid placements cannot resolve a
topology assumption that the measured graph already contradicts.

Published X/T junction construction combines network reconstruction with atomic
relaxation, but its growth heuristics are not requirements for every task
([Savostyanov and Shmygin, 2020](https://doi.org/10.22226/2410-3535-2020-3-277-282)).
CoNTub v2's construction applies to three identical tubes with C3 symmetry; its
defect counts are not a template for an arbitrary asymmetric T junction
([CoNTub v2, 2011](https://pubs.acs.org/doi/abs/10.1021/ci200056p)).
Choose a method within its applicability, without turning its assumptions into
new user constraints. For carbon relaxation, check that the available potential
actually supports the proposed bonding; do not substitute a convenient calculator
solely because it runs.

## Other finite or filled systems

Build only with tools and libraries authorized by the live OAW graph and user
request. For filling, choose a reproducible placement method and seed, check
all guest–guest and guest–host contacts, and distinguish a plausible initial
geometry from an optimized structure. For periodic hosts, use minimum-image
distances and verify periodic-image separation. For nonperiodic systems, do
not enable PBC merely because a builder's default does so.
For a finite system with a specified vacuum margin, size/center the cell from the
final coordinates (ASE `Atoms.center(vacuum=...)` is available). Inspect the actual
face clearances after any geometry changes instead of assuming the chosen cell
length provides the requested margin.

Save a cell-aware format such as ExtXYZ when cell or PBC matters. If the
result should replace the connected Atom Structure, use the authorized
file-backed conversion/import path with a fresh inspected revision. A file
written in Sandbox alone does not update an OAW document.
