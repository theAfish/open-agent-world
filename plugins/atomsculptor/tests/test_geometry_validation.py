"""Known finite graphene roll-up and adversarial validator fixtures."""
import importlib.util
import math
from pathlib import Path

import pytest


from skill_materialization import skill_scripts
SCRIPTS = skill_scripts("structure_inspect")
spec = importlib.util.spec_from_file_location("structure_validation", SCRIPTS / "structure_validation.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def finite_tube(n=5, m=5, repeats=4):
    # Test fixture from two graphene basis sites; no prebuilt tube generator.
    b = 1.42
    a1, a2 = (math.sqrt(3)*b, 0), (math.sqrt(3)*b/2, 1.5*b)
    dr = math.gcd(2*n+m, 2*m+n)
    c = (n*a1[0]+m*a2[0], m*a2[1])
    t1, t2 = (2*m+n)//dr, -(2*n+m)//dr
    t = (t1*a1[0]+t2*a2[0], t2*a2[1])
    cm, tm = math.hypot(*c), math.hypot(*t)
    radius = cm/(2*math.pi)
    found = {}
    for i in range(-30, 31):
        for j in range(-30, 31):
            for basis in ((0, 0), ((a1[0]+a2[0])/3, a2[1]/3)):
                p = (i*a1[0]+j*a2[0]+basis[0], j*a2[1]+basis[1])
                z = sum(x*y for x, y in zip(p, t))/tm
                if z < -1e-8 or z >= repeats*tm-1e-8:
                    continue
                theta = 2*math.pi*(sum(x*y for x, y in zip(p, c))/cm % cm)/cm + 0.37
                point = (radius*math.cos(theta), radius*math.sin(theta), z)
                found.setdefault(tuple(round(v, 7) for v in point), point)
    points = list(found.values())
    lo = [min(p[k] for p in points) for k in range(3)]
    hi = [max(p[k] for p in points) for k in range(3)]
    cell = [[hi[k]-lo[k]+17 if i == k else 0 for i in range(3)] for k in range(3)]
    return [[p[k]-lo[k]+8.5 for k in range(3)] for p in points], cell


@pytest.mark.parametrize("n,m,repeats", [(5, 5, 4), (6, 0, 3), (6, 3, 2)])
def test_valid_unrelaxed_tubes_with_free_angular_origin(n, m, repeats):
    positions, cell = finite_tube(n, m, repeats)
    result = validation.measure_nanotube(["C"]*len(positions), positions, cell, [False]*3,
                                         n=n, m=m, axial_repeats=repeats)
    assert result["passed"], result
    assert result["unrolled_lattice_max_residual_angstrom"] < 1e-6
    assert result["actual_axial_span_angstrom"] < result["nominal_repeat_length_angstrom"]
    assert all(min(pair) == pytest.approx(8.5) for pair in result["cell_face_margins_angstrom"])
    if (n, m, repeats) == (5, 5, 4):
        assert result["atom_count"] == 80
        assert result["radius_angstrom"]["mean"] == pytest.approx(3.39000029)
        assert result["actual_axial_span_angstrom"] == pytest.approx(8.6082925)


def test_count_radius_pbc_and_actual_box_margin_failures():
    positions, cell = finite_tube()
    def check(points, box=cell, pbc=None):
        return validation.measure_nanotube(["C"]*len(points), points, box, pbc or [False]*3,
                                           n=5, m=5, axial_repeats=4)
    assert not check(positions[:-1])["passed"]
    assert not check(positions, pbc=[True, False, False])["passed"]
    centered = [[v-cell[k][k]/2 for k, v in enumerate(p)] for p in positions]
    assert "face margin" in check(centered)["error"]
    shifted = [[p[0]+1 if index == 0 else p[0], p[1], p[2]] for index, p in enumerate(positions)]
    assert "Radius" in check(shifted)["error"]


def test_cylinder_shape_and_count_do_not_prove_graphene_lattice():
    positions, cell = finite_tube()
    # Move one atom around the cylinder, keeping its radius and axial position.
    center = [sum(p[k] for p in positions)/len(positions) for k in (0, 1)]
    x, y = positions[0][0]-center[0], positions[0][1]-center[1]
    positions[0][:2] = [center[0]+x*math.cos(.08)-y*math.sin(.08),
                        center[1]+x*math.sin(.08)+y*math.cos(.08)]
    result = validation.measure_nanotube(["C"]*80, positions, cell, [False]*3, n=5, m=5, axial_repeats=4)
    assert not result["passed"]
    assert "graphene lattice" in result["error"]


def test_per_atom_nearest_neighbor_and_skew_cell_faces():
    positions = [[2, 2, 2], [3, 2, 2], [7, 2, 2]]
    result = validation.measure_structure(["C"]*3, positions, [[10, 0, 0], [3, 10, 0], [0, 0, 10]],
                                          [False]*3, min_vacuum=1)
    assert result["passed"]
    assert result["nearest_neighbor_angstrom"] == {"min": 1, "max": 4, "mean": 2}
    assert result["cell_face_margins_angstrom"][0][0] == pytest.approx(1.4/math.sqrt(1.09))
    assert not validation.measure_structure(["C"]*2, [[0, 0, 0], [0, 0, 0]], None, [False]*3)["passed"]


def test_thresholds_are_distinct_from_observed_distances_and_violations():
    positions, cell = finite_tube()
    result = validation.measure_nanotube(["C"]*80, positions, cell, [False]*3, n=5, m=5, axial_repeats=4)
    assert result["violations"] == []
    assert result["acceptance_thresholds"]["min_distance"] == pytest.approx(.994)
    assert result["measurements"]["minimum_pair_distance_angstrom"] == pytest.approx(1.40964138)
    assert not result["measurements"]["overlap_detected"]
    assert result["measurements"]["nominal_total_length_angstrom"] == pytest.approx(9.83804859)
    positions[1] = positions[0][:]
    failed = validation.measure_nanotube(["C"]*80, positions, cell, [False]*3, n=5, m=5, axial_repeats=4)
    assert failed["measurements"]["minimum_pair_distance_angstrom"] == 0
    assert failed["overlap_detected"] and any("overlap" in v for v in failed["violations"])
    assert not validation.measure_structure(["C"], [[math.nan, 0, 0]], None, [False]*3)["passed"]
    disconnected = validation.measure_structure(["C"]*4,
        [[0, 0, 0], [1, 0, 0], [5, 0, 0], [6, 0, 0]], None, [False]*3,
        connectivity_cutoff=1.5, require_connected=True)
    assert not disconnected["passed"] and disconnected["connected_components"] == 2


def test_region_checker_rejects_interior_defect_even_when_graph_is_connected():
    # Four corners with one edge stretched beyond cutoff: still connected,
    # but the two interior sites no longer have the expected ring coordination.
    positions = [[0,0,0], [1,0,0], [1,1,0], [0,1.2,0]]
    regions = [{"name":"interior", "indices":[0,3], "degree_min":2,"degree_max":2},
               {"name":"other", "indices":[1,2], "degree_min":2,"degree_max":2}]
    failed = validation.measure_structure(["C"]*4,positions,None,[False]*3,
        connectivity_cutoff=1.1,require_connected=True,region_checks=regions)
    assert failed["connected_components"]==1 and not failed["passed"]
    assert failed["region_coordination"][0]["violating_atom_count"]==2
    positions[3]=[0,1,0]
    good=validation.measure_structure(["C"]*4,positions,None,[False]*3,
        connectivity_cutoff=1.1,require_connected=True,region_checks=regions)
    assert good["passed"] and all(r["passed"] for r in good["region_coordination"])


@pytest.mark.parametrize("indices", [[], [0,0], [0,4], [0,True], [0]])
def test_regions_cannot_hide_defects_by_empty_duplicate_invalid_or_partial_coverage(indices):
    rows, errors=validation.measure_regions([2,2,2,2],
        [{"name":"claimed_interior", "indices":indices,"degree_min":2,"degree_max":2}])
    assert errors


def test_allowed_boundary_must_not_include_an_interior_row():
    degrees=[1,2,2,1]
    ends={"name":"open_ends","indices":[0,3],"degree_min":1,"degree_max":1}
    interior={"name":"interior","indices":[1,2],"degree_min":2,"degree_max":2}
    assert not validation.measure_regions(degrees,[ends,interior])[1]
    rows,errors=validation.measure_regions(degrees,[{**ends,"indices":[0,1,3]}, {**interior,"indices":[2]}])
    assert errors and rows[0]["violating_atom_indices"]==[1]


def test_periodic_boundary_overlap_cannot_pass_as_finite_cluster():
    pytest.importorskip("ase")
    result = validation.measure_structure(["C"]*2, [[.05, 0, 0], [.95, 0, 0]],
        [[1, 0, 0], [0, 10, 0], [0, 0, 10]], [True, False, False], min_distance=.2)
    assert not result["passed"] and result["overlap_detected"]
    assert result["minimum_pair_distance_angstrom"] == pytest.approx(.1)
    assert result["validation_scope"] == "periodic_cell_geometry"
    rejected = validation.measure_structure(["C"]*2, [[.05, 0, 0], [.95, 0, 0]],
        [[1, 0, 0], [0, 10, 0], [0, 0, 10]], [True, False, False], distance_mode="finite")
    assert rejected["validation_status"] == "unsupported" and not rejected["passed"]


def test_single_periodic_atom_counts_images_and_cell_connectivity_has_explicit_scope():
    pytest.importorskip("ase")
    result = validation.measure_structure(["C"], [[0, 0, 0]],
        [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [True]*3,
        connectivity_cutoff=1.01, nearest_neighbor_max=1.1,
        region_checks=[{"name":"cell", "indices":[0], "degree_min":6, "degree_max":6}])
    assert result["passed"] and result["coordination_counts"] == {6: 1}
    assert result["nearest_neighbor_angstrom"]["max"] == pytest.approx(1)
    assert "does not prove percolation" in result["connectivity_convention"]


def test_large_finite_chain_uses_exact_spatial_neighbors():
    pytest.importorskip("scipy")
    positions = [[i, 0, 0] for i in range(5001)]
    result = validation.measure_structure(["C"]*len(positions), positions, None, [False]*3,
        connectivity_cutoff=1.01, require_connected=True, nearest_neighbor_max=1.1)
    assert result["passed"] and result["distance_backend"] == "scipy_ckdtree"
    assert result["connected_components"] == 1
    assert result["coordination_counts"] == {1: 2, 2: 4999}


def test_periodic_skew_cell_search_and_invalid_cell_are_explicit():
    pytest.importorskip("ase")
    cell = [[2, 0, 0], [1, 2, 0], [0, 0, 5]]
    result = validation.measure_structure(["C"]*2, [[0, 0, 0], [.99, 1.98, 0]], cell,
        [True, True, False], min_distance=.1)
    assert not result["passed"] and result["minimum_pair_distance_angstrom"] == pytest.approx(math.sqrt(.0005))
    bad = validation.measure_structure(["C"], [[0, 0, 0]], [[0, 0, 0]]*3, [True]*3)
    assert not bad["passed"] and bad["validation_status"] == "unsupported"


def test_missing_neighbor_dependency_is_unsupported_never_a_false_pass(monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name in {"ase", "scipy.spatial"}:
            raise ModuleNotFoundError("Fixture unavailable", name=name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    result = validation.measure_structure(["C"], [[0, 0, 0]],
        [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [True]*3)
    assert not result["passed"] and result["validation_status"] == "unsupported"
    assert validation.measure_structure(["C"]*2, [[0, 0, 0], [1, 0, 0]], None, [False]*3)["passed"]
