"""Read and validate atomic structures — inspect a file, measure distances, detect close contacts."""
import json as _json
from pathlib import Path
from typing import Any

import numpy as np
from ase import Atoms
from ase.data import covalent_radii
from ase.io import read
from ase.neighborlist import neighbor_list

from oaw_runtime import build_cli_parser, run_cli
from oaw_runtime import display_path, resolve_output_path, workspace_root

_LARGE_CIF_SIZE_BYTES = 1_000_000


def _iter_path_candidates(path_str: str) -> list[Path]:
    p = Path(path_str)
    candidates: list[Path] = [p]
    if not p.is_absolute():
        candidates.append(workspace_root() / p)
        if p.parent == Path("."):
            candidates.append(workspace_root() / p.name)
    seen: set[str] = set()
    unique: list[Path] = []
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _resolve_existing_file(path_str: str) -> Path | None:
    for candidate in _iter_path_candidates(path_str):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_atoms_from_path(path_str: str):
    resolved = _resolve_existing_file(path_str)
    if resolved is None:
        return {"error": f"File not found: {path_str}"}
    try:
        if resolved.suffix.lower() == ".cif" and resolved.stat().st_size >= _LARGE_CIF_SIZE_BYTES:
            from pymatgen.io.ase import AseAtomsAdaptor
            from pymatgen.io.cif import CifParser
            parser = CifParser(str(resolved))
            structures = parser.parse_structures(primitive=False)
            if not structures:
                return {"error": f"No structures found in CIF file: {path_str}"}
            return AseAtomsAdaptor.get_atoms(structures[0])
        return read(resolved)
    except Exception as exc:
        return {"error": str(exc)}


def _load_atoms(folder: str, file_name: str):
    path_str = file_name if folder in ("", ".") else str(Path(folder) / file_name)
    return _load_atoms_from_path(path_str)


def read_structure(folder: str, file_name: str) -> dict:
    """Summarizes the structure (formula, atom count, cell, PBC; full atom list when ≤ 10 atoms)."""
    atoms = _load_atoms(folder, file_name)
    if isinstance(atoms, dict) and "error" in atoms:
        return atoms

    num_atoms = len(atoms)
    result: dict[str, Any] = {
        "file": file_name,
        "chemical_formula": atoms.get_chemical_formula(),
        "num_atoms": num_atoms,
        "cell_vectors_angstrom": atoms.cell.array.tolist() if atoms.cell is not None else None,
        "periodic_boundary_conditions": atoms.pbc.tolist(),
    }
    if num_atoms <= 10:
        result["atoms"] = [
            {
                "index": index,
                "symbol": atom.symbol,
                "position_angstrom": atoms.positions[index].tolist(),
            }
            for index, atom in enumerate(atoms)
        ]
    return result


def read_structures_in_text(folder: str, file_name: str) -> dict:
    """Returns the raw structure file contents as text."""
    path_str = file_name if folder in ("", ".") else str(Path(folder) / file_name)
    file_path = _resolve_existing_file(path_str)
    if file_path is None:
        return {"error": f"File not found: {path_str}"}
    try:
        return {"raw_file_text": file_path.read_text(encoding="utf-8")}
    except Exception as exc:
        return {"error": str(exc)}


def calculate_distance(folder: str, file_name: str, index1: int, index2: int) -> dict:
    """Distance (Å) between two atom indices in the referenced structure."""
    atoms = _load_atoms(folder, file_name)
    if isinstance(atoms, dict) and "error" in atoms:
        return atoms

    num_atoms = len(atoms)
    for requested_index in (index1, index2):
        if requested_index < 0 or requested_index >= num_atoms:
            return {"error": f"Atom index {requested_index} is out of bounds for {num_atoms} atoms"}

    pos1 = atoms.positions[index1]
    pos2 = atoms.positions[index2]
    return {
        "file": file_name,
        "atom1": {"index": index1, "symbol": atoms[index1].symbol, "position_angstrom": pos1.tolist()},
        "atom2": {"index": index2, "symbol": atoms[index2].symbol, "position_angstrom": pos2.tolist()},
        "distance_angstrom": float(np.linalg.norm(pos1 - pos2)),
    }


def check_close_atoms(folder: str, file_name: str, tolerance: float = -0.5) -> dict:
    """Detect pairs whose distance is below covalent_radii_sum + tolerance.

    Useful for validating a freshly built structure for steric clashes.
    """
    atoms = _load_atoms(folder, file_name)
    if isinstance(atoms, dict) and "error" in atoms:
        return atoms

    radii = np.array([covalent_radii[a.number] for a in atoms])
    cutoff = float(radii.max() * 2 + tolerance)
    i, j, d = neighbor_list("ijd", atoms, cutoff)

    pair_mask = i < j
    close_mask = d < (radii[i] + radii[j] + tolerance)
    mask = pair_mask & close_mask

    close_pairs = []
    for idx1, idx2, dist in zip(i[mask], j[mask], d[mask]):
        min_dist = radii[idx1] + radii[idx2] + tolerance
        close_pairs.append({
            "atom1": {"index": int(idx1), "symbol": atoms[int(idx1)].symbol},
            "atom2": {"index": int(idx2), "symbol": atoms[int(idx2)].symbol},
            "distance_angstrom": round(float(dist), 3),
            "min_distance_angstrom": round(float(min_dist), 3),
        })
    num_close = len(close_pairs)
    if num_close > 10:
        close_pairs = close_pairs[:10]
        close_pairs.append({"note": f"{num_close - 10} more pairs not shown"})
    return {
        "file": file_name,
        "number_of_detected_close_pairs": num_close,
        "close_pairs": close_pairs,
    }


def _json_list(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode metadata that this converter previously attached to an extxyz file."""
    if not isinstance(value, str):
        return fallback
    try:
        decoded = _json.loads(value)
    except ValueError:
        return fallback
    if not isinstance(decoded, list) or not decoded or not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in decoded):
        return fallback
    return decoded


def to_atomsculptor_document(folder: str, file_name: str) -> dict:
    """Convert a structure file into the AtomSculptor StructureDocument JSON.

    The returned ``document`` value can be passed unchanged as the
    ``structure`` argument of ``replace_atom_structure``; stable
    ``atomsculptor_id`` per-atom values survive when the file carries them.
    """
    atoms = _load_atoms(folder, file_name)
    if isinstance(atoms, dict) and "error" in atoms:
        return atoms

    layers = _json_list(atoms.info.get("atomsculptor_layers"), [
        {"id": "atoms", "name": "Atoms", "kind": "atoms", "visible": True}])
    declared = {layer["id"] for layer in layers}
    active_layers = _json_list(atoms.info.get("atomsculptor_active_layers"), [])
    active = [layer["id"] for layer in active_layers if layer["id"] in declared] or [layers[0]["id"]]

    stable_ids = atoms.arrays.get("atomsculptor_id")
    layer_values = atoms.arrays.get("layer_id")
    occupancy_values = atoms.arrays.get("occupancy")
    label_values = atoms.arrays.get("atomsculptor_label")
    entries: list[dict[str, Any]] = []
    for index in range(len(atoms)):
        entry: dict[str, Any] = {
            "id": int(stable_ids[index]) if stable_ids is not None and index < len(stable_ids) else index,
            "symbol": atoms[index].symbol,
            "x": float(atoms.positions[index][0]),
            "y": float(atoms.positions[index][1]),
            "z": float(atoms.positions[index][2]),
            "layer_id": str(layer_values[index]) if layer_values is not None and index < len(layer_values) and str(layer_values[index]) in declared else layers[0]["id"],
        }
        if label_values is not None and index < len(label_values):
            entry["label"] = str(label_values[index])
        if occupancy_values is not None and index < len(occupancy_values):
            entry["occupancy"] = float(occupancy_values[index])
        entries.append(entry)
    if len({entry["id"] for entry in entries}) != len(entries):
        for new_id, entry in enumerate(entries):
            entry["id"] = new_id

    cell_array = np.asarray(atoms.cell.array, dtype=float)
    cell = cell_array.tolist() if (atoms.pbc.any() or np.abs(cell_array).max() > 1e-12) else None
    return {
        "document": {
            "format_version": 1,
            "atoms": entries,
            "bonds": [],
            "cell": cell,
            "pbc": [bool(value) for value in atoms.pbc],
            "layers": layers,
            "active_layer_ids": active,
            "selected_atom_ids": [],
            "source_name": Path(file_name).name,
            "source_metadata": {"converted_by": "atomsculptor structure-inspect"},
        }
    }


def from_atomsculptor_document(document: str, output_name: str = "structure.extxyz") -> dict:
    """Write an AtomSculptor StructureDocument JSON into a structure file.

    ``document`` is either inline JSON or the path of a JSON file already in
    the workspace; ``output_name`` is relative to the workspace root.  Atom IDs
    and layer assignments are written back as per-atom columns so the round
    trip preserves stable identities.
    """
    raw = str(document).strip()
    if not raw.startswith("{"):
        source = _resolve_existing_file(document)
        if source is None:
            return {"error": f"Document file not found: {document}"}
        raw = source.read_text(encoding="utf-8")
    try:
        value = _json.loads(raw)
    except ValueError as exc:
        return {"error": f"Invalid document JSON: {exc}"}
    records = value.get("atoms") if isinstance(value, dict) else None
    if not isinstance(records, list):
        return {"error": "Document must contain an atoms list"}

    symbols: list[str] = []
    positions: list[list[float]] = []
    ids: list[int] = []
    layer_ids: list[str] = []
    labels: list[str] = []
    occupancies: list[float] = []
    for entry in records:
        if not isinstance(entry, dict):
            continue
        try:
            position = [float(entry.get(axis, 0.0)) for axis in ("x", "y", "z")]
        except (TypeError, ValueError):
            continue
        symbols.append(str(entry.get("symbol", "X"))[:3] or "X")
        positions.append(position)
        ids.append(int(entry.get("id", len(ids))))
        layer_ids.append(str(entry.get("layer_id", "atoms"))[:120] or "atoms")
        labels.append(str(entry.get("label", ""))[:120])
        occupancies.append(float(entry.get("occupancy", 1.0)))
    if not symbols:
        return {"error": "Document contains no atoms"}

    pbc_value = value.get("pbc")
    pbc = [bool(item) for item in pbc_value] if isinstance(pbc_value, list) and len(pbc_value) == 3 else [False, False, False]
    atoms = Atoms(symbols=symbols, positions=positions, pbc=pbc)
    cell = value.get("cell")
    if isinstance(cell, list) and len(cell) == 3 and all(isinstance(row, list) and len(row) == 3 for row in cell):
        atoms.cell = cell
    atoms.new_array("atomsculptor_id", np.array(ids, dtype=int))
    atoms.new_array("layer_id", np.array(layer_ids, dtype="U120"))
    atoms.new_array("atomsculptor_label", np.array(labels, dtype="U120"))
    atoms.new_array("occupancy", np.array(occupancies, dtype=float))
    atoms.info["atomsculptor_layers"] = _json.dumps(value.get("layers") or [
        {"id": "atoms", "name": "Atoms", "kind": "atoms", "visible": True}])
    atoms.info["atomsculptor_active_layers"] = _json.dumps(value.get("active_layer_ids") or ["atoms"])

    output_path = resolve_output_path(output_name)
    write(output_path, atoms)
    return {"output_structure_file": display_path(output_path), "atom_count": len(atoms)}


_TOOLS = {
    "read_structure": read_structure,
    "read_structures_in_text": read_structures_in_text,
    "calculate_distance": calculate_distance,
    "check_close_atoms": check_close_atoms,
    "to_atomsculptor_document": to_atomsculptor_document,
    "from_atomsculptor_document": from_atomsculptor_document,
}


if __name__ == "__main__":
    parser = build_cli_parser(
        prog="structure_inspect.py",
        description_lines=[
            "Read and validate atomic structures.",
            f"Working directory: {workspace_root()}",
            "",
        ],
        tool_functions=_TOOLS,
    )
    raise SystemExit(run_cli(argv=None, parser=parser, tool_functions=_TOOLS))
