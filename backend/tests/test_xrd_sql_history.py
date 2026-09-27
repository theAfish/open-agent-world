"""SQL history integration: real card lifecycle, grants, replay and byte lineage."""
import base64
import json
import sqlite3
from types import SimpleNamespace
from threading import Event

import pytest
from backend.tests.conftest import create_node
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from oaw_xrd import history, sql_history as sql


@pytest.fixture
def archive(client, tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    root.mkdir()
    monkeypatch.setattr(history, '_root', lambda: root)
    owner = create_node(client, 'xrd.match')
    database = create_node(client, 'data.sqlite')
    edge = client.post('/api/edges', json={'source': owner['id'], 'target': database['id'], 'relationship': 'xrd.history-store'})
    assert edge.status_code == 201, edge.text
    ctx = SimpleNamespace(node_id=owner['id'], actor_id=None, cancelled=Event())
    directory = root / 'oaw-example'
    directory.mkdir()
    manifest = {'run_id': 'example', 'agent_id': owner['id'], 'workflow_stage': 'search', 'status': 'completed', 'created_at_ns': 12}
    (directory / 'oaw.json').write_text(json.dumps(manifest))
    (directory / 'input.json').write_text(json.dumps({'budget': 5, 'cif': 'private-large-string'}))
    (directory / 'result.json').write_text(json.dumps({'mode': 'match', 'candidates': [{'candidate_id': 'a', 'metrics': {'rwp_percent': 12.3}, 'converged': False}]}))
    return client, ctx, directory, edge.json(), database


def test_idempotent_migration_sql_only_history_and_exact_file_bytes(archive):
    client, ctx, directory, _, _ = archive
    raw = b'\xef\xbb\xbf' + b'X' * (2*1024*1024)
    (directory / 'source.cif').write_bytes(raw)
    assert sql.migrate(ctx, {})['imported_runs'] == 1
    with sql.connection(sql.database(ctx.node_id)) as con:
        count = con.execute('SELECT count(*) FROM xrd_records').fetchone()[0]
        assert con.execute('SELECT rwp_percent FROM xrd_evaluations').fetchone()[0] == 12.3
    sql.migrate(ctx, {})
    with sql.connection(sql.database(ctx.node_id)) as con:
        assert con.execute('SELECT count(*) FROM xrd_records').fetchone()[0] == count
    directory.rename(directory.with_name('offline-source'))
    assert history.list_runs(ctx, {})['total'] == 1
    assert history.inspect_run(ctx, {'run_id': 'example'})['parameters'] == {'budget': 5}
    assert base64.b64decode(history.read_file(ctx, {'run_id': 'example', 'name': 'source.cif'})['data']) == raw
    assert sql.records(ctx, {'run_id': 'example'})['items'][0]['payload']['converged'] is False


def test_reports_validate_schema_owner_evidence_actor_and_revocation(archive):
    client, ctx, _, _, _ = archive
    sql.migrate(ctx, {})
    analyst = create_node(client, 'agent')
    edge = client.post('/api/edges', json={'source': analyst['id'], 'target': ctx.node_id, 'relationship': 'xrd.results-report'}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tool = next(t for t in client.portal.call(provider.list_tools, analyst['id']) if t.name == 'xrd_submit_report')
    args = {'target': ctx.node_id, 'schema_version': 1, 'run_id': 'example', 'summary': 'Candidate evidence only', 'evidence': ['result.json'], 'limitations': ['Not phase confirmation'], 'conclusion': 'inconclusive'}
    invoke = lambda a: client.portal.call(provider.invoke_tool, analyst['id'], tool.capability_id, a)
    result = invoke(args)
    assert result['stored']
    assert invoke(args)['record_id'] == result['record_id']
    for bad in [dict(args, run_id='foreign'), dict(args, evidence=['../secret']), dict(args, actor_id='forged'), dict(args, schema_version=2), dict(args, limitations=[])]:
        with pytest.raises((ValueError, ResourceValidationError)):
            invoke(bad)
    rows = sql.records(ctx, {'run_id': 'example', 'kind': 'agent_report'})
    assert rows['total'] == 1 and rows['items'][0]['actor_id'] == analyst['id']
    client.delete('/api/edges/' + edge['id'])
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        invoke(args)
    ctx.node_id = 'other-owner'
    # Direct store read also scopes owner, even if another workbench points to same DB.
    assert sql.database(ctx.node_id) is None


def test_binding_revocation_missing_database_and_multiple_targets(archive):
    client, ctx, _, edge, database = archive
    path = sql.database(ctx.node_id)
    second = create_node(client, 'data.sqlite')
    extra = client.post('/api/edges', json={'source': ctx.node_id, 'target': second['id'], 'relationship': 'xrd.history-store'}).json()
    with pytest.raises(ValueError, match='一个'):
        sql.migrate(ctx, {})
    client.delete('/api/edges/' + extra['id'])
    path.rename(path.with_suffix('.offline'))
    with pytest.raises(ValueError, match='缺失'):
        sql.database(ctx.node_id)
    assert not path.exists()
    client.delete('/api/edges/' + edge['id'])
    assert sql.database(ctx.node_id) is None


def test_capture_atomic_on_mismatched_multiphase_and_schema(archive):
    _, ctx, directory, _, _ = archive
    sql.migrate(ctx, {})
    path = directory / 'multiphase-state.json'
    path.write_text(json.dumps({'run_id': 'wrong', 'options': {'owner_node_id': ctx.node_id}}))
    with pytest.raises(ValueError, match='来源'):
        sql.capture(directory)
    with sql.connection(sql.database(ctx.node_id)) as con:
        assert con.execute("SELECT count(*) FROM xrd_records WHERE kind='state'").fetchone()[0] == 0
        con.execute('UPDATE xrd_schema SET version=999')
    with pytest.raises(ValueError, match='schema'):
        sql.capture(directory)


def test_multiphase_records_keep_baseline_separate_and_review_attempts(archive):
    _, ctx, directory, _, _ = archive
    state = {'run_id': 'example', 'options': {'owner_node_id': ctx.node_id}, 'status': 'completed',
             'decision_requests': [{'selection_token': 'token', 'status': 'submitted'}],
             'trials': [{'trial_id': 't1', 'score': 80}], 'baseline': {'trials': [{'trial_id': 'b1', 'score': 78}]},
             'pywpem_review': {'reviews': [{'full': {'metrics': {'rwp_percent': 12.1}, 'converged': False}}]},
             'review_started_at_ns': 123}
    (directory / 'multiphase-state.json').write_text(json.dumps(state))
    history.archive_review(directory, state)
    assert history.inspect_run(ctx, {'run_id': 'example'})['record_counts']['evaluation'] == 2
    assert sql.records(ctx, {'run_id': 'example', 'kind': 'decision'})['total'] == 1
    with sql.connection(sql.database(ctx.node_id)) as con:
        assert con.execute("SELECT count(*) FROM xrd_records WHERE actor_id='BO_baseline'").fetchone()[0] == 1
    assert history.read_file(ctx, {'run_id': 'example', 'name': 'review-history/123/state.json'})
