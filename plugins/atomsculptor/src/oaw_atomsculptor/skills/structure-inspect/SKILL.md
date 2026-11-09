---
name: structure_inspect
description: Read and validate atomic structures — summarize a file, dump raw text, measure inter-atomic distances, detect overlapping atoms.
when_to_use: Use to confirm what is in a structure file before / after another step (atom count, formula, cell, PBC), to measure a specific bond distance, or to flag unrealistic close contacts.
entry: scripts/structure_inspect.py
---

# Structure Inspect

CLI commands for reading and independently checking structure files, plus two
deterministic converters for the AtomSculptor document:

Use `run_skill_script` with `script="scripts/structure_inspect.py"` and the
command/options in `argv`. Python is selected by the Sandbox automatically;
the whole read-only bundle, including its imports, is projected for that
command. Do not copy individual bundle files or infer host runtime paths.

When the staged AtomSculptor Builder exposes it, prefer `validate_modelling_artifact(sandbox,
skill, structure_path, checks)` for a saved HOST artifact. It
uses this same authorized inspector, waits for native receipts; it does not
import or advance any stage.

Preserve component identity as actual per-atom label arrays in NPZ/extxyz;
`Artifact.components` records counts rather than membership. `read_structure`
lists saved array names/shapes/types. A region checks file may use either
explicit `indices` or `selector: {"array": "component_id", "values": [1]}`
alongside `name`, `degree_min`, `degree_max`. Complete disjoint partition
and degree checks still apply. Labels survive
translation and reordered atom rows; guessed index ranges and nearest points
in unrelated frames do not establish identity.

In staged custom-modelling Runs, Builder steps stage a Run-specific helper module via a native Sandbox
write. Import the module name in `HOST STABLE CONTEXT.modelling_io`: `load_inputs`
returns a path-indexed list manifest (empty when absent), verifies bytes and
offers `array`, `atoms`, `label_indices`, `json` and `receipt`. `constraint`,
`artifact` and `emit_measurements` serialize the existing host contract, including
NumPy scalar values. Put controls inside constraints. Failed checks remain
failed, and nonfinite values are rejected. `transform_points` requires the same
explicit `frame.reference` in both coordinate systems; prefer saved labels.
The host frame includes diagnostic/candidate examples. Read
`references/measurement-contract.json` through this authorized Skill for the full
versioned schema, optional fields and limits. It is generated from the same
`Measurement` types that validate stdout; examples are shapes, not measured facts.
Each constraint with `check_ids` must include nonempty `regions` naming the
actual populations counted in `coverage`; check IDs alone do not bind coverage.
Use `read_modelling_source` with a script path and `query`, Python `symbol`, or
1-based `start_line`/`end_line` to locate a repair. Character `offset`/`limit`
provide bounded continuation. It returns raw text, line positions and the current
native-confirmed saved version without running a command; avoid rereading a whole
unchanged file when the needed region is already visible.

`emit_measurements` saves oversized ordinary diagnostics to `.oaw-reports/` in
the Sandbox and emits bounded references. Scalar facts and scientific checks
remain in the report. For a format-only repair, load that saved JSON and
re-envelope the result; do not repeat expensive modelling just to serialize it.
The full stdout also remains in OAW's native invocation archive.

Optional instruments use the same read-only executor:

- `analyze_topology --folder . --file-name shape.extxyz --connectivity-cutoff 1.8`:
  explicit distance cutoff in Å, ASE image-aware coordination and local geometric
  proxies. Finite structures additionally report NetworkX simple chordless ring
  counts, bounded by `--max-ring-size` (default 8) and `--max-cycles` (10000).
  Counts are complete only within the stated size bound and when `complete=true`.
  Periodic ring enumeration is explicitly unsupported; periodic coordination is
  still measured. These are geometric diagnostics, not unique material ring
  statistics or electronic sp²/sp³ assignments.
- `analyze_symmetry` requires `--symprec`, `--angle-tolerance`,
  `--molecular-tolerance`. pymatgen evaluates bulk space groups or finite-object
  point groups. Partial periodicity is unsupported; do not treat a slab as bulk.
- `list_calculators` lists actual import availability of pure ASE EMT, LJ, Morse,
  Tersoff and EAM; it does not validate their suitability for the current material.
- `measure_energy_forces` requires `--calculator`, `--parameters-file` (JSON
  constructor options), and `--parameter-source`. Tersoff needs a workspace
  `parameters_file`; EAM needs a workspace `potential`. No model is downloaded or
  force field selected automatically. `--reference-file` optionally compares
  energy for the same composition/cell/PBC. Reports give raw unconstrained forces
  in eV/Å and energy in eV; they do not certify physical stability. Use the same
  explicitly configured calculator with `relax_structure` when local relaxation
  is justified, then independently inspect the saved result.

Select parameters and reference cases with documented applicability. There is
no built-in default carbon-junction potential or universal ring/force criterion.

- `read_structure`         — formula, num atoms, cell, PBC, full atom list if ≤ 10 atoms.
- `read_structures_in_text`— raw file contents (text formats only).
- `calculate_distance`     — distance between two atom indices.
- `check_close_atoms`      — pairs whose distance is below `covalent_radii_sum + tolerance`.
- `validate_structure` — finite coordinates, count/elements, per-atom nearest
  neighbors, overlaps, PBC and true cell-face vacuum margins. Distances are
  selected by the file's PBC (`--distance-mode auto`, the default). Periodic
  cells include image neighbors and nonzero self images; finite clusters use
  Cartesian distances. Explicit `finite`/`periodic` must agree with the PBC.
- `validate_nanotube` — the above plus radius, unrolled graphene lattice,
  chirality and complete axial windows for a **finite, fully nonperiodic,
  unrelaxed, open single-wall CNT**. Every PBC flag must be false; this validator
  does not cover periodic tubes, nested tubes or junctions. For periodic files,
  use `validate_structure` with PBC-consistent distance settings and any additional
  task-specific checks. `nanotube_builder` defaults to periodic z; generate a
  finite tube with `--pbc-z false` for this specialized validator.
  The tube axis must be Cartesian (`--axis 0`, `1` or `2`; default `2`).
- `to_atomsculptor_document` — convert a structure file to the canonical
  StructureDocument JSON (stable atom IDs preserved when present).
- `from_atomsculptor_document` — write a StructureDocument (inline JSON or a
  workspace JSON path) to a structure file, keeping atom IDs and layers as
  per-atom columns.

```bash
python3 scripts/structure_inspect.py read_structure --folder . --file-name Fe_bcc.extxyz
python3 scripts/structure_inspect.py calculate_distance --folder . --file-name slab.extxyz --index1 0 --index2 5
python3 scripts/structure_inspect.py check_close_atoms --folder . --file-name slab.extxyz --tolerance -0.3
python3 scripts/structure_inspect.py validate_structure --folder . --file-name cluster.extxyz --expected-count 80 --expected-symbol C --nonperiodic true --min-vacuum 8
python3 scripts/structure_inspect.py validate_nanotube --folder . --file-name cnt_5_5_4.extxyz --n 5 --m 5 --axial-repeats 4 --min-vacuum 8
python3 scripts/structure_inspect.py to_atomsculptor_document --folder . --file-name slab.extxyz
python3 scripts/structure_inspect.py to_atomsculptor_document --folder . --file-name slab.extxyz --output-name slab.document.json
python3 scripts/structure_inspect.py from_atomsculptor_document --document structure.json --output-name bulk.extxyz
```

After custom generation, validate the **saved file** before conversion/import;
do not substitute a generated script's own assertions. The validator reports
`passed`, file path and measured values; failure produces a nonzero exit code.
`acceptance_thresholds` contains requested limits, `measurements` contains
observed values, and `violations` lists exactly what failed. In particular,
`minimum_pair_distance_angstrom` is measured; a threshold of 0.994 Å does not
mean any atom pair was measured at that distance. Cell face margins use origin
(0,0,0) and the cell vectors; half the difference of box and structure sizes
does not establish that the atoms lie within the box. Fix violations, then
validate the saved file again; do not rewrite valid geometry for a new parameter
preference. The rolled Cartesian chord can be shorter than a planar C–C bond.
Small finite clusters use pair distances; larger clusters use a spatial tree,
and periodic cells use ASE neighbor lists with an expanding, complete nearest
search. Missing dependencies or unsupported cell/mode combinations return
`passed=false`, with `validation_status=unsupported`; an incomplete nearest
search is `unknown`, never a pass. Reports name `validator_version`,
`validation_scope`, `distance_backend` and conventions. Periodic connectedness
refers to atom rows in the cell, not infinite-lattice percolation. Geometric
validation alone does not prove the requested shape or physical stability.
Nanotube inspection does not call
ASE/MCKit nanotube generators or produce new coordinates. It distinguishes
nominal repeat length from first-to-last atom span; those need not be equal.
For C–C=1.42 Å, (5,5) has 20 atoms per ~2.4595 Å axial repeat. A four-repeat
half-open window contains 80 atoms and can have ~8.6083 Å actual axial span.
Use explicit constraints from the user, not guessed acceptance constants.

Global connectedness alone does not prove a requested interface is healed.
For region-specific coordination checks, validate_structure also accepts
--region-checks-file with a positive --connectivity-cutoff. The JSON file must
contain regions, a list of objects with name, indices,
degree_min and degree_max. indices are zero-based rows of the file being
validated. Regions must be nonempty, disjoint and cover every atom. Include allowed
open boundaries explicitly; do not select a wider end slab that also includes
interior rows. The inspector computes actual degrees and reports violating
atoms by region. The degree ranges and region meanings are declared modelling
assumptions, not inferred material laws or proof of stability. Each validation
reads the current structure and rules files.

For custom shape/path checks, use a known passing and failing control to test
the checker. A free-space path through an open end and outside the object does
not establish the requested internal connection: restrict the allowed domain
or block that alternative route. Keep these checks separate from global
graph connectedness and file/cell validation.

`folder` may be `"."` to use the sandbox root. Large CIF files (> 1 MB) are
parsed via `pymatgen.io.cif.CifParser` to avoid ASE's slow path.

Use the two converters whenever a structure must move between an OAW Atom
Structure card and a Sandbox file. Never retype or summarize atom lists. For
small manual workflows, the inline `document` form is supported, but the
file-backed bridge below is the default for Agent workflows.

For large structures, use `stage_atom_structure_file` to copy the exact
revisioned document into the authorized Sandbox without sending it through
the model. Run `from_atomsculptor_document` against that returned JSON path.
After a modelling script, run `to_atomsculptor_document` with `--output-name`
to write a canonical JSON file in the Sandbox, then call
`import_atom_structure_file` with that path and a freshly inspected Structure
revision. These two OAW tools check both Structure and Sandbox permissions and
return only file metadata and document summary to the model.
After import, call `inspect_atom_structure` again and confirm revision and
atom count before claiming that the requested document changed.
