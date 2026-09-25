"""Staged refinement is confined to owned snapshots and preserves result lineage."""
import asyncio
import base64
import hashlib
import io
import json
import sys
from types import SimpleNamespace
import zipfile

import pytest
from pydantic import ValidationError

from oaw_xrd import MatchConfig
from oaw_xrd import pipeline
from oaw_xrd.runtime import XRDRuntime


CIF = 'data_original\n_cell_length_a 5\n_atom_site_fract_x 0\n'
DIGEST = hashlib.sha256(CIF.encode()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def source(text=CIF):
    return {'filename': 'original.cif', 'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


def output(text=CIF):
    return {'filename': 'output.cif', 'source_base64': base64.b64encode(text.encode()).decode(),
            'sha256': hashlib.sha256(text.encode()).hexdigest()}


def write_run(root, run_id, *, agent_id='owner', stage='search', status='completed', order=1, result=None, match_run_id='search1'):
    directory = root / f'oaw-{run_id}'
    directory.mkdir(parents=True)
    dump(directory / 'oaw.json', {'run_id': run_id, 'agent_id': agent_id, 'status': status,
         'workflow_stage': stage, 'workflow_match_run_id': match_run_id, 'created_at_ns': order})
    if result is not None:
        dump(directory / 'result.json', result)
    return directory


def search(root, run_id='search1', *, order=1, agent_id='owner'):
    result = {'mode': 'match', 'candidates': [
        {'node_id': 'ref1', 'filename': 'first', 'score': .8, 'cifs': [{'node_id': 'cif1'}]},
        {'node_id': 'ref2', 'filename': 'second', 'score': .7, 'cifs': [{'node_id': 'cif2'}]}]}
    directory = write_run(root, run_id, order=order, agent_id=agent_id, result=result)
    snapshot = [{'node_id': 'pattern', 'revision': 2, 'value': {'kind': 'pattern', 'filename': 'observed.txt',
                'points': [[10 + i, i] for i in range(20)], 'sha256': 'original-pattern'}}]
    for index in (1, 2):
        snapshot.append({'node_id': f'cif{index}', 'value': {'kind': 'cif', 'reference_node_id': f'ref{index}',
                        **source(), 'source_base64': output()['source_base64']}})
    dump(directory / 'input-snapshot.json', snapshot)
    dump(directory / 'input.json', {'wavelength': 1.540593})
    return directory


def options(**updates):
    return MatchConfig(workflow_stage='preopt', workflow_match_run_id='search1', selected_candidate_ids=['ref1', 'ref2']).model_copy(update=updates)


def build(root, config=None, agent='owner'):
    return asyncio.run(pipeline.build_pipeline_input(root, agent, config or options()))


def preopt(root, *, run_id='preopt1', status='completed', order=2, match_id='search1'):
    original = build(root)
    directory = write_run(root, run_id, stage='preopt', status=status, order=order, match_run_id=match_id,
        result={'mode': 'pipeline', 'stage': 'preopt', 'match_run_id': match_id, 'candidates': [
            {'candidate_id': 'ref1', 'label': 'first', 'status': 'completed', 'accepted': True,
             'source_sha256': DIGEST, 'output_cif': output(CIF.replace('5', '5.1'))},
            {'candidate_id': 'ref2', 'label': 'second', 'status': 'rejected', 'accepted': False,
             'source_sha256': DIGEST, 'output_cif': output('never_use_rejected_output')} ]})
    dump(directory / 'pipeline-input.json', original)
    return directory


def test_multi_candidate_uses_frozen_full_pattern_and_server_cif(tmp_path):
    search(tmp_path)
    config = options(wavelength=9, cif='client_supplied', intensity_csv='client_supplied')
    result = build(tmp_path, config)
    assert result['wavelength'] == 1.540593
    assert len(result['pattern']['points']) == 20
    assert [c['candidate_id'] for c in result['candidates']] == ['ref1', 'ref2']
    assert all(c['source_cif']['text'] == CIF for c in result['candidates'])
    assert result['candidates'][0]['search_score'] == .8


@pytest.mark.parametrize('selected', [[], ['ref1', 'ref1'], ['foreign'], ['ref1'] * 11])
def test_reject_invalid_selection(tmp_path, selected):
    search(tmp_path)
    with pytest.raises(ValueError):
        build(tmp_path, options(selected_candidate_ids=selected))


@pytest.mark.parametrize('run_id', ['../search1', '/secret', r'..\secret', '', 'x:y'])
def test_run_path_validation(tmp_path, run_id):
    search(tmp_path)
    with pytest.raises(ValueError, match='运行编号'):
        build(tmp_path, options(workflow_match_run_id=run_id))


def test_reject_foreign_and_stale_search(tmp_path):
    search(tmp_path)
    with pytest.raises(ValueError, match='其他节点'):
        build(tmp_path, agent='different-agent')
    search(tmp_path, 'search2', order=2)
    with pytest.raises(ValueError, match='旧检索'):
        build(tmp_path)


def test_missing_or_tampered_cif_is_per_candidate_failure(tmp_path):
    directory = search(tmp_path)
    snapshot = json.loads((directory / 'input-snapshot.json').read_text())
    snapshot = [item for item in snapshot if item['node_id'] != 'cif1']
    dump(directory / 'input-snapshot.json', snapshot)
    result = build(tmp_path)
    assert '读取权限' in result['candidates'][0]['input_error']
    assert result['candidates'][1]['source_cif']['sha256'] == DIGEST
    snapshot[1]['value']['sha256'] = '0' * 64
    dump(directory / 'input-snapshot.json', snapshot)
    assert '哈希' in build(tmp_path)['candidates'][1]['input_error']


def test_cod_uses_authorized_slot_identity_not_candidate_url(tmp_path, monkeypatch):
    directory = search(tmp_path)
    result = json.loads((directory / 'result.json').read_text())
    result['candidates'] = [{'node_id': 'lib:slot2:7222155', 'filename': 'COD', 'metadata': {
        'library_node_id': 'lib:slot2', 'reference_code': '7222155', 'url': 'file:///secret'}}]
    dump(directory / 'result.json', result)
    snapshot = json.loads((directory / 'input-snapshot.json').read_text())
    snapshot.append({'node_id': 'lib', 'value': {'kind': 'library', 'slots': [None, {'kind': 'library'}]}})
    dump(directory / 'input-snapshot.json', snapshot)
    calls = []
    async def prepare(value, arguments):
        calls.append(arguments)
        return {'structure': output()}
    monkeypatch.setattr(pipeline.structures, 'prepare_cod_structure', prepare)
    value = build(tmp_path, options(selected_candidate_ids=['lib:slot2:7222155']))
    assert calls == [{'cod_id': '7222155'}]
    assert value['candidates'][0]['source_cif']['sha256'] == DIGEST


def test_fit_adopts_only_accepted_output_rejected_uses_original(tmp_path):
    search(tmp_path)
    preopt(tmp_path)
    result = build(tmp_path, options(workflow_stage='fit', workflow_preopt_run_id='preopt1'))
    accepted, rejected = result['candidates']
    assert accepted['source_cif']['sha256'] == DIGEST
    assert accepted['starting_cif']['text'] == CIF.replace('5', '5.1')
    assert rejected['starting_cif']['text'] == CIF
    assert rejected['preopt'] == {'accepted': False, 'status': 'rejected', 'fallback_to_original': True}


def test_fit_never_runs_failed_preopt_and_rejects_tampered_accepted_output(tmp_path):
    search(tmp_path)
    directory = preopt(tmp_path)
    result = json.loads((directory / 'result.json').read_text())
    result['candidates'][1].update(status='failed', error='optimizer failed')
    dump(directory / 'result.json', result)
    config = options(workflow_stage='fit', workflow_preopt_run_id='preopt1')
    assert build(tmp_path, config)['candidates'][1]['input_error'] == 'optimizer failed'
    result['candidates'][0]['output_cif']['sha256'] = '0' * 64
    dump(directory / 'result.json', result)
    with pytest.raises(ValueError, match='哈希'):
        build(tmp_path, config)


def test_fit_rejects_other_search_or_agent_preopt(tmp_path):
    search(tmp_path)
    directory = preopt(tmp_path, match_id='different-search')
    config = options(workflow_stage='fit', workflow_preopt_run_id='preopt1')
    with pytest.raises(ValueError, match='同一流程'):
        build(tmp_path, config)
    manifest = json.loads((directory / 'oaw.json').read_text())
    manifest['agent_id'] = 'other-agent'
    dump(directory / 'oaw.json', manifest)
    with pytest.raises(ValueError, match='其他节点'):
        build(tmp_path, config)


def test_fit_waits_for_running_preopt_even_with_partial_results(tmp_path):
    search(tmp_path)
    preopt(tmp_path, status='running')
    with pytest.raises(ValueError, match='仍在运行'):
        build(tmp_path, options(workflow_stage='fit', workflow_preopt_run_id='preopt1'))


def test_rerunning_preopt_hides_fit_from_prior_preopt_lineage(tmp_path):
    search(tmp_path)
    preopt(tmp_path)
    write_run(tmp_path, 'fit1', stage='fit', order=3, result={'mode': 'pipeline', 'stage': 'fit',
              'match_run_id': 'search1', 'preopt_run_id': 'preopt1', 'candidates': []})
    runtime = XRDRuntime(None)
    runtime.run_root = tmp_path
    asyncio.run(runtime.create_agent(SimpleNamespace(agent_id='owner', provider_config={'mode': 'match'})))
    assert asyncio.run(runtime.get_agent('owner')).details['workflow']['fit']['run_id'] == 'fit1'
    preopt(tmp_path, run_id='preopt2', order=4)
    workflow = asyncio.run(runtime.get_agent('owner')).details['workflow']
    assert 'fit' not in workflow
    assert workflow['previous_fit']['run_id'] == 'fit1'


def test_match_schema_bounds_pipeline_options():
    for update in ({'selected_candidate_ids': ['x'] * 11}, {'preopt_max_nfev': 0},
                   {'preopt_coordinate_window': .2}, {'preopt_cell_window': .6}, {'workflow_stage': 'unknown'}):
        with pytest.raises(ValidationError):
            MatchConfig(**update)


def test_reload_keeps_matching_result_and_previous_success_after_failure(tmp_path):
    search(tmp_path)
    preopt(tmp_path)
    directory = write_run(tmp_path, 'preopt2', stage='preopt', status='failed', order=3)
    dump(directory / 'failure.json', {'error': 'preparation failed'})
    runtime = XRDRuntime(None)
    runtime.run_root = tmp_path
    config = SimpleNamespace(agent_id='owner', provider_config={'mode': 'match'})
    asyncio.run(runtime.create_agent(config))
    info = asyncio.run(runtime.get_agent('owner'))
    assert info.details['result']['mode'] == 'match'
    assert info.details['last_run']['run_id'] == 'preopt2'
    step = info.details['workflow']['preopt']
    assert step['status'] == 'failed' and 'result' not in step
    assert step['previous_success']['run_id'] == 'preopt1'
    assert step['previous_success']['result']['stage'] == 'preopt'
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(info.details['archive'])))
    assert 'failure.json' in archive.namelist()
    search(tmp_path, 'search2', order=4)
    workflow = asyncio.run(runtime.get_agent('owner')).details['workflow']
    assert workflow['match_run_id'] == 'search2' and 'preopt' not in workflow


def test_preparation_failure_has_manifest_failure_report_and_retains_search(tmp_path, monkeypatch):
    search(tmp_path)
    runtime = XRDRuntime(None)
    runtime.run_root = tmp_path
    config = SimpleNamespace(agent_id='owner', provider_config=options().model_dump())
    asyncio.run(runtime.create_agent(config))
    monkeypatch.setenv('OAW_XRD_PYTHON', sys.executable)
    async def failed_prepare(root, agent_id, options, **kwargs):
        assert (root / 'oaw-stage1/oaw.json').is_file()
        raise ValueError('could not acquire inputs')
    monkeypatch.setattr(pipeline, 'build_pipeline_input', failed_prepare)
    async def run():
        return [event async for event in runtime.execute(config, SimpleNamespace(run_id='stage1'), None)]
    with pytest.raises(ValueError, match='acquire'):
        asyncio.run(run())
    assert json.loads((tmp_path / 'oaw-stage1/oaw.json').read_text())['status'] == 'failed'
    assert (tmp_path / 'oaw-stage1/failure.json').is_file()
    assert asyncio.run(runtime.get_agent('owner')).details['result']['mode'] == 'match'


@pytest.mark.parametrize('worker_status', ['completed', 'failed'])
def test_runtime_dispatches_pipeline_and_keeps_results_when_all_candidates_fail(tmp_path, monkeypatch, worker_status):
    search(tmp_path)
    runtime = XRDRuntime(None)
    runtime.run_root = tmp_path
    config = SimpleNamespace(agent_id='owner', provider_config=options(wavelength=2).model_dump())
    asyncio.run(runtime.create_agent(config))
    monkeypatch.setenv('OAW_XRD_PYTHON', sys.executable)
    async def worker(*args, **kwargs):
        directory = pipeline.run_directory(tmp_path, 'stage1')
        # Downstream parameters are tied to the selected search, including wavelength.
        assert json.loads((directory / 'input.json').read_text())['wavelength'] == 1.540593
        assert len(json.loads((directory / 'pipeline-input.json').read_text())['candidates']) == 2
        dump(directory / 'result.json', {'mode': 'pipeline', 'stage': 'preopt', 'status': worker_status,
             'match_run_id': 'search1', 'candidates': [{'candidate_id': 'ref1', 'label': 'first',
             'status': worker_status, 'error': 'candidate failure' if worker_status == 'failed' else None}],
             'interpretation': 'candidate hypotheses'})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('oaw_xrd.runtime.asyncio.create_subprocess_exec', worker)
    async def run():
        return [event async for event in runtime.execute(config, SimpleNamespace(run_id='stage1'), None)]
    if worker_status == 'failed':
        with pytest.raises(RuntimeError, match='所有候选'):
            asyncio.run(run())
    else:
        events = asyncio.run(run())
        assert events[-1].run_status == 'succeeded'
    assert json.loads((tmp_path / 'oaw-stage1/oaw.json').read_text())['status'] == worker_status
    details = asyncio.run(runtime.get_agent('owner')).details
    assert details['result']['mode'] == 'match'
    assert details['workflow']['preopt']['result']['candidates'][0]['status'] == worker_status

def test_new_workflow_boundary_hides_old_results_without_removing_history(tmp_path):
    from oaw_xrd.results_context import read_results_context
    search(tmp_path)
    runtime = XRDRuntime(None)
    runtime.run_root = tmp_path
    config = SimpleNamespace(agent_id='fresh-owner', provider_config={'mode': 'match', 'workflow_started_at_ms': 2})
    write_run(tmp_path, 'prior', agent_id='fresh-owner', order=1_000_000, result={'mode':'match','candidates':[]})
    asyncio.run(runtime.create_agent(config))
    assert 'result' not in asyncio.run(runtime.get_agent('fresh-owner')).details
    assert read_results_context(tmp_path, 'fresh-owner')['status'] == 'no_search'
    assert len(pipeline.owned_runs(tmp_path, 'fresh-owner')) == 1
    write_run(tmp_path, 'next', agent_id='fresh-owner', order=3_000_000, result={'mode':'match','candidates':[]})
    assert asyncio.run(runtime.get_agent('fresh-owner')).details['workflow']['match_run_id'] == 'next'
    asyncio.run(runtime.delete_agent('fresh-owner'))


def test_new_workflow_rejects_previous_spectrum_preoptimization(tmp_path):
    search(tmp_path)
    with pytest.raises(ValueError, match='新流程需要重新检索'):
        build(tmp_path, options(workflow_started_at_ms=1))
