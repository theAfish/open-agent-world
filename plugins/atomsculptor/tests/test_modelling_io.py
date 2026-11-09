"""Sandbox support code reads saved identities and serializes the existing host contract."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from oaw_atomsculptor import modelling_io as io
from oaw_atomsculptor.measurements import parse_measurements


def test_manifest_optional_and_path_indexed_reads_current_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert io.load_inputs([]).entries == {}
    (tmp_path / 'data.json').write_text('{"count": 4}')
    receipt = {'path': 'data.json'}
    (tmp_path / 'inputs.json').write_text(json.dumps([receipt]))
    inputs = io.load_inputs(['--oaw-input-manifest', 'inputs.json'])
    assert inputs.json('data.json')['count'] == 4 and inputs.receipt('data.json') == receipt
    (tmp_path / 'data.json').write_text('{"count": 9}')
    assert inputs.json('data.json')['count'] == 9
    with pytest.raises(ValueError, match='list'): io.HostInputs({'data.json': receipt})
    with pytest.raises(ValueError, match='Duplicate'): io.HostInputs([receipt, receipt])
    with pytest.raises(ValueError, match='Expected one'): io.load_inputs(['--oaw-input-manifest'])


def test_failed_checks_are_not_repaired_by_serialization(capsys):
    io.emit_measurements(constraints=[io.constraint('actual_failure', False, 'Measured shortfall')], values={'n': 3})
    report, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and report['constraints'][0]['passed'] is False and report['values']['n'] == 3
    with pytest.raises(ValueError): io.emit_measurements(values={'n': float('nan')})
    with pytest.raises(TypeError): io.emit_measurements(controls=[])


def test_large_diagnostics_saved_once_without_reexecuting_calculation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    values = {"count": 7, "coordinates": list(range(20000)), "explanation": "x" * 5000}
    checks = [io.constraint("actual_failure", False, "Measured shortfall")]
    io.emit_measurements(values=values, constraints=checks)
    report, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and report["values"]["count"] == 7
    assert report["constraints"][0]["passed"] is False
    saved = Path(report["values"]["coordinates"]["detail_source"])
    original = json.loads(saved.read_text())["oaw_measurements"]
    assert original["values"] == values and len(list(Path(".oaw-reports").iterdir())) == 1
    # Re-enveloping a saved result is ordinary serialization, not execution.
    io.emit_measurements(**original)
    assert parse_measurements(capsys.readouterr().out)[0]["constraints"][0]["passed"] is False
    with pytest.raises(ValueError, match="finite"):
        io.emit_measurements(values={"hidden": [float("nan")] * 200})


def test_long_explanation_cannot_discard_a_measured_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    reason = "Measured mismatch. " * 500
    io.emit_measurements(constraints=[io.constraint("failure", False, reason)])
    report, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and report["constraints"][0]["passed"] is False
    assert len(report["constraints"][0]["reason"]) <= 400
    saved = next(Path(".oaw-reports").iterdir())
    assert json.loads(saved.read_text())["oaw_measurements"]["constraints"][0]["reason"] == reason


def test_support_import_works_for_a_script_in_a_subdirectory(tmp_path):
    folder = tmp_path/'scripts'; folder.mkdir()
    (folder/'_fixture_modelling_io.py').write_bytes(Path(io.__file__).read_bytes())
    (folder/'step.py').write_text('from _fixture_modelling_io import load_inputs\nprint(load_inputs().entries)\n')
    result = subprocess.run([sys.executable, 'scripts/step.py'], cwd=tmp_path, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == '{}', result.stderr


def test_numpy_manifest_arrays_labels_and_report_values(tmp_path, monkeypatch, capsys):
    np = pytest.importorskip('numpy')
    monkeypatch.chdir(tmp_path)
    np.savez('data.npz', positions=np.arange(12).reshape(4,3), component=np.array([2,1,2,1]))
    receipt = {'path': 'data.npz'}
    inputs = io.HostInputs([receipt])
    assert inputs.array('data.npz', 'positions').shape == (4,3)
    assert inputs.label_indices('data.npz', 'component', 1).tolist() == [1,3]
    with pytest.raises(ValueError): inputs.label_indices('data.npz', 'component', True)
    io.emit_measurements(values={'n': np.int64(4), 'coords': np.array([1.,2.])},
                         constraints=[io.constraint('measured', np.bool_(False), controls=[{'name':'control', 'passed':np.bool_(True)}])])
    report, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and report['values']['n'] == 4 and report['constraints'][0]['passed'] is False


def test_coordinate_mapping_requires_declared_shared_reference():
    np = pytest.importorskip('numpy')
    source = {'reference': 'lattice', 'origin': [0,0,0], 'axes': np.eye(3).tolist()}
    target = {'reference': 'lattice', 'origin': [8,9,10], 'axes': [[0,1,0],[-1,0,0],[0,0,1]]}
    points = np.array([[1,2,3], [2,3,4]])
    mapped = io.transform_points(points, source, target)
    assert np.allclose(mapped, [[6,10,13],[5,11,14]])
    assert np.allclose(io.transform_points(mapped, target, source), points)
    with pytest.raises(ValueError, match='reference'): io.transform_points(points, source, {**target, 'reference':'other'})
    with pytest.raises(ValueError, match='finite'): io.transform_points(points, source, {**target, 'origin': [float('nan'),0,0]})


def test_standard_structure_writer_preserves_cell_labels_and_ids(tmp_path,monkeypatch):
    np=pytest.importorskip('numpy');ase=pytest.importorskip('ase');from ase.io import read
    monkeypatch.chdir(tmp_path)
    atoms=ase.Atoms('CO',positions=[[3,4,5],[4.234567891,4,5]],cell=[[20,0,0],[2,21,0],[0,0,22]],pbc=False)
    atoms.new_array('component',np.array([0,1]));atoms.new_array('atomsculptor_id',np.array([9,17]))
    result=io.write_extxyz(Path('new/shape.extxyz'),atoms);back=read(result['path'])
    assert np.allclose(back.cell.array,atoms.cell.array) and back.arrays['component'].tolist()==[0,1]
    assert back.arrays['atomsculptor_id'].tolist()==[9,17] and result['round_trip']
    assert result['independent_validation'] is False
    assert result['atom_count'] == 2 and 'sha256' not in result
    io.artifact(result['path'], units='angstrom', frame={'origin': [0,0,0], 'axes': np.eye(3).tolist()},
                components={'C': 1, 'O': 1}, layout=result['layout'])
    assert result['layout'] == {'format': 'xyz', 'primary_dataset': 'extxyz_atoms'}
    # A normal Path.cwd()/filename is still the same authorized output.
    assert io.write_extxyz(tmp_path/'absolute.extxyz', atoms)['round_trip']
    with pytest.raises(ValueError,match='workspace'):io.write_extxyz('../outside.extxyz',atoms)
    with pytest.raises(ValueError,match='workspace'):io.write_extxyz(tmp_path.parent/'outside.extxyz',atoms)
    (tmp_path/'escape').symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError,match='workspace'):io.write_extxyz('escape/outside.extxyz',atoms)


def test_published_helper_example_writes_and_reports_without_fallback(tmp_path, monkeypatch, capsys):
    from oaw_atomsculptor.execution_steps import ExecutionSteps
    ase = pytest.importorskip('ase')
    from ase.io import read
    steps = ExecutionSteps(); steps.enable_helpers()
    contract = steps.helper_contract()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, contract['module'], io)
    atoms = ase.Atoms('CO', positions=[[3, 4, 5], [4.2, 4, 5]], cell=[20, 20, 20], pbc=False)
    namespace = {'atoms': atoms}
    # Run exactly the public example, including the named path/atoms arguments.
    exec(contract['example'], namespace)
    saved = read('candidate.extxyz')
    assert saved.get_chemical_symbols() == ['C', 'O']
    assert namespace['write_receipt']['round_trip']
    report, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and report['kind'] == 'diagnostic' and report['values']['atom_count'] == 2
    assert contract['signatures']['write_extxyz'] == 'write_extxyz(path, atoms)'
    assert contract['input_methods']['array'] == 'inputs.array(path, dataset)'


def test_saved_report_reenvelopes_measured_data_and_failed_checks_without_recalculation(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    values = {'observed_count': 7, 'saved_positions': list(range(200))}
    io.emit_measurements(values=values, constraints=[io.constraint('measured_failure', False, 'Observed shortfall')],
                         report_path='reports/measurement.json')
    capsys.readouterr()
    saved = json.loads(Path('reports/measurement.json').read_text())['oaw_measurements']
    assert saved['values'] == values and not saved['constraints'][0]['passed']
    # The reporting branch consumes the native saved record, rather than calling
    # the scientific computation again or reconstructing its values in prose.
    saved['artifacts'] = []
    io.emit_measurements(**saved, report_path='reports/measurement.json')
    parsed, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and parsed['constraints'][0]['passed'] is False
    assert json.loads(Path('reports/measurement.json').read_text())['oaw_measurements']['values'] == values
    with pytest.raises(ValueError, match='inside'):
        io.emit_measurements(values=values, report_path='../outside.json')
    original = Path('reports/measurement.json').read_text()
    with pytest.raises(ValueError):
        io.emit_measurements(constraints=[{'passed': float('nan')}], report_path='reports/measurement.json')
    assert Path('reports/measurement.json').read_text() == original


def test_invalid_envelope_points_to_saved_data_for_a_reporting_only_repair(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    item = io.artifact('shape.extxyz', units='angstrom', frame={'origin': [0,0,0], 'axes': [[1,0,0],[0,1,0],[0,0,1]]},
                       components={'object': 4}, layout={'format': 'not_a_format'})
    io.emit_measurements(artifacts=[item], values={'measured_count': 4},
        constraints=[io.constraint('actual_failure', False)], report_path='saved.json')
    parsed, issues = parse_measurements(capsys.readouterr().out)
    assert parsed is None and issues[0]['saved_report_file'] == 'saved.json'
    saved = json.loads(Path('saved.json').read_text())['oaw_measurements']
    saved['artifacts'][0]['layout'] = {'format': 'xyz', 'primary_dataset': 'extxyz_atoms'}
    io.emit_measurements(**saved, report_path='saved.json')
    parsed, issues = parse_measurements(capsys.readouterr().out)
    assert not issues and parsed['values']['measured_count'] == 4
    assert parsed['constraints'][0]['passed'] is False


@pytest.mark.parametrize('optimizer', ['FIRE', 'LBFGS'])
def test_optional_ase_relaxation_preserves_identity_fixed_atoms_and_input(optimizer):
    np = pytest.importorskip('numpy'); ase = pytest.importorskip('ase')
    from ase.calculators.lj import LennardJones
    from ase.constraints import FixAtoms
    atoms = ase.Atoms('Ar3', positions=[[0, 0, 0], [1.2, 0, 0], [9, 0, 0]], cell=[20, 20, 20])
    atoms.new_array('atomsculptor_id', np.array([7, 42, 99]))
    atoms.set_constraint(FixAtoms(indices=[2]))
    original = atoms.positions.copy()
    relaxed, result = io.relax_structure(atoms, calculator=LennardJones(), movable_indices=[1, 2],
                                        fmax=0.01, steps=200, optimizer=optimizer, maxstep=0.05)
    assert result['converged'] and result['max_force'] < 0.01 and result['final_energy'] < result['initial_energy']
    assert 0 < result['steps'] <= 200 and not result['independent_validation']
    assert np.array_equal(relaxed.positions[[0, 2]], original[[0, 2]])
    assert np.array_equal(atoms.positions, original) and atoms.calc is None and len(atoms.constraints) == 1
    assert relaxed.arrays['atomsculptor_id'].tolist() == [7, 42, 99] and np.array_equal(relaxed.cell, atoms.cell)


def test_relaxation_reports_unconverged_and_rejects_invalid_region_or_physics():
    np = pytest.importorskip('numpy'); ase = pytest.importorskip('ase')
    from ase.calculators.lj import LennardJones
    atoms = ase.Atoms('Ar2', positions=[[0, 0, 0], [1.5, 0, 0]])
    args = dict(calculator=LennardJones(), movable_indices=[1], fmax=0.000001, steps=1, maxstep=0.01)
    _, result = io.relax_structure(atoms, **args)
    assert not result['converged'] and result['steps'] == 1 and np.isfinite(result['max_force'])
    for indices in ([], [1, 1], [-1], [2], [1.0], [True]):
        with pytest.raises(ValueError, match='indices'):
            io.relax_structure(atoms, **{**args, 'movable_indices': indices})
    with pytest.raises(ValueError, match='calculator'):
        io.relax_structure(atoms, **{**args, 'calculator': None})
    with pytest.raises(ValueError, match='fmax'):
        io.relax_structure(atoms, **{**args, 'fmax': float('nan')})
    with pytest.raises(ValueError, match='maxstep'):
        io.relax_structure(atoms, **{**args, 'maxstep': 0})
    atoms.positions[1, 0] = float('nan')
    with pytest.raises(ValueError, match='nonfinite'):
        io.relax_structure(atoms, **args)
