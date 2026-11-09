---
name: nanotube_builder
description: Build carbon nanotubes (CNTs) of any chirality (n,m), length, bond, and PBC.
when_to_use: Use when the user asks to build a CNT, an armchair/zigzag/chiral tube, or a starting tube to be filled later. Pair with `nanostructure_creation` for the design checklist.
entry: scripts/nanotube_builder.py
---

# Nanotube Builder

Single-command CLI built on top of `ase.build.nanotube`. Reports diameter,
chirality type (armchair / zigzag / chiral), tube length, cell, and atom count.
Use it only when the user permits ASE's prebuilt nanotube generator. A user
prohibition on prebuilt generators takes precedence over this Skill.

Read this Skill through the connected OAW Skill Toolbox (or direct Skill card),
then invoke `scripts/nanotube_builder.py` with `run_skill_script` and an
independently authorized Sandbox. Pass CLI options as `argv`; the script is
mounted read-only and writes its output to the Sandbox workspace.

```bash
# The first two commands use the script's periodic-z default.
python3 scripts/nanotube_builder.py build_nanotube --n 7 --m 7 --length 4
python3 scripts/nanotube_builder.py build_nanotube --n 10 --m 0 --length 6 --bond 1.42 --vacuum 20.0
python3 scripts/nanotube_builder.py build_nanotube --n 5 --m 5 --length 4 --pbc-z false --vacuum 15.0 --output-file cnt.extxyz
```

The corresponding `run_skill_script` arguments are:

```json
{"sandbox":"new_sandbox","skill":"nanotube_builder","script":"scripts/nanotube_builder.py","argv":["build_nanotube","--n","5","--m","5","--length","4","--pbc-z","false","--vacuum","15.0","--output-file","cnt.extxyz"]}
```

Use the exact authorized Sandbox/Skill selectors in the tool declaration.
`argv` starts with the sub-command, not `python3` or the script path.
`--output-file` chooses the workspace output filename; use distinct filenames
for constituents that must both be retained.

The last example is a **finite** tube: PBC is false in all directions and
`vacuum` pads both radial directions and both open axial ends. For a periodic
tube, use `--pbc-z true`; its axial cell uses ASE's translational period, not
the occupied atom span. `structure_inspect.validate_nanotube` is limited to
finite, fully nonperiodic single tubes, so use it with the last example only.
For periodic tubes, use `validate_structure` with the file's PBC and additional
task-specific checks instead of that finite-tube validator.
Inspect the resulting file's atom count, PBC, cell,
nearest-neighbour distances and axial clearance before importing it into an
Atom Structure. Use the Structure file bridge and a fresh document revision;
the Skill itself does not write an OAW card.

For chirality / diameter guidance and post-build steps, read the separate
`nanostructure_creation` Skill: use `read_skills` with the authorized `toolbox`
and `skill_id="nanostructure_creation"`, or `read_skill` with its independently
authorized `skill` selector. Omit `file_path` to read its instructions; a sibling
Skill is not a relative file inside this bundle.
