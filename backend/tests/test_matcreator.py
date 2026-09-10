import json
import os
import pytest
from backend.tests.conftest import create_node
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError

def action(client, node, name, args):
    current = client.get(f"/api/nodes/{node['id']}/document").json()
    return client.post(f"/api/nodes/{node['id']}/actions/{name}", json={"arguments": args, "expected_revision": current['revision']})


@pytest.fixture
def summoning_client(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app

    settings = replace(Settings.for_data_root(tmp_path / "world"), agent_runtime="core.mock")
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize("relationship", ["matcreator.kdg.use", "matcreator.kdg.learn", "matcreator.kdg.curate"])
def test_summoned_agent_keeps_shared_graph_and_sandbox_connections(summoning_client, relationship):
    from backend.tests.test_summoning import stock, invoke, settle

    client = summoning_client
    box = create_node(client, "oaw.barracks")
    agent = create_node(client, "agent")
    graph = create_node(client, "matcreator.kdg")
    sandbox = create_node(client, "sandbox")
    stock(client, box, agent)
    for target, relation in [(graph, relationship), (sandbox, "execute")]:
        response = client.post("/api/edges", json={"source": agent["id"],
            "target": target["id"], "relationship": relation})
        assert response.status_code == 201, response.text
    services = client.app.state.services
    original_caps = {(c.kind, c.target_id) for c in services.capabilities.derive(agent["id"]).capabilities}
    instance = settle(client, box, invoke(client, box, action="summon", agent_id=agent["id"], prompt="Inspect knowledge"))
    assert instance["status"] == "succeeded"
    summoned = instance["entry_agent_id"]
    assert instance["node_ids"] == [summoned]
    assert {(c.kind, c.target_id) for c in services.capabilities.derive(summoned).capabilities} == original_caps
    edges = [e for e in services.world.list_edges() if e.source == summoned]
    assert {(e.target, e.relationship) for e in edges} == {(graph["id"], relationship), (sandbox["id"], "execute")}
    provider = WorldAgentCapabilityProvider(services)
    result = client.portal.call(provider.invoke_tool, summoned, "operation:knowledge_search", {"knowledge": graph["id"]})
    assert result["value"]["nodes"] == []
    graph_edge = next(e for e in edges if e.target == graph["id"])
    assert client.delete(f"/api/edges/{graph_edge.id}").status_code == 200
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, summoned, "operation:knowledge_search", {"knowledge": graph["id"]})
    invoke(client, box, action="reclaim", instance_id=instance["id"])
    for shared in [graph, sandbox]:
        assert client.get(f"/api/nodes/{shared['id']}").status_code == 200
    assert {(c.kind, c.target_id) for c in services.capabilities.derive(agent["id"]).capabilities} == original_caps

def test_palette_assimilation_has_no_temporary_source_and_preview_is_read_only(client):
    target = create_node(client, 'matcreator.kdg')
    before = client.get('/api/world').json()['nodes']
    original = client.get(f"/api/nodes/{target['id']}/document").json()
    url = f"/api/nodes/{target['id']}/transformations/assimilate"
    request = {'source_type': 'matcreator.core', 'expected_revision': original['revision']}
    preview = client.post(url, json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['consumed_ids'] == []
    assert client.get('/api/world').json()['nodes'] == before
    assert client.get(f"/api/nodes/{target['id']}/document").json() == original
    committed = client.post(url, json={**request, 'confirm': True})
    assert committed.status_code == 200, committed.text
    nodes = client.get('/api/world').json()['nodes']
    assert not any(node['type'] == 'matcreator.core' for node in nodes)
    assert any(node.get('parent_id') == target['id'] for node in nodes)
    graph = client.get(f"/api/nodes/{target['id']}/document").json()['value']
    assert next(iter(graph['snapshots'].values()))['provenance']['node_id'] is None
    assert client.post(url, json={**request, 'confirm': True}).status_code == 409

def test_assimilation_preserves_resources_and_consumes_only_on_commit(client):
    source = create_node(client, "matcreator.core")
    target = create_node(client, "matcreator.kdg")
    original = client.get(f"/api/nodes/{source['id']}/document").json()
    destination = client.get(f"/api/nodes/{target['id']}/document").json()
    request = {"source_id": source['id'], "source_revision": original['revision'], "expected_revision": destination['revision']}
    url = f"/api/nodes/{target['id']}/transformations/assimilate"
    preview = client.post(url, json=request)
    assert preview.status_code == 200, preview.text
    assert client.get(f"/api/nodes/{source['id']}/document").status_code == 200
    response = client.post(url, json={**request, "confirm": True})
    assert response.status_code == 200, response.text
    assert response.json()['committed']
    assert client.get(f"/api/nodes/{source['id']}/document").status_code == 404
    graph = client.get(f"/api/nodes/{target['id']}/document").json()['value']
    snapshot = next(iter(graph['snapshots'].values()))
    assert snapshot['package']['package_id'] == 'matcreator.core'
    assert snapshot['provenance']['node_id'] == source['id']
    assert [s['files'] for s in graph['skills']] == [s['files'] for s in original['value']['skills']]
    search = action(client, target, 'search', {'query': 'structure', 'limit': 2}).json()
    assert len(search['value']['nodes']) == 2
    assert 'snapshots' not in search['value']
    inspected = action(client, target, 'inspect', {'entry_id': search['value']['nodes'][0]['id']}).json()
    assert inspected['value']['resources'][0]['skill_node_id']

def test_assimilation_rollback(client, monkeypatch):
    source = create_node(client, 'matcreator.core')
    target = create_node(client, 'matcreator.kdg')
    source_before = client.get(f"/api/nodes/{source['id']}/document").json()
    before = client.get(f"/api/nodes/{target['id']}/document").json()
    def fail(ids):
        raise ValueError('injected failure after target write')
    monkeypatch.setattr(client.app.state.services.world, 'delete_cards', fail)
    with pytest.raises(ValueError, match='injected failure'):
        client.post(f"/api/nodes/{target['id']}/transformations/assimilate", json={
            'source_id': source['id'], 'source_revision': source_before['revision'],
            'expected_revision': before['revision'], 'confirm': True})
    assert client.get(f"/api/nodes/{source['id']}/document").json() == source_before
    assert client.get(f"/api/nodes/{target['id']}/document").json() == before

def test_nested_toolsets_and_live_direct_access(client):
    source = create_node(client, 'matcreator.ai')
    package = client.get(f"/api/nodes/{source['id']}/document").json()['value']
    family = next(s for s in package['skills'] if s['id'] == 'mattergen')
    assert 'mattergen-finetune/scripts/build_cif_property_csv.py' in family['files']
    agent = create_node(client, 'agent')
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': source['id'], 'relationship': 'matcreator.ai.use'}).json()
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tool = next(t for t in client.portal.call(provider.list_tools, agent['id']) if t.name == 'read_skills')
    result = client.portal.call(provider.invoke_tool, agent['id'], tool.capability_id, {'toolbox': source['id']})
    assert any(s['name'] == family['name'] for s in result['skills'])
    client.delete('/api/edges/' + edge['id'])
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], tool.capability_id, {'toolbox': source['id']})

def test_graph_validation_and_review(client):
    target = create_node(client, 'matcreator.kdg')
    assert action(client, target, 'edit', {'title': 'Li diffusion', 'type': 'procedure'}).status_code == 200
    entry = action(client, target, 'search', {}).json()['value']['nodes'][0]
    assert action(client, target, 'connect', {'source': entry['id'], 'target': 'missing', 'relation': 'dependency'}).status_code == 422
    assert action(client, target, 'save_memory', {'title': 'Diffusion trial', 'content': 'Observed stable conversion', 'session_id': 'trial', 'source_ids': [entry['id']], 'success': True}).status_code == 200
    queue = action(client, target, 'search', {'review': True}).json()['value']['nodes']
    assert len(queue) == 1
    assert action(client, target, 'distill', {'memory_ids': [queue[0]['id']], 'title': 'Check frame count', 'content': 'Verify every output frame', 'evidence': 'Trial output inspected'}).status_code == 200
    assert action(client, target, 'search', {'review': True}).json()['value']['nodes'] == []
    assert len(action(client, target, 'expand', {'ids': [entry['id']]}).json()['value']['nodes']) == 2


def test_graph_displays_its_workspace_and_curates_imported_entries_locally(client):
    graph = create_node(client, 'matcreator.kdg')
    definition = next(item for item in client.get('/api/catalog').json()['node_types'] if item['id'] == graph['type'])
    assert definition['container']['member_display'] == 'workspace'
    current = client.get(f"/api/nodes/{graph['id']}/document").json()
    imported = client.post(f"/api/nodes/{graph['id']}/transformations/assimilate", json={
        'source_type': 'matcreator.core', 'expected_revision': current['revision'], 'confirm': True})
    assert imported.status_code == 200, imported.text
    before = client.get(f"/api/nodes/{graph['id']}/document").json()['value']
    entry = before['entries'][0]
    updated = action(client, graph, 'edit', {'entry_id': entry['id'], 'title': 'Local notes', 'content': 'Reviewed for this graph', 'type': entry['type']})
    assert updated.status_code == 200, updated.text
    inspected = action(client, graph, 'inspect', {'entry_id': entry['id']}).json()['value']
    assert inspected['entry']['id'] == entry['id']
    assert inspected['entry']['owner'] == 'user'
    assert inspected['entry']['provenance'] == entry['provenance']
    assert inspected['entry']['resources'] == entry['resources']
    assert inspected['resources']
    changed = client.get(f"/api/nodes/{graph['id']}/document").json()['value']
    assert changed['snapshots'] == before['snapshots']
    assert changed['skills'] == before['skills']
    # Both edited and untouched imported entries can leave this graph.
    for selected in [entry, before['entries'][1]]:
        assert action(client, graph, 'delete_entry', {'entry_id': selected['id']}).status_code == 200
        assert action(client, graph, 'inspect', {'entry_id': selected['id']}).status_code == 422
    after = client.get(f"/api/nodes/{graph['id']}/document").json()['value']
    assert after['snapshots'] == before['snapshots']
    assert after['skills'] == before['skills']
    removed = {entry['id'], before['entries'][1]['id']}
    assert not any(edge['source'] in removed or edge['target'] in removed for edge in after['edges'])
    assert action(client, graph, 'replace', {**after, 'snapshots': {}}).status_code == 422

def test_large_progressive_graph_and_scoped_capabilities(client):
    graph = create_node(client, 'matcreator.kdg')
    payload = {'entries': [{'id': f'e{i}', 'title': f'Knowledge {i}', 'type': 'capability'} for i in range(1200)],
               'edges': [{'source': 'e0', 'target': f'e{i}', 'relation': 'dependency'} for i in range(1, 500)]}
    assert action(client, graph, 'replace', payload).status_code == 200
    first = action(client, graph, 'search', {'limit': 100}).json()['value']
    assert len(first['nodes']) == 100 and first['next_offset'] == 100 and first['total'] == 1200
    second = action(client, graph, 'expand', {'ids': ['e0'], 'offset': 100, 'limit': 100}).json()['value']
    assert 100 <= len(second['nodes']) <= 101
    assert any(e['id'] == 'e0' for e in second['nodes'])
    assert second['edges']
    agent = create_node(client, 'agent')
    response = client.post('/api/edges', json={'source': agent['id'], 'target': graph['id'], 'relationship': 'matcreator.kdg.use'})
    assert response.status_code == 201
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    names = {t.name for t in client.portal.call(provider.list_tools, agent['id'])}
    assert 'knowledge_search' in names and 'knowledge_edit' not in names and 'run_skill_script' not in names
    with pytest.raises(PermissionDeniedError):
        client.portal.call(provider.invoke_tool, agent['id'], 'operation:knowledge_save_memory', {'knowledge': graph['id'], 'title': 'x', 'content': 'x', 'session_id': 'x', 'expected_revision': 1})

def test_snapshots_immutable_and_source_revision_checked(client):
    source = create_node(client, 'matcreator.core')
    graph = create_node(client, 'matcreator.kdg')
    original = client.get(f"/api/nodes/{source['id']}/document").json()
    request = {'source_id': source['id'], 'source_revision': original['revision'], 'expected_revision': 1, 'confirm': True}
    url = f"/api/nodes/{graph['id']}/transformations/assimilate"
    assert client.post(url, json={**request, 'source_revision': 0}).status_code == 409
    assert client.post(url, json=request).status_code == 200
    value = client.get(f"/api/nodes/{graph['id']}/document").json()['value']
    assert action(client, graph, 'replace', {**value, 'snapshots': {}}).status_code == 422
    duplicate = create_node(client, 'matcreator.core')
    duplicate_doc = client.get(f"/api/nodes/{duplicate['id']}/document").json()
    current = client.get(f"/api/nodes/{graph['id']}/document").json()
    response = client.post(url, json={**request, 'source_id': duplicate['id'], 'source_revision': duplicate_doc['revision'], 'expected_revision': current['revision']})
    assert response.status_code == 422
    assert client.get(f"/api/nodes/{duplicate['id']}/document").status_code == 200

@pytest.mark.parametrize('package', ['core', 'simulation', 'ai', 'research'])
def test_all_published_packages_assimilate(client, package):
    source = create_node(client, 'matcreator.' + package)
    graph = create_node(client, 'matcreator.kdg')
    original = client.get(f"/api/nodes/{source['id']}/document").json()
    destination = client.get(f"/api/nodes/{graph['id']}/document").json()
    response = client.post(f"/api/nodes/{graph['id']}/transformations/assimilate", json={'source_id': source['id'],
        'source_revision': original['revision'], 'expected_revision': destination['revision'], 'confirm': True})
    assert response.status_code == 200, response.text
    result = client.get(f"/api/nodes/{graph['id']}/document").json()['value']
    assert len(result['skills']) == len(original['value']['skills'])
    if package == 'research':
        assert any('assets/bohrium-jobs.zip' in s['files'] for s in result['skills'])

@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_PYTHON'), reason='requires an explicitly provisioned native Python with ASE')
def test_native_ase_before_after_assimilation_and_artifact(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    settings = replace(Settings.for_data_root(tmp_path / 'world'), agent_runtime='core.mock', sandbox_runtime=os.environ.get('OAW_TEST_SANDBOX_RUNTIME', 'wsl:Ubuntu'))
    with TestClient(create_app(settings)) as client:
        agent = create_node(client, 'agent')
        source = create_node(client, 'matcreator.core')
        graph = create_node(client, 'matcreator.kdg')
        workspace = tmp_path / 'scientific-workspace'
        workspace.mkdir()
        sandbox = create_node(client, 'sandbox', config={'workspace_path': str(workspace.resolve()), 'workspace_access': 'read_write'})
        collection = create_node(client, 'core.artifact-collection')
        environment = create_node(client, 'environment')
        configured = action(client, environment, 'replace', {'variables': {'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}})
        assert configured.status_code == 200, configured.text
        def link(target, relationship):
            response = client.post('/api/edges', json={'source': agent['id'], 'target': target['id'], 'relationship': relationship})
            assert response.status_code == 201, response.text
            return response.json()
        link(source, 'matcreator.core.use')
        knowledge_edge = link(graph, 'matcreator.kdg.use')
        link(collection, 'artifact.manage')
        link(environment, 'environment.use')
        provider = WorldAgentCapabilityProvider(client.app.state.services)
        def invoke(name, args):
            return client.portal.call(provider.invoke_tool, agent['id'], 'operation:' + name, args)
        initial = client.get(f"/api/nodes/{source['id']}/document").json()
        skill = next(s for s in initial['value']['skills'] if s['id'] == 'local-structure')
        with pytest.raises(PermissionDeniedError):
            invoke('run_skill_script', {'sandbox': sandbox['id'], 'skill': skill['node_id'], 'script': 'scripts/build_structure.py'})
        execution_edge = link(sandbox, 'execute')
        started = client.post(f"/api/sandboxes/{sandbox['id']}/start")
        assert started.status_code == 200, started.text
        from pathlib import Path
        info = client.get(f"/api/sandboxes/{sandbox['id']}").json()
        interpreter = os.environ['OAW_MATCREATOR_PYTHON']
        extra = []
        if settings.sandbox_runtime.startswith('wsl:'):
            import subprocess
            provisioner = Path(__file__).resolve().parents[2] / 'plugins/matcreator/provision_science.py'
            drive_path = provisioner.as_posix()
            wsl_path = '/mnt/' + drive_path[0].lower() + drive_path[2:]
            drive_workspace = workspace.resolve().as_posix()
            wsl_workspace = '/mnt/' + drive_workspace[0].lower() + drive_workspace[2:]
            subprocess.run(['wsl.exe', '-d', settings.sandbox_runtime.split(':', 1)[1], '--', 'python3', wsl_path,
                '--destination', wsl_workspace + '/python-libs'], check=True)
            extra = ['--library-path', 'python-libs']
        def execute(skill_id, repeat):
            result = invoke('run_skill_script', {'sandbox': sandbox['id'], 'skill': skill_id, 'script': 'scripts/build_structure.py',
                'interpreter': [str(interpreter)], 'environment': environment['id'], 'argv': ['--repeat', str(repeat), '--output', 'copper' + str(repeat), *extra]})
            assert result['exit_code'] == 0, result['stdout'] + result['stderr']
            assert json.loads(result['stdout'])['atoms'] == 4 * repeat**3
            version = invoke('publish_artifact', {'collection': collection['id'], 'sandbox': sandbox['id'],
                'paths': [f'copper{repeat}.cif', f'copper{repeat}.extxyz', f'copper{repeat}.json'],
                'finalized': True, 'request_key': f'copper-{repeat}', 'name': f'Copper {4 * repeat**3}'})
            assert len(version['manifest']) == 3
            return version
        execute(skill['node_id'], 2)
        target = client.get(f"/api/nodes/{graph['id']}/document").json()
        response = client.post(f"/api/nodes/{graph['id']}/transformations/assimilate", json={'source_id': source['id'],
            'source_revision': initial['revision'], 'expected_revision': target['revision'], 'confirm': True})
        assert response.status_code == 200, response.text
        hits = invoke('knowledge_search', {'knowledge': graph['id'], 'query': 'copper'})
        selected = next(e for e in hits['value']['nodes'] if e['title'] == 'Local structure demo')
        detail = invoke('knowledge_inspect', {'knowledge': graph['id'], 'entry_id': selected['id']})
        resource_id = detail['value']['resources'][0]['skill_node_id']
        version = execute(resource_id, 3)
        client.delete('/api/edges/' + knowledge_edge['id'])
        with pytest.raises(PermissionDeniedError):
            execute(resource_id, 3)
        base = f"/api/artifact-collections/{collection['id']}/versions/{version['version_id']}"
        report = client.get(base + '/content', params={'path': 'copper3.json'}).json()
        assert report['atoms'] == 108 and report['roundtrip_verified']
        assert client.delete(f"/api/nodes/{sandbox['id']}").status_code == 200
