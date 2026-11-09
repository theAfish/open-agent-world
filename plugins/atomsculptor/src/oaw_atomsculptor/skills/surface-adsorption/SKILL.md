---
name: surface_adsorption
description: "Recipe for placing adsorbates on surfaces: site selection, height, orientation, periodic-image clearance."
when_to_use: Use when placing a molecule on a slab. Combine with `surface_builder` (for the slab) and `supercell_dimension_check` (for sizing).
---

# Surface Adsorption Instructions

This is instruction-only guidance. No adsorption-site builder is bundled in
this Toolbox. Use connected OAW resources and user-permitted Sandbox code for
placement, then verify the output file and import it into Atom Structure only
through the authorized file bridge with a fresh revision. The numerical
distances below are starting hypotheses, not universal pass/fail thresholds.

## Common Workflow

1. **Create substrate surface**
   - Generate an appropriate surface (see the connected `surface_creation` Skill)
   - Determine supercell size based on adsorbate and periodic image requirements
   - Choose vacuum for the slab/adsorbate extent and target accuracy
   - Test layer count against convergence of the properties of interest

2. **Generate adsorbate molecule**
   - Use ASE `molecule()` function for pre-optimized molecular geometries
   - Alternatively, read from database or construct manually
   - Verify molecular geometry is reasonable

3. **Place adsorbate on surface**
   - Choose adsorption site (top, bridge, hollow, or element-specific)
   - Set adsorption height (typical distances: 2.2-2.7 Å for O-metal bonds)
   - Orient molecule appropriately for desired adsorption mode
   - Consider multiple configurations (parallel, perpendicular orientations)

4. **Verify structure integrity**
   - Check all interatomic distances, especially between adsorbate and surface
   - Compare shortest contacts with the specific adsorbate/substrate chemistry;
     no single minimum distance applies to every element pair.
   - Calculate periodic image distances
   - Verify actual adsorbate-image separation meets the selected convergence criterion

5. **Save and document**
   - Save structure in appropriate format (.extxyz, POSCAR)
   - Document adsorption site, height, and orientation parameters
   - Record supercell size and layer count

## Key Considerations

### Supercell Sizing for Adsorbates
- Account for adsorbate size, the actual surface basis and image interactions.
- A trial 10 Å image clearance is a possible starting hypothesis, not a universal
  minimum. Select and converge a value for the chosen chemistry/calculation.
- Test larger in-plane cells when the measured separation or convergence requires it.
- Verify actual periodic image distances after construction

### Reproducible Supercell Sizing
Record the input structure, cell setting, Miller indices, termination, slab-layer
definition and repeat factors. Read atom count and dimensions from the generated
file. A repeat alone multiplies the starting atom count by the product of its
factors; document any subsequent passivation, addition or removal separately.
No fixed SiO2 atom-count table is supplied: orientation/repeat labels alone do
not specify a unique slab. See `surface_creation`'s material notes for provenance.

### Layer Optimization for DFT
- Compare slab thicknesses using the target surface/adsorption properties.
- For equal atom populations per layer, fixed area and unchanged termination,
  reducing 8 layers to 5 removes 37.5% of slab atoms. Adsorbates, passivation or
  unequal layer populations change the total reduction.
- Five layers are not generally sufficient; choose the converged thickness.

### Adsorption Parameters
- **Adsorption height**: Depends on adsorbate and surface
  - O-metal: typically 2.2-2.7 Å
  - C-metal: typically 2.0-2.5 Å
  - N-metal: typically 2.0-2.5 Å
  - H-metal: typically 1.5-2.0 Å
  - Benzene on an oxide: a trial height around 3.5 Å is a placement hypothesis,
    not proof of binding or a universal equilibrium distance
- **Orientation**: Match molecular symmetry to surface site symmetry
- **Site selection**: Consider coordination and reactivity

### Adsorption Site Types
| Site Type | Description | Typical Use |
|-----------|-------------|-------------|
| top_X | Above specific element | Element-specific binding |
| bridge | Between two surface atoms | Bidentate adsorption |
| hollow | Center of surface ring | Maximum coordination |
| fcc/hcp | 3-fold sites on FCC | Common for close-packed |

### Periodic Image Distance Checking
- For each atom in adsorbate, find distance to nearest periodic image
- Compare the minimum distance with the chosen image-separation criterion
- Use ASE or similar tools to calculate these distances
- Larger molecules require larger supercells

## Common Pitfalls and Fixes

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Steric clashes | Chemically implausible contacts | Increase adsorption height or reorient; recheck element-specific distances |
| Periodic image interactions | Observables change with in-plane size | Increase the supercell and check convergence |
| Incorrect adsorption geometry | Unexpected binding mode | Adjust molecule orientation; check adsorption site |
| Too large supercell | Excessive computational cost | Use the smallest cell meeting the chosen accuracy criterion |
| Wrong surface termination | Unexpected surface chemistry | Check termination for polar surfaces |

### Supercell Sizing Guidelines
- Repeat factors alone do not determine clearance: the starting lattice, skew
  and adsorbate extent matter. Measure distances to translated adsorbate images,
  not just in-plane cell-vector lengths.
- Calculate actual separations and compare properties as the cell is enlarged.

## Additional Tips

### Benzene Adsorption Example (SiO2)
- Benzene diameter: ~5 Å
- Possible trial orientations include (110) and (101); atom counts depend on
  the selected bulk cell, thickness and termination
- Site types: top_Si, top_O, bridge, hollow
- Compare parallel and perpendicular orientations and relevant sites rather
  than assuming the most stable configuration in advance
- A 3.5 Å starting height is illustrative and needs chemistry-specific validation

### General Workflow for Adsorption Studies
1. Build bulk structure from Materials Project
2. Choose the requested orientations, comparing alternatives when relevant
3. Converge slab thickness for the target properties
4. Build supercells meeting the chosen image-separation/convergence criterion
5. Place adsorbates at the requested sites, comparing alternatives when relevant
6. Validate all configurations
7. Generate a batch only when the requested study needs one

### Validation Checklist
- [ ] Adsorbate-surface contacts checked for the actual chemistry
- [ ] Periodic image distances measured against the chosen criterion
- [ ] Molecular geometry not severely distorted
- [ ] Correct surface termination
- [ ] Layer count/thickness justified for the target accuracy
- [ ] Vacuum per side recorded and justified
- [ ] Documentation includes supercell size, site, orientation, height
