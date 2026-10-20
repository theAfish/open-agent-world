"""Run separately in a scientific Python environment; backend needs no ASE."""
import hashlib
import json
import subprocess
import sys

import pytest

ase = pytest.importorskip("ase")
np = pytest.importorskip("numpy")
from ase.io import read, write
from test_geometry_validation import SCRIPTS, finite_tube


def cli(tmp_path, *args):
    result = subprocess.run([sys.executable, str(SCRIPTS / "structure_inspect.py"), *args],
                            cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_saved_labels_survive_reordering_translation(tmp_path):
    atoms = ase.Atoms('C4', positions=[[0,0,0],[1,0,0],[2,0,0],[3,0,0]], pbc=False)
    atoms.new_array('component', np.array([0,1,1,0]))
    atoms = atoms[[2,0,3,1]]
    atoms.positions += [45,12,8]
    file = tmp_path / 'reordered.extxyz'
    write(file, atoms)
    regions = [{'name':'ends', 'selector':{'array':'component','values':[0]}, 'degree_min':1,'degree_max':1},
               {'name':'interior', 'selector':{'array':'component','values':[1]}, 'degree_min':2,'degree_max':2}]
    rules = tmp_path / 'regions.json'
    rules.write_text(json.dumps({'regions':regions}))
    def check():
        return cli(tmp_path,'validate_structure','--folder','.','--file-name',file.name,
            '--connectivity-cutoff','1.1','--region-checks-file',rules.name)
    code, report = check()
    assert code == 0 and report['passed'] and [r['atom_count'] for r in report['region_coordination']] == [2,2]
    regions[0] = {**{k:v for k,v in regions[0].items() if k!='selector'}, 'indices':[0,3]}
    rules.write_text(json.dumps({'regions':regions}))
    assert check()[0] != 0  # Stale row guesses must fail, not replace saved identities.
    regions[0].pop('indices'); regions[0]['selector'] = {'array':'absent','values':[0]}
    rules.write_text(json.dumps({'regions':regions}))
    assert check()[0] != 0


def test_saved_cnt_validation_conversion_and_round_trip(tmp_path):
    positions, cell = finite_tube()
    atoms = ase.Atoms("C80", positions=positions, cell=cell, pbc=False)
    atoms.new_array("atomsculptor_id", np.arange(11, 91))
    source = tmp_path / "cnt_5_5_4.extxyz"
    write(source, atoms)
    code, measured = cli(tmp_path, "validate_nanotube", "--folder", ".", "--file-name", source.name,
                          "--n", "5", "--m", "5", "--axial-repeats", "4", "--min-vacuum", "8")
    assert code == 0 and measured["passed"]
    assert measured["file"] == source.name and "file_sha256" not in measured
    assert measured["atom_count"] == 80
    code, exported = cli(tmp_path, "to_atomsculptor_document", "--folder", ".", "--file-name", source.name,
                          "--output-name", "cnt.document.json")
    assert code == 0
    assert exported["input_structure_file"] == source.name
    document = tmp_path / "cnt.document.json"
    assert exported["output_document_file"] == document.name and "output_sha256" not in exported
    data = json.loads(document.read_text())
    assert len(data["atoms"]) == 80 and data["atoms"][0]["id"] == 11
    # Preserve empty/whitespace/Unicode labels and layer IDs through extxyz.
    data["layers"][0]["id"] = "tube layer"
    data["active_layer_ids"] = ["tube layer"]
    for index, entry in enumerate(data["atoms"]):
        entry["layer_id"] = "tube layer"
        entry["label"] = "C site α" if index == 0 else ""
    document.write_text(json.dumps(data))
    code, _ = cli(tmp_path, "from_atomsculptor_document", "--document", document.name,
                   "--output-name", "roundtrip.extxyz")
    assert code == 0
    restored = read(tmp_path / "roundtrip.extxyz")
    np.testing.assert_allclose(restored.positions, atoms.positions)
    np.testing.assert_equal(restored.arrays["atomsculptor_id"], atoms.arrays["atomsculptor_id"])
    code, _ = cli(tmp_path, "to_atomsculptor_document", "--folder", ".", "--file-name", "roundtrip.extxyz",
                   "--output-name", "restored.json")
    assert code == 0
    restored_doc = json.loads((tmp_path / "restored.json").read_text())
    assert [entry["label"] for entry in restored_doc["atoms"]] == [entry["label"] for entry in data["atoms"]]
    assert all(entry["layer_id"] == "tube layer" for entry in restored_doc["atoms"])


def test_validator_rejects_actual_bad_box_and_count(tmp_path):
    positions, cell = finite_tube()
    atoms = ase.Atoms("C80", positions=positions, cell=cell, pbc=False)
    atoms.positions -= np.diag(cell)/2
    write(tmp_path / "bad.extxyz", atoms)
    code, result = cli(tmp_path, "validate_nanotube", "--folder", ".", "--file-name", "bad.extxyz",
                       "--n", "5", "--m", "5", "--axial-repeats", "4")
    assert code == 1 and not result["passed"] and "face margin" in result["error"]
    code, result = cli(tmp_path, "validate_structure", "--folder", ".", "--file-name", "bad.extxyz",
                       "--expected-count", "79")
    assert code == 1 and "Expected 79 atoms" in result["error"]


def test_region_rules_measure_current_structure_and_find_connected_defects(tmp_path):
    source=tmp_path/"sample.extxyz"
    write(source,ase.Atoms("C4",positions=[[0,0,0],[1,0,0],[1,1,0],[0,1.2,0]],pbc=False))
    rules={
        "regions":[{"name":"interior","indices":[0,1,2,3],"degree_min":2,"degree_max":2}]}
    rules_file=tmp_path/"regions.json";rules_file.write_text(json.dumps(rules))
    args=["validate_structure","--folder",".","--file-name",source.name,
        "--connectivity-cutoff","1.1","--require-connected","true",
        "--region-checks-file",rules_file.name]
    code,result=cli(tmp_path,*args)
    assert code==1 and result["connected_components"]==1
    assert result["region_coordination"][0]["violating_atom_count"]==2
    rules_file.write_text(json.dumps({**rules,"regions":None}))
    assert cli(tmp_path,*args)[0]==1
    write(source,ase.Atoms("C4",positions=[[0,0,0],[1,0,0],[1,1,0],[0,1,0]],pbc=False))
    rules_file.write_text(json.dumps(rules))
    code,result=cli(tmp_path,*args)
    assert code==0 and result["passed"]


@pytest.mark.parametrize("malformation", ["duplicate", "out_of_range"])
def test_region_rules_reject_ambiguous_or_invalid_atom_indices(tmp_path, malformation):
    source=tmp_path/"sample.extxyz"
    write(source,ase.Atoms("C2",positions=[[0,0,0],[1,0,0]],pbc=False))
    region={"name":"all","indices":[0,1],"degree_min":1,"degree_max":1}
    if malformation=="out_of_range": region["indices"]=[0,2]
    rules=json.dumps({"regions":[region]})
    if malformation=="duplicate":
        rules=rules.replace('"degree_min": 1', '"degree_min": 0, "degree_min": 1')
    path=tmp_path/"regions.json";path.write_text(rules)
    code,result=cli(tmp_path,"validate_structure","--folder",".","--file-name",source.name,
        "--connectivity-cutoff","1.1","--region-checks-file",path.name)
    assert code==1 and not result["passed"]
    assert "unique fields" in result["error"] if malformation=="duplicate" else "indices" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize('nonperiodic', [True, False])
async def test_delivery_pipeline_uses_real_cli_then_imports_and_reads_back_once(tmp_path, nonperiodic):
    import asyncio
    from backend.agents.models import ScopedToolDefinition
    from oaw_atomsculptor.delivery import run_delivery_step, _validate_argv
    from test_workflow import evidence, activate, execute, tool
    positions, cell = finite_tube()
    write(tmp_path / 'candidate.extxyz', ase.Atoms('C80', positions=positions, cell=cell, pbc=False))
    commands = []
    def skill(argv):
        commands.append(argv)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'structure_inspect.py'), *argv],
                                cwd=tmp_path, text=True, capture_output=True, timeout=30)
        return {'command_id': f'cli-{len(commands)}', 'exit_code': result.returncode,
                'stdout': result.stdout, 'stderr': result.stderr}
    e = evidence(); await activate(e)
    await execute(e, 'probe.py')
    assert e.workflow.advance(e, 'probe', 'box', 'probe.py')['ok']
    await execute(e, 'build.py')
    argv = _validate_argv('candidate.extxyz', {'expected_count': 80, 'min_vacuum': 8, 'nonperiodic': True})
    result = skill(argv)
    assert result['exit_code'] == 0, result
    await tool(e, 'run_skill_script', {'sandbox': 'box', 'argv': argv, 'script': 'scripts/structure_inspect.py'}, result)
    assert e.workflow.advance(e, 'build', 'box', 'build.py', structure_path='candidate.extxyz')['ok']
    names = ('run_skill_script', 'import_atom_structure_file', 'inspect_atom_structure')
    definitions = [ScopedToolDefinition('operation:' + name, name, 'Fixture operation') for name in names]
    e.configure_tools(definitions)
    revision = 1; imported = []; digest = None
    async def invoke(capability, args):
        nonlocal revision, digest
        name = capability.removeprefix('operation:')
        if name == 'run_skill_script':
            return skill(args['argv'])
        if name == 'import_atom_structure_file':
            data = (tmp_path / args['file_name']).read_bytes()
            assert 'expected_file_sha256' not in args
            assert args['expected_revision'] == revision
            document = json.loads(data); assert len(document['atoms']) == 80
            imported.append(document); revision += 1; digest = hashlib.sha256(data).hexdigest()
        return {'revision': revision,  'summary': {'atom_count': 80}}
    result = await run_delivery_step(e, definitions, invoke, asyncio.Event(), call_id='delivery',
        sandbox='box', skill='fixture', structure_path='candidate.extxyz', target='structure',
        output_name='candidate.json', validate={'expected_count': 80, 'min_vacuum': 8, 'nonperiodic': nonperiodic})
    assert result['ok'], result
    assert len(imported) == 1 and e.confirmed['structure'] == 2 and e.workflow.current == 'complete'
    assert [x[0] for x in commands] == ['validate_structure', 'validate_structure', 'to_atomsculptor_document']
