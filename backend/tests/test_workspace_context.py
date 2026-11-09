"""Session/group workspace identity, shared documents and migration integrity."""
import sqlite3

import pytest

from backend.card_state import state_session
from backend.conversations import ConversationSessionCreate
from backend.errors import ResourceValidationError
from backend.persistence.database import Database
from backend.tests.conftest import create_node
from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.tests.test_task_workspaces import new_run, in_run
from backend.sandbox.task_workspace import binding


def setup_sessions(client):
    services = client.app.state.services
    store = services.conversations
    room = create_node(client, 'conversation')
    for actor in services.world.list_cards():
        if services.plugins.has_trait(actor.type, 'core.agent') and any(
            cap.kind == 'sandbox.execute' for cap in services.capabilities.derive(actor.id).capabilities):
            response = client.post('/api/edges', json={'source': actor.id, 'target': room['id'], 'relationship': 'participate'})
            assert response.status_code == 201, response.text
    a = store.create_session(room['id'], ConversationSessionCreate(title='First', group_title='Experiment'))
    b = store.create_session(room['id'], ConversationSessionCreate(title='Second', group_id=a.group_id))
    c = store.create_session(room['id'], ConversationSessionCreate(title='Separate group'))
    return room, a, b, c


def test_unconnected_sandbox_does_not_adopt_a_world_conversation(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    room = create_node(client, 'conversation')
    session = services.conversations.create_session(room['id'], ConversationSessionCreate(title='Unrelated'))
    url = f"/api/nodes/{sandbox['id']}/workspace-context"
    value = client.get(url).json()
    assert value['session_id'] == 'default' and value['conversation_id'] is None
    assert value['directory'] == 'Default session'
    response = client.get(url, headers={'X-OAW-State-Session': session.id})
    assert response.status_code == 422 and 'not connected' in response.text
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    from backend.workspace_context import _records
    assert session.id not in _records(services, sandbox['id'])[1]['sessions']
    # A standalone Run also ignores an unrelated browser request context.
    run = new_run(services, agent)
    with in_run(run), state_session(session.id):
        assert binding(services, sandbox['id'], agent['id'])['session_id'] == 'default'


@pytest.mark.parametrize('caller, context_id', [('summon', 'private-model-context'), ('user', 'fresh-model-context')])
def test_private_run_context_is_not_a_conversation_selection(runtime_client, caller, context_id):
    from backend.sandbox.task_workspace import initial_policy
    from backend.workspace_context import resolve_workspace
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    run = services.run_manager.store.create(agent_id=agent['id'], runtime_provider_id='core.mock',
        caller_kind=caller, context_id=context_id)
    run = services.run_manager.store.update_lifecycle(run.run_id, task_workspace=initial_policy(
        services.run_manager.store, agent_id=run.agent_id, run_id=run.run_id, caller_kind=caller))
    with in_run(run):
        workspace = resolve_workspace(services, sandbox['id'])
        assert workspace['session_id'] == 'default' and workspace['conversation_id'] is None
        with pytest.raises(ResourceValidationError, match='no longer exists'):
            resolve_workspace(services, sandbox['id'], session_id=context_id)


def test_deleted_conversation_run_context_does_not_fall_back(runtime_client):
    from backend.workspace_context import resolve_workspace
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    room, session, _, _ = setup_sessions(client)
    services = client.app.state.services
    run = new_run(services, agent, session=session, caller='conversation')
    services.conversations.delete_session(room['id'], session.id)
    with in_run(run), pytest.raises(ResourceValidationError, match='no longer exists'):
        resolve_workspace(services, sandbox['id'])


def test_multiple_conversations_require_context_and_do_not_guess_or_prebind(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    room, a, _, _ = setup_sessions(client)
    other = create_node(client, 'conversation')
    response = client.post('/api/edges', json={'source': agent['id'], 'target': other['id'], 'relationship': 'participate'})
    assert response.status_code == 201
    services = client.app.state.services
    b = services.conversations.create_session(other['id'], ConversationSessionCreate(title=a.title))
    from backend.workspace_context import _records
    before = _records(services, sandbox['id'])[1]
    url = f"/api/nodes/{sandbox['id']}/workspace-context"
    pending = client.get(url).json()
    assert pending['bound'] is False and pending['directory'] == ''
    assert _records(services, sandbox['id'])[1] == before
    assert client.get(f"/api/sandboxes/{sandbox['id']}/files", params={'operation': 'list', 'root': 'workspace'}).status_code == 200
    response = client.post(f"/api/sandboxes/{sandbox['id']}/execute", headers={'X-OAW-State-Session': 'default'},
        json={'argv': ['cmd.exe', '/c', 'echo ambiguous']})
    assert response.status_code == 422 and 'Select a connected Conversation' in response.text
    first = client.get(url, headers={'X-OAW-State-Session': a.id}).json()
    second = client.get(url, headers={'X-OAW-State-Session': b.id}).json()
    assert first['conversation_id'] == room['id'] and second['conversation_id'] == other['id']
    assert first['directory'] != second['directory']  # duplicate names do not merge identities
    run = new_run(services, agent, session=a)
    with in_run(run), state_session(b.id):
        assert binding(services, sandbox['id'], agent['id'])['session_id'] == a.id


def test_mode_change_preserves_only_related_conversations(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    _, a, _, unused = setup_sessions(client)
    services = client.app.state.services
    other = create_node(client, 'conversation')
    unrelated = services.conversations.create_session(other['id'], ConversationSessionCreate(title='Unrelated', group_title='Later group'))
    response = client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    assert response.status_code == 200, response.text
    from backend.workspace_context import _records
    saved = _records(services, sandbox['id'])[1]['sessions']
    assert saved[a.id]['scope_type'] == saved[unused.id]['scope_type'] == 'session'
    assert unrelated.id not in saved
    # Connecting another Conversation later uses the current policy, rather
    # than an unrelated session's age establishing a historical relationship.
    client.post('/api/edges', json={'source': agent['id'], 'target': other['id'], 'relationship': 'participate'})
    value = client.get(f"/api/nodes/{sandbox['id']}/workspace-context", headers={'X-OAW-State-Session': unrelated.id}).json()
    assert value['scope_type'] == 'group' and value['directory'] == unrelated.group_title


def test_multiple_sandboxes_do_not_select_a_view_source_by_id(runtime_client):
    client, _, _ = runtime_client
    agent, first, *_ = setup_skill(client)
    second = create_node(client, 'sandbox')
    assert client.post('/api/edges', json={'source': agent['id'], 'target': second['id'], 'relationship': 'execute'}).status_code == 201
    viewer = create_node(client, 'atomsculptor.structure')
    client.post('/api/edges', json={'source': agent['id'], 'target': viewer['id'], 'relationship': 'atomsculptor.structure.modify'})
    _, a, _, _ = setup_sessions(client)
    url = f"/api/nodes/{viewer['id']}/workspace-context"
    response = client.get(url, headers={'X-OAW-State-Session': a.id})
    assert response.status_code == 422 and 'Multiple Sandboxes' in response.text
    services = client.app.state.services
    run = new_run(services, agent, session=a)
    with in_run(run):
        left = binding(services, first['id'], agent['id'])
        right = binding(services, second['id'], agent['id'])
        assert left['sandbox_id'] == first['id'] and right['sandbox_id'] == second['id']
        assert left['session_id'] == right['session_id'] == a.id
    response = client.post('/api/edges', json={'source': viewer['id'], 'target': second['id'], 'relationship': 'core.file-preview'})
    assert response.status_code == 201, response.text
    value = client.get(url, headers={'X-OAW-State-Session': a.id}).json()
    assert value['sandbox_id'] == second['id']


def test_inferred_workspace_session_still_requires_publication_permission(runtime_client):
    from backend.deployment_workspace import workspace_router
    client, _, _ = runtime_client
    _, sandbox, *_ = setup_skill(client)
    room, a, _, _ = setup_sessions(client)
    manifest = {'permissions': {sandbox['id']: ['files', 'preview']}}
    client.app.include_router(workspace_router(manifest), prefix='/api/test-publication')
    url = f"/api/test-publication/workspace/nodes/{sandbox['id']}/workspace-context"
    assert client.get(url).status_code == 404
    assert client.get(url, headers={'X-OAW-State-Session': a.id}).status_code == 404
    manifest['permissions'][room['id']] = ['sessions']
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()['conversation_id'] == room['id']


@pytest.mark.parametrize('options', [{}, {'task_id': 'manual-task', 'detached': True}])
def test_direct_agent_execution_preserves_selected_session(runtime_client, options):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    _, a, _, _ = setup_sessions(client)
    response = client.post(f"/api/agents/{agent['id']}/run", headers={'X-OAW-State-Session': a.id},
        json={'prompt': 'Continue in this session', **options})
    assert response.status_code == 202, response.text
    services = client.app.state.services
    run = services.run_manager.get_run(response.json()['run_id'])
    assert run.context_id == a.id
    with in_run(run):
        assert binding(services, sandbox['id'], agent['id'])['directory'] == a.title


def test_global_shared_files_and_group_permissions_follow_native_session_bindings(runtime_client):
    client, backend, _ = runtime_client
    _, sandbox, *_ = setup_skill(client)
    url = f"/api/sandboxes/{sandbox['id']}"
    scope_url = f"/api/nodes/{sandbox['id']}/workspace-context"
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'shared'}})
    room, a, b, _ = setup_sessions(client)
    headers = lambda session: {'X-OAW-State-Session': session.id}
    for session in (a, b):
        value = client.get(scope_url, headers=headers(session)).json()
        assert value['scope_type'] == 'shared' and value['directory'] == '' and value['writable_directory'] is None
    assert client.post(url + '/files', params={'path': 'global.txt'}, headers=headers(a), content=b'global').status_code == 201
    assert client.get(url + '/files', params={'operation': 'preview', 'path': 'global.txt'}, headers=headers(b)).json()['text'] == 'global'
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    store = client.app.state.services.conversations
    c = store.create_session(room['id'], ConversationSessionCreate(title='Group member', group_id=a.group_id))
    d = store.create_session(room['id'], ConversationSessionCreate(title='Group peer', group_id=a.group_id))
    e = store.create_session(room['id'], ConversationSessionCreate(title='Other', group_title='Other group'))
    owned = client.get(scope_url, headers=headers(c)).json()['directory']
    assert owned == a.group_title
    assert client.get(scope_url, headers=headers(d)).json()['directory'] == owned
    assert client.get(scope_url, headers=headers(a)).json()['directory'] == ''  # history retained
    other = client.get(scope_url, headers=headers(e)).json()['directory']
    assert client.post(url + '/files', params={'path': owned + '/owned.txt'}, headers=headers(c), content=b'owned').status_code == 201
    assert client.post(url + '/files', params={'path': owned + '/peer.txt'}, headers=headers(d), content=b'peer').status_code == 201
    assert client.get(url + '/files', params={'operation': 'preview', 'path': owned + '/owned.txt'}, headers=headers(e)).json()['text'] == 'owned'
    for method, path, body in [('post', 'global.txt', b'bad'), ('post', other + '/bad.txt', b'bad')]:
        assert getattr(client, method)(url + '/files', params={'path': path}, headers=headers(c), content=body).status_code == 403
    assert client.delete(url + '/files', params={'path': owned + '/owned.txt'}, headers=headers(e)).status_code == 403
    assert client.post(url + '/files/move', headers=headers(c), json={'path': owned + '/owned.txt', 'destination': other + '/moved.txt'}).status_code == 403
    assert client.post(url + '/files', params={'path': owned + '/ok.txt'}, headers=headers(c), content=b'ok').status_code == 201


def test_named_directories_handle_duplicates_collisions_and_session_renames(runtime_client):
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    room = create_node(client, 'conversation')
    client.post('/api/edges', json={'source': agent['id'], 'target': room['id'], 'relationship': 'participate'})
    store = client.app.state.services.conversations
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    (root / 'Repeated').mkdir(); (root / 'Repeated/keep.txt').write_text('pre-existing')
    a = store.create_session(room['id'], ConversationSessionCreate(title='Repeated'))
    b = store.create_session(room['id'], ConversationSessionCreate(title='Repeated'))
    bad = store.create_session(room['id'], ConversationSessionCreate(title='../unsafe:name'))
    unicode_title = store.create_session(room['id'], ConversationSessionCreate(title='界' * 150))
    truncated = store.create_session(room['id'], ConversationSessionCreate(title='a' * 179 + '.x'))
    url = f"/api/nodes/{sandbox['id']}/workspace-context"
    values = [client.get(url, headers={'X-OAW-State-Session': row.id}).json() for row in (a, b, bad, unicode_title, truncated)]
    assert len({value['directory'].casefold() for value in values}) == 5
    assert all('/' not in value['directory'] and ':' not in value['directory'] for value in values)
    assert all(len(value['directory'].encode('utf-8')) <= 255 and not value['directory'].endswith(('.', ' ')) for value in values)
    assert not any(value['claimed'] for value in values)
    for session, value in zip((a, b, bad, unicode_title, truncated), values):
        response = client.post(f"/api/sandboxes/{sandbox['id']}/files", headers={'X-OAW-State-Session': session.id},
            params={'path': value['directory'] + '/result.txt'}, content=b'owned')
        assert response.status_code == 201, response.text
        assert (root / response.json()['path']).read_bytes() == b'owned'
    values = [client.get(url, headers={'X-OAW-State-Session': row.id}).json() for row in (a, b, bad, unicode_title, truncated)]
    assert all((root / value['directory']).is_dir() for value in values)
    assert values[0]['directory'].startswith('Repeated (')
    assert (root / 'Repeated/keep.txt').read_text() == 'pre-existing'
    store.rename_session(room['id'], a.id, 'Renamed')
    assert client.get(url, headers={'X-OAW-State-Session': a.id}).json()['directory'] == values[0]['directory']


@pytest.mark.parametrize('mode', ['session', 'group'])
def test_unused_workspace_follows_current_name_and_only_first_write_claims_it(runtime_client, mode):
    from backend.capabilities.provider import WorldAgentCapabilityProvider

    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    assert client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': mode}}).status_code == 200
    room = create_node(client, 'conversation')
    client.post('/api/edges', json={'source': agent['id'], 'target': room['id'], 'relationship': 'participate'})
    store = services.conversations
    session = store.create_session(room['id'], ConversationSessionCreate(title='New session', group_title='New group'))
    peer = store.create_session(room['id'], ConversationSessionCreate(title='Peer', group_id=session.group_id)) if mode == 'group' else session
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    original_files = list(root.iterdir())
    url = f"/api/nodes/{sandbox['id']}/workspace-context"
    context = lambda current: client.get(url, headers={'X-OAW-State-Session': current.id}).json()
    first = context(session)
    context(peer)
    run, peer_run = [new_run(services, agent, session=current, task_id=task) for current, task in ((session, 'first-task'), (peer, 'peer-task'))]
    for saved_run in (run, peer_run):
        with in_run(saved_run):
            assert not binding(services, sandbox['id'], agent['id'])['claimed']
    assert list(root.iterdir()) == original_files
    title = 'Named workspace'
    if mode == 'group':
        store.rename_group(room['id'], session.group_id, title)
    else:
        store.rename_session(room['id'], session.id, title)
    renamed = context(session)
    assert renamed['directory'] == title and renamed['key'] == first['key'] and not renamed['claimed']
    assert context(peer)['directory'] == title
    assert list(root.iterdir()) == original_files
    # A foreign directory with the same title must never be adopted by a writer.
    (root / title).mkdir()
    (root / title / 'keep.txt').write_text('foreign')

    async def write():
        with in_run(run):
            await WorldAgentCapabilityProvider(services).invoke_tool(agent['id'], 'operation:write_sandbox_text_file',
                {'sandbox': sandbox['id'], 'path': 'result.txt', 'content': 'owned'})
    client.portal.call(write)
    owned = context(session)
    assert owned['claimed'] and owned['directory'].startswith(title + ' (')
    assert (root / owned['directory'] / 'result.txt').read_text() == 'owned'
    assert list((root / title).iterdir()) == [root / title / 'keep.txt']
    for saved_run in (run, peer_run):
        current = services.run_manager.get_run(saved_run.run_id).lifecycle['sandbox_workspaces'][sandbox['id']]
        assert current['directory'] == owned['directory'] and current['claimed']
        assert current['task_id'] == saved_run.task_id and current['origin_run_id'] == saved_run.run_id
    if mode == 'group':
        store.rename_group(room['id'], session.group_id, 'Later name')
    else:
        store.rename_session(room['id'], session.id, 'Later name')
    assert context(session)['directory'] == owned['directory']
    assert context(session)['label'] == 'Later name'
    assert not (root / first['directory']).exists() and not (root / 'Later name').exists()


def test_default_session_group_switch_and_frozen_run_binding(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    _, a, b, c = setup_sessions(client)
    other = create_node(client, 'agent')
    def workspace(run, actor):
        with in_run(run):
            return binding(services, sandbox['id'], actor['id'])
    first = new_run(services, agent, session=a)
    bound = workspace(first, agent)
    assert bound['directory'] == a.title
    assert workspace(new_run(services, other, session=a), other)['directory'] == bound['directory']
    assert workspace(new_run(services, agent, session=b), agent)['directory'] != bound['directory']
    work_a = workspace(new_run(services, agent, session=a, caller='work', task_id='board:same-item'), agent)
    work_b = workspace(new_run(services, agent, session=b, caller='work', task_id='board:same-item'), agent)
    assert work_a['directory'] != work_b['directory']
    # Changing organizational scope never reconfigures or starts the runtime.
    reply = client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    assert reply.status_code == 200, reply.text
    assert workspace(first, agent) == bound
    assert workspace(new_run(services, agent, session=a, source=first.run_id), agent)['directory'] == bound['directory']
    assert workspace(new_run(services, agent, session=b, source=first.run_id), agent)['directory'] == b.title
    # Existing sessions, even unused ones, retain the old organization.
    assert workspace(new_run(services, agent, session=a), agent)['directory'] == a.title
    assert workspace(new_run(services, agent, session=c), agent)['directory'] == c.title
    d = services.conversations.create_session(a.conversation_id, ConversationSessionCreate(title='New group member', group_id=a.group_id))
    e = services.conversations.create_session(a.conversation_id, ConversationSessionCreate(title='Another member', group_id=a.group_id))
    shared = workspace(new_run(services, agent, session=d), agent)
    assert shared['directory'] == a.group_title
    assert workspace(new_run(services, other, session=e), other)['directory'] == shared['directory']
    url = f"/api/nodes/{sandbox['id']}/workspace-context"
    value = client.get(url, headers={'X-OAW-State-Session': d.id}).json()
    assert value['scope_id'] == a.group_id and value['label'] == 'Experiment'
    assert client.get(url, headers={'X-OAW-State-Session': 'missing'}).status_code == 422
    assert client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'task'}}).status_code == 422


def test_new_session_ui_listing_is_empty_and_manual_terminal_creates_the_same_directory(runtime_client):
    client, backend, native = runtime_client
    _, sandbox, *_ = setup_skill(client)
    _, a, _, _ = setup_sessions(client)
    headers = {'X-OAW-State-Session': a.id}
    path = a.title
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    context = client.get(f"/api/nodes/{sandbox['id']}/workspace-context", headers=headers).json()
    assert context['directory'] == path
    response = client.get(f"/api/sandboxes/{sandbox['id']}/files", headers=headers,
        params={'operation': 'list', 'path': path})
    assert response.status_code == 200 and response.json()['entries'] == []
    assert response.json()['exists'] is False
    assert not (root / path).exists()  # Merely opening the view does not allocate it.
    response = client.post(f"/api/sandboxes/{sandbox['id']}/execute", headers=headers,
        json={'argv': ['cmd.exe', '/c', 'echo scoped'], 'working_directory': path})
    assert response.status_code == 200, response.text
    assert native.last_cwd == root / path
    assert response.json()['working_directory'] == path


def test_workspace_catalog_includes_manual_work_and_allows_browsing_other_saved_scopes(runtime_client):
    client, backend, native = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    _, a, b, _ = setup_sessions(client)
    headers_a = {'X-OAW-State-Session': a.id}
    path_a, path_b = a.title, b.title
    # A manually used workspace has no Agent Run, but remains in the picker.
    reply = client.post(f"/api/sandboxes/{sandbox['id']}/execute", headers=headers_a,
        json={'argv': ['cmd.exe', '/c', 'echo manual'], 'working_directory': path_a})
    assert reply.status_code == 200, reply.text
    run = new_run(services, agent, session=b)
    with in_run(run):
        saved = binding(services, sandbox['id'], agent['id'])
    root = backend._sandboxes_root / sandbox['id'] / 'workspace'
    client.get(f"/api/nodes/{sandbox['id']}/workspace-context", headers={'X-OAW-State-Session': b.id})
    assert not (root / path_b).exists()
    from backend.sandbox.task_workspace import ensure_directory
    async def use_workspace():
        with in_run(run):
            await ensure_directory(services, sandbox['id'], agent['id'])
    client.portal.call(use_workspace)
    assert (root / path_b).is_dir()
    url = f"/api/sandboxes/{sandbox['id']}"
    items = client.get(url + '/workspaces', headers=headers_a).json()['items']
    assert {item['directory'] for item in items} == {path_a, path_b}
    assert next(item for item in items if item['directory'] == path_a)['label'] == a.title
    assert client.get(url + '/task-workspaces', headers=headers_a).json()['items'] == items
    # Browsing B's saved directory need not change the Conversation selection.
    reply = client.post(url + '/execute', headers=headers_a,
        json={'argv': ['cmd.exe', '/c', 'echo selected'], 'working_directory': path_b})
    assert reply.status_code == 200, reply.text
    assert native.last_cwd == root / path_b
    assert client.get(f"/api/nodes/{sandbox['id']}/workspace-context", headers=headers_a).json()['directory'] == path_a
    with in_run(run):
        current = binding(services, sandbox['id'], agent['id'])
        assert current['directory'] == saved['directory'] and current['key'] == saved['key']
    # Root overview commands retain the original root cwd, rather than the
    # current session; all receipts stay available through native history.
    reply = client.post(url + '/execute', headers=headers_a, json={'argv': ['cmd.exe', '/c', 'echo root']})
    assert reply.status_code == 200 and native.last_cwd == root
    history = client.get(url + '/history').json()
    assert {item['working_directory'] for item in history} == {'', path_a, path_b}


def test_structure_document_and_browser_follow_same_scope_and_run_stays_bound(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    _, a, b, c = setup_sessions(client)
    structure = create_node(client, 'atomsculptor.structure')
    response = client.post('/api/edges', json={'source': agent['id'], 'target': structure['id'],
        'relationship': 'atomsculptor.structure.modify'})
    assert response.status_code == 201, response.text
    url = f"/api/nodes/{structure['id']}/state"
    def read(session):
        return client.get(url, headers={'X-OAW-State-Session': session.id}).json()['value']
    def write(session, value):
        response = client.put(url, headers={'X-OAW-State-Session': session.id}, json={'value': value})
        assert response.status_code == 200, response.text
    write(a, {'view': 'session A'})
    assert read(a) == {'view': 'session A'} and read(b) == {}
    run = new_run(services, agent, session=a)
    with in_run(run):
        handle = services.card_state.bind(structure['id'])
        assert handle.get()['value'] == {'view': 'session A'}
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    d = services.conversations.create_session(a.conversation_id, ConversationSessionCreate(title='Group first', group_id=a.group_id))
    e = services.conversations.create_session(a.conversation_id, ConversationSessionCreate(title='Group second', group_id=a.group_id))
    write(d, {'view': 'group'})
    assert read(d) == read(e) == {'view': 'group'} and read(c) == {}
    assert read(a) == {'view': 'session A'}
    with in_run(run), state_session(b.id):
        assert services.card_state.bind(structure['id']).get()['value'] == {'view': 'session A'}
    # Returning to session mode restores existing state, with no copy or merge.
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'session'}})
    assert read(a) == {'view': 'session A'} and read(b) == {}
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    with state_session(d.id):
        stale = services.card_state.bind(structure['id'])
    services.conversations.delete_group(a.conversation_id, a.group_id)
    with pytest.raises(ResourceValidationError, match='group no longer exists'):
        stale.get()


def test_old_scope_constraints_migrate_without_losing_rows_or_revisions(client, tmp_path):
    from backend.tests.test_card_state import sessions, headers
    board = create_node(client, 'oaw.tasks')
    room, a, _ = sessions(client)
    result = client.post(f"/api/nodes/{board['id']}/actions/upsert", headers=headers(a),
        json={'arguments': {'tasks': [{'id': 'one', 'title': 'Keep this'}]}, 'expected_revision': 0})
    assert result.status_code == 200, result.text
    path = tmp_path / 'old.db'
    clone = sqlite3.connect(path)
    with client.app.state.services.database.locked() as source:
        source.backup(clone)
    # Simulate the previous installed schema on a disposable copy only.
    clone.execute('PRAGMA writable_schema=ON')
    for table in ('cards', 'card_state_instances'):
        sql = clone.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
        sql = sql.replace(", 'group'", '').replace(",'group'", '')
        clone.execute('UPDATE sqlite_master SET sql=? WHERE name=?', (sql, table))
    clone.commit()
    clone.close()
    before = sqlite3.connect(path)
    tables = ['cards', 'edges', 'conversation_sessions', 'conversation_groups', 'card_state_instances', 'state_scopes', 'state_values']
    saved = {table: before.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall() for table in tables}
    before.close()
    migrated = Database(path)
    try:
        with migrated.locked() as db:
            for table in tables:
                assert [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2')] == saved[table]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            assert db.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            db.execute("UPDATE cards SET state_scope='group' WHERE id=?", (board['id'],))
            db.execute("UPDATE card_state_instances SET scope_type='group',scope_id=? WHERE card_id=?", (a['group_id'], board['id']))
            scope_id = db.execute('SELECT state_scope_id FROM card_state_instances WHERE card_id=?', (board['id'],)).fetchone()[0]
            db.execute('DELETE FROM conversation_sessions WHERE group_id=?', (a['group_id'],))
            assert db.execute('SELECT 1 FROM state_scopes WHERE scope_id=?', (scope_id,)).fetchone()
            db.execute('DELETE FROM conversation_groups WHERE id=?', (a['group_id'],))
            assert not db.execute('SELECT 1 FROM state_scopes WHERE scope_id=?', (scope_id,)).fetchone()
    finally:
        migrated.close()


def test_shared_structure_state_is_preserved_when_new_sessions_are_isolated(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'shared'}})
    structure = create_node(client, 'atomsculptor.structure')
    client.post('/api/edges', json={'source': agent['id'], 'target': structure['id'], 'relationship': 'atomsculptor.structure.modify'})
    room, a, _, _ = setup_sessions(client)
    with state_session(a.id):
        handle = services.card_state.bind(structure['id'])
        handle.set({'saved_structure': 'keep'})
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'session'}})
    new = services.conversations.create_session(room['id'], ConversationSessionCreate(title='Isolated'))
    with state_session(new.id):
        assert services.card_state.bind(structure['id']).get()['value'] == {}
    with state_session(a.id):
        assert services.card_state.bind(structure['id']).get()['value'] == {'saved_structure': 'keep'}


def test_group_execution_recovery_and_shutdown_use_native_group_members(client, monkeypatch):
    from dataclasses import replace
    from backend.errors import ConflictError
    from backend.plugins.state import ScopedStateSpec
    services = client.app.state.services
    node_type = services.plugins.node_type('oaw.tasks')
    monkeypatch.setitem(services.plugins._nodes, 'oaw.tasks', replace(node_type,
        state=ScopedStateSpec(supported_scopes=('group',), default_scope='group')))
    board = create_node(client, 'oaw.tasks')
    _, a, b, c = setup_sessions(client)
    engine = services.node_execution
    for session in (a, c):
        with state_session(session.id):
            engine.save(board['id'], {'status': 'running', 'attempts': [], 'error': None})
    recovered = []
    async def reconcile(node_id):
        recovered.append(services.card_state.identity(node_id))
    monkeypatch.setattr(engine, 'reconcile', reconcile)
    client.portal.call(engine._restore_node, services.world.get_card(board['id']))
    assert set(recovered) == {('group', a.group_id), ('group', c.group_id)}
    with state_session(a.id):
        key = engine.worker_key(board['id'])
    with state_session(b.id):
        assert engine.worker_key(board['id']) == key
    engine.workers[key] = object()
    try:
        with pytest.raises(ConflictError):
            engine.assert_session_idle(b.id)
        async def stop(node_id):
            assert engine.worker_key(node_id) == key
            engine.workers.pop(key)
        monkeypatch.setattr(engine, 'stop', stop)
        client.portal.call(engine.shutdown)
        assert not engine.workers
    finally:
        engine.workers.clear()


def test_structure_template_seeds_the_new_group_after_connections_are_restored(runtime_client):
    from backend.node_documents import read_document, write_document
    from oaw_atomsculptor.structure import StructureDocument
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    room, a, _, _ = setup_sessions(client)
    structure = create_node(client, 'atomsculptor.structure')
    client.post('/api/edges', json={'source': agent['id'], 'target': structure['id'], 'relationship': 'atomsculptor.structure.modify'})
    client.patch(f"/api/nodes/{sandbox['id']}", json={'config': {'workspace_scope': 'group'}})
    with state_session(a.id):
        current = read_document(services, structure['id'])
        value = StructureDocument(source_name='Copied group structure').model_dump(mode='json')
        write_document(services, structure['id'], value, current['revision'])
    headers = {'X-OAW-State-Session': a.id}
    template = client.post('/api/legions', headers=headers, json={'name': 'Scoped model',
        'node_ids': [room['id'], agent['id'], sandbox['id'], structure['id']]})
    assert template.status_code == 201, template.text
    instance = client.post(f"/api/legions/{template.json()['id']}/instances", headers=headers, json={'as_group': True})
    assert instance.status_code == 201, instance.text
    copied = next(node for node in instance.json()['nodes'] if node['type'] == 'atomsculptor.structure')
    assert client.get(f"/api/nodes/{copied['id']}/document").json()['value']['source_name'] == 'Copied group structure'
    namespaces = services.card_state.existing(copied['id'])
    assert len(namespaces) == 1 and namespaces[0][0] == 'group' and namespaces[0][1] != a.group_id


def test_group_batch_without_browser_header_captures_a_native_session(client, monkeypatch):
    import asyncio
    from dataclasses import replace
    from backend.plugins.state import ScopedStateSpec
    from backend.tests.test_node_execution import setup_board, start
    services = client.app.state.services
    definition = services.plugins.node_type('oaw.tasks')
    monkeypatch.setitem(services.plugins._nodes, 'oaw.tasks', replace(definition,
        state=ScopedStateSpec(supported_scopes=('group',), default_scope='group')))
    room = create_node(client, 'conversation')
    session = next(row for row in services.conversations.list_sessions(room['id']) if row.is_default)
    _, url, agent, _ = setup_board(client)
    response = start(client, url)
    assert response.status_code == 200, response.text
    async def finish():
        workers = list(services.node_execution.workers.values())
        if workers:
            await asyncio.wait_for(asyncio.gather(*workers), 5)
    client.portal.call(finish)
    runs = services.run_manager.list_runs(agent_id=agent['id'])
    assert runs and all(run.context_id == session.id for run in runs)
