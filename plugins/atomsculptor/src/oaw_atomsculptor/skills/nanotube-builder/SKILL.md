---
name: nanotube-builder
description: Build carbon nanotubes (CNTs) of any chirality (n,m), length, bond, and PBC.
when_to_use: Use when the user asks to build a CNT, an armchair/zigzag/chiral tube, or a starting tube to be filled later. Pair with `nanostructure-creation` for the design checklist.
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
python3 scripts/nanotube_builder.py build_nanotube --n 5 --m 5 --length 4 --pbc-z false --vacuum 15.0
```

The last example is a **finite** tube: PBC is false in all directions and
`vacuum` pads both radial directions and both open axial ends. For a periodic
tube, use `--pbc-z true`; its axial cell uses ASE's translational period, not
the occupied atom span. Inspect the resulting file's atom count, PBC, cell,
nearest-neighbour distances and axial clearance before importing it into an
Atom Structure. Use the Structure file bridge and a fresh document revision;
the Skill itself does not write an OAW card.

For chirality / diameter rules of thumb and post-build steps (filling,
thermal perturbation, PBC consistency), read the
[`nanostructure-creation`](../nanostructure-creation/SKILL.md) skill.
