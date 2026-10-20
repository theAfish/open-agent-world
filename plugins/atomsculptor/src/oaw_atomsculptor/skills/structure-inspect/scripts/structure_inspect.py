"""Read and validate atomic structures — inspect a file, measure distances, detect close contacts."""
import base64
import json as _json
from pathlib import Path
from typing import Any

import numpy as np
from ase import Atoms
from ase.data import covalent_radii
from ase.io import read, write
from ase.neighborlist import neighbor_list

from oaw_runtime import build_cli_parser, run_cli
from oaw_runtime import display_path, resolve_output_path, workspace_root
from structure_validation import measure_structure, measure_nanotube

_LARGE_CIF_SIZE_BYTES = 1_000_000


def _unique_json_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


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
        "atom_arrays": {name: {"shape": list(array.shape), "dtype": str(array.dtype)}
                        for name, array in atoms.arrays.items()},
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


def _resolve_regions(atoms, regions):
    """Resolve saved label selectors, then let the existing partition audit judge coverage."""
    if not isinstance(regions, list) or not 1 <= len(regions) <= 32:
        raise ValueError("Region checks require 1..32 named regions")
    result = []
    for row in regions:
        if not isinstance(row, dict) or "selector" not in row:
            result.append(row)
            continue
        if set(row) != {"name", "selector", "degree_min", "degree_max"}:
            raise ValueError("Use indices OR selector with name and degree range")
        selector = row["selector"]
        if not isinstance(selector, dict) or set(selector) != {"array", "values"}:
            raise ValueError("Selector requires array and values from saved atom labels")
        name, values = selector["array"], selector["values"]
        labels = atoms.arrays.get(name) if isinstance(name, str) else None
        if labels is None or labels.shape != (len(atoms),) or labels.dtype.kind not in "biuU":
            raise ValueError("Selector requires an actual one-dimensional discrete atom array")
        kind = labels.dtype.kind
        if (not isinstance(values, list) or not values or any(
                type(v) is not bool if kind == "b" else type(v) is not int if kind in "iu" else
                not isinstance(v, str) for v in values)):
            raise ValueError("Selector values must match the saved label type")
        ids = np.flatnonzero(np.isin(labels, values)).tolist()
        result.append({**{k: v for k, v in row.items() if k != "selector"}, "indices": ids})
    return result


def _validate_file(folder, file_name, measure, **acceptance):
    path = file_name if folder in ("", ".") else str(Path(folder) / file_name)
    resolved = _resolve_existing_file(path)
    if resolved is None:
        return {"error": f"File not found: {path}"}
    atoms = _load_atoms_from_path(path)
    if isinstance(atoms, dict):
        return atoms
    if acceptance.get("region_checks") is not None:
        try:
            acceptance["region_checks"] = _resolve_regions(atoms, acceptance["region_checks"])
        except ValueError as exc:
            return {"passed": False, "error": str(exc)}
    result = measure(atoms.get_chemical_symbols(), atoms.positions.tolist(),
                     atoms.cell.array.tolist(), atoms.pbc.tolist(), **acceptance)
    result["file"] = display_path(resolved)
    return result


def validate_structure(folder: str, file_name: str, expected_count: int | None = None,
                       expected_symbol: str | None = None, min_vacuum: float | None = None,
                       min_distance: float = 0.1, nonperiodic: bool = False,
                       nearest_neighbor_min: float | None = None,
                       nearest_neighbor_max: float | None = None,
                       connectivity_cutoff: float | None = None, require_connected: bool = False,
                       region_checks_file: str | None = None, distance_mode: str = "auto") -> dict:
    """Independently inspect geometry with distances matching the file's PBC."""
    rules = None
    if region_checks_file is not None:
        resolved = _resolve_existing_file(region_checks_file)
        if resolved is None:
            return {"passed": False, "error": "Region checks require an existing workspace file"}
        with resolved.open("rb") as stream:
            raw = stream.read(131073)
        if len(raw) > 131072:
            return {"passed": False, "error": "Region checks file is too large"}
        try:
            rules = _json.loads(raw, object_pairs_hook=_unique_json_fields)
        except (ValueError, UnicodeError, RecursionError):
            return {"passed": False, "error": "Region checks file must contain JSON with unique fields"}
        if (not isinstance(rules, dict) or set(rules) != {"regions"}
                or not isinstance(rules["regions"], list) or not rules["regions"]):
            return {"passed": False, "error": "Region checks need a nonempty regions list"}
    result = _validate_file(folder, file_name, measure_structure, expected_count=expected_count,
        expected_symbol=expected_symbol, min_vacuum=min_vacuum, min_distance=min_distance,
        nonperiodic=nonperiodic, nearest_neighbor_min=nearest_neighbor_min,
        nearest_neighbor_max=nearest_neighbor_max, connectivity_cutoff=connectivity_cutoff,
        require_connected=require_connected, region_checks=rules["regions"] if rules else None,
        distance_mode=distance_mode)
    if rules is not None:
        result["region_checks_file"] = region_checks_file
    return result


def validate_nanotube(folder: str, file_name: str, n: int, m: int, axial_repeats: int,
                      bond_length: float = 1.42, min_vacuum: float = 8.0,
                      axis: int = 2, tolerance: float = 0.02) -> dict:
    """Measure and verify an unrelaxed open CNT, without constructing coordinates."""
    return _validate_file(folder, file_name, measure_nanotube, n=n, m=m,
        axial_repeats=axial_repeats, bond_length=bond_length, min_vacuum=min_vacuum,
        axis=axis, tolerance=tolerance)


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


def _read_strings(atoms, name):
    encoded = atoms.arrays.get(name + "_b64")
    if encoded is not None:
        return [base64.b64decode(str(value).removeprefix("b64:"), altchars=b"-_", validate=True).decode("utf-8")
                for value in encoded]
    return atoms.arrays.get(name)


def _write_strings(atoms, name, values):
    # ASE extxyz uses whitespace-separated per-atom fields. Empty strings and
    # labels/layer IDs containing spaces otherwise create unreadable rows.
    if any(not value or any(char.isspace() for char in value) for value in values):
        values = ["b64:" + base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii") for value in values]
        name += "_b64"
    atoms.new_array(name, np.asarray(values, dtype=str))


def to_atomsculptor_document(folder: str, file_name: str, output_name: str | None = None) -> dict:
    """Convert a structure file into the AtomSculptor StructureDocument JSON.

    The returned ``document`` value can be passed unchanged as the
    ``structure`` argument of ``replace_atom_structure``; stable
    ``atomsculptor_id`` per-atom values survive when the file carries them.
    """
    source_path = _resolve_existing_file(file_name if folder in ("", ".") else str(Path(folder) / file_name))
    if source_path is None:
        return {"error": f"File not found: {file_name}"}
    atoms = _load_atoms(folder, file_name)
    if isinstance(atoms, dict) and "error" in atoms:
        return atoms

    layers = _json_list(atoms.info.get("atomsculptor_layers"), [
        {"id": "atoms", "name": "Atoms", "kind": "atoms", "visible": True}])
    declared = {layer["id"] for layer in layers}
    active_layers = _json_list(atoms.info.get("atomsculptor_active_layers"), [])
    active = [layer["id"] for layer in active_layers if layer["id"] in declared] or [layers[0]["id"]]

    stable_ids = atoms.arrays.get("atomsculptor_id")
    try:
        layer_values = _read_strings(atoms, "layer_id")
        label_values = _read_strings(atoms, "atomsculptor_label")
    except (ValueError, UnicodeError):
        return {"error": "Invalid encoded atom labels or layer IDs"}
    occupancy_values = atoms.arrays.get("occupancy")
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
    result = {
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
    if output_name is not None:
        if not output_name.lower().endswith(".json"):
            return {"error": "output_name must end with .json"}
        output_path = resolve_output_path(output_name)
        output_path.write_text(_json.dumps(result["document"], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return {"output_document_file": display_path(output_path),
                "input_structure_file": display_path(source_path),
                "atom_count": len(entries), "size_bytes": output_path.stat().st_size}
    return result


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
    _write_strings(atoms, "layer_id", layer_ids)
    _write_strings(atoms, "atomsculptor_label", labels)
    atoms.new_array("occupancy", np.array(occupancies, dtype=float))
    atoms.info["atomsculptor_layers"] = _json.dumps(value.get("layers") or [
        {"id": "atoms", "name": "Atoms", "kind": "atoms", "visible": True}])
    atoms.info["atomsculptor_active_layers"] = _json.dumps(value.get("active_layer_ids") or ["atoms"])

    output_path = resolve_output_path(output_name)
    write(output_path, atoms)
    return {"output_structure_file": display_path(output_path), "atom_count": len(atoms)}


def analyze_topology(folder: str, file_name: str, connectivity_cutoff: float,
                     max_ring_size: int = 8, max_cycles: int = 10000) -> dict:
    """Measure cutoff-graph rings and local geometry; never infer hybridization or acceptance."""
    from scientific_analysis import topology
    atoms = _load_atoms(folder, file_name)
    return atoms if isinstance(atoms, dict) else {"file": file_name, **topology(atoms, connectivity_cutoff, max_ring_size, max_cycles)}


def analyze_symmetry(folder: str, file_name: str, symprec: float,
                     angle_tolerance: float, molecular_tolerance: float) -> dict:
    """Explicit tolerances; space group for bulk, point group for finite objects."""
    from scientific_analysis import symmetry
    atoms = _load_atoms(folder, file_name)
    return atoms if isinstance(atoms, dict) else {"file": file_name, **symmetry(atoms, symprec, angle_tolerance, molecular_tolerance)}


def list_calculators() -> dict:
    """List optional pure ASE calculators without executing or downloading a model."""
    from scientific_analysis import calculator_inventory
    return calculator_inventory()


def measure_energy_forces(folder: str, file_name: str, calculator: str,
                          parameters_file: str, parameter_source: str, reference_file: str | None = None) -> dict:
    """Read explicit JSON calculator parameters; energy/forces do not prove stability."""
    from scientific_analysis import _local, make_calculator, energy_forces
    atoms = _load_atoms(folder, file_name)
    if isinstance(atoms, dict):
        return atoms
    parameters = _json.loads(_local(parameters_file).read_text(encoding="utf-8"), object_pairs_hook=_unique_json_fields)
    reference = _load_atoms(folder, reference_file) if reference_file else None
    if isinstance(reference, dict):
        return reference
    instance = make_calculator(calculator, parameters, parameter_source)
    return {"file": file_name, "parameters": parameters,
            **energy_forces(atoms, instance, parameter_source, reference)}


_TOOLS = {
    "analyze_topology": analyze_topology,
    "analyze_symmetry": analyze_symmetry,
    "list_calculators": list_calculators,
    "measure_energy_forces": measure_energy_forces,
    "read_structure": read_structure,
    "validate_structure": validate_structure,
    "validate_nanotube": validate_nanotube,
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
