# Surface Creation — Validation Checklist

Read with `read_skills` arguments
`{"toolbox":"atomsculptor_skills","skill_id":"surface_creation","file_path":"references/checklist.md"}`.
For a direct Skill connection use `read_skill` arguments
`{"skill":"surface_creation","file_path":"references/checklist.md"}` instead.
Copy the exact authorized toolbox/skill selector from the tool declaration.

## Before generating
- [ ] Bulk source confirmed (MP ID recorded, or hand-built parameters noted).
- [ ] Cell converted to conventional if applicable (FCC primitives flagged).

## After generating
- [ ] Correct Miller indices and surface orientation.
- [ ] Surface termination and structural units match the requested physical model.
- [ ] Layer count/thickness and vacuum justified for the target properties.
- [ ] ASE equivalent-layer count distinguished from individual atomic planes.
- [ ] Vacuum recorded per side; a fixed layer count or distance is not convergence proof.
- [ ] Intended PBC checked (an isolated slab commonly uses True, True, False).
- [ ] Actual adsorbate-image separation measured and tested for convergence as needed.

## Output
- [ ] Saved as `.extxyz` (preferred). Optionally also CIF / POSCAR.
- [ ] Documented: MP ID, Miller indices, layers, vacuum, supercell expansion.

## Format notes
- `.extxyz` preserves PBC and per-atom info — preferred for surfaces.
- CIF for visualization; POSCAR for VASP.
- ASE handles conversions, for example: `ase convert slab.extxyz slab.cif`.
- Reinspect the converted file; output formats can change which metadata/PBC survive.

## Reconstruction & relaxation
- Real surfaces may reconstruct — plan a relaxation step if accuracy matters.
- Multiple stable terminations may exist for clean surfaces — check literature.
