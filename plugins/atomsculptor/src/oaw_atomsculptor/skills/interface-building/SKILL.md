---
name: interface-building
description: Plan, select and validate coherent interfaces between two materials.
when_to_use: Read before using the interface-builder Skill or writing custom interface-stacking code.
---

# Interface building

This is an instruction-only OAW Skill. There is no standalone
`build_interface` Agent capability or old `structure_tools.py` in this
Toolbox. The connected `interface-builder` Skill provides a ZSL-based script;
read it and call `run_skill_script` with a separately authorized Sandbox.
For a structured AtomSculptor interface request, use its typed values and
the file-backed Structure/Sandbox bridge rather than copying atom arrays
through the model.

1. Establish both input structures, orientations, terminations, in-plane
   periodicity, slab thicknesses and target gap. A bulk structure and a slab
   are not interchangeable inputs. Do not assume a Materials Project cell is
   conventional; inspect it and convert where the chosen method requires it.
2. Choose lattice-match area and strain tolerances appropriate to the user
   request. If using ZSL, inspect all returned candidates rather than silently
   accepting the first match. If no match exists, report the constraint and
   ask whether strain, orientation or area limits may change.
3. Check each candidate's actual strain, area, atom count, interfacial gap,
   cross-interface contacts and preservation of molecular units or bonds.
   For a nonperiodic vacuum direction, verify that PBC and vacuum agree with
   the intended downstream calculation; no single setting is universal.
4. For a structured request, persist the returned candidate metadata using
   `record_interface_candidates` with a freshly inspected document revision.
   Do not import one candidate as the final Atom Structure until the user or
   request selects it. Import by its exact returned Sandbox file name.

The old material-specific LPSCl and SiO2 numbers are examples, not default
parameters or evidence that a new candidate is chemically sound. Validate
the actual generated structure and report any unrelaxed geometry as such.
