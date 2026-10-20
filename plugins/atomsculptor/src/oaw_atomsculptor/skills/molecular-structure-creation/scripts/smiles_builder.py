"""Create an RDKit 3D SMILES molecule, optionally placing it beside an ASE structure."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smiles", required=True)
    parser.add_argument("--input-name", help="Optional existing structure file in the Sandbox")
    parser.add_argument("--output-name", required=True)
    args = parser.parse_args()
    if not args.output_name.lower().endswith(".extxyz"):
        parser.error("--output-name must end with .extxyz")
    try:
        import numpy as np
        from ase import Atoms
        from ase.io import read, write
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise SystemExit("Install the Skill's requirements.txt in the selected OAW Sandbox") from exc

    molecule = Chem.MolFromSmiles(args.smiles)
    if molecule is None:
        parser.error("Invalid SMILES")
    molecule = Chem.AddHs(molecule)
    if AllChem.EmbedMolecule(molecule, randomSeed=42) != 0:
        raise SystemExit("RDKit could not generate a 3D conformer for this SMILES")
    if AllChem.UFFHasAllMoleculeParams(molecule):
        AllChem.UFFOptimizeMolecule(molecule, maxIters=200)
    conformer = molecule.GetConformer()
    symbols = [atom.GetSymbol() for atom in molecule.GetAtoms()]
    positions = np.array(conformer.GetPositions(), dtype=float)
    generated = Atoms(symbols, positions=positions)
    if args.input_name:
        original = read(args.input_name)
        if not isinstance(original, Atoms):
            raise SystemExit("Input must contain exactly one structure")
        if any(original.pbc):
            raise SystemExit("Automatic side placement is unsafe for a periodic host; choose an explicit adsorption site instead")
        start = int(np.max(original.arrays.get("atomsculptor_id", np.arange(len(original))))) + 1 if len(original) else 0
        if len(original):
            generated.positions[:, 0] += float(original.positions[:, 0].max() - positions[:, 0].min() + 3.0)
        combined = original.copy()
        combined += generated
        old_ids = original.arrays.get("atomsculptor_id", np.arange(len(original)))
        combined.set_array("atomsculptor_id", np.concatenate((old_ids, np.arange(start, start + len(generated)))).astype(int))
        old_layers = original.arrays.get("layer_id", np.array(["atoms"] * len(original), dtype="U120"))
        layer = str(old_layers[0]) if len(old_layers) else "atoms"
        combined.set_array("layer_id", np.concatenate((old_layers, np.array([layer] * len(generated), dtype="U120"))).astype("U120"))
    else:
        combined = generated
        combined.center(vacuum=5.0)
        combined.new_array("atomsculptor_id", np.arange(len(combined), dtype=int))
        combined.new_array("layer_id", np.array(["atoms"] * len(combined), dtype="U120"))
    write(args.output_name, combined)
    print(json.dumps({"output": args.output_name, "added_atoms": len(generated),
                      "atom_count": len(combined), "method": "RDKit ETKDG + optional UFF"}))


if __name__ == "__main__":
    main()
