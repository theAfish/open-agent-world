"""Deterministic measurements of supplied coordinates; no structure generators.

The pure-Python core can be tested without a Sandbox scientific environment.
Small finite clusters retain the dependency-free path. Larger clusters use a
spatial tree; periodic structures use ASE's existing image-aware neighbor list.
"""
from __future__ import annotations

import math
from collections import Counter

VALIDATOR_VERSION = "2"


def _neighbors(positions, cell, pbc, cutoff, periodic):
    """Return exact nearest distances and directed coordination edges.

    In a periodic cell each atom has its own image at a periodic cell vector,
    providing a finite upper bound on every nearest distance. This also counts
    multiple images and nonzero self images, rather than only minimum images.
    """
    if periodic:
        from ase import Atoms
        from ase.neighborlist import neighbor_list
        bound = min(math.sqrt(_dot(v, v)) for v, flag in zip(cell, pbc) if flag)
        radius = max(bound / max(1, len(positions))**(1/3), cutoff or 0)
        atoms = Atoms(positions=positions, cell=cell, pbc=pbc)
        while True:
            i, j, distances = neighbor_list("ijd", atoms, math.nextafter(radius, math.inf), self_interaction=False)
            nearest = [math.inf] * len(positions)
            edges = []
            for a, b, distance in zip(i, j, distances):
                a, b, distance = int(a), int(b), float(distance)
                nearest[a] = min(nearest[a], distance)
                if cutoff is not None and distance <= cutoff:
                    edges.append((a, b))
            if all(math.isfinite(d) for d in nearest) or radius >= bound:
                break
            radius = min(bound, radius * 2)
        return nearest, edges, "ase_neighbor_list"
    from scipy.spatial import cKDTree
    tree = cKDTree(positions)
    nearest = tree.query(positions, k=2)[0][:, 1].tolist() if len(positions) > 1 else []
    edges = []
    if cutoff is not None:
        for a, b in tree.query_pairs(cutoff, output_type="ndarray"):
            edges.extend(((int(a), int(b)), (int(b), int(a))))
    return nearest, edges, "scipy_ckdtree"


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def _stats(values):
    return {"min": min(values), "max": max(values), "mean": sum(values)/len(values)} if values else None


def _report(result, errors):
    result["passed"] = not errors
    result["validation_status"] = "failed" if errors else "passed"
    result["violations"] = errors
    # Keep established measurement keys available to file/CLI consumers while
    # making their semantics explicit to model callers.
    result["measurements"] = {key: value for key, value in result.items()
        if key not in {"passed", "violations", "error", "acceptance_thresholds", "measurements",
                       "validation_scope", "distance_convention", "cell_convention"}}
    if errors:
        result["error"] = "; ".join(errors)
    return result


def measure_structure(symbols, positions, cell, pbc, *, expected_count=None,
                      expected_symbol=None, min_vacuum=None, min_distance=0.1,
                      nonperiodic=False, nearest_neighbor_min=None, nearest_neighbor_max=None,
                      connectivity_cutoff=None, require_connected=False, region_checks=None,
                      distance_mode="auto"):
    errors = []
    if (not positions or len(positions) != len(symbols) or
        any(len(row) != 3 or not all(math.isfinite(x) for x in row) for row in positions)):
        return {"passed": False, "error": "Nonempty finite 3D coordinates and matching elements are required"}
    for value in (min_vacuum, min_distance, nearest_neighbor_min, nearest_neighbor_max, connectivity_cutoff):
        if value is not None and (not math.isfinite(value) or value < 0):
            return {"passed": False, "error": "Acceptance distances must be finite and nonnegative"}
    n = len(positions)
    if require_connected and (connectivity_cutoff is None or connectivity_cutoff <= 0):
        return {"passed": False, "error": "Connectedness requires a positive connectivity_cutoff"}
    if expected_count is not None and n != expected_count:
        errors.append(f"Expected {expected_count} atoms, found {n}")
    if expected_symbol and any(symbol != expected_symbol for symbol in symbols):
        errors.append(f"Expected only {expected_symbol}")
    if len(pbc) != 3:
        return {"passed": False, "error": "Three PBC flags are required"}
    if distance_mode not in {"auto", "finite", "periodic"}:
        return {"passed": False, "error": "distance_mode must be auto, finite or periodic"}
    periodic = any(pbc) if distance_mode == "auto" else distance_mode == "periodic"
    if (periodic and not any(pbc)) or (not periodic and any(pbc)):
        return {"passed": False, "validation_status": "unsupported",
                "error": "Distance mode must agree with the structure's PBC flags"}
    valid_cell = (cell is not None and len(cell) == 3 and all(len(v) == 3 and
                  all(math.isfinite(x) for x in v) for v in cell))
    if periodic and (not valid_cell or abs(_dot(cell[0], _cross(cell[1], cell[2]))) <= 1e-12):
        return {"passed": False, "validation_status": "unsupported",
                "error": "Periodic distances require a finite nonsingular cell"}
    if nonperiodic and any(pbc):
        errors.append("Expected nonperiodic boundary conditions")
    nearest = [math.inf] * n
    parents, coordination = list(range(n)), [0]*n
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    backend = "python_pairs"
    if periodic or n > 5000:
        try:
            nearest, edges, backend = _neighbors(positions, cell, pbc, connectivity_cutoff, periodic)
        except ImportError as exc:
            return {"passed": False, "validation_status": "unsupported",
                    "error": f"Neighbor validation dependency unavailable: {exc.name}"}
        for i, j in edges:
            parents[root(i)] = root(j)
            coordination[i] += 1
    else:
        for i, a in enumerate(positions):
            for j in range(i):
                d = math.dist(a, positions[j])
                nearest[i] = min(nearest[i], d)
                nearest[j] = min(nearest[j], d)
                if connectivity_cutoff is not None and d <= connectivity_cutoff:
                    parents[root(i)] = root(j)
                    coordination[i] += 1
                    coordination[j] += 1
    components = len({root(i) for i in range(n)}) if connectivity_cutoff is not None else None
    regional = None
    if region_checks is not None:
        if connectivity_cutoff is None or connectivity_cutoff <= 0:
            errors.append("Region coordination checks require a positive connectivity_cutoff")
        else:
            regional, regional_errors = measure_regions(coordination, region_checks)
            errors.extend(regional_errors)
    if require_connected and components != 1:
        errors.append(f"Expected one connected structure, found {components} components")
    nearest = nearest if n > 1 or periodic else []
    if any(not math.isfinite(d) for d in nearest):
        return {"passed": False, "validation_status": "unknown", "error": "Nearest-neighbor search was incomplete"}
    if nearest and min(nearest) < min_distance:
        errors.append(f"Atoms overlap: minimum separation {min(nearest):.6g} Å")
    if nearest_neighbor_min is not None and (not nearest or min(nearest) < nearest_neighbor_min):
        errors.append("Nearest-neighbor lower bound failed")
    if nearest_neighbor_max is not None and (not nearest or max(nearest) > nearest_neighbor_max):
        errors.append("Nearest-neighbor upper bound failed")
    margins = None
    if (cell is not None and len(cell) == 3 and all(len(row) == 3 and
            all(math.isfinite(x) for x in row) for row in cell)):
        volume = _dot(cell[0], _cross(cell[1], cell[2]))
        if abs(volume) > 1e-12:
            reciprocal = [[x/volume for x in _cross(cell[(i+1)%3], cell[(i+2)%3])] for i in range(3)]
            margins = []
            for vector in reciprocal:
                fraction = [_dot(p, vector) for p in positions]
                norm = math.sqrt(_dot(vector, vector))
                margins.append([min(fraction)/norm, (1-max(fraction))/norm])
    if min_vacuum is not None:
        if margins is None:
            errors.append("Vacuum requires a finite nonsingular cell")
        else:
            for i, faces in enumerate(margins):
                if not pbc[i] and min(faces) < min_vacuum - 1e-7:
                    errors.append(f"Cell axis {i} face margin is below {min_vacuum} Å")
    lo = [min(p[i] for p in positions) for i in range(3)]
    hi = [max(p[i] for p in positions) for i in range(3)]
    result = {
        "passed": not errors, "atom_count": n, "elements": dict(Counter(symbols)),
        "pbc": list(pbc), "coordinate_bounds_angstrom": [lo, hi],
        "coordinate_span_angstrom": [b-a for a, b in zip(lo, hi)],
        "nearest_neighbor_angstrom": _stats(nearest),
        "minimum_pair_distance_angstrom": min(nearest) if nearest else None,
        "overlap_detected": bool(nearest and min(nearest) < min_distance),
        "cell_face_margins_angstrom": margins,
        "connected_components": components,
        "coordination_counts": dict(Counter(coordination)) if connectivity_cutoff is not None else None,
        "region_coordination": regional,
        "validator_version": VALIDATOR_VERSION, "distance_backend": backend,
        "validation_status": "failed" if errors else "passed",
        "validation_scope": "periodic_cell_geometry" if periodic else "finite_cluster_geometry",
        "distance_convention": "Periodic image distances including nonzero self images" if periodic else
                               "Cartesian finite-cluster distances, no periodic images",
        "connectivity_convention": "Connectedness of atom rows in the cell; does not prove percolation in the infinite lattice" if periodic else
                                   "Connectedness of the finite atom graph",
        "cell_convention": "Cell origin is (0,0,0); opposite faces are defined by the cell vectors. Margins are signed distances from actual coordinates to each face.",
        "acceptance_thresholds": {"expected_count": expected_count, "expected_symbol": expected_symbol,
                   "min_vacuum": min_vacuum, "min_distance": min_distance,
                   "nonperiodic": nonperiodic, "nearest_neighbor_min": nearest_neighbor_min,
                   "nearest_neighbor_max": nearest_neighbor_max, "connectivity_cutoff": connectivity_cutoff,
                   "require_connected": require_connected, "distance_mode": distance_mode},
    }
    return _report(result, errors)


def measure_regions(coordination, regions):
    """Audit a complete disjoint atom partition, including permitted boundaries.

    Region membership and degree ranges are declared hypotheses; this function
    verifies actual coverage/counts. It does not infer shape or physical validity.
    """
    if not isinstance(regions, list) or not 1 <= len(regions) <= 32:
        return [], ["region_checks must contain 1..32 named regions"]
    n = len(coordination)
    seen, names, rows, errors = set(), set(), [], []
    for row in regions:
        if (not isinstance(row, dict) or set(row) != {"name", "indices", "degree_min", "degree_max"}
                or not isinstance(row["name"], str) or not 0 < len(row["name"]) <= 100):
            return [], ["Each region needs name, indices, degree_min and degree_max"]
        name, ids, low, high = (row[k] for k in ("name", "indices", "degree_min", "degree_max"))
        if name in names:
            return [], ["Region names must be unique"]
        names.add(name)
        if (not isinstance(ids, list) or not ids or len(ids) > n
                or any(type(i) is not int or not 0 <= i < n for i in ids)):
            return [], [f"Region {name}: indices must be nonempty, in-range integer atom rows"]
        if len(set(ids)) != len(ids) or seen.intersection(ids):
            return [], [f"Region {name}: duplicate/overlapping membership"]
        if type(low) is not int or type(high) is not int or not 0 <= low <= high:
            return [], [f"Region {name}: require integer 0 <= degree_min <= degree_max"]
        seen.update(ids)
        violating = [i for i in ids if not low <= coordination[i] <= high]
        rows.append({"name":name, "atom_count":len(ids), "degree_counts":dict(Counter(coordination[i] for i in ids)),
            "expected_degree_range":[low,high], "violating_atom_count":len(violating),
            "violating_atom_indices":violating[:32], "indices_truncated":len(violating)>32, "passed":not violating})
        if violating:
            errors.append(f"Region {name}: {len(violating)} atoms violate declared coordination range")
    if len(seen) != n:
        errors.append(f"Regions cover {len(seen)} of {n} atoms; explicitly include allowed boundaries and every interior atom")
    return rows, errors


def measure_nanotube(symbols, positions, cell, pbc, *, n, m, axial_repeats,
                     bond_length=1.42, min_vacuum=8.0, axis=2, tolerance=0.02):
    """Verify an unrelaxed, open, single-wall rolled graphene lattice.

    Unroll the actual atoms and check their lattice/basis membership. Atom count
    and radius alone cannot establish chirality. Translation and angular origin
    are free; no coordinates are generated or exported by this inspector.
    """
    if (not isinstance(n, int) or not isinstance(m, int) or n < 1 or m < 0 or m > n or
        not isinstance(axial_repeats, int) or axial_repeats < 1 or axis not in (0, 1, 2) or
        not math.isfinite(bond_length) or bond_length <= 0 or
        not math.isfinite(tolerance) or tolerance <= 0):
        return {"passed": False, "error": "Require integer n>=m>=0, n>0, positive repeats/bond/tolerance and axis 0..2"}
    q = n*n+n*m+m*m
    dr = math.gcd(2*n+m, 2*m+n)
    unit_atoms = 4*q//dr
    circumference = math.sqrt(3)*bond_length*math.sqrt(q)
    radius = circumference/(2*math.pi)
    period = 3*bond_length*math.sqrt(q)/dr
    result = measure_structure(symbols, positions, cell, pbc,
        expected_count=unit_atoms*axial_repeats, expected_symbol="C", nonperiodic=True,
        min_vacuum=min_vacuum, min_distance=0.7*bond_length,
        nearest_neighbor_min=0.85*bond_length, nearest_neighbor_max=1.1*bond_length,
        connectivity_cutoff=1.1*bond_length, require_connected=True)
    if "coordinate_span_angstrom" not in result:
        return result
    transverse = [i for i in range(3) if i != axis]
    center = [sum(p[i] for p in positions)/len(positions) for i in transverse]
    radial = [math.hypot(*(p[i]-c for i, c in zip(transverse, center))) for p in positions]
    angles = [math.atan2(p[transverse[1]]-center[1], p[transverse[0]]-center[0]) for p in positions]
    errors = [result["error"]] if "error" in result else []
    if max(abs(r-radius) for r in radial) > tolerance:
        errors.append("Radius does not match requested chirality and bond length")
    z0 = min(p[axis] for p in positions)
    counts = [0]*axial_repeats
    for point in positions:
        bucket = math.floor((point[axis]-z0+1e-7)/period)
        if 0 <= bucket < axial_repeats:
            counts[bucket] += 1
    if counts != [unit_atoms]*axial_repeats:
        errors.append("Incomplete axial repeat windows")
    # Inverse mapping of the observed cylinder onto the graphene sheet.
    a = math.sqrt(3)*bond_length
    a1, a2 = (a, 0), (a/2, 1.5*bond_length)
    chiral = (n*a1[0]+m*a2[0], m*a2[1])
    t1, t2 = (2*m+n)//dr, -(2*n+m)//dr
    translation = (t1*a1[0]+t2*a2[0], t2*a2[1])
    u = [x/circumference for x in chiral]
    v = [x/period for x in translation]
    basis = ((a1[0]+a2[0])/3, a2[1]/3)
    residuals = []
    for direction in (1, -1):
        for anchor_basis in (1, -1):
            distances = []
            for point, angle in zip(positions, angles):
                s = ((angle-angles[0]) % (2*math.pi))*radius
                z = direction*(point[axis]-positions[0][axis])
                x, y = s*u[0]+z*v[0], s*u[1]+z*v[1]
                options = []
                for b in ((0, 0), (anchor_basis*basis[0], anchor_basis*basis[1])):
                    f2 = (y-b[1])/a2[1]
                    f1 = (x-b[0]-f2*a2[0])/a1[0]
                    rx = round(f1)*a1[0]+round(f2)*a2[0]+b[0]
                    ry = round(f2)*a2[1]+b[1]
                    options.append(math.hypot(x-rx, y-ry))
                distances.append(min(options))
            residuals.append(max(distances))
    lattice_residual = min(residuals)
    if lattice_residual > tolerance:
        errors.append("Unrolled coordinates do not match the requested graphene lattice")
    result.update({
        "passed": not errors, "chirality": [n, m], "axial_repeats": axial_repeats,
        "atoms_per_repeat": unit_atoms, "repeat_window_atom_counts": counts,
        "axis": axis, "tube_center_transverse_angstrom": center,
        "radius_angstrom": _stats(radial), "diameter_angstrom": 2*sum(radial)/len(radial),
        "expected_radius_angstrom": radius, "axial_period_angstrom": period,
        "nominal_repeat_length_angstrom": period*axial_repeats,
        "nominal_total_length_angstrom": period*axial_repeats,
        "actual_axial_span_angstrom": result["coordinate_span_angstrom"][axis],
        "unrolled_lattice_max_residual_angstrom": lattice_residual,
        "validation_scope": "Unrelaxed open single-wall nanotube; fixed Cartesian axis",
    })
    return _report(result, errors)
