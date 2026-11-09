import importlib.util
import json
import subprocess
import sys

import numpy as np
import pytest
from ase import Atoms
from ase.io import write
from skill_materialization import skill_scripts

SCRIPTS = skill_scripts("structure_inspect")
spec = importlib.util.spec_from_file_location("scientific_fixture", SCRIPTS / "scientific_analysis.py")
science = importlib.util.module_from_spec(spec)
spec.loader.exec_module(science)


def test_each_executable_skill_packages_the_same_standalone_helper():
    from importlib.resources import files
    from oaw_atomsculptor.skill_package import atomsculptor_skills
    canonical = files("oaw_atomsculptor").joinpath("skill_cli.py").read_text()
    executable = [s for s in atomsculptor_skills().skills if "scripts/oaw_runtime.py" in s.files]
    assert len(executable) == 8 and all(s.files["scripts/oaw_runtime.py"] == canonical for s in executable)


def test_chordless_ring_definition_and_periodic_scope_are_explicit():
    angles = np.arange(6) * np.pi / 3
    atoms = Atoms("C6", positions=np.column_stack((np.cos(angles), np.sin(angles), np.zeros(6))))
    result = science.topology(atoms, 1.1)
    assert result["rings"]["counts"] == {"6": 1} and result["rings"]["complete"]
    assert result["coordination_histogram"] == {2: 6}
    atoms.set_cell([10, 10, 10]); atoms.pbc = True
    periodic = science.topology(atoms, 1.1)
    assert periodic["rings"]["counts"] is None and not periodic["rings"]["complete"]
    # A one-row periodic cell still has six image neighbours, not degree zero.
    primitive = Atoms("C", positions=[[0, 0, 0]], cell=[1, 1, 1], pbc=True)
    assert science.topology(primitive, 1.1)["coordination_histogram"] == {6: 1}


def test_local_geometry_is_a_proxy_and_cycle_limit_marks_partial_results():
    vectors = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]]) / np.sqrt(3)
    atoms = Atoms("C5", positions=np.vstack((np.zeros(3), vectors)))
    result = science.topology(atoms, 1.1)
    assert result["local_environments"][0]["tetrahedral_angle_rms_deviation_degrees"] < 1e-10
    assert result["physical_stability"] == "not_assessed" and "sp3" in result["interpretation"]
    tetrahedron = Atoms("C4", positions=vectors)
    assert not science.topology(tetrahedron, 2, max_cycles=1)["rings"]["complete"]
    with pytest.raises(ValueError):
        science.topology(atoms, float("nan"))


def test_symmetry_respects_periodicity_and_user_tolerances():
    pytest.importorskip("pymatgen.symmetry.analyzer")
    atom = Atoms("Cu", cell=[3, 3, 3], pbc=True)
    result = science.symmetry(atom, .01, 5, .1)
    assert result["number"] == 221 and result["settings"]["symprec_angstrom"] == .01
    atom.pbc = [True, True, False]
    assert science.symmetry(atom, .01, 5, .1)["status"] == "unsupported_partial_periodicity"
    water = Atoms("OH2", positions=[[0, 0, 0], [.76, .59, 0], [-.76, .59, 0]])
    assert science.symmetry(water, .01, 5, .1)["symbol"] == "C2v"


def test_explicit_calculator_reference_comparison_preserves_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    atoms = Atoms("Ar2", positions=[[0, 0, 0], [1.5, 0, 0]])
    original = atoms.positions.copy()
    calculator = science.make_calculator("lj", {"epsilon": 1, "sigma": 1}, "ASE LJ reference fixture; reduced parameters")
    result = science.energy_forces(atoms, calculator, "fixture", atoms.copy())
    assert result["energy_difference_eV"] == 0 and result["max_force_eV_per_angstrom"] > 0
    assert atoms.calc is None and np.array_equal(atoms.positions, original)
    with pytest.raises(ValueError, match="matching composition"):
        science.energy_forces(atoms, calculator, "fixture", Atoms("Ar"))
    with pytest.raises(ValueError, match="parameter_source"):
        science.make_calculator("lj", {}, "")
    with pytest.raises(ValueError, match="workspace"):
        science.make_calculator("tersoff", {"parameters_file": "../outside"}, "fixture")


def test_materialized_science_cli_runs_without_host_plugin_imports(tmp_path):
    write(tmp_path / "pair.extxyz", Atoms("Ar2", positions=[[0, 0, 0], [1.5, 0, 0]]))
    (tmp_path / "parameters.json").write_text('{"epsilon": 1, "sigma": 1}')
    argv = [sys.executable, str(SCRIPTS / "structure_inspect.py"), "measure_energy_forces", "--folder", ".",
            "--file-name", "pair.extxyz", "--calculator", "lj", "--parameters-file", "parameters.json",
            "--parameter-source", "ASE reference fixture"]
    result = subprocess.run(argv, cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["max_force_eV_per_angstrom"] > 0 and report["physical_stability"] == "not_assessed"
