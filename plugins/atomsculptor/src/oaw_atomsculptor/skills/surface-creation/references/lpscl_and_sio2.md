# LPSCl and α-SiO2 — Input and Provenance Notes

Read only for these specific materials. Use `read_skills` arguments
`{"toolbox":"atomsculptor_skills","skill_id":"surface_creation","file_path":"references/lpscl_and_sio2.md"}`.
For a direct Skill connection use `read_skill` arguments
`{"skill":"surface_creation","file_path":"references/lpscl_and_sio2.md"}` instead.
Copy the exact authorized toolbox/skill selector from the tool declaration.

## LPSCl (Li6PS5Cl)
- Inspect the chosen bulk structure and identify its actual structural units,
  including PS4 tetrahedra. Do not infer other motifs from the formula alone.
- Measure P–S distances from that input and record the source; a single reference
  distance is not a universal constraint on every termination.
- Compare terminations against the requested surface chemistry, recording any
  broken units, passivation or changed stoichiometry.

## α-Quartz SiO2
- Example bulk source: `mp-6930` (trigonal P3_221); retain the retrieved structure
  and its provenance before choosing a cell setting.
- Common orientations: (001), (100), (101), (110).
- Record the input cell setting, Miller basis, construction backend, layer
  definition/count, termination, vacuum per side and in-plane repeats.
- Report dimensions and atom counts measured from the saved output. A pure
  4×4×1 repeat gives 16 times the unrepeated slab's atom count; any removals,
  additions or passivation need separate documentation.
- Converge thickness, vacuum and adsorbate-image separation for the target
  properties. Five layers or a trial 10 Å separation is not proof of convergence.
- Earlier fixed atom-count tables lacked reproducible construction inputs and
  are omitted. Orientation and repeat factors alone cannot determine those counts.

## Lattice parameters for common reference materials

Approximate starting references only; retain the selected structure's actual
parameters and provenance rather than enforcing these values on every model.

| Material        | Structure | Lattice constant (Å) | Space group  |
|-----------------|-----------|----------------------|--------------|
| Fe              | BCC       | 2.866                | Im-3m (229)  |
| Cu              | FCC       | 3.615                | Fm-3m (225)  |
| Pt              | FCC       | 3.924                | Fm-3m (225)  |
| SiO2 (α-quartz) | Trigonal  | a=4.91, c=5.40       | P3_221 (154) |
