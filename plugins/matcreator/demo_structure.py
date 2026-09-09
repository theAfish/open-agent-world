"""Generate and verify a periodic copper supercell with ASE; no calculator."""
import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output", default="copper")
    parser.add_argument("--library-path", help="Explicit directory of provisioned Python dependencies inside the Sandbox")
    args = parser.parse_args()
    if args.library_path:
        import sys
        sys.path.insert(0, str(Path(args.library_path).resolve()))
    from ase import Atoms
    from ase.io import read, write
    if not 1 <= args.repeat <= 12:
        parser.error("--repeat must be between 1 and 12")
    base = Path(args.output)
    base.parent.mkdir(parents=True, exist_ok=True)
    # Conventional FCC unit cell; no calculator or scipy-dependent builder.
    atoms = Atoms("Cu4", scaled_positions=[(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)],
                  cell=[3.6, 3.6, 3.6], pbc=True).repeat((args.repeat,) * 3)
    paths = [str(base.with_suffix(ext)) for ext in (".extxyz", ".cif")]
    for path in paths:
        write(path, atoms)
        restored = read(path)
        assert len(restored) == len(atoms)
        assert abs(restored.get_volume() - atoms.get_volume()) < 1e-6
    report = {"status": "success", "formula": atoms.get_chemical_formula(), "atoms": len(atoms),
              "volume_angstrom3": atoms.get_volume(), "paths": paths, "roundtrip_verified": True}
    base.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))

if __name__ == "__main__":
    main()
