from contextlib import aclosing
from dataclasses import replace

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.conversations.models import ConversationSessionCreate
from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError
from backend.runs import InvocationCaller, InvocationContext, RunStore
from backend.runs.manager import _current_invocation
from backend.runs.models import RunStatus
from backend.tests.test_conversation_attachments import room, upload, ref
from backend.tests.test_conversations import _create, _connect


@pytest.fixture
def files(client):
    conversation, session, base = room(client)
    agent = _create(client, 'agent', 'File worker')
    edge = _connect(client, agent['id'], conversation['id'])
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    run = RunStore(services.database).create(agent_id=agent['id'], run_id='runtime-files',
        runtime_provider_id='core.mock', caller_kind='conversation', caller_id=conversation['id'], context_id=session['id'])
    services.run_manager.store.update_status(run.run_id, RunStatus.RUNNING)
    context = InvocationContext(run_id=run.run_id, agent_id=agent['id'], parent_run_id=None,
        root_run_id=run.run_id, caller=InvocationCaller('conversation', conversation['id']),
        context_id=session['id'], task_id=None, runtime_provider_id='core.mock')

    async def invoke(method, *args, current=context, agent_id=agent['id'], run_id=run.run_id, **kwargs):
        token = _current_invocation.set(current)
        try:
            return await getattr(provider, method)(agent_id, run_id, *args, **kwargs)
        finally:
            _current_invocation.reset(token)

    def call(method, *args, **kwargs):
        return client.portal.call(lambda: invoke(method, *args, **kwargs))

    def send(attachment, session_id=session['id']):
        return services.conversations.add_message(conversation['id'], session_id,
            sender_kind='user', sender_id=None, sender_name='You', content='Read this file', attachments=[attachment])

    # The test uses real retained bytes and normal session visibility, without
    # starting an unrelated model execution to manufacture a delivery.
    from backend.conversations.models import ConversationAttachment
    data = b'%PDF-1.7\n\0binary attachment\xff\n'
    attachment = upload(client, base, 'paper.pdf', data)
    message = send(ConversationAttachment.model_validate(attachment))
    services.run_manager.store.update_lifecycle(run.run_id, conversation_max_sequence=message.sequence)
    return dict(client=client, services=services, provider=provider, conversation=conversation, session=session,
        base=base, agent=agent, edge=edge, run=run, context=context, invoke=invoke, call=call,
        send=send, attachment=attachment, data=data, message=message)


def test_runtime_lists_current_history_and_reads_verified_binary(files):
    state = files
    listing = state['call']('conversation_attachments')
    assert listing == [{**state['attachment'], 'message_id': state['message'].id,
        'sequence': state['message'].sequence, 'conversation_id': state['conversation']['id'],
        'session_id': state['session']['id']}]
    assert state['call']('read_conversation_attachment', **ref(state['attachment'])) == state['data']
    assert state['services'].resources.artifacts.consumers[state['attachment']['version_id']] == 0


def test_runtime_publishes_large_file_stream_with_verified_digest_and_reference(files):
    import hashlib
    state = files
    chunk = b'z' * (256 * 1024)
    digest = hashlib.sha256()
    for _ in range(132):
        digest.update(chunk)
    closed = []
    async def chunks():
        try:
            for _ in range(132):
                yield chunk
        finally:
            closed.append(True)
    result = state['call']('publish_conversation_attachment_stream', 'large-output.xlsx', chunks(),
        request_key='large-output', expected_sha256=digest.hexdigest())
    assert result['size_bytes'] == 33 * 1024 * 1024
    record = state['services'].resources.artifacts.get(result['version_id'])
    assert record['manifest'][0]['sha256'] == digest.hexdigest()
    assert closed and 'data' not in result


async def stage_attachment(state, directory, reference):
    from backend.agents.execution import NativeRuntimeExecutionProvider, RuntimeExecutionRequest
    execution = await NativeRuntimeExecutionProvider().open(RuntimeExecutionRequest(
        state['agent']['id'], state['run'].run_id, directory / 'project', directory / 'home'))
    token = _current_invocation.set(state['context'])
    try:
        stream = state['provider'].stream_conversation_attachment(
            state['agent']['id'], state['run'].run_id, **ref(reference))
        return await execution.write_file_stream('inputs/' + reference['name'], stream)
    finally:
        _current_invocation.reset(token)
        await execution.close()


def test_runtime_streams_large_retained_attachment_into_execution_workspace(files, tmp_path):
    from backend.conversations.models import ConversationAttachment
    state = files
    chunk = b'x' * (256 * 1024)
    response = state['client'].post(state['base'] + '/attachments', params={'filename': 'large.xlsx'},
        content=iter([chunk] * 132), headers={'Content-Type': 'application/octet-stream'})
    assert response.status_code == 201, response.text
    attachment = response.json()
    message = state['send'](ConversationAttachment.model_validate(attachment))
    state['services'].run_manager.store.update_lifecycle(state['run'].run_id, conversation_max_sequence=message.sequence)
    target = state['client'].portal.call(lambda: stage_attachment(state, tmp_path, attachment))
    from pathlib import Path
    assert Path(target).stat().st_size == 33 * 1024 * 1024
    assert state['services'].resources.artifacts.consumers[attachment['version_id']] == 0


@pytest.mark.parametrize('failure', ['revoked_at_eof', 'checksum', 'cancelled'])
def test_streamed_attachment_is_not_published_before_verified_eof(files, tmp_path, monkeypatch, failure):
    import asyncio
    from concurrent.futures import CancelledError
    state = files
    store = state['services'].resources.artifacts
    original = store.read

    async def read(*args, **kwargs):
        async with aclosing(original(*args, **kwargs)) as stream:
            async for chunk in stream:
                yield chunk
                if failure == 'cancelled':
                    raise asyncio.CancelledError
        if failure == 'revoked_at_eof':
            await state['services'].delete_edge(state['edge']['id'])

    if failure == 'checksum':
        path = store.path(state['attachment']['version_id']) / state['attachment']['path']
        path.write_bytes(b'x' * len(state['data']))
    monkeypatch.setattr(store, 'read', read)
    # BlockingPortal exposes task cancellation through concurrent.futures.
    error = CancelledError if failure == 'cancelled' else PermissionDeniedError if failure == 'revoked_at_eof' else ResourceValidationError
    with pytest.raises(error):
        state['client'].portal.call(lambda: stage_attachment(state, tmp_path, state['attachment']))
    assert not list((tmp_path / 'project').rglob('*.*'))
    assert store.consumers[state['attachment']['version_id']] == 0


def test_runtime_history_boundary_excludes_later_attachments_and_tracks_resends(files):
    from backend.conversations.models import ConversationAttachment
    state = files
    later = upload(state['client'], state['base'], 'later.txt', b'later input')
    state['send'](ConversationAttachment.model_validate(later))
    assert [entry['name'] for entry in state['call']('conversation_attachments')] == ['paper.pdf']
    with pytest.raises(PermissionDeniedError):
        state['call']('read_conversation_attachment', **ref(later))
    repeated = state['send'](ConversationAttachment.model_validate(state['attachment']))
    state['services'].run_manager.store.update_lifecycle(state['run'].run_id, conversation_max_sequence=repeated.sequence)
    entry = next(item for item in state['call']('conversation_attachments') if item['name'] == 'paper.pdf')
    assert entry['message_id'] == repeated.id and entry['sequence'] == repeated.sequence


def test_delegated_runtime_inherits_admitting_conversation_history_boundary(files):
    from backend.conversations.models import ConversationAttachment
    state = files
    later = upload(state['client'], state['base'], 'queued.txt', b'not admitted yet')
    state['send'](ConversationAttachment.model_validate(later))
    child = RunStore(state['services'].database).create(agent_id=state['agent']['id'],
        run_id='delegated-files', runtime_provider_id='core.mock', caller_kind='delegation',
        parent_run_id=state['run'].run_id, context_id=state['session']['id'])
    context = replace(state['context'], run_id=child.run_id, parent_run_id=state['run'].run_id,
        caller=InvocationCaller('delegation'))
    assert [entry['name'] for entry in state['call']('conversation_attachments', current=context, run_id=child.run_id)] == ['paper.pdf']
    with pytest.raises(PermissionDeniedError):
        state['call']('read_conversation_attachment', **ref(later), current=context, run_id=child.run_id)


@pytest.mark.parametrize('removed', ['reference', 'released'])
def test_unavailable_old_attachment_does_not_block_current_files(files, removed):
    from backend.conversations.models import ConversationAttachment
    state = files
    later = upload(state['client'], state['base'], 'current.txt', b'current input')
    message = state['send'](ConversationAttachment.model_validate(later))
    state['services'].run_manager.store.update_lifecycle(state['run'].run_id, conversation_max_sequence=message.sequence)
    store = state['services'].resources.artifacts
    if removed == 'reference':
        store.remove_reference(state['services'], state['conversation']['id'], state['attachment']['version_id'])
    else:
        state['client'].portal.call(lambda: store.release(state['services'], state['conversation']['id'], state['attachment']['version_id']))
    assert [entry['name'] for entry in state['call']('conversation_attachments')] == ['current.txt']
    assert state['call']('read_conversation_attachment', **ref(later)) == b'current input'
    with pytest.raises(PermissionDeniedError):
        state['call']('read_conversation_attachment', **ref(state['attachment']))


@pytest.mark.parametrize('denial', ['unsent', 'other_session', 'removed_participant', 'revoked_connection', 'wrong_context', 'wrong_agent', 'wrong_run', 'ended_run'])
def test_runtime_file_reads_require_current_run_and_session_authority(files, denial):
    state = files
    reference, kwargs = ref(state['attachment']), {}
    if denial == 'unsent':
        reference = ref(upload(state['client'], state['base'], 'draft.txt', b'not sent'))
    elif denial == 'other_session':
        other = state['client'].portal.call(lambda: state['services'].create_conversation_session(
            state['conversation']['id'], ConversationSessionCreate(title='Other', participant_ids=[state['agent']['id']])))
        base = f"/api/conversations/{state['conversation']['id']}/sessions/{other.id}"
        attachment = upload(state['client'], base)
        from backend.conversations.models import ConversationAttachment
        state['send'](ConversationAttachment.model_validate(attachment), other.id)
        reference = ref(attachment)
    elif denial == 'removed_participant':
        assert state['client'].delete(state['base'] + '/participants/' + state['agent']['id']).status_code == 200
    elif denial == 'revoked_connection':
        assert state['client'].delete('/api/edges/' + state['edge']['id']).status_code == 200
    elif denial == 'wrong_context':
        kwargs['current'] = replace(state['context'], context_id='different-session')
    elif denial == 'wrong_agent':
        kwargs['agent_id'] = 'other-agent'
    elif denial == 'wrong_run':
        kwargs['run_id'] = 'other-run'
    else:
        state['services'].run_manager.store.update_status(state['run'].run_id, RunStatus.SUCCEEDED)
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        state['call']('read_conversation_attachment', **reference, **kwargs)


@pytest.mark.parametrize('failure', ['path', 'limit', 'invalid_limit', 'size', 'checksum'])
def test_runtime_reads_reject_bad_paths_limits_and_corrupt_artifacts(files, failure):
    state = files
    args = ref(state['attachment'])
    if failure == 'path':
        args['path'] = '../paper.pdf'
    elif failure in {'limit', 'invalid_limit'}:
        args['max_bytes'] = len(state['data']) - 1 if failure == 'limit' else True
    else:
        path = state['services'].resources.artifacts.path(args['version_id']) / args['path']
        path.write_bytes(state['data'][:-1] if failure == 'size' else b'x' * len(state['data']))
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        state['call']('read_conversation_attachment', **args)
    assert not state['services'].resources.artifacts.consumers.get(state['attachment']['version_id'])


@pytest.mark.parametrize('when', ['during_read', 'at_eof'])
def test_runtime_rechecks_revocation_through_read_completion(files, monkeypatch, when):
    state = files
    store = state['services'].resources.artifacts
    original = store.read

    async def revoke():
        await state['services'].delete_edge(state['edge']['id'])

    async def read(*args, **kwargs):
        async with aclosing(original(*args, **kwargs)) as stream:
            async for chunk in stream:
                if when == 'during_read':
                    await revoke()
                yield chunk
        if when == 'at_eof':
            await revoke()

    monkeypatch.setattr(store, 'read', read)
    with pytest.raises(PermissionDeniedError):
        state['call']('read_conversation_attachment', **ref(state['attachment']))
    assert store.consumers[state['attachment']['version_id']] == 0


def test_runtime_publishes_idempotent_attachment_then_delivers_through_existing_tool(files):
    state = files
    output = state['call']('publish_conversation_attachment', 'result.txt', b'finished result', request_key='result')
    assert state['call']('publish_conversation_attachment', 'result.txt', b'finished result', request_key='result') == output
    with pytest.raises(ConflictError):
        state['call']('publish_conversation_attachment', 'result.txt', b'changed result', request_key='result')
    record = state['services'].resources.artifacts.get(output['version_id'])
    assert record['provenance']['agent_id'] == state['agent']['id']
    assert record['provenance']['run_id'] == state['run'].run_id
    assert record['provenance']['session_id'] == state['session']['id']
    assert len(state['client'].get(state['base'] + '/timeline').json()['items']) == 1

    async def deliver():
        token = _current_invocation.set(state['context'])
        try:
            tools = await state['provider'].list_tools(state['agent']['id'])
            tool = next(tool for tool in tools if tool.name == 'send_conversation_message')
            return await state['provider'].invoke_tool(state['agent']['id'], tool.capability_id,
                {'conversation': output['conversation_id'], 'attachments': [ref(output)]})
        finally:
            _current_invocation.reset(token)

    sent = state['client'].portal.call(deliver)
    assert sent['attachments'][0]['version_id'] == output['version_id']
    response = state['client'].get(state['base'] + '/attachments/' + output['version_id'], params={'path': output['path']})
    assert response.content == b'finished result'


def test_runtime_empty_output_and_request_key_scope_are_preserved(files):
    state = files
    output = state['call']('publish_conversation_attachment', 'empty.txt', b'', request_key='empty')
    assert output['size_bytes'] == 0
    other = state['client'].portal.call(lambda: state['services'].create_conversation_session(
        state['conversation']['id'], ConversationSessionCreate(title='Other', participant_ids=[state['agent']['id']])))
    run = RunStore(state['services'].database).create(agent_id=state['agent']['id'], run_id='other-files',
        runtime_provider_id='core.mock', caller_kind='conversation', caller_id=state['conversation']['id'], context_id=other.id)
    context = replace(state['context'], run_id=run.run_id, context_id=other.id, root_run_id=run.run_id)
    with pytest.raises(ConflictError):
        state['call']('publish_conversation_attachment', 'empty.txt', b'', request_key='empty', current=context, run_id=run.run_id)


@pytest.mark.parametrize('failure', ['path', 'data', 'key', 'too_large', 'quota'])
def test_runtime_publication_validates_bounded_bytes_and_existing_storage_limits(files, failure):
    state = files
    name, data, key = 'result.bin', b'result', 'result'
    if failure == 'path':
        name = '../result.bin'
    elif failure == 'data':
        data = '/host/private/file'
    elif failure == 'key':
        key = ''
    elif failure == 'too_large':
        data = b'x' * (32 * 1024 * 1024 + 1)
    else:
        state['services'].resources.artifacts.max_version_bytes = 1
    with pytest.raises(ResourceValidationError):
        state['call']('publish_conversation_attachment', name, data, request_key=key)
    if failure == 'quota':
        record = state['services'].resources.artifacts.all()[-1]
        assert record['state'] == 'failed' and record['cleanup'] == 'complete'


@pytest.mark.parametrize('revoked', ['connection', 'participant'])
def test_runtime_publication_rechecks_authority_at_finalization(files, monkeypatch, revoked):
    import backend.resources.artifacts as artifacts
    state = files
    original = artifacts.run_file_operation

    async def operation(function, *args, **kwargs):
        result = await original(function, *args, **kwargs)
        if function.__name__ == 'rename':
            if revoked == 'connection':
                await state['services'].delete_edge(state['edge']['id'])
            else:
                await state['services'].remove_conversation_session_participant(
                    state['conversation']['id'], state['session']['id'], state['agent']['id'])
        return result

    monkeypatch.setattr(artifacts, 'run_file_operation', operation)
    with pytest.raises(PermissionDeniedError):
        state['call']('publish_conversation_attachment', 'result.bin', b'result', request_key='result')
    store = state['services'].resources.artifacts
    record = store.all()[-1]
    assert record['state'] == 'failed' and record['cleanup'] == 'complete'
    assert not store.path(record['version_id']).exists()


def test_cancelled_runtime_publication_cleans_uncommitted_files(files, monkeypatch):
    import asyncio
    import backend.resources.artifacts as artifacts
    state = files
    original = artifacts.run_file_operation

    async def operation(function, *args, **kwargs):
        result = await original(function, *args, **kwargs)
        if function.__name__ == 'rename':
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(artifacts, 'run_file_operation', operation)

    async def publish():
        with pytest.raises(asyncio.CancelledError):
            await state['invoke']('publish_conversation_attachment', 'cancelled.bin', b'partial', request_key='cancelled')

    state['client'].portal.call(publish)
    store = state['services'].resources.artifacts
    record = store.all()[-1]
    assert record['state'] == 'failed' and record['cleanup'] == 'complete'
    assert not store.path(record['version_id']).exists()
    assert not store.path(record['version_id'], staging=True).exists()


@pytest.mark.parametrize('invalid', ['scope', 'request_key', 'max_bytes', 'connection'])
def test_streamed_publication_closes_rejected_source_before_reading_or_spooling(files, monkeypatch, invalid):
    import backend.capabilities.provider as module
    state = files
    class UnreadSource:
        reads = 0
        closed = False
        def __aiter__(self):
            return self
        async def __anext__(self):
            self.reads += 1
            pytest.fail('Rejected publication must not consume file data')
        async def aclose(self):
            self.closed = True
    source = UnreadSource()
    def forbidden_spool(*args, **kwargs):
        pytest.fail('Rejected publication must not create a spool')
    monkeypatch.setattr(module, 'receive_file', forbidden_spool)
    arguments = {'request_key': 'rejected'}
    if invalid == 'scope':
        arguments['current'] = None
    elif invalid == 'request_key':
        arguments['request_key'] = ''
    elif invalid == 'max_bytes':
        arguments['max_bytes'] = 0
    else:
        state['client'].delete('/api/edges/' + state['edge']['id'])
    before = len(state['services'].resources.artifacts.all())
    error = PermissionDeniedError if invalid == 'connection' else ResourceValidationError
    with pytest.raises(error):
        state['call']('publish_conversation_attachment_stream', 'never-read.bin', source, **arguments)
    assert source.closed and source.reads == 0
    assert len(state['services'].resources.artifacts.all()) == before
