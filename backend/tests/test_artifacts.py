"""Combined resource/capability/lifecycle tests. Native process execution is mocked."""
import asyncio
import hashlib
from uuid import uuid4

import pytest

from backend.tests.test_skill_runtime import runtime_client, setup_skill
from backend.tests.conftest import create_node
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ConflictError
from backend.resources.artifact_models import ArtifactPublish
import os
from backend.sandbox.models import SandboxValidationError, SandboxSecurityError


def setup(runtime_client):
    client, backend, _ = runtime_client
    agent, sandbox, _, _, _ = setup_skill(client)
    collection = create_node(client, 'core.artifact-collection')
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': collection['id'], 'relationship': 'artifact.manage'})
    assert edge.status_code == 201, edge.text
    workspace = client.portal.call(backend.get, sandbox['id']).workspace
    return client, backend, agent, sandbox, collection, workspace, edge.json()


def publish(client, sandbox, collection, paths, **options):
    request = {'sandbox_id': sandbox['id'], 'paths': paths, 'finalized': True, 'name': 'Result', 'request_key': str(uuid4()), **options}
    return client.post(f"/api/artifact-collections/{collection['id']}/versions", json=request)


def test_collection_accepts_palette_status_on_create_and_update(client):
    collection = create_node(client, 'core.artifact-collection', status='available', config={})
    assert collection['status'] == 'available'
    assert collection['config']['status'] == 'available'

    response = client.patch(f"/api/nodes/{collection['id']}", json={'status': 'available'})
    assert response.status_code == 200, response.text
    assert response.json()['config']['status'] == 'available'


def test_large_binary_bundle_immutable_reclamation_and_reference_retention(runtime_client):
    client, backend, agent, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'bundle').mkdir()
    content = bytes(range(256)) * (17 * 4096)
    (workspace / 'bundle/data.bin').write_bytes(content)
    (workspace / 'bundle/empty').mkdir()
    (workspace / 'bundle/report.txt').write_text('original', encoding='utf-8')
    response = publish(client, sandbox, collection, ['bundle'])
    assert response.status_code == 200, response.text
    version = response.json()
    assert version['state'] == 'ready'
    assert next(e for e in version['manifest'] if e['path'] == 'bundle/data.bin')['sha256'] == hashlib.sha256(content).hexdigest()
    assert len(response.content) < 4000
    (workspace / 'bundle/data.bin').write_bytes(b'edited')
    base = f"/api/artifact-collections/{collection['id']}/versions/{version['version_id']}"
    assert client.get(base + '/content', params={'path': 'bundle/data.bin'}).content == content
    assert client.get(base + '/preview', params={'path': 'bundle/data.bin'}).json()['state'] == 'binary'
    assert client.delete(f"/api/nodes/{agent['id']}").status_code == 200
    assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200
    other = create_node(client, 'sandbox')
    assert client.post(f"/api/sandboxes/{other['id']}/start").status_code == 200
    copied = client.post(base + '/materialize', json={'sandbox_id': other['id'], 'destination': 'copy'})
    assert copied.status_code == 200, copied.text
    destination = client.portal.call(backend.get, other['id']).workspace
    assert (destination / 'copy/bundle/data.bin').read_bytes() == content
    assert (destination / 'copy/bundle/empty').is_dir()
    assert client.delete(f"/api/artifact-collections/{collection['id']}/references/{version['version_id']}").json()['retained']
    assert client.get('/api/artifacts/retained').json()[0]['state'] == 'ready'
    assert client.put(f"/api/artifact-collections/{collection['id']}/references/{version['version_id']}").status_code == 200
    assert client.delete(base).json()['state'] == 'deleted'
    assert client.delete(base).json()['state'] == 'deleted'
    assert (destination / 'copy/bundle/data.bin').read_bytes() == content


def test_idempotency_limits_and_permissions(runtime_client):
    client, _, agent, sandbox, collection, workspace, edge = setup(runtime_client)
    (workspace / 'file').write_bytes(b'hello')
    first = publish(client, sandbox, collection, ['file'], request_key='once').json()
    again = publish(client, sandbox, collection, ['file'], request_key='once').json()
    assert first == again
    assert publish(client, sandbox, collection, ['file'], request_key='once', name='changed').status_code == 409
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    actual = client.portal.call(provider.invoke_tool, agent['id'], 'operation:inspect_artifacts', {'collection': collection['id']})
    assert actual[0]['version_id'] == first['version_id']
    assert client.delete('/api/edges/' + edge['id']).status_code == 200
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], 'operation:inspect_artifacts', {'collection': collection['id'], 'version_id': first['version_id']})
    services.resources.artifacts.max_version_bytes = 4
    failed = publish(client, sandbox, collection, ['file'])
    assert failed.status_code == 422, failed.text
    assert all(r['state'] != 'staging' for r in services.resources.artifacts.all())


def test_collection_management_cannot_release_shared_user_retention(runtime_client):
    client, _, agent, sandbox, collection, workspace, _ = setup(runtime_client)
    other = create_node(client, 'core.artifact-collection')
    (workspace / 'file').write_bytes(b'shared retained bytes')
    version = publish(client, sandbox, collection, ['file']).json()['version_id']
    assert client.put(f"/api/artifact-collections/{other['id']}/references/{version}").status_code == 200
    services = client.app.state.services
    store = services.resources.artifacts
    provider = WorldAgentCapabilityProvider(services)
    with pytest.raises(PermissionDeniedError, match='trusted user'):
        client.portal.call(store.release, services, collection['id'], version, agent['id'])
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], 'operation:manage_artifact_references',
            {'collection': other['id'], 'version_id': version})
    removed = client.portal.call(provider.invoke_tool, agent['id'], 'operation:manage_artifact_references',
        {'collection': collection['id'], 'version_id': version})
    assert removed == {'removed': True, 'retained': True}
    assert store.listing(services, collection['id']) == []
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], 'operation:manage_artifact_references',
            {'collection': collection['id'], 'version_id': version, 'action': 'add', 'source_collection_id': other['id']})
    assert client.post('/api/edges', json={'source': agent['id'], 'target': other['id'], 'relationship': 'artifact.read'}).status_code == 201
    added = client.portal.call(provider.invoke_tool, agent['id'], 'operation:manage_artifact_references',
        {'collection': collection['id'], 'version_id': version, 'action': 'add', 'source_collection_id': other['id']})
    assert added['version_id'] == version
    base = f"/api/artifact-collections/{other['id']}/versions/{version}"
    assert client.get(base + '/content', params={'path': 'file'}).content == b'shared retained bytes'
    assert client.post(base + '/materialize', json={'sandbox_id': sandbox['id'], 'destination': 'shared-copy'}).status_code == 200
    assert (workspace / 'shared-copy/file').read_bytes() == b'shared retained bytes'
    assert client.delete(base).json()['state'] == 'deleted'
    assert not store.path(version).exists()
    assert client.get('/api/artifacts/retained').json() == []
    assert client.get('/api/artifacts/history').json()[0]['state'] == 'deleted'


def test_retained_listing_excludes_inactive_and_historical_states(runtime_client):
    client, _, _, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'file').write_bytes(b'data')
    record = publish(client, sandbox, collection, ['file']).json()
    store = client.app.state.services.resources.artifacts
    for state, retained in [('staging', True), ('failed', True), ('deleting', False),
                            ('deleted', False), ('ready', False), ('ready', True)]:
        record.update(state=state)
        record['retention']['retained'] = retained
        store.save(record)
        assert len(client.get('/api/artifacts/retained').json()) == int(state == 'ready' and retained)
        assert len(client.get('/api/artifacts/history').json()) == 1


def test_explicit_integrity_verification_distinguishes_unavailability(runtime_client, monkeypatch):
    client, _, _, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'file').write_bytes(b'hello')
    record = publish(client, sandbox, collection, ['file']).json()
    store = client.app.state.services.resources.artifacts
    endpoint = f"/api/artifact-collections/{collection['id']}/versions/{record['version_id']}/verify"
    assert client.post(endpoint).json()['integrity'] == 'verified'
    original = store.validate
    def unavailable(record):
        raise PermissionError('temporary storage access failure')
    monkeypatch.setattr(store, 'validate', unavailable)
    assert client.post(endpoint).json()['integrity'] == 'unavailable'
    assert store.get(record['version_id']) == record
    monkeypatch.setattr(store, 'validate', original)
    (store.path(record['version_id']) / 'file').write_bytes(b'wrong')
    assert client.post(endpoint).json()['integrity'] == 'corrupt'
    assert store.get(record['version_id']) == record


def test_new_version_and_input_provenance_are_independent_of_retention(runtime_client):
    client, _, _, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'file').write_text('first', encoding='utf-8')
    first = publish(client, sandbox, collection, ['file']).json()
    (workspace / 'file').write_text('second', encoding='utf-8')
    response = publish(client, sandbox, collection, ['file'], artifact_id=first['artifact_id'],
        inputs=[{'collection_id': collection['id'], 'version_id': first['version_id']}])
    assert response.status_code == 200, response.text
    second = response.json()
    assert second['artifact_id'] == first['artifact_id'] and second['version_id'] != first['version_id']
    assert second['provenance']['inputs'][0]['content_sha256'] == first['content_sha256']
    base = f"/api/artifact-collections/{collection['id']}/versions/"
    assert client.delete(base + first['version_id']).json()['state'] == 'deleted'
    assert client.get(base + second['version_id'] + '/content', params={'path': 'file'}).content == b'second'


def test_scoped_publication_and_materialization(runtime_client):
    client, backend, agent, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'file').write_bytes(b'\0exact')
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    version = client.portal.call(provider.invoke_tool, agent['id'], 'operation:publish_artifact',
        {'collection': collection['id'], 'sandbox': sandbox['id'], 'paths': ['file'], 'finalized': True, 'name': 'exact', 'request_key': 'key'})
    copied = client.portal.call(provider.invoke_tool, agent['id'], 'operation:materialize_artifact',
        {'collection': collection['id'], 'sandbox': sandbox['id'], 'version_id': version['version_id'], 'destination': 'copy'})
    assert copied['state'] == 'copied'
    assert (workspace / 'copy/file').read_bytes() == b'\0exact'


def test_publication_pins_source_and_detects_revocation(runtime_client, monkeypatch):
    client, backend, agent, sandbox, collection, workspace, edge = setup(runtime_client)
    (workspace / 'file').write_bytes(b'hello')
    services = client.app.state.services
    original = backend.file_operation
    async def transfer(node, operation, **options):
        if operation == 'read_chunk':
            with pytest.raises(ConflictError):
                await services.delete_card(node)
            services.world.delete_edge(edge['id'])
        return await original(node, operation, **options)
    monkeypatch.setattr(backend, 'file_operation', transfer)
    provider = WorldAgentCapabilityProvider(services)
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], 'operation:publish_artifact',
            {'collection': collection['id'], 'sandbox': sandbox['id'], 'paths': ['file'], 'finalized': True, 'name': 'exact', 'request_key': 'key'})
    assert services.resources.artifacts.all()[0]['state'] == 'failed'
    assert not services.resources.artifacts.source_leases


def test_source_modification_and_active_reader_block_delete(runtime_client, monkeypatch):
    client, backend, _, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'file').write_bytes(b'hello')
    original = backend.file_operation
    async def modify(node, operation, **options):
        if operation == 'read_chunk':
            (workspace / 'file').write_bytes(b'changed')
        return await original(node, operation, **options)
    monkeypatch.setattr(backend, 'file_operation', modify)
    assert publish(client, sandbox, collection, ['file']).status_code == 422
    monkeypatch.setattr(backend, 'file_operation', original)
    version = publish(client, sandbox, collection, ['file']).json()
    services = client.app.state.services
    store = services.resources.artifacts
    async def consume_and_delete():
        async with store.consume(services, collection['id'], version['version_id']):
            with pytest.raises(ConflictError):
                await store.release(services, collection['id'], version['version_id'])
        assert (await store.release(services, collection['id'], version['version_id']))['state'] == 'deleted'
    client.portal.call(consume_and_delete)


def test_concurrent_idempotency_release_failure_and_live_read_revocation(runtime_client, monkeypatch):
    client, backend, agent, sandbox, collection, workspace, edge = setup(runtime_client)
    (workspace / 'file').write_bytes(b'X' * (512 * 1024))
    services = client.app.state.services
    store = services.resources.artifacts
    original = backend.file_operation
    async def scenario():
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def paused(node, operation, **options):
            if operation == 'read_chunk':
                entered.set()
                await proceed.wait()
            return await original(node, operation, **options)
        monkeypatch.setattr(backend, 'file_operation', paused)
        request = ArtifactPublish(sandbox_id=sandbox['id'], paths=['file'], finalized=True, name='one', request_key='same')
        pending = asyncio.create_task(store.publish(services, collection['id'], request, agent['id']))
        await entered.wait()
        with pytest.raises(ConflictError, match='progress'):
            await store.publish(services, collection['id'], request, agent['id'])
        proceed.set()
        version = await pending
        assert len(store.all()) == 1
        reader = store.read(services, collection['id'], version['version_id'], 'file', agent['id'])
        assert len(await anext(reader)) == 256 * 1024
        services.world.delete_edge(edge['id'])
        with pytest.raises(PermissionDeniedError):
            await anext(reader)
        await reader.aclose()
        original_remove = store.remove_bytes
        async def denied(record):
            raise PermissionError('injected storage removal failure')
        monkeypatch.setattr(store, 'remove_bytes', denied)
        with pytest.raises(PermissionError):
            await store.release(services, collection['id'], version['version_id'])
        assert store.get(version['version_id'])['cleanup'] == 'failed'
        monkeypatch.setattr(store, 'remove_bytes', original_remove)
        await store.recover(interrupted=False)
        assert store.get(version['version_id'])['state'] == 'deleted'
    client.portal.call(scenario)


@pytest.mark.parametrize('path', ['../secret', '/absolute', 'C:/secret', 'a\\b', 'a/../b', 'a//b', 'file:stream', 'a.', 'NUL'])
def test_artifact_transfer_rejects_adversarial_paths(runtime_client, path):
    client, _, _, sandbox, collection, workspace, _ = setup(runtime_client)
    response = publish(client, sandbox, collection, [path])
    assert response.status_code == 422, response.text
    assert all(r['state'] == 'failed' for r in client.app.state.services.resources.artifacts.all())


def test_artifact_transfer_rejects_hardlinks(runtime_client):
    client, _, _, sandbox, collection, workspace, _ = setup(runtime_client)
    (workspace / 'original').write_bytes(b'cannot freeze a live hardlink')
    os.link(workspace / 'original', workspace / 'link')
    assert publish(client, sandbox, collection, ['link']).status_code == 503


def test_stop_failure_closes_admission_and_survives_history_rollover(runtime_client, monkeypatch):
    client, backend, _, sandbox, _, _, _ = setup(runtime_client)
    original = backend.terminate
    async def denied(node):
        raise SandboxSecurityError('injected process termination failure')
    monkeypatch.setattr(backend, 'terminate', denied)
    response = client.post(f"/api/sandboxes/{sandbox['id']}/stop")
    assert response.status_code == 503
    debt = client.get('/api/lifecycle').json()['commands'][0]
    assert debt['cleanup'] == 'failed' and not debt['termination_confirmed']
    response = client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={'argv': ['cmd.exe']})
    assert response.status_code == 409
    monkeypatch.setattr(backend, 'terminate', original)
    assert client.post('/api/lifecycle/retry-cleanup').status_code == 200
    assert not client.get('/api/lifecycle').json()['commands']


def test_summoned_run_publishes_then_reclaims_private_workspace(runtime_client):
    from backend.tests.test_runs import RecordingProvider
    from backend.tests.test_summoning import stock, equip, invoke, settle
    from backend.agents import AgentEvent, AgentEventType
    client, backend, native = runtime_client
    services = client.app.state.services
    collection = create_node(client, 'core.artifact-collection')
    library = create_node(client, 'oaw.barracks')
    shared = create_node(client, 'text', content='Shared source')
    agent = create_node(client, 'agent')
    sandbox = create_node(client, 'sandbox')
    equip(client, sandbox, agent)
    stock(client, library, agent)
    for node, relationship in [(collection, 'artifact.publish'), (shared, 'read')]:
        response = client.post('/api/edges', json={'source': agent['id'], 'target': node['id'], 'relationship': relationship})
        assert response.status_code == 201, response.text
    original = native.run_appcontainer
    def generate(profile, argv, **options):
        (options['cwd'] / 'published.bin').write_bytes(b'\0summoned output\xff')
        return original(profile, argv, **options)
    native.run_appcontainer = generate
    class PublishingProvider(RecordingProvider):
        async def execute(self, config, context, runtime_input):
            capabilities = services.capabilities.derive(context.agent_id).capabilities
            target = next(c.target_id for c in capabilities if c.kind == 'sandbox.execute')
            await services.start_sandbox(target)
            await services.execute_sandbox(target, ['generate-output'], agent_id=context.agent_id)
            provider = WorldAgentCapabilityProvider(services)
            await provider.invoke_tool(context.agent_id, 'operation:publish_artifact', {
                'collection': collection['id'], 'sandbox': target, 'paths': ['published.bin'], 'name': 'Run output',
                'finalized': True, 'request_key': context.run_id})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {'text': 'Published'}, run_status='succeeded')
    services.install_runtime_provider('core.mock', PublishingProvider())
    instance = settle(client, library, invoke(client, library, action='summon', agent_id=agent['id'], prompt='Generate and publish'))
    assert instance['status'] == 'succeeded', instance
    version = services.resources.artifacts.listing(services, collection['id'])[0]
    assert instance['artifacts'][0]['version_id'] == version['version_id']
    assert version['provenance']['run_id'] == instance['run_id']
    private_sandbox = next(services.world.get_card(key) for key in instance['node_ids'] if services.world.get_card(key).type == 'sandbox')
    private_path = client.portal.call(backend.get, private_sandbox.id).workspace
    reclaimed = invoke(client, library, action='reclaim', instance_id=instance['id'])
    assert reclaimed['reclaimed'] and not private_path.exists()
    assert invoke(client, library, action='reclaim', instance_id=instance['id'])['reclaimed']
    assert services.world.get_card(shared['id']) and services.world.get_card(collection['id'])
    reader = create_node(client, 'agent')
    client.post('/api/edges', json={'source': reader['id'], 'target': collection['id'], 'relationship': 'artifact.read'})
    async def consume():
        return b''.join([chunk async for chunk in services.resources.artifacts.read(services, collection['id'], version['version_id'], 'published.bin', reader['id'])])
    assert client.portal.call(consume) == b'\0summoned output\xff'
    assert client.get('/api/lifecycle').json()['runs'][-1]['artifacts'][0]['version_id'] == version['version_id']


@pytest.mark.skipif(not os.environ.get('OAW_TEST_SANDBOX_RUNTIME'), reason='requires selected native runtime')
def test_real_sandbox_publication_reclamation_and_consumption(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    runtime = os.environ['OAW_TEST_SANDBOX_RUNTIME']
    settings = replace(Settings.for_data_root(tmp_path / 'native'), agent_runtime='core.mock', sandbox_runtime=runtime)
    with TestClient(create_app(settings)) as client:
        sandbox = create_node(client, 'sandbox')
        collection = create_node(client, 'core.artifact-collection')
        started = client.post(f"/api/sandboxes/{sandbox['id']}/start")
        assert started.status_code == 200, started.text
        command = ['cmd.exe', '/d', '/c', 'echo retained-result>result.txt'] if runtime == 'windows' else ['/bin/sh', '-c', 'printf retained-result > result.txt']
        result = client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={'argv': command})
        assert result.status_code == 200 and result.json()['exit_code'] == 0, result.text
        response = publish(client, sandbox, collection, ['result.txt'])
        assert response.status_code == 200, response.text
        version = response.json()
        assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200
        target = create_node(client, 'sandbox')
        assert client.post(f"/api/sandboxes/{target['id']}/start").status_code == 200
        base = f"/api/artifact-collections/{collection['id']}/versions/{version['version_id']}"
        copied = client.post(base + '/materialize', json={'sandbox_id': target['id'], 'destination': 'copy'})
        assert copied.status_code == 200, copied.text
        argv = ['cmd.exe', '/d', '/c', 'type copy\\result.txt'] if runtime == 'windows' else ['cat', 'copy/result.txt']
        result = client.post(f"/api/sandboxes/{target['id']}/execute", json={'argv': argv})
        assert result.status_code == 200 and 'retained-result' in result.json()['stdout'], result.text

