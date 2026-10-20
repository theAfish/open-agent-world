"""Durable history pagination, legacy recovery, and lazy body reads."""
import json

from backend.persistence.database import Database
from backend.plugins import create_builtin_registry
from backend.runs.history import RunActivityHistory
from backend.runs.invocation_logs import RunInvocationLogs
from backend.runs.reasoning import RunReasoning
from backend.runs.store import RunStore
from backend.state import StateStore


def test_status_ticks_are_not_transcript_rows_including_previously_indexed_ticks(tmp_path):
    db = Database(tmp_path / 'ticks.sqlite3')
    try:
        run = RunStore(db).create(agent_id='a', runtime_provider_id='test', caller_kind='user')
        archive = RunActivityHistory(db, run.run_id)
        stamp = '2026-10-19T00:00:01Z'
        for i, tick in enumerate(({'phase': 'model'}, {'kind': 'status', 'text': ' \n\t'},
                                  {'kind': 'context_usage', 'text': 'internal usage'}, {'text': {'phase': 'model'}})):
            archive.public('agent_progress', tick, str(i), stamp)
        assert archive.page()['total'] == 0
        # Existing databases keep their records, but empty entries must not
        # consume a page or hide the real activity beyond a run of status ticks.
        for i in range(60):
            archive.save(f'old-tick-{i}', {'type': 'agent_progress', 'kind': 'status', 'timestamp': stamp, 'text': ' \n\t'})
        archive.public('agent_progress', {'kind': 'status', 'text': 'Checking the files'}, 'visible', stamp)
        archive.thinking({'provider_message_id': 'p', 'created_at': stamp})
        page = archive.page(limit=1)
        assert page['total'] == 2 and page['next_offset'] == 1
        assert page['items'][0]['text'] == 'Checking the files'
        assert archive.page(offset=1)['items'][0]['kind'] == 'model_reasoning'
    finally:
        db.close()


def test_public_calls_survive_cache_rotation_and_database_reopen(tmp_path):
    path = tmp_path / 'history.sqlite3'
    db = Database(path)
    run = RunStore(db).create(agent_id='a', runtime_provider_id='test', caller_kind='user')
    archive = RunActivityHistory(db, run.run_id)
    for i in range(230):
        for kind in ('tool_started', 'tool_completed'):
            archive.public(kind, {'call_id': str(i), 'name': 'read',
                **({'arguments': {'path': f'file-{i}'}} if kind == 'tool_started' else {'response': {'content': f'output-{i}'}})},
                f'{i}:{kind}', f'2026-10-19T00:{i // 60:02}:{i % 60:02}.000000+00:00')
    RunStore(db).update_lifecycle(run.run_id, tool_count=230, tool_trace_truncated=True)
    db.close()
    reopened = Database(path)
    try:
        archive = RunActivityHistory(reopened, run.run_id)
        first = archive.page()
        assert first['total'] == 230 and len(first['items']) == 50 and first['next_offset'] == 50
        assert not first['incomplete']
        rows, offset = first['items'], first['next_offset']
        while offset is not None:
            page = archive.page(offset)
            rows += page['items']
            offset = page['next_offset']
        assert [row['call_id'] for row in rows] == [str(i) for i in range(230)]
        assert all(row['type'] == 'tool_completed' and 'response' not in row and 'arguments' not in row for row in rows)
        assert archive.get(rows[0]['activity_id'])['arguments'] == {'path': 'file-0'}
        assert archive.get(rows[-1]['activity_id'])['response'] == {'content': 'output-229'}
        assert archive.page(limit=10, latest=True)['items'] == rows[-10:]
    finally:
        reopened.close()


def test_legacy_native_bodies_backfill_metadata_and_read_one_record(tmp_path, monkeypatch):
    db = Database(tmp_path / 'legacy.sqlite3')
    try:
        state = StateStore(db, create_builtin_registry())
        run = RunStore(db).create(agent_id='a', runtime_provider_id='test', caller_kind='user')
        scope = state.ensure_scope('run', run.run_id)
        stamp = '2026-10-19T00:00:01+00:00'
        tools = {f'tool-{i}': {'id': f'tool-{i}', 'run_id': run.run_id, 'call_id': str(i), 'role': 'agent',
            'run_attempt': 0, 'name': 'read', 'state': 'returned', 'started_at': stamp, 'updated_at': stamp,
            'returned_at': stamp, 'arguments': {'source': 'large input' * 10000}, 'response': {'stdout': 'large output' * 10000}}
            for i in range(2)}
        thought = {'provider_message_id': 'thought/1', 'kind': 'model_reasoning', 'text': 'saved thinking' * 2000,
            'run_id': run.run_id, 'created_at': stamp, 'updated_at': stamp, 'streaming': False}
        state.set(scope, 'tool_invocation_logs', tools, run_id=run.run_id)
        state.set(scope, 'model_reasoning', {'thought/1': thought}, run_id=run.run_id)
        monkeypatch.setattr(state, 'get', lambda *args: (_ for _ in ()).throw(AssertionError('whole Run was decoded')))
        archive = RunActivityHistory(db, run.run_id)
        page = archive.page()
        assert page['total'] == 3
        assert 'large input' not in json.dumps(page) and 'large output' not in json.dumps(page)
        assert 'saved thinking' not in json.dumps(page)
        assert RunInvocationLogs(state, run.run_id).get('tools', 'tool-0') == tools['tool-0']
        assert RunReasoning(state, run.run_id).get('thought/1')['text'] == thought['text']
        assert archive.page()['items'] == page['items']
    finally:
        db.close()


def test_activity_api_separates_directory_from_bodies_and_run_scope(client):
    services = client.app.state.services
    run = services.run_manager.store.create(agent_id='fixture', runtime_provider_id='test', caller_kind='user')
    scope = services.state.ensure_scope('run', run.run_id)
    logs = RunInvocationLogs(services.state, run.run_id)
    identity = logs.start_tool('builder', 'call', 0, 'read', {'path': '/work/file'})
    logs.finish_tool(identity, {'stdout': 'full result'})
    thinking = RunReasoning(services.state, run.run_id)
    thinking.save({'provider_message_id': 'thought', 'text': 'provider summary', 'streaming': True})
    thinking.interrupt()
    result = client.get(f'/api/runs/{run.run_id}/activity?limit=1')
    assert result.status_code == 200
    first = result.json()
    assert len(first['items']) == 1 and first['next_offset'] == 1 and first['total'] == 2
    assert 'full result' not in result.text and 'provider summary' not in result.text
    assert client.get(f'/api/runs/{run.run_id}/invocations/tools/{identity}').json()['response']['stdout'] == 'full result'
    assert client.get(f'/api/runs/{run.run_id}/reasoning/thought').json()['interrupted'] is True
    assert client.get('/api/runs/missing/activity').status_code == 404
    assert client.get(f'/api/runs/{run.run_id}/activity?limit=101').status_code == 422


def test_legacy_public_tail_reports_only_actual_missing_history(tmp_path):
    db = Database(tmp_path / 'legacy-tail.sqlite3')
    try:
        store = RunStore(db)
        run = store.create(agent_id='a', runtime_provider_id='test', caller_kind='user')
        store.update_lifecycle(run.run_id, tool_count=100, tool_trace_truncated=True, tool_trace=[
            {'id': 'start', 'type': 'tool_started', 'name': 'read', 'call_id': 'same', 'timestamp': '2026-10-19T00:00:01Z', 'arguments': {'path': 'x'}},
            {'id': 'end', 'type': 'tool_completed', 'name': 'read', 'call_id': 'same', 'timestamp': '2026-10-19T00:00:02Z', 'response': {'ok': True}},
        ])
        archive = RunActivityHistory(db, run.run_id)
        page = archive.page()
        assert page['incomplete'] and page['total'] == 1
        assert page['items'][0]['type'] == 'tool_completed'
        assert archive.get(page['items'][0]['activity_id'])['arguments'] == {'path': 'x'}
    finally:
        db.close()
