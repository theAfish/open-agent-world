"""Native Run export through live Agent and Sandbox graph permissions."""
import json
import os

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.runs import RunStatus
from backend.runs.reasoning import RunReasoning
from backend.sandbox import history
from backend.sandbox.transfers import CHUNK_BYTES
from backend.tests.conftest import create_node
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def connect(client, source, target, relationship):
    response = client.post('/api/edges', json={'source': source['id'], 'target': target['id'], 'relationship': relationship})
    assert response.status_code == 201, response.text
    return response.json()


def setup_reviewer(client):
    _, sandbox, skill, toolbox, _ = setup_skill(client)
    reviewer = create_node(client, 'atomsculptor.log-review-agent')
    edge = connect(client, reviewer, sandbox, 'execute')
    return reviewer, sandbox, skill, toolbox, [edge]


def saved_run(services, agent, sandbox, task_id=None):
    store = services.run_manager.store
    run = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='user', task_id=task_id)
    stamp = run.created_at.isoformat()
    scope = services.state.ensure_scope('run', run.run_id)
    services.state.set(scope, 'input', 'Keep the native task prompt.', run_id=run.run_id)
    services.state.set(scope, 'tool_invocation_logs', {'t1': {'id': 't1', 'call_id': 'call1', 'role': 'agent',
        'started_at': stamp, 'state': 'returned', 'name': 'execute_command',
        'arguments': {'sandbox': sandbox['id']}, 'response': {'command_id': 'c1', 'exit_code': 0}}}, run_id=run.run_id)
    services.state.set(scope, 'model_request_logs', {'m1': {'id': 'm1', 'role': 'agent', 'model_request': 1,
        'model_attempt': 1, 'started_at': stamp, 'state': 'returned', 'input_tokens': 321,
        'request': {'contents': [{'parts': [{'text': 'Native complete request'}]}]}}}, run_id=run.run_id)
    RunReasoning(services.state, run.run_id).save({'provider_message_id': 'thinking1', 'text': 'Saved visible thinking.', 'streaming': False})
    history.save(services, sandbox['id'], {'id': 'c1-' + run.run_id, 'run_id': run.run_id, 'state': 'finished',
        'argv': ['python', 'build.py'], 'exit_code': 0, 'stdout': 'native command output'})
    store.update_status(run.run_id, RunStatus.SUCCEEDED)
    return store.get(run.run_id)


def call(client, reviewer, name, **arguments):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    return client.portal.call(provider.invoke_tool, reviewer['id'], 'operation:' + name, arguments)


def test_export_latest_task_keeps_native_logs_large_files_and_current_snapshot_provenance(runtime_client):
    client, backend, native = runtime_client
    reviewer, sandbox, *_ = setup_reviewer(client)
    source = create_node(client, 'agent', name='Source modeller')
    connect(client, reviewer, source, 'agent.run_history')
    services = client.app.state.services
    first = saved_run(services, source, sandbox, 'native-task')
    latest = saved_run(services, source, sandbox, 'native-task')
    unrelated = create_node(client, 'agent')
    saved_run(services, unrelated, sandbox, 'native-task')
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    (root / 'build.py').write_text("raise AssertionError('ARCHIVED_SOURCE_MUST_NOT_EXECUTE')")
    (root / 'empty.txt').touch()
    large = b'z' * (17 * 1024 * 1024)
    (root / 'large.bin').write_bytes(large)
    if os.name != 'nt':
        (root / 'outside-link').symlink_to(root.parent)
    before = {p.name: p.lstat().st_mtime_ns for p in root.iterdir()}
    listing = call(client, reviewer, 'list_agent_runs', agent=source['id'], limit=1)
    assert listing['total'] == 2 and listing['next_offset'] == 1
    assert listing['items'][0]['run_id'] == latest.run_id
    receipt = call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=sandbox['id'])
    assert receipt['state'] == 'exported' and receipt['selected_run_id'] == latest.run_id
    assert receipt['run_ids'] == [first.run_id, latest.run_id]
    archive = root / receipt['archive_path']
    task = json.loads((archive / 'task.json').read_text())
    last = archive / task['attempts'][-1]['directory']
    assert json.loads((last / 'model requests.json').read_text())[0]['input_tokens'] == 321
    assert json.loads((last / 'model thinking.json').read_text())[0]['text'] == 'Saved visible thinking.'
    assert (last / 'prompt.txt').read_text() == 'Keep the native task prompt.'
    assert json.loads((last / 'tool calls.json').read_text())[0]['id'] == 't1'
    assert json.loads((last / 'record catalog.json').read_text())['schema_version'] == 2
    assert (last / 'timeline.jsonl').is_file()
    assert not (last / 'agent logs.txt').exists()  # Full payloads stay in canonical JSON.
    copied = archive / 'sandbox files' / sandbox['id'] / 'workspace'
    assert (copied / 'large.bin').read_bytes() == large
    assert (copied / 'empty.txt').read_bytes() == b''
    assert (copied / 'build.py').read_bytes() == (root / 'build.py').read_bytes()
    collection = json.loads((archive / 'collection.json').read_text())
    assert all(row['provenance'] == 'collection_snapshot' for row in collection['files'])
    if os.name != 'nt':
        assert any(row['kind'] == 'blocked_file' for row in collection['warnings'])
        assert not (copied / 'outside-link').exists()
    assert all((root / name).lstat().st_mtime_ns == stamp for name, stamp in before.items())
    assert native.last_argv == ()
    # Re-collection does not recursively absorb its own prior exported archive.
    second = call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=sandbox['id'])
    assert second['collected_files'] == receipt['collected_files']
    assert any(row['kind'] == 'previous_review_export_excluded' for row in second['warnings'])


def test_run_and_destination_permissions_are_independent_and_foreign_runs_are_denied(runtime_client):
    client, backend, _ = runtime_client
    reviewer, sandbox, _, _, edges = setup_reviewer(client)
    source = create_node(client, 'agent')
    foreign = create_node(client, 'agent')
    services = client.app.state.services
    run = saved_run(services, source, sandbox)
    other = saved_run(services, foreign, sandbox)
    with pytest.raises(PermissionDeniedError):
        call(client, reviewer, 'list_agent_runs', agent=source['id'])
    read_edge = connect(client, reviewer, source, 'agent.run_history')
    with pytest.raises(PermissionDeniedError):
        call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=sandbox['id'], run_id=other.run_id)
    client.delete('/api/edges/' + edges[0]['id'])
    assert call(client, reviewer, 'list_agent_runs', agent=source['id'])['items'][0]['run_id'] == run.run_id
    with pytest.raises(PermissionDeniedError):
        call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=sandbox['id'])
    client.delete('/api/edges/' + read_edge['id'])
    with pytest.raises(PermissionDeniedError):
        call(client, reviewer, 'list_agent_runs', agent=source['id'])


def test_missing_source_access_retains_logs_with_explicit_file_gap_and_active_latest_is_reported(runtime_client):
    client, backend, _ = runtime_client
    reviewer, destination, *_ = setup_reviewer(client)
    source = create_node(client, 'agent')
    source_sandbox = create_node(client, 'sandbox')
    connect(client, reviewer, source, 'agent.run_history')
    services = client.app.state.services
    previous = saved_run(services, source, source_sandbox)
    receipt = call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=destination['id'])
    assert receipt['collected_files'] == 0
    assert receipt['warnings'][0]['kind'] == 'sandbox_access_missing'
    active = services.run_manager.store.create(agent_id=source['id'], runtime_provider_id='core.mock', caller_kind='user')
    with pytest.raises(ResourceValidationError, match='still active'):
        call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=destination['id'])
    explicit = call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=destination['id'],
                    run_id=previous.run_id, include_task_attempts=False)
    assert explicit['run_ids'] == [previous.run_id] and active.run_id not in explicit['run_ids']


def test_revocation_during_file_capture_stops_before_publishing_archive(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    reviewer, sandbox, *_ = setup_reviewer(client)
    source = create_node(client, 'agent')
    edge = connect(client, reviewer, source, 'agent.run_history')
    services = client.app.state.services
    saved_run(services, source, sandbox)
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    (root / 'data.bin').write_bytes(b'x' * (CHUNK_BYTES + 1))
    original = backend.file_operation
    revoked = False
    async def revoke(*args, **kwargs):
        nonlocal revoked
        result = await original(*args, **kwargs)
        if args[1] == 'read_chunk' and not revoked:
            services.world.delete_edge(edge['id'])
            revoked = True
        return result
    monkeypatch.setattr(backend, 'file_operation', revoke)
    with pytest.raises(PermissionDeniedError):
        call(client, reviewer, 'export_agent_run', agent=source['id'], sandbox=sandbox['id'])
    assert revoked and not list(root.glob('oaw-run-review-*'))


def test_only_report_agent_can_create_a_run_history_reading_edge(runtime_client):
    client, _, _ = runtime_client
    ordinary = create_node(client, 'agent')
    source = create_node(client, 'agent')
    reviewer = create_node(client, 'atomsculptor.log-review-agent')
    refused = client.post('/api/edges', json={'source': ordinary['id'], 'target': source['id'], 'relationship': 'agent.run_history'})
    assert refused.status_code == 422
    connected = connect(client, reviewer, source, 'agent.run_history')
    assert connected['source'] == reviewer['id']
    assert call(client, reviewer, 'list_agent_runs', agent=source['id'])['items'] == []
    reverse = client.post('/api/edges', json={'source': source['id'], 'target': reviewer['id'], 'relationship': 'agent.run_history'})
    # Native graph normalization can orient a drag from either side; the
    # persisted edge must always grant Report Agent → source, never the reverse.
    assert reverse.status_code in {201, 409}
    if reverse.status_code == 201:
        assert reverse.json()['source'] == reviewer['id']
    # Simulate a previously saved edge admitted by an older broad definition.
    # Reusing OAW's current type validation must revoke it without migrating or
    # silently reversing the user's graph.
    services = client.app.state.services
    with services.database.transaction() as db:
        db.execute('UPDATE edges SET source_id=? WHERE id=?', (ordinary['id'], connected['id']))
    with pytest.raises(PermissionDeniedError):
        call(client, ordinary, 'list_agent_runs', agent=source['id'])


@pytest.mark.parametrize('scope_type', ['session', 'group'])
def test_first_export_claims_its_workspace_before_writing_and_retains_it_for_execution(runtime_client, scope_type):
    from backend.conversations import ConversationSessionCreate
    from backend.sandbox.task_workspace import binding, ensure_directory
    from backend.tests.test_task_workspaces import in_run, new_run

    client, backend, _ = runtime_client
    reviewer, sandbox, *_ = setup_reviewer(client)
    source = create_node(client, 'agent')
    connect(client, reviewer, source, 'agent.run_history')
    services = client.app.state.services
    saved_run(services, source, sandbox)
    response = client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': scope_type}})
    assert response.status_code == 200, response.text
    room = create_node(client, 'conversation')
    session = services.conversations.create_session(room['id'], ConversationSessionCreate(title='Review', group_title='Review'))
    run = new_run(services, reviewer, session=session)
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    # The title collides with a folder the session/group does not own.
    (root / 'Review').mkdir()
    (root / 'Review' / 'untouched.txt').write_text('existing input')

    async def scenario():
        with in_run(run):
            provider = WorldAgentCapabilityProvider(services)
            receipt = await provider.invoke_tool(reviewer['id'], 'operation:export_agent_run', {
                'agent': source['id'], 'sandbox': sandbox['id'], 'include_sandbox_files': False})
            owned = binding(services, sandbox['id'], reviewer['id'])
            assert owned['claimed'] and owned['directory'] != 'Review'
            archive = root / receipt['archive_workspace_path']
            assert archive == root / owned['directory'] / receipt['archive_path']
            assert (archive / 'task.json').is_file()
            assert (await ensure_directory(services, sandbox['id'], reviewer['id']))['directory'] == owned['directory']
            assert list((root / 'Review').iterdir()) == [root / 'Review' / 'untouched.txt']

    client.portal.call(scenario)
