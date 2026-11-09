# Crystal Builder — Reference Notes

Read only when you need extra detail beyond `SKILL.md`. Use `read_skills` with
`{"toolbox":"atomsculptor_skills","skill_id":"crystal_builder","file_path":"references/notes.md"}`.
For a direct Skill connection use `read_skill` with
`{"skill":"crystal_builder","file_path":"references/notes.md"}` instead.
Copy the exact authorized toolbox/skill selector from the tool declaration.

## Picking `crystalstructure`

| Element class | Typical structure | Notes |
|---------------|-------------------|-------|
| Alkali / α-Fe | bcc               | Use `a` only. |
| Cu, Al, Pt    | fcc               | Use `a`; pass `cubic=true` for the conventional 4-atom cell. |
| Mg, Ti, Zn    | hcp               | Provide `a` and `covera` (default = ideal sqrt(8/3)). |
| Si, Ge, C     | diamond           | Provide `a`. |
| NaCl, MgO     | rocksalt          | Provide `a` (compound binary). |
| ZnS, GaAs     | zincblende        | Provide `a`. |
| CsCl          | cesiumchloride    | Provide `a`. |
| ZnO, AlN      | wurtzite          | Provide `a`, `c`, `u`. |

## Output

By default the structure is written to `<element>_<crystalstructure>.extxyz` in
the sandbox output dir. Pass `--output-name` to override.
