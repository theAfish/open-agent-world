import json

from oaw_xrd.preset import PRESET_ID


def test_exported_workbench_installs_and_opens_in_recipient_profile(client, tmp_path):
    from backend.legions.presets import preset_record
    from backend.packs.content import CreatorRequest, prepare_export, export_archive
    from backend.config import Settings
    from backend.main import create_app
    from fastapi.testclient import TestClient
    registry = client.app.state.services.plugins
    result, files = prepare_export(preset_record(PRESET_ID, registry),
        CreatorRequest(legion_id=PRESET_ID, id='research.xrdworkbench', name='XRD workbench'), registry)
    assert result['can_export'], result['issues']
    assert b'oaw:model:' not in files['content/legion.json']
    settings = Settings.for_data_root(tmp_path / 'recipient')
    with TestClient(create_app(settings)) as recipient:
        installed = recipient.post('/api/packs/install', content=export_archive(files),
            headers={'X-OAW-Pack-Install':'1', 'Content-Type':'application/vnd.oaw.pack'})
        assert installed.status_code == 201, installed.text
    with TestClient(create_app(settings)) as recipient:
        response = recipient.post('/api/legions/presets/research.xrdworkbench.legion/instances', json={})
        assert response.status_code == 201, response.text
        assert len(response.json()['nodes']) == 10


def test_workbench_ships_in_clean_profile_and_restores_isolated_graphs(client):
    templates = client.get('/api/legions/presets').json()
    template = next(t for t in templates if t['id'] == PRESET_ID)
    assert template['compatible'], template['issues']
    assert template['node_count'] == 10
    library = client.get('/api/card-library').json()
    assert library['preset_pack_ids'][PRESET_ID] == ['research.xrd.default']
    instances = []
    for _ in range(2):
        response = client.post(f'/api/legions/presets/{PRESET_ID}/instances', json={})
        assert response.status_code == 201, response.text
        graph = response.json()
        instances.append(graph)
        nodes = {n['type']: n for n in graph['nodes']}
        owner, db = nodes['xrd.match'], nodes['data.sqlite']
        assert nodes['xrd.spectrum-canvas']['config']['source_node_id'] == owner['id']
        assert nodes['xrd.structure-canvas']['config']['source_node_id'] == owner['id']
        assert any(e['source'] == owner['id'] and e['target'] == db['id'] and e['relationship'] == 'xrd.history-store' for e in graph['edges'])
        assert nodes['legion']['config']['workspace_layout']['version'] == 2
        assert owner['config']['workflow_match_run_id'] == ''
        doc = client.get(f"/api/nodes/{owner['id']}/document").json()['value']
        assert not doc['path'] and not doc['slots']
        assert 'oaw:model:' not in json.dumps(graph)
    assert {n['id'] for n in instances[0]['nodes']}.isdisjoint(n['id'] for n in instances[1]['nodes'])


def test_workbench_artwork_is_served(client):
    response = client.get('/api/plugins/research.xrd/assets/workbench-cover')
    assert response.status_code == 200
    assert 'image/svg+xml' in response.headers['content-type']
    assert b'>XRD</text>' in response.content
    assert response.content.count(b'<text ') == 1


def test_sql_template_copies_configuration_not_private_rows(client):
    from backend.tests.conftest import create_node
    from oaw_sqlite.engine import FILE_NAME
    import sqlite3
    db = create_node(client, 'data.sqlite')
    agent = create_node(client, 'agent')
    root = client.app.state.services.resources.node_storage_path(db['id'])
    with sqlite3.connect(root / FILE_NAME) as connection:
        connection.execute('CREATE TABLE private_data(secret TEXT)')
        connection.execute("INSERT INTO private_data VALUES ('do-not-export')")
    saved = client.post('/api/legions', json={'name':'Empty database template', 'node_ids':[db['id'],agent['id']]}).json()
    response = client.post(f"/api/legions/{saved['id']}/instances", json={})
    assert response.status_code == 201, response.text
    restored = next(n for n in response.json()['nodes'] if n['type'] == 'data.sqlite')
    fresh = client.app.state.services.resources.node_storage_path(restored['id'])
    with sqlite3.connect(fresh / FILE_NAME) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name='private_data'").fetchall()
