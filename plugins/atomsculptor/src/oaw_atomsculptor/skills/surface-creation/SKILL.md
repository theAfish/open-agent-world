---
name: surface_creation
description: Workflow and pitfalls for generating crystal surfaces (slabs) from bulk structures.
when_to_use: Use when the user asks for a surface, slab, Miller-indexed face, or surface termination. Read this BEFORE invoking any surface-building tool.
---

# Surface Creation

Instruction-only skill (no entry script). Use it to plan a
surface-generation task. Read from a connected OAW Skill card. Execute the
separate `surface_builder` Skill through `run_skill_script` and an authorized
Sandbox. If the slab should replace the open Atom Structure, use the
file-backed conversion/import path with a freshly inspected revision.
Do not treat this instruction-only Skill as a runnable builder.

For deeper material-specific notes, drill down via:

- [references/lpscl_and_sio2.md](references/lpscl_and_sio2.md) — input and provenance notes
  for LPSCl (Li6PS5Cl) and α-SiO2 surfaces.
- [references/checklist.md](references/checklist.md) — validation checklist and
  PBC / vacuum / layer-count rules of thumb.

## Common Workflow

1. **Obtain bulk crystal structure**
   - Materials Project search (preferred for real materials), or
   - Manual creation via `crystal_builder` skill / pymatgen / ASE.

2. **Choose an appropriate cell setting (often conventional for FCC)**
   - MP often returns primitive cells (60° angles for FCC).
   - The packaged surface_builder script uses ASE's conventional-cell surface
     convention and accepts `need_conventional=True`. Record the chosen setting
     because Miller indices refer to that basis.
   - Use established cell transformations rather than guessing a new basis.

3. **Generate surface using the packaged ASE surface builder**
   - Specify Miller indices in the selected input cell's basis.
   - `layers` counts ASE equivalent layers, not individual atomic planes.
   - `vacuum` is padding on each side of the slab. For example, 15 Å means
     approximately 15 Å per side. Choose thickness and vacuum by the requested
     accuracy and convergence tests, not a fixed universal layer count.
   - pymatgen `SlabGenerator` is an alternative for separately implemented
     termination/thickness workflows; it is not the backend of this script.

4. **Build supercell for desired adsorbate coverage**
   - Measure actual adsorbate-image separation. A trial 10 Å clearance can be a
     starting hypothesis; converge it for the chosen chemistry and calculation.

5. **Validate** (Miller indices, termination, surface coordination, intended PBC,
   structural-unit integrity).

6. **Save and document** (`.extxyz` preferred; record MP ID, orientation,
   layers, vacuum thickness).

## Key Considerations

### Miller indices and stability
- BCC: (100), (110), (111). (110) often most stable.
- FCC: (111) most stable, (100) common.
- Perovskites: (001) common, multiple terminations.
- Oxides: (001), (100), (101), (110).

### Primitive vs. conventional cell

| Cell type    | Angles               | Use case            |
|--------------|----------------------|---------------------|
| Primitive    | Depends on lattice   | Often the smallest computational cell |
| Conventional | Depends on lattice   | Often convenient for indexed surfaces |

### Common pitfalls

| Pitfall                              | Fix                                                  |
|--------------------------------------|------------------------------------------------------|
| Primitive vs. conventional cell      | Inspect and convert when the chosen surface method needs it. |
| Wrong surface termination            | Check all terminations, pick one preserving units.   |
| Insufficient vacuum                  | Increase and verify convergence for the intended calculation. |
| Excessive slab cost                  | Test fewer layers while checking convergence of the target properties. |
| Interacting adsorbate images          | Enlarge the in-plane cell and test image separation. |
| Unexpected broken structural units   | Compare terminations with the selected input and requested surface chemistry. |
