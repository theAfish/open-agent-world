"""Small modelling helpers, staged by native Sandbox writes; no host execution.

This file is also a standalone Sandbox module. Scientific decisions and final
measurement validation stay in the existing host contract and Skill inspector.
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import sys


def _json_value(value):
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(v) for v in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if hasattr(value, "item"):
        return _json_value(value.item())
    return value


def project_diagnostics(record, *, source="native execution stdout"):
    """Bound diagnostics/prose, never verdicts, check IDs, coverage or objectives.

    The full source remains in an existing report file or native Run archive.
    record_pointer describes that JSON record, not a tool-result read pointer.
    """
    def check(value):
        pending = [value]
        while pending:
            item = pending.pop()
            if item is None or type(item) in (bool, str):
                continue
            if type(item) in (int, float):
                try:
                    valid = math.isfinite(item)
                except OverflowError:
                    valid = False
                if not valid:
                    raise ValueError("Diagnostic numbers must be finite and representable")
            elif isinstance(item, dict):
                if any(not isinstance(k, str) or not 0 < len(k) <= 200 for k in item):
                    raise ValueError("Diagnostic keys must be nonempty strings of at most 200 characters")
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            else:
                raise ValueError("Diagnostics must contain JSON values")

    projected = False
    def preview(value, pointer, depth=0):
        nonlocal projected
        large = (depth >= 4 and isinstance(value, (dict, list)) or
                 isinstance(value, str) and len(value) > 1200 or
                 isinstance(value, (dict, list)) and len(value) > 128)
        if large:
            projected = True
            result = {"detail_source": source, "record_pointer": pointer,
                      "type": type(value).__name__, "size": len(value)}
            if isinstance(value, str):
                result["text_preview"] = value[:400]
            return result
        if isinstance(value, dict):
            return {k: preview(v, pointer + "/" + k.replace("~", "~0").replace("/", "~1"), depth+1)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [preview(v, pointer + "/" + str(i), depth+1) for i, v in enumerate(value)]
        return value

    result = dict(record)
    def prose(text, limit):
        nonlocal projected
        if isinstance(text, str) and len(text) > limit:
            projected = True
            suffix = " ... [full explanation: " + source[:90] + "]"
            return text[:limit-len(suffix)] + suffix
        return text
    if isinstance(record.get("constraints"), list):
        constraints = []
        for check_item in record["constraints"]:
            if not isinstance(check_item, dict):
                constraints.append(check_item)
                continue
            item = dict(check_item)
            if "reason" in item:
                item["reason"] = prose(item["reason"], 400)
            if isinstance(item.get("controls"), list):
                item["controls"] = [{**c, "reason": prose(c["reason"], 400)}
                                    if isinstance(c, dict) and "reason" in c else c for c in item["controls"]]
            constraints.append(item)
        result["constraints"] = constraints
    if "contradiction" in record:
        result["contradiction"] = prose(record["contradiction"], 1200)
    for field in ("values", "measurements", "metadata"):
        value = record.get(field)
        if isinstance(value, dict):
            check(value)
            pointer = "/oaw_measurements/" + field
            summary = preview(value, pointer)
            # Preserve scalar facts alongside arrays; they are also used by the
            # existing recovery observer to distinguish new experiments.
            if len(json.dumps(summary, ensure_ascii=False)) > 4000:
                for key in sorted(summary, key=lambda k: len(json.dumps(summary[k])), reverse=True):
                    child = summary[key]
                    if not isinstance(child, (dict, list, str)):
                        continue
                    reference = {"detail_source": source, "record_pointer": pointer + "/" +
                                 key.replace("~", "~0").replace("/", "~1"),
                                 "type": type(value.get(key)).__name__, "size": len(value[key])}
                    if len(json.dumps(reference)) < len(json.dumps(child)):
                        summary[key] = reference
                        projected = True
                    if len(json.dumps(summary, ensure_ascii=False)) <= 4000:
                        break
            if len(json.dumps(summary, ensure_ascii=False)) > 4000:
                facts = {}
                for key, item in value.items():
                    if item is None or type(item) in (bool, int, float):
                        trial = {**facts, key: item}
                        if len(json.dumps(trial, ensure_ascii=False)) <= 3000:
                            facts = trial
                summary = {"saved_diagnostic": {"detail_source": source, "record_pointer": pointer,
                           "type": "dict", "size": len(value)}, "scalar_facts": facts}
                projected = True
            result[field] = summary
    return result, projected


class HostInputs:
    """Path-indexed manifest for bounded reads inside the Sandbox workspace."""
    def __init__(self, entries):
        if not isinstance(entries, list) or any(not isinstance(a, dict) or not
                {"path"} <= a.keys() or not isinstance(a["path"], str) for a in entries):
            raise ValueError("OAW input manifest must be a list of artifact receipts")
        self.entries = {a["path"]: a for a in entries}
        if len(self.entries) != len(entries):
            raise ValueError("Duplicate OAW input path")

    def receipt(self, path):
        if path not in self.entries:
            raise ValueError("No host input receipt for " + path)
        return self.entries[path]

    def read_bytes(self, path):
        item = self.receipt(path)
        root = Path.cwd().resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Input is outside Sandbox workspace")
        with target.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("OAW input exceeds size limit: " + path)
        return raw

    def json(self, path):
        return json.loads(self.read_bytes(path))

    def array(self, path, dataset):
        import numpy as np
        value = np.load(io.BytesIO(self.read_bytes(path)), allow_pickle=False)
        if isinstance(value, np.lib.npyio.NpzFile):
            with value:
                return value[dataset].copy()
        if dataset != "array":
            raise ValueError("NPY dataset is array; NPZ needs an actual saved key")
        return value

    def atoms(self, path):
        from ase.io import read
        # Extended XYZ preserves actual atom rows and their saved label arrays.
        return read(io.StringIO(self.read_bytes(path).decode()), format="extxyz")

    def label_indices(self, path, field, value):
        import numpy as np
        labels = (self.atoms(path).get_array(field) if Path(path).suffix.lower() in {".xyz", ".extxyz"}
                  else self.array(path, field))
        if labels.ndim != 1 or labels.dtype.kind not in "biuU":
            raise ValueError("Use a saved one-dimensional discrete atom label array")
        if (type(value) is not bool if labels.dtype.kind == "b" else
                type(value) is not int if labels.dtype.kind in "iu" else not isinstance(value, str)):
            raise ValueError("Selector value must match the saved label type")
        return np.flatnonzero(labels == value)


def load_inputs(argv=None):
    """Absent manifest means no inputs; the flag is appended by OAW, not argv."""
    args = list(sys.argv[1:] if argv is None else argv)
    manifests = [args[i+1] for i, v in enumerate(args[:-1]) if v == "--oaw-input-manifest"]
    if (args and args[-1] == "--oaw-input-manifest") or len(manifests) > 1:
        raise ValueError("Expected one --oaw-input-manifest PATH")
    return HostInputs(json.loads(Path(manifests[0]).read_text(encoding="utf-8")) if manifests else [])


def transform_points(points, source_frame, target_frame):
    """Map saved coordinates through an explicitly shared reference frame.

    axes are row basis vectors; origin/axes locate that reference in each saved
    coordinate system. Unrelated files do not acquire a mapping by guessing.
    """
    import numpy as np
    reference = source_frame.get("reference")
    if not reference or reference != target_frame.get("reference"):
        raise ValueError("Coordinate mapping needs the same declared frame.reference; prefer saved labels")
    source = np.asarray(source_frame["axes"], float)
    target = np.asarray(target_frame["axes"], float)
    if source.shape != (3, 3) or target.shape != (3, 3) or not all(
            np.isfinite(a).all() and abs(np.linalg.det(a)) > 1e-12 for a in (source, target)):
        raise ValueError("Frame axes must be finite and nonsingular")
    origins = [np.asarray(frame["origin"], float) for frame in (source_frame, target_frame)]
    points = np.asarray(points, float)
    if any(o.shape != (3,) or not np.isfinite(o).all() for o in origins) or points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("Use finite Nx3 coordinates and three-component frame origins")
    return ((points - origins[0]) @ np.linalg.inv(source)) @ target + origins[1]


def constraint(name, passed, reason="", *, check_ids=(), regions=(), controls=()):
    return _json_value(dict(name=name, passed=passed, reason=reason,
                           check_ids=check_ids, regions=regions, controls=controls))


def artifact(path, *, units, frame, components, layout=None):
    result = dict(path=str(path), units=units, frame=frame, components=components)
    if layout is not None:
        result["layout"] = layout
    return _json_value(result)


def write_extxyz(path, atoms):
    """Use ASE's standard serializer and prove file round-trip, not geometry acceptance."""
    import numpy as np
    from ase.io import read, write
    target = Path(path)
    if not target.resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError("Structure output must stay inside the Sandbox workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    write(target, atoms, format="extxyz")
    back = read(target, format="extxyz")
    if (not np.array_equal(back.numbers, atoms.numbers) or not np.array_equal(back.pbc, atoms.pbc)
            or not np.allclose(back.positions, atoms.positions, atol=1e-8, rtol=0)
            or not np.allclose(back.cell.array, atoms.cell.array, atol=1e-8, rtol=0)):
        raise ValueError("Structure round-trip changed coordinates, cell, elements or PBC")
    for name, value in atoms.arrays.items():
        observed = back.arrays.get(name)
        if observed is None or observed.shape != value.shape or (
                not np.allclose(observed, value, atol=1e-8, rtol=0) if value.dtype.kind in "fc" else
                not np.array_equal(observed, value)):
            raise ValueError("Structure round-trip changed atom array " + name)
    return {"path": str(target), "atom_count": len(back), "round_trip": True, "independent_validation": False,
            "layout": {"format": "xyz", "primary_dataset": "extxyz_atoms"}}


def relax_structure(atoms, *, calculator, movable_indices, fmax, steps, optimizer="FIRE", maxstep=None):
    """Optional fixed-cell ASE relaxation, with caller-chosen physics and region.

    No potential, parameters, moving region or convergence threshold is inferred.
    Convergence is local force convergence under this calculator and constraints,
    not scientific acceptance. The input Atoms and its constraints stay intact.
    """
    import numpy as np
    from ase.constraints import FixAtoms
    from ase.optimize import FIRE, LBFGS

    if calculator is None or not all(callable(getattr(calculator, method, None))
                                     for method in ("get_potential_energy", "get_forces")):
        raise ValueError("Supply an explicit ASE calculator suitable for this system")
    indices = np.asarray(movable_indices)
    if (indices.ndim != 1 or indices.dtype.kind not in "iu" or not len(indices)
            or len(np.unique(indices)) != len(indices) or np.any(indices < 0) or np.any(indices >= len(atoms))):
        raise ValueError("movable_indices must be distinct in-range integer atom indices")
    if (isinstance(fmax, (bool, np.bool_)) or not np.isfinite(fmax) or fmax <= 0
            or type(steps) is not int or steps < 1):
        raise ValueError("Supply a positive finite fmax and a positive integer steps")
    if maxstep is not None and (isinstance(maxstep, (bool, np.bool_)) or not np.isfinite(maxstep) or maxstep <= 0):
        raise ValueError("maxstep must be positive and finite")
    optimizers = {"FIRE": FIRE, "LBFGS": LBFGS}
    if optimizer not in optimizers:
        raise ValueError("Choose an available ASE optimizer: FIRE or LBFGS")
    relaxed = atoms.copy()
    relaxed.calc = calculator
    fixed = np.ones(len(relaxed), dtype=bool)
    fixed[indices] = False
    relaxed.set_constraint([*relaxed.constraints, FixAtoms(mask=fixed)])

    def observe():
        energy = float(relaxed.get_potential_energy())
        raw_forces = relaxed.get_forces(apply_constraint=False)
        forces = relaxed.get_forces()  # ASE applies existing and added constraints.
        if (not np.isfinite(relaxed.positions).all() or not np.isfinite(relaxed.cell.array).all()
                or not np.isfinite(energy) or raw_forces.shape != (len(relaxed), 3) or not np.isfinite(raw_forces).all()
                or forces.shape != (len(relaxed), 3) or not np.isfinite(forces).all()):
            raise ValueError("Relaxation produced nonfinite geometry, energy or forces")
        return energy, float(np.linalg.norm(forces, axis=1).max())

    initial_energy, _ = observe()
    options = {"maxstep": maxstep} if maxstep is not None else {}
    engine = optimizers[optimizer](relaxed, logfile=None, **options)
    engine.attach(observe, interval=1)
    converged = bool(engine.run(fmax=fmax, steps=steps))
    final_energy, final_force = observe()
    return relaxed, dict(optimizer=optimizer, calculator=type(calculator).__module__ + "." + type(calculator).__name__,
                        requested_movable_count=int(len(indices)), fixed_complement_count=int(fixed.sum()),
                        existing_constraint_count=len(atoms.constraints), steps=engine.get_number_of_steps(),
                        fmax=float(fmax), max_force=final_force, initial_energy=initial_energy, final_energy=final_energy,
                        energy_unit="eV", force_unit="eV/angstrom", converged=converged,
                        independent_validation=False)


def emit_measurements(*, schema_version=1, kind="diagnostic", measurement_revision=None, constraints=(), coverage=None,
                      artifacts=(), values=None, measurements=None, metadata=None, objective=None,
                      assumption_invalid=False, contradiction="", report_path=None):
    """Serialize native/NumPy values; controls belong inside constraint(), never here.

    Failed checks and nonfinite data are never repaired into passing values.
    The host's existing Measurement model validates this exact envelope.
    report_path optionally keeps the full data in an ordinary Sandbox JSON file
    so a reporting branch can re-envelope it without rerunning the calculation.
    """
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("Unsupported measurement schema_version")
    result = dict(schema_version=schema_version, kind=kind, constraints=constraints, coverage=coverage or {},
                  artifacts=artifacts, values=values or {}, metadata=metadata or {},
                  assumption_invalid=assumption_invalid, contradiction=contradiction)
    if measurement_revision is not None:
        result["measurement_revision"] = measurement_revision
    if objective is not None:
        result["objective"] = objective
    if measurements is not None:
        result["measurements"] = measurements
    result = _json_value(result)
    path = None
    if report_path is not None:
        path = Path(report_path)
        if path.suffix.lower() != ".json" or not path.resolve().is_relative_to(Path.cwd().resolve()):
            raise ValueError("Measurement report must be a JSON file inside the Sandbox workspace")
        result["metadata"]["oaw_report_file"] = str(path.resolve().relative_to(Path.cwd().resolve()))
    projected, changed = project_diagnostics(result)
    if changed or path is not None:
        if path is None:
            from uuid import uuid4
            folder = Path(".oaw-reports")
            if not folder.resolve().is_relative_to(Path.cwd().resolve()):
                raise ValueError("Report output must stay inside the Sandbox workspace")
            path = folder / (uuid4().hex + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps({"oaw_measurements": result}, ensure_ascii=False, allow_nan=False)
        with path.open("w" if report_path is not None else "x", encoding="utf-8") as stream:
            stream.write(encoded)
        projected, _ = project_diagnostics(result, source=str(path))
    print(json.dumps({"oaw_measurements": projected}, allow_nan=False))
