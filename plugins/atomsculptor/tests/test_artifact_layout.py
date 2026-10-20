"""Inspect native bytes without loading arrays or trusting producer membership claims."""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
import struct
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from backend.agents.models import ScopedToolDefinition
from oaw_atomsculptor.execution_steps import typed_step_functions
from oaw_atomsculptor.measurements import INPUT_GUARD, parse_measurements
from test_measurement_handoffs import artifact, record
from test_execution_steps import setup, prior


def npy(shape, dtype='<f8', body=b''):
    header = repr({'descr': dtype, 'fortran_order': False, 'shape': shape}).encode() + b'\n'
    return b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header + body


def archive(**datasets):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, content in datasets.items():
            z.writestr(name + '.npy', content)
    return buffer.getvalue()


def guard(root, name, payload, layout=None):
    (root / name).write_bytes(payload)
    item = {'path': name}
    if layout is not None:
        item['layout'] = layout
    return subprocess.run([sys.executable, '-c', INPUT_GUARD, json.dumps([item])],
                          cwd=root, capture_output=True, text=True)


@pytest.mark.parametrize('name,payload,expected', [
    ('data.npy', npy((4, 3)), {'format': 'npy', 'datasets': {'array': {'shape': [4, 3]}}}),
    ('data.json', b'{"positions":[[0,0,0]],"meta":{"units":"angstrom"}}',
     {'format': 'json', 'datasets': {'positions': {'length': 1}, 'meta': {'type': 'dict'}}}),
    ('data.xyz', b'2\ncomment\nC 0 0 0\nC 1 0 0\n', {'format': 'xyz', 'declared_atom_count': 2}),
])
def test_native_header_descriptions(tmp_path, name, payload, expected):
    result = guard(tmp_path, name, payload)
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)['oaw_input_metadata'][0]
    assert metadata['format'] == expected['format'] and not metadata['subset_membership_verified']
    for key, values in expected.get('datasets', {}).items():
        for field, value in values.items():
            assert metadata['datasets'][key][field] == value
    if 'declared_atom_count' in expected:
        assert metadata['declared_atom_count'] == expected['declared_atom_count']


@pytest.mark.parametrize('change', ['valid', 'missing_primary', 'missing_subset', 'wrong_format'])
def test_npz_layout_checks_names_without_asserting_membership(tmp_path, change):
    data = archive(points=npy((4, 3)), selected=npy((2, 3)))
    layout = {'format': 'npz', 'primary_dataset': 'points', 'subsets': {'selected': 'points'}}
    if change == 'missing_primary': layout['primary_dataset'] = 'positions'
    if change == 'missing_subset': layout['subsets'] = {'missing': 'points'}
    if change == 'wrong_format': layout['format'] = 'npy'
    result = guard(tmp_path, 'data.npz', data, layout)
    if change != 'valid':
        assert result.returncode != 0 and 'OAW' in result.stderr
        return
    metadata = json.loads(result.stdout)['oaw_input_metadata'][0]
    assert metadata['datasets']['points']['shape'] == [4, 3]
    assert metadata['datasets']['points']['dtype'] == '<f8'
    assert metadata['datasets']['selected']['shape'] == [2, 3]
    assert not metadata['subset_membership_verified']


def test_object_array_inspection_never_unpickles_and_malformed_headers_are_explicit(tmp_path):
    import pickle
    class Payload:
        def __reduce__(self):
            return (eval, ("__import__('pathlib').Path('executed').write_text('bad')",))
    result = guard(tmp_path, 'object.npy', npy((1,), '|O', pickle.dumps(Payload())))
    assert result.returncode == 0 and not (tmp_path / 'executed').exists()
    assert json.loads(result.stdout)['oaw_input_metadata'][0]['datasets']['array']['dtype'] == '|O'
    result = guard(tmp_path, 'bad.npy', b'\x93NUMPY\x01\x00\xff\xffshort')
    assert result.returncode == 0
    assert json.loads(result.stdout)['oaw_input_metadata'][0]['format'] == 'unavailable'


def test_optional_layout_is_validated_by_existing_measurement_contract():
    layout = {'format': 'npz', 'primary_dataset': 'points', 'subsets': {'selected': 'points'}}
    parsed, issues = parse_measurements(record(artifacts=[artifact(path='data.npz', layout=layout)]))
    assert not issues and parsed['artifacts'][0]['layout'] == layout
    parsed, issues = parse_measurements(record(artifacts=[artifact(layout={**layout, 'format': 'pickle'})]))
    assert parsed is None and issues


@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['npz', 'xyz'])
async def test_verified_layout_enters_manifest_and_host_handoff_without_changing_input_identity(tmp_path, format):
    payload = (archive(points=npy((4, 3)), selected=npy((2, 3))) if format == 'npz' else
        b'4\nLattice="10 0 0 0 10 0 0 0 10" Properties=species:S:1:pos:R:3:id:I:1 pbc="F F F"\n'
        b'C 0 0 0 0\nC 1 0 0 1\nC 2 0 0 2\nC 3 0 0 3\n')
    dataset = 'points' if format == 'npz' else 'pos'
    layout = {'format': format, 'primary_dataset': 'points' if format == 'npz' else 'extxyz_atoms',
              'subsets': {'selected': 'points'} if format == 'npz' else {}}
    output = artifact(path='data.' + format,  layout=layout)
    e, _, first = await setup(stdout=record(artifacts=[output]))
    await first()
    assert e.workflow.assess_previous(e, prior(e))['ok']
    (tmp_path / output['path']).write_bytes(payload)
    definitions = [ScopedToolDefinition('operation:' + name, name, 'fixture')
                   for name in ('write_sandbox_text_file', 'execute_command')]
    e.configure_tools(definitions)
    calls = []
    async def native(capability, args):
        name = capability.removeprefix('operation:')
        calls.append((name, deepcopy(args)))
        if name == 'write_sandbox_text_file':
            path = tmp_path / args['path']
            if args.get('expected_sha256'):
                assert hashlib.sha256(path.read_bytes()).hexdigest() == args['expected_sha256']
            content = args.get('content')
            if content is None:
                content = path.read_text()
                for edit in args['edits']:
                    assert content.count(edit['old']) == 1
                    content = content.replace(edit['old'], edit['new'])
            path.write_text(content)
            return {'written': len(content)}
        if args['argv'][1].startswith('.oaw-input-check-'):
            result = subprocess.run([sys.executable, *args['argv'][1:]], cwd=tmp_path, capture_output=True, text=True)
            return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr, 'command_id': 'guard'}
        return {'exit_code': 0, 'stdout': record(kind='diagnostic', values={'count': 4}), 'command_id': 'consumer'}
    functions = typed_step_functions(e, definitions, native, asyncio.Event())
    common = dict(sandbox='box', script_path='consumer.py', question='One dataset shape', expected_output='Count',
                  stop_condition='One measurement', input_artifacts=[{'path': output['path']}],
                  input_manifest_path='inputs.json')
    result = await functions[0](**common, source="print('first')", tool_context=SimpleNamespace(function_call_id='consume1'))
    assert result['ok']
    manifest = json.loads((tmp_path / 'inputs.json').read_text())[0]
    assert manifest['inspection']['datasets'][dataset]['shape'] == [4, 3]
    assert manifest['layout'] == output['layout'] and not manifest['inspection']['subset_membership_verified']
    assert e.artifact_handoffs[('box', output['path'])]['inspection'] == manifest['inspection']
    identity = e.current_experiment()['execution_inputs']['input_artifacts'][0]
    assert identity['layout'] == output['layout'] and 'inspection' not in identity
    result = await functions[1](**common, edits=[{'old': 'first', 'new': 'second'}],
         previous_assessment=prior(e),
        tool_context=SimpleNamespace(function_call_id='consume2'))
    assert result['ok'] and json.loads((tmp_path / 'inputs.json').read_text())[0] == manifest


@pytest.mark.parametrize('payload', [b'', b'\x93NUMPY\x01\x00\x01\x00{'])
def test_malformed_native_header_is_unavailable_without_losing_hash_receipt(tmp_path, payload):
    result = guard(tmp_path, 'bad.xyz' if not payload else 'bad.npy', payload)
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed['oaw_input_metadata'][0]['format'] == 'unavailable'
    assert parsed['oaw_inputs_verified'][0]['path'] == ('bad.xyz' if not payload else 'bad.npy')


@pytest.mark.parametrize('payload,primary,valid', [
    (b'1\ncomment\nC 1 2 3\n', 'atoms', True),
    (b"1\nProducer's arbitrary comment\nC 1 2 3\n", 'atoms', True),
    (b'1\ncomment\nC 1 2 3\n', 'extxyz_atoms', False),
    (b'2\ncomment\nC 1 2 3\n', 'atoms', False),
    (b'1\ncomment\nC nan 2 3\n', 'atoms', False),
    (b'1\nProperties=species:S:1:pos:R:3:id:I:1\nC 1 2 3\n', 'extxyz_atoms', False),
    (b'1\nProperties=species:S:1:pos:R:3:id:I:1\nC 1 2 3 7\n', 'extxyz_atoms', True),
    (b'1\nProperties=species:S:1:pos:R:3\nC 1 2 3\n', 'missing', False),
])
def test_xyz_layout_requires_real_rows_and_properties(tmp_path, payload, primary, valid):
    result = guard(tmp_path, 'atoms.extxyz', payload, {'format': 'xyz', 'primary_dataset': primary})
    assert (result.returncode == 0) is valid
    if not valid:
        diagnosis = json.loads(result.stdout)['oaw_input_error']
        assert diagnosis['field'].startswith('layout.') and diagnosis['path'] == 'atoms.extxyz'
        assert 'observed' in diagnosis


@pytest.mark.asyncio
async def test_guard_blocker_reuses_native_failure_and_routes_without_old_science_review(tmp_path):
    from test_target_recovery import correction
    from test_subtask_lifecycle import ACTION
    payload = b'1\ncomment\nC 1 2 3\n'
    output = artifact(path='data.xyz', 
        layout={'format': 'xyz', 'primary_dataset': 'extxyz_atoms'})
    e, _, producer = await setup(stdout=record(artifacts=[output]))
    await producer()
    assert e.workflow.assess_previous(e, prior(e))['ok']
    e.workflow.schedule_experiment({**ACTION, 'artifact': 'consumer.py', 'input_paths': ['data.xyz']}, origin='assessment')
    (tmp_path / 'data.xyz').write_bytes(payload)
    definitions = [ScopedToolDefinition('operation:' + n, n, 'fixture')
                   for n in ('write_sandbox_text_file', 'execute_command')]
    e.configure_tools(definitions); e.steps.available = True
    calls = []
    async def native(capability, args):
        calls.append((capability, deepcopy(args)))
        if capability.endswith('write_sandbox_text_file'):
            (tmp_path / args['path']).write_text(args['content'])
            return {'written': len(args['content'].encode())}
        if args['argv'][1].startswith('.oaw-input-check-'):
            check = subprocess.run([sys.executable, *args['argv'][1:]], cwd=tmp_path, capture_output=True, text=True)
            return {'command_id': 'guard1', 'exit_code': check.returncode, 'stdout': check.stdout, 'stderr': check.stderr}
        return {'command_id': 'consumer1', 'exit_code': 0, 'stdout': record(values={'count': 1})}
    functions = typed_step_functions(e, definitions, native, asyncio.Event())
    args = dict(sandbox='box', script_path='consumer.py', source="print('same retained source')",
        question=ACTION['question'], expected_output=ACTION['expected_output'], stop_condition=ACTION['stop_condition'],
        input_artifacts=[{'path': output['path']}], input_manifest_path='inputs.json')
    rejected = await functions[0](**args, tool_context=SimpleNamespace(function_call_id='first'))
    assert rejected['error']['diagnosis']['field'] == 'layout.primary_dataset'
    assert len(calls) == 2 and ('box', 'consumer.py') not in e.scripts
    recovery = e.steps.target_recovery(e)
    assert recovery and e.steps.records[-1]['state'] == 'input_rejected'
    retry = await functions[0](**args, tool_context=SimpleNamespace(function_call_id='retry'))
    assert retry['error']['reused_input_check'] and len(calls) == 2
    text, allowed = e.actions.next_action(e)
    assert 'NOT saved or executed' in text and allowed == {'finish_task'}
    denied = e.workflow.before_tool('structure_builder', 'finish_task', {'result': {
        'status': 'needs_plan_review', 'reason': 'Input layout blocked'}}, e)
    assert denied['error']['code'] == 'input_dependency_blocked'
    assert e.workflow.before_tool('structure_builder', 'finish_task', {'result': {
        'status': 'blocked', 'reason': 'Native guard1 rejected layout.primary_dataset'}}, e) is None
    # A corrected producer declaration changes the guard identity; the exact
    # unexecuted consumer can now run once using the retained action.
    e.artifact_handoffs[('box', 'data.xyz')]['layout']['primary_dataset'] = 'atoms'
    assert e.steps.current_input_blocker(e) is None
    fixed = await functions[-1](**correction(recovery, path='consumer.py'), tool_context=SimpleNamespace(function_call_id='fixed'))
    assert fixed['ok'] and fixed['target_correction']['code_reused']
    assert (tmp_path / 'consumer.py').read_text() == args['source']
    assert len(calls) == 7 and e.latest_experiment().command_id == 'consumer1'


def test_producer_rejects_incompatible_primary_names_before_handoff():
    for format, primary in [('xyz', 'invented_atoms'), ('npy', 'points')]:
        parsed, issues = parse_measurements(record(artifacts=[artifact(layout={'format': format, 'primary_dataset': primary})]))
        assert parsed is None and any('primary_dataset' in issue['message'] for issue in issues)
