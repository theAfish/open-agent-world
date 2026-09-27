import base64
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from plugins.xrd.src.oaw_xrd import history


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(history, '_root', lambda: tmp_path)
    return tmp_path


def save(root, run_id, owner='owner', **extra):
    directory = root / f'oaw-{run_id}'
    directory.mkdir()
    manifest = {'run_id': run_id, 'agent_id': owner, 'status': 'completed',
                'workflow_stage': 'search', 'created_at_ns': 1, **extra}
    (directory / 'oaw.json').write_text(json.dumps(manifest), encoding='utf-8')
    return directory


def context():
    return SimpleNamespace(node_id='owner', cancelled=Event())


def test_history_paginates_owned_runs_including_multiphase_and_failures(store):
    for i in range(52):
        save(store, f'run-{i:03d}', created_at_ns=i, status='failed' if i == 0 else 'completed')
    save(store, 'foreign', owner='other', created_at_ns=999)
    save(store, 'multi', owner='jev', source_owner_node_id='owner', workflow_stage='multiphase', created_at_ns=100)
    first = history.list_runs(context(), {})
    assert first['total'] == 53 and len(first['items']) == 50
    assert first['items'][0]['run_id'] == 'multi'
    assert history.list_runs(context(), {'offset': 50})['items'][-1]['status'] == 'failed'


def test_history_preserves_bytes_and_does_not_modify_files(store):
    directory = save(store, 'one')
    raw = b'\xef\xbb\xbfdata_test\n'
    (directory / 'source.cif').write_bytes(raw)
    (directory / 'input.json').write_text(json.dumps({'iterations': 5, 'cif': 'large', 'intensity_csv': 'large'}))
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    detail = history.inspect_run(context(), {'run_id': 'one'})
    assert detail['parameters'] == {'iterations': 5}
    value = history.read_file(context(), {'run_id': 'one', 'name': 'source.cif'})
    assert base64.b64decode(value['data']) == raw and len(value['sha256']) == 64
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize('arguments', [
    {'run_id': '../one', 'name': 'oaw.json'},
    {'run_id': 'foreign', 'name': 'oaw.json'},
    {'run_id': 'one', 'name': '../oaw-foreign/oaw.json'},
    {'run_id': 'one', 'name': 'C:/secret'},
])
def test_history_denies_cross_owner_and_path_escape(store, arguments):
    save(store, 'one'); save(store, 'foreign', owner='other')
    with pytest.raises(ValueError):
        history.read_file(context(), arguments)


def test_restart_exposes_interrupted_without_overwriting_evidence(store):
    directory = save(store, 'unfinished', status='running')
    assert history.list_runs(context(), {})['items'][0]['status'] == 'interrupted'
    assert json.loads((directory / 'oaw.json').read_text())['status'] == 'running'
    history.ACTIVE_RUNS.add('unfinished')
    try:
        assert history.list_runs(context(), {})['items'][0]['status'] == 'running'
    finally:
        history.ACTIVE_RUNS.discard('unfinished')


def test_review_retries_keep_separate_immutable_attempts(store):
    directory = save(store, 'multi')
    outputs = directory / 'pywpem-review'
    outputs.mkdir()
    (outputs / 'result.json').write_text('first')
    history.archive_review(directory, {'review_started_at_ns': 10, 'status': 'failed'})
    (outputs / 'result.json').write_text('second')
    history.archive_review(directory, {'review_started_at_ns': 10, 'status': 'completed'})
    history.archive_review(directory, {'review_started_at_ns': 20, 'status': 'completed'})
    assert (directory / 'review-history/10/outputs/result.json').read_text() == 'first'
    assert (directory / 'review-history/20/outputs/result.json').read_text() == 'second'
