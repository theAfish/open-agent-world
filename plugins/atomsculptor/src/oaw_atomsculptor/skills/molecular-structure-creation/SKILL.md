---
name: molecular_structure_creation
description: Methods for building isolated molecules (ASE database, SMILES via RDKit, organometallics, endohedral cages, NEB pathways).
when_to_use: Use when constructing a single molecule, an organometallic compound, or a molecule-in-cage system.
---

# Molecular Structure Creation Instructions

## OAW SMILES button

For a structured `molecule` request, use the bundled `scripts/smiles_builder.py`
with the exact `smiles` value. Its declared OAW shared Python environment already
includes RDKit; use the native preparation/readiness receipt rather than reinstall
requirements on every task. The script generates a reproducible RDKit conformer
and writes ExtXYZ. To add the molecule to an existing structure, supply a
file-backed input created by `structure_inspect`'s
`from_atomsculptor_document` converter; the script keeps its per-atom stable
IDs and places the new molecule outside its current X extent. This is an
initial placement, not a chemically optimized adsorption site.
Periodic hosts are rejected by this automatic placement path because placing
outside the cell can wrap onto occupied atoms; use an explicit site workflow.

```sh
python3 scripts/smiles_builder.py --smiles CCO --output-name ethanol.extxyz
python3 scripts/smiles_builder.py --smiles CCO --input-name host.extxyz --output-name host_ethanol.extxyz
```

Convert the output with `to_atomsculptor_document` and write it back only
with the latest structure revision. If the original structure cannot be
materialized in the Sandbox, do not claim the molecule was added to it.

## Common Workflow

The methods below are possible starting approaches, not additional bundled
OAW executables. Only the `smiles_builder.py` script above is packaged with
this Skill. A Fibonacci-sphere point set is not by itself a chemically bonded
fullerene; do not present one as a validated cage without topology and bond
checks. Use only user-permitted libraries and authorized OAW resources.

1. **Search for existing structure data**
   - Check Materials Project for crystalline molecular compounds
   - Note: MP mainly stores crystalline materials, not isolated molecules
   - For simple molecules, use ASE's built-in molecule database

2. **Determine construction method**
   - **Option A: ASE molecule database** - for common molecules (H2O, CO2, C60, etc.)
   - **Option B: SMILES strings** - for organic molecules with RDKit
   - **Option C: Manual construction** - for complex organometallics
   - **Option D: Endohedral/encapsulated structures** - for molecules inside cages

3. **Build molecular structure**
   - For ASE database: Use `ase.build.molecule()` function
   - For SMILES: Use RDKit to generate 3D coordinates
   - For manual: Define atomic positions based on literature/experimental data
   - For endohedral: Obtain a chemically defined cage, then choose an initial
     guest position and verify its contacts

4. **Validate molecular geometry**
   - Check bond lengths match expected values
   - Verify coordination and geometry
   - For organometallics: check metal-ligand distances
   - For endohedral: verify guest fits within cage (consider vdW radii)

5. **NEB pathway generation (if needed)**
   - Identify insertion/extraction pathways
   - Generate intermediate images between initial and final states
   - Consider ring/cage openings and steric constraints

6. **Geometry optimization (if needed)**
   - Use force fields (MMFF, UFF) for initial optimization
   - Note: EMT calculator does not support all elements (e.g., Fe)
   - If no suitable calculator is available, retain a geometry based on a cited
     source and report that optimization/energetic stability was not evaluated

7. **Save and document**
   - Export in XYZ, CIF, or POSCAR formats
   - Document construction method and parameters
   - Record reference if using literature values

## Key Considerations

### Molecular Geometry Parameters
- Use experimental or literature values when available
- Illustrative starting geometry (Å), not universal acceptance thresholds:
  - C-C depends on the molecule and bond environment; use the chosen structure
    or a cited reference rather than one value for every aromatic bond
  - Aliphatic single C-C bonds are often around 1.54
  - C-H: 1.08-1.10
  - O-H (water): 0.97
  - H-O-H angle: 104°
  - Metal-C (organometallics): varies by metal
  - Fe-C (ferrocene): ~2.05

### Endohedral Fullerene Construction
- C60 carbon centers lie roughly 3.5 Å from the center in a common geometry;
  measure the actual cage used. This is not an accessible cavity radius.
- Evaluate guest-cage contacts using the actual coordinates and a documented
  radius/interaction model. A nominal radius subtraction does not prove that
  a guest fits, can enter the cage, or is energetically stable.

### Encapsulated Molecules (Molecule-in-Cage)
**Cage Size Calculation:**
1. Measure the guest's extent from the chosen conformer, distinguishing full
   diameter from center-to-atom radius and documenting any vdW convention.
2. Compare it with an actual cage geometry and evaluate all guest-cage contacts.
3. A geometric clearance is an initial placement hypothesis, not a universal
   fit/energy criterion; do not choose a radius by an unexplained fixed buffer.

**Cage Construction Methods:**
- **C60**: Use `ase.build.molecule('C60')` when that generator is permitted.
- **Other fullerenes**: Use a sourced molecular geometry or construct an explicit
  bonded topology with an appropriate embedding and validation.
- **Fibonacci sphere**: A possible geometric seed only. Uniform sampling does
  not create a chemically valid carbon cage or establish its bonding/ring topology.

**Optional Geometric Seed (not a fullerene generator):**
```python
import numpy as np

def fibonacci_sphere(radius, n_points):
    if type(n_points) is not int or n_points < 2:
        raise ValueError("n_points must be an integer >= 2")
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("radius must be finite and positive")
    points = []
    phi = np.pi * (3.0 - np.sqrt(5.0))  # golden angle
    for i in range(n_points):
        y = 1.0 - (i / (n_points - 1)) * 2.0
        r = np.sqrt(1.0 - y * y)
        theta = phi * i
        x = np.cos(theta) * r
        z = np.sin(theta) * r
        points.append([x * radius, y * radius, z * radius])
    return points
```

**RDKit SMILES to 3D:**
```python
from rdkit import Chem
from rdkit.Chem import AllChem
mol = Chem.MolFromSmiles("CN1C=NC2=C1C(=O)N(C(=O)N2C)C")  # Caffeine
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol)
AllChem.MMFFOptimizeMolecule(mol)
```

### NEB Pathway Generation
- Identify possible insertion/extraction routes
- C60 has 20 hexagonal rings and 12 pentagonal rings
  ([MPI-FKF reference](https://www2.fkf.mpg.de/andersen/fullerene/intro.html)).
- Ring-center geometry does not equal a guest-accessible opening. Measure the
  actual ring and interactions rather than assuming a fixed aperture radius.
- Generate intermediate images (typically 7-12 images)
- Consider cage deformation for larger guests

### ASE Molecule Database Examples
- `molecule('H2O')` - Water with O-H=0.97Å, H-O-H=104°
- `molecule('C60')` - Buckminsterfullerene
- `molecule('C6H6')` - Benzene
- Random orientations are normal for liquid simulations
- **Note**: ASE only has C60, not larger fullerenes

### Organometallic Compound Construction
- Build metal-ligand framework manually
- Example: Ferrocene Fe(C5H5)2
  - Staggered conformation common
  - Fe-C distance: 2.05 Å
  - Ring separation: 3.30 Å

## Common Pitfalls and Fixes

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| MP doesn't have molecule | Search returns no results | Build manually using experimental parameters |
| EMT calculator unsupported | Optimization fails for this chemistry | Use a suitable explicit calculator or report that relaxation was not performed |
| Wrong coordination geometry | Metal center has wrong coordination | Verify against known structures (CSD, literature) |
| Unrealistic bond lengths | Bonds too short or too long | Check experimental values from databases |
| Wrong molecular conformation | Unexpected shape/stereochemistry | Choose appropriate conformer (staggered vs. eclipsed) |
| Guest too large for cage | Severe steric clash | Use larger cage; calculate needed radius from molecule extent |
| Cage too small for molecule | Guest-cage contacts unsuitable | Choose a sourced larger cage or explicitly construct and validate a bonded topology |

## Additional Tips

### Construction Strategy for Complex Molecules
1. Break down into sub-units (e.g., rings, ligands)
2. Build each sub-unit separately
3. Position sub-units relative to each other
4. Check all bond lengths and angles
5. Verify overall geometry and symmetry

### Encapsulation Workflow
1. Build guest molecule (SMILES → RDKit 3D)
2. Calculate maximum molecular extent
3. Select a chemically defined cage and measure its actual geometry
4. Verify cage connectivity/bonding; a geometric point seed alone is insufficient
5. Choose an initial guest position/orientation, using the center only as a trial
6. Check guest-cage distances with the declared steric model
7. Relax with a suitable calculator if requested/available; report untested stability

### NEB Pathway Considerations
- Compare candidate paths for the actual guest/cage and selected energy model;
  ring size alone does not establish a barrier or synthesis mechanism
- Save NEB structures as ASE trajectory files for direct use in calculations

### Useful Tools and Databases
- **ASE molecule database**: Common small molecules (H2O, CO2, CH4, C60, etc.)
- **RDKit with SMILES**: Organic molecules with automatic 3D generation
- **Cambridge Structural Database (CSD)**: Experimental crystal structures
- **Literature values**: Always cite sources for experimental parameters

### File Formats for Molecules
- XYZ: Most common for isolated molecules
- .extxyz: Preserves cell/PBC info if needed
- CIF: If structure has crystallographic information
- POSCAR: For VASP calculations

### Validation Checklist
- [ ] All bond lengths within expected ranges
- [ ] Coordination numbers correct
- [ ] Molecular symmetry as expected
- [ ] No steric clashes
- [ ] Correct conformation chosen
- [ ] Documentation includes parameter sources
- [ ] For encapsulated: guest fits with buffer space
