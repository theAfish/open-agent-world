---
name: surface_builder
description: Build a surface slab (Miller indices, layers, vacuum) from a bulk crystal, optionally first symmetrizing to the conventional cell.
when_to_use: Use to construct an arbitrary (hkl) slab from a bulk structure. Read the `surface_creation` skill for picking Miller indices, layer count, and vacuum thickness; read `surface_defect_creation` / `surface_adsorption` for follow-on steps.
entry: scripts/surface_builder.py
---

# Surface Builder

Read this Skill from a connected OAW card and execute its bundled script with
`run_skill_script` in an independently authorized Sandbox. The CLI below is
an argument example, not a host command to run against the Skill bundle.
For a connected Atom Structure, stage its exact document file first; convert
the generated slab to a document file and import with a fresh revision.

```bash
python3 scripts/surface_builder.py build_surface \
  --folder . --file-name Fe.extxyz \
  --miller-indices '[1,0,0]' --layers 5 --vacuum 15.0 \
  --need-conventional true \
  --output-name Fe_100.extxyz
```

`need_conventional=true` runs `pymatgen.symmetry.analyzer.SpacegroupAnalyzer`
first to produce the conventional standard cell. The surface is then built by
`ase.build.surface`, which expects a conventional input cell; if conversion is
disabled, verify that the supplied cell already has the intended setting.
`layers` means ASE equivalent layers, not a count of individual atomic planes.
`vacuum=15.0` adds approximately 15 Å on each side of the slab, rather than
15 Å total. Choose layer count and vacuum for the requested model and convergence
requirements; the example is not a universal DFT prescription.
