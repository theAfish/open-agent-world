"""Optional read-only instruments; measurements are not acceptance verdicts.

Uses ASE neighbours/calculators, NetworkX chordless cycles and pymatgen symmetry.
No potential, bonding threshold or physical-stability criterion is inferred.
"""
import importlib
import itertools
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from ase.neighborlist import neighbor_list


def _positive(value, name):
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")


def _valid_atoms(atoms):
    if not len(atoms) or not np.isfinite(atoms.positions).all() or not np.isfinite(atoms.cell.array).all():
        raise ValueError("Use a nonempty finite structure")


def topology(atoms, connectivity_cutoff, max_ring_size=8, max_cycles=10000):
    """Finite chordless rings; periodic-image-aware local geometric proxies."""
    _valid_atoms(atoms)
    _positive(connectivity_cutoff, "connectivity_cutoff")
    if type(max_ring_size) is not int or max_ring_size < 3 or type(max_cycles) is not int or max_cycles < 1:
        raise ValueError("max_ring_size >= 3 and max_cycles >= 1 must be integers")
    import networkx as nx
    first, second, vectors = neighbor_list("ijD", atoms, connectivity_cutoff, self_interaction=False)
    local = [[] for _ in atoms]
    for i, vector in zip(first, vectors):
        norm = np.linalg.norm(vector)
        if norm <= 1e-12:
            raise ValueError("Coincident neighbours cannot define local geometry")
        local[int(i)].append(vector / norm)
    degrees = [len(v) for v in local]
    environments = []
    for i, vectors in enumerate(local):
        row = {"index": i, "coordination": len(vectors)}
        if len(vectors) == 3:
            row["threefold_nonplanarity"] = abs(float(np.linalg.det(vectors)))
        if len(vectors) == 4:
            angles = [math.degrees(math.acos(float(np.clip(np.dot(a, b), -1, 1))))
                      for a, b in itertools.combinations(vectors, 2)]
            tetrahedral = math.degrees(math.acos(-1 / 3))
            row["tetrahedral_angle_rms_deviation_degrees"] = float(np.sqrt(np.mean((np.array(angles) - tetrahedral)**2)))
        environments.append(row)
    rings = {"definition": "simple chordless cycles of the cutoff graph", "max_ring_size": max_ring_size}
    if any(atoms.pbc):
        rings.update(status="unsupported_periodic_graph", counts=None, complete=False)
    else:
        graph = nx.Graph()
        graph.add_nodes_from(range(len(atoms)))
        graph.add_edges_from(zip(map(int, first), map(int, second)))
        counts = Counter()
        complete = True
        for number, cycle in enumerate(nx.chordless_cycles(graph, length_bound=max_ring_size)):
            if number == max_cycles:
                complete = False
                break
            counts[str(len(cycle))] += 1
        rings.update(status="measured", counts=dict(sorted(counts.items())), complete=complete, max_cycles=max_cycles)
    return {"connectivity_cutoff_angstrom": connectivity_cutoff, "pbc": atoms.pbc.tolist(),
            "coordination_histogram": dict(sorted(Counter(degrees).items())), "rings": rings,
            "local_environments": environments,
            "interpretation": "Geometric proxies only; coordination and planarity do not establish electronic sp2/sp3 hybridization.",
            "physical_stability": "not_assessed"}


def symmetry(atoms, symprec, angle_tolerance, molecular_tolerance):
    _valid_atoms(atoms)
    _positive(symprec, "symprec")
    _positive(molecular_tolerance, "molecular_tolerance")
    if not math.isfinite(angle_tolerance) or angle_tolerance < 0:
        raise ValueError("angle_tolerance must be finite and nonnegative")
    settings = dict(symprec_angstrom=symprec, angle_tolerance_degrees=angle_tolerance,
                    molecular_tolerance_angstrom=molecular_tolerance)
    if any(atoms.pbc) and not all(atoms.pbc):
        return {"status": "unsupported_partial_periodicity", "pbc": atoms.pbc.tolist(), "settings": settings}
    from pymatgen.io.ase import AseAtomsAdaptor
    if all(atoms.pbc):
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
        analyzer = SpacegroupAnalyzer(AseAtomsAdaptor.get_structure(atoms), symprec=symprec, angle_tolerance=angle_tolerance)
        result = {"kind": "space_group", "symbol": analyzer.get_space_group_symbol(), "number": analyzer.get_space_group_number()}
    else:
        from pymatgen.symmetry.analyzer import PointGroupAnalyzer
        analyzer = PointGroupAnalyzer(AseAtomsAdaptor.get_molecule(atoms), tolerance=molecular_tolerance)
        result = {"kind": "point_group", "symbol": analyzer.sch_symbol}
    return {"status": "measured", "settings": settings, **result}


# Explicit, pure ASE implementations: availability is not suitability. No model
# downloads, executable discovery, arbitrary imports or automatic potential fit.
CALCULATORS = {
    "emt": ("ase.calculators.emt", "EMT"),
    "lj": ("ase.calculators.lj", "LennardJones"),
    "morse": ("ase.calculators.morse", "MorsePotential"),
    "tersoff": ("ase.calculators.tersoff", "Tersoff"),
    "eam": ("ase.calculators.eam", "EAM"),
}


def calculator_inventory():
    rows = []
    for name, (module, cls) in CALCULATORS.items():
        try:
            getattr(importlib.import_module(module), cls)
            available, error = True, None
        except (ImportError, AttributeError) as exc:
            available, error = False, str(exc)
        rows.append({"name": name, "available": available, "error": error,
                     "parameters_required": name in {"tersoff", "eam"}, "validated_for_this_task": False})
    return {"calculators": rows, "guidance": "Choose physics and parameters with a cited source and reference-system validation; availability alone is insufficient."}


def _local(path):
    target = (Path.cwd() / path).resolve()
    if not target.is_relative_to(Path.cwd().resolve()):
        raise ValueError("Calculator parameter file must stay inside the Sandbox workspace")
    return target


def make_calculator(name, parameters, parameter_source):
    if name not in CALCULATORS or not isinstance(parameters, dict) or not isinstance(parameter_source, str) or not parameter_source.strip():
        raise ValueError("Choose a listed calculator, explicit parameter object and parameter_source")
    module, cls = CALCULATORS[name]
    factory = getattr(importlib.import_module(module), cls)
    options = dict(parameters)
    if name == "tersoff":
        path = _local(options.pop("parameters_file"))
        return factory.from_lammps(path, **options)
    if name == "eam":
        options["potential"] = str(_local(options["potential"]))
    return factory(**options)


def energy_forces(atoms, calculator, parameter_source, reference=None):
    _valid_atoms(atoms)
    if not isinstance(parameter_source, str) or not parameter_source.strip():
        raise ValueError("Provide parameter_source")
    def measure(value):
        copy = value.copy()
        copy.calc = calculator
        energy = float(copy.get_potential_energy())
        forces = np.asarray(copy.get_forces(apply_constraint=False))
        if not math.isfinite(energy) or forces.shape != (len(value), 3) or not np.isfinite(forces).all():
            raise ValueError("Calculator returned invalid energy/forces")
        return energy, forces
    energy, forces = measure(atoms)
    result = {"energy_eV": energy, "max_force_eV_per_angstrom": float(np.linalg.norm(forces, axis=1).max()),
              "calculator": type(calculator).__module__ + "." + type(calculator).__name__,
              "parameter_source": parameter_source, "physical_stability": "not_assessed",
              "constraints_applied_to_forces": False}
    if reference is not None:
        _valid_atoms(reference)
        if Counter(atoms.numbers) != Counter(reference.numbers) or not np.array_equal(atoms.pbc, reference.pbc) or not np.allclose(atoms.cell, reference.cell):
            raise ValueError("Relative energy requires matching composition, cell and PBC")
        reference_energy, _ = measure(reference)
        result.update(reference_energy_eV=reference_energy, energy_difference_eV=energy-reference_energy)
    return result
