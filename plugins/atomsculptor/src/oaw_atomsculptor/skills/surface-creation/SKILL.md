---
name: surface-creation
description: Workflow and pitfalls for generating crystal surfaces (slabs) from bulk structures.
when_to_use: Use when the user asks for a surface, slab, Miller-indexed face, or surface termination. Read this BEFORE invoking any surface-building tool.
---

# Surface Creation

Instruction-only skill (no entry script). Use it to plan a
surface-generation task. Read from a connected OAW Skill card. Execute the
separate `surface-builder` Skill through `run_skill_script` and an authorized
Sandbox. If the slab should replace the open Atom Structure, use the
file-backed conversion/import path with a freshly inspected revision.
Do not treat this instruction-only Skill as a runnable builder.

For deeper material-specific notes, drill down via:

- [references/lpscl_and_sio2.md](references/lpscl_and_sio2.md) — worked examples
  for LPSCl (Li6PS5Cl) and α-SiO2 surfaces.
- [references/checklist.md](references/checklist.md) — validation checklist and
  PBC / vacuum / layer-count rules of thumb.

## Common Workflow

1. **Obtain bulk crystal structure**
   - Materials Project search (preferred for real materials), or
   - Manual creation via `crystal-builder` skill / pymatgen / ASE.

2. **Choose an appropriate cell setting (often conventional for FCC)**
   - MP often returns primitive cells (60° angles for FCC).
   - For the packaged surface-builder script, use the conventional cell when
     the selected orientation or input setting requires it. It accepts
     `need_conventional=True`.
   - Never hand-roll cell transformations — use proper tools.

3. **Generate surface using SlabGenerator**
   - Specify Miller indices.
   - Layers: 4–8 (5 is a good default for DFT).
   - Vacuum: ≥ 10–15 Å.

4. **Build supercell for desired adsorbate coverage**
   - Periodic images of any adsorbate must be > 10 Å apart.

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
| Insufficient vacuum                  | Increase to ≥ 10 Å.                                  |
| Too many layers for DFT              | Reduce to 5; verify properties hold.                 |
| Adsorbate periodic images < 10 Å     | Enlarge supercell.                                   |
| Broken structural units (PS4, dimers)| Test all terminations; see lpscl_and_sio2 reference. |
