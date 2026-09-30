"""Explicit Materials Project lookup for an OAW Sandbox with network access.

Requires mp-api in that Sandbox and MP_API_KEY in its selected Environment
Profile. Never print the key or persist it in the output file.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    search = subcommands.add_parser("search")
    search.add_argument("--formula", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--output", required=True)
    download = subcommands.add_parser("download")
    download.add_argument("--material-id", required=True)
    download.add_argument("--output", required=True)
    args = parser.parse_args()
    key = os.environ.get("MP_API_KEY")
    if not key:
        parser.error("MP_API_KEY is missing from the selected Sandbox Environment Profile")
    output = Path(args.output)
    if output.suffix.lower() not in ({".json"} if args.command == "search" else {".cif"}):
        parser.error("Search output must be .json and download output must be .cif")
    if args.command == "search" and not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    try:
        from mp_api.client import MPRester
    except ImportError as exc:
        raise SystemExit("Install mp-api in the selected OAW Sandbox before running this Skill") from exc

    with MPRester(key) as client:
        if args.command == "search":
            fields = ["material_id", "formula_pretty", "energy_above_hull", "symmetry", "band_gap"]
            records = client.materials.summary.search(formula=args.formula, fields=fields,
                                                      all_fields=False, chunk_size=args.limit, num_chunks=1)
            data = [{"material_id": str(item.material_id), "formula": item.formula_pretty,
                     "energy_above_hull": item.energy_above_hull,
                     "space_group": str(item.symmetry.symbol) if item.symmetry else None,
                     "band_gap": item.band_gap} for item in records[:args.limit]]
            output.write_text(json.dumps({"query": args.formula, "results": data}, indent=2), encoding="utf-8")
            print(json.dumps({"output": str(output), "result_count": len(data)}))
        else:
            if not args.material_id.startswith("mp-"):
                parser.error("--material-id must be a Materials Project ID such as mp-13")
            structure = client.get_structure_by_material_id(args.material_id)
            structure.to(filename=str(output), fmt="cif")
            print(json.dumps({"output": str(output), "material_id": args.material_id,
                              "atom_count": len(structure)}))


if __name__ == "__main__":
    main()
