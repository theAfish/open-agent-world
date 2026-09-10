import base64

import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.conversations.models import ConversationSessionCreate
from backend.errors import PermissionDeniedError
from backend.runs import InvocationCaller, InvocationContext
from backend.runs.manager import _current_invocation
from backend.tests.test_conversations import _create, _connect
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def room(client):
    node = _create(client, 'conversation', 'Files')
    session = client.get(f"/api/conversations/{node['id']}").json()['sessions'][0]
    return node, session, f"/api/conversations/{node['id']}/sessions/{session['id']}"


def upload(client, base, name='report.txt', data=b'hello'):
    result = client.post(base + '/attachments', params={'filename': name}, content=data,
                         headers={'Content-Type': 'application/octet-stream'})
    assert result.status_code == 201, result.text
    return result.json()


def ref(file):
    return {key: file[key] for key in ('version_id', 'path')}


def test_general_admits_existing_and_new_connections_once(client):
    node, session, base = room(client)
    first = _create(client, 'agent', 'First')
    second = _create(client, 'agent', 'Second')
    edge = _connect(client, first['id'], node['id'])
    _connect(client, second['id'], node['id'])
    services = client.app.state.services
    assert set(services.conversations.get_session(node['id'], session['id']).participant_ids) == {first['id'], second['id']}
    assert client.delete(base + '/participants/' + first['id']).status_code == 200
    summary = client.get('/api/conversations/' + node['id']).json()
    assert summary['sessions'][0]['participant_ids'] == [second['id']]
    assert client.delete('/api/edges/' + edge['id']).status_code == 200
    _connect(client, first['id'], node['id'])
    assert first['id'] in services.conversations.get_session(node['id'], session['id']).participant_ids
    # Simulate an existing connection that predates the admission migration.
    with services.conversations.database.transaction() as db:
        db.execute('DELETE FROM conversation_default_admissions')
        db.execute('DELETE FROM conversation_participants WHERE session_id=?', (session['id'],))
    assert len(client.get('/api/conversations/' + node['id']).json()['sessions'][0]['participant_ids']) == 2


def test_upload_send_download_scope_and_limits(client):
    node, session, base = room(client)
    file = upload(client, base, '数据.bin', bytes(range(256)))
    posted = client.post(base + '/messages', json={'attachments': [ref(file)]})
    assert posted.status_code == 202, posted.text
    assert posted.json()['message']['content'] == ''
    assert posted.json()['message']['attachments'] == [file]
    assert client.get(base + '/timeline').json()['items'][0]['attachments'] == [file]
    url = base + '/attachments/' + file['version_id']
    response = client.get(url, params={'path': file['path'], 'preview': 'true'})
    assert response.content == bytes(range(256))
    assert response.headers['content-disposition'].startswith('attachment;')
    other = client.post(f"/api/conversations/{node['id']}/sessions", json={'title': 'Other'}).json()
    other_base = f"/api/conversations/{node['id']}/sessions/{other['id']}"
    assert client.post(other_base + '/messages', json={'attachments': [ref(file)]}).status_code == 403
    assert client.get(other_base + '/attachments/' + file['version_id'], params={'path': file['path']}).status_code == 403
    assert client.post(base + '/messages', json={}).status_code == 422
    assert client.post(base + '/attachments', params={'filename': '../escape'}, content=b'x').status_code == 422
    services = client.app.state.services
    services.resources.artifacts.max_version_bytes = 2
    assert client.post(base + '/attachments', params={'filename': 'large.txt'}, content=b'123').status_code == 422
    failed = services.resources.artifacts.all()[-1]
    assert failed['state'] == 'failed' and failed['cleanup'] == 'complete'


def test_image_preview_and_active_content_download(client):
    _, _, base = room(client)
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1cAAAAASUVORK5CYII=')
    image = upload(client, base, 'result.png', png)
    response = client.get(base + '/attachments/' + image['version_id'], params={'path': image['path'], 'preview': 'true'})
    assert response.content == png
    assert response.headers['content-type'] == 'image/png'
    svg = upload(client, base, 'unsafe.svg', b'<svg onload="alert(1)"/>')
    response = client.get(base + '/attachments/' + svg['version_id'], params={'path': svg['path'], 'preview': 'true'})
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')


def test_attachment_and_admission_survive_restart(data_root):
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        node, session, base = room(client)
        agent = _create(client, 'agent', 'Member')
        _connect(client, agent['id'], node['id'])
        attachment = upload(client, base, 'saved.txt', b'persistent bytes')
        assert client.post(base + '/messages', json={'attachments': [ref(attachment)]}).status_code == 202
        assert client.delete(base + '/participants/' + agent['id']).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/conversations/' + node['id']).json()['sessions'][0]['participant_ids'] == []
        assert client.get(base + '/timeline').json()['items'][0]['attachments'] == [attachment]
        assert client.get(base + '/attachments/' + attachment['version_id'], params={'path': attachment['path']}).content == b'persistent bytes'


def test_agent_attachment_tools_publish_read_materialize_and_revocation(runtime_client):
    client, backend, _ = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client)
    node, session, base = room(client)
    edge = _connect(client, agent['id'], node['id'])
    file = upload(client, base)
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    workspace = client.portal.call(backend.get, sandbox['id']).workspace
    (workspace / 'result.png').write_bytes(b'image result')

    async def invoke(name, args, session_id=session['id']):
        token = _current_invocation.set(InvocationContext(run_id='attachment-test', agent_id=agent['id'],
            parent_run_id=None, root_run_id='attachment-test', caller=InvocationCaller('conversation', node['id']),
            context_id=session_id, task_id=None, runtime_provider_id='core.mock'))
        try:
            return await provider.invoke_tool(agent['id'], 'operation:' + name, args)
        finally:
            _current_invocation.reset(token)

    args = {'collection': node['id']}
    assert client.portal.call(invoke, 'inspect_artifacts', args) == []  # Unsent user draft.
    assert client.post(base + '/messages', json={'attachments': [ref(file)]}).status_code == 202
    inspected = client.portal.call(invoke, 'inspect_artifacts', {**args, **ref(file)})
    assert inspected['text'] == 'hello'
    client.portal.call(invoke, 'materialize_artifact', {**args, 'version_id': file['version_id'],
        'sandbox': sandbox['id'], 'destination': 'received'})
    assert (workspace / 'received/report.txt').read_bytes() == b'hello'
    published = client.portal.call(invoke, 'publish_artifact', {**args, 'sandbox': sandbox['id'],
        'paths': ['result.png'], 'name': 'Result', 'finalized': True, 'request_key': 'result'})
    sent = client.portal.call(invoke, 'send_conversation_message', {'conversation': node['id'],
        'attachments': [{'version_id': published['version_id'], 'path': 'result.png'}]})
    assert sent['sender_kind'] == 'agent' and sent['attachments'][0]['media_type'] == 'image/png'
    assert client.get(base + '/timeline').json()['items'][-1]['attachments'] == sent['attachments']
    other = client.portal.call(services.create_conversation_session, node['id'],
        ConversationSessionCreate(title='Private', participant_ids=[agent['id']]))
    with pytest.raises(PermissionDeniedError):
        client.portal.call(invoke, 'inspect_artifacts', {**args, **ref(file)}, other.id)
    assert client.delete(base + '/participants/' + agent['id']).status_code == 200
    with pytest.raises(PermissionDeniedError):
        client.portal.call(invoke, 'inspect_artifacts', args)
    assert client.post(base + '/participants', json={'participant_ids': [agent['id']]}).status_code == 200
    assert client.delete('/api/edges/' + edge['id']).status_code == 200
    with pytest.raises(PermissionDeniedError):
        client.portal.call(invoke, 'send_conversation_message', {'conversation': node['id'], 'content': 'late'})
