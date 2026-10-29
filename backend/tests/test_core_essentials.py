"""Core consolidation preserves saved content and exact ownership boundaries."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.card_library import KEY, CardLibraryStore, LibraryEdit
from backend.config import Settings
from backend.legions.validation import compatibility_issues
from backend.main import create_app
from backend.migrations.core_essentials import CORE, CORE_PACK, CONTRIBUTIONS, RETIRED_PACKS
from backend.persistence.database import Database
from backend.plugins.builtin import create_builtin_registry
from backend.tests.conftest import create_node


def test_default_catalog_has_core_utilities_and_no_codex(client):
    catalog = client.get('/api/catalog').json()
    owners = {card['id']: card['plugin_id'] for card in catalog['node_types']}
    for contribution in CONTRIBUTIONS.values():
        assert all(owners[node] == CORE for node in contribution['node_type'])
    core = next(pack for pack in catalog['packs'] if pack['id'] == CORE_PACK)
    assert {'oaw.barracks', 'oaw.barracks.summoner', 'oaw.skills', 'oaw.skills.skill', 'oaw.tasks'} <= set(core['cards'])
    assert not RETIRED_PACKS.keys() & {pack['id'] for pack in catalog['packs']}
    assert 'openai.codex.agent' not in owners
    assert not client.app.state.services.plugins.has_runtime_provider('openai.codex')
    # The absorbed utilities cannot be disabled or removed independently.
    state = client.get('/api/card-library').json()
    for owner in CONTRIBUTIONS:
        assert client.delete(f'/api/packs/{owner}', headers={'X-OAW-Pack-Install': '1'}).status_code == 422
        assert client.post('/api/card-library/actions', json={
            'expected_revision': state['revision'], 'action': 'set_plugin_enabled',
            'id': owner, 'enabled': False}).status_code == 404
    assert create_node(client, 'oaw.barracks')['missing_plugin'] is None


def test_core_only_restart_migrates_world_and_saved_legion_without_changing_documents(tmp_path, monkeypatch):
    monkeypatch.setenv('OPEN_AGENT_WORLD_CORE_ONLY', '1')
    settings = Settings.for_data_root(tmp_path)
    with TestClient(create_app(settings)) as client:
        board = create_node(client, 'oaw.tasks')
        agent = create_node(client, 'agent')
        edge = client.post('/api/edges', json={'source': agent['id'], 'target': board['id'], 'relationship': 'oaw.tasks.edit'}).json()
        url = f"/api/nodes/{board['id']}/document"
        value = client.get(url).json()
        saved = client.post('/api/legions', json={'name': 'Existing plan', 'node_ids': [board['id'], agent['id']]}).json()
    with sqlite3.connect(settings.database_path) as db:
        db.execute('UPDATE cards SET plugin_id=? WHERE id=?', ('oaw.tasks', board['id']))
        db.execute('UPDATE edges SET plugin_id=? WHERE id=?', ('oaw.tasks', edge['id']))
        payload = json.loads(db.execute('SELECT blueprint_json FROM legions WHERE id=?', (saved['id'],)).fetchone()[0])
        next(node for node in payload['nodes'] if node['type'] == 'oaw.tasks')['plugin_id'] = 'oaw.tasks'
        payload['edges'][0]['plugin_id'] = 'oaw.tasks'
        db.execute('UPDATE legions SET blueprint_json=? WHERE id=?', (json.dumps(payload), saved['id']))
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.get(f"/api/nodes/{board['id']}").json()['missing_plugin'] is None
            assert client.get(url).json() == value
            record = client.app.state.services.legions.get(saved['id'])
            assert compatibility_issues(record, client.app.state.services.plugins) == []
            assert client.post(f"/api/legions/{saved['id']}/instances", json={'as_group': True}).status_code == 201


def test_library_migrates_collected_utilities_without_moving_deck_entries(tmp_path):
    db = Database(tmp_path / 'world.db')
    try:
        store = CardLibraryStore(db, create_builtin_registry())
        state = store.edit(LibraryEdit(expected_revision=store.read().revision, action='open_pack', id=CORE_PACK))
        state = store.edit(LibraryEdit(expected_revision=state.revision, action='update_deck', id='starter', entries=[{'id': 'oaw.tasks'}]))
        payload = state.model_dump(mode='json')
        payload['card_definitions']['oaw.tasks']['plugin_id'] = 'oaw.tasks'
        payload['collection']['oaw.tasks'].update(plugin_id='oaw.tasks', source_pack_ids=['oaw.tasks.default'])
        with db.transaction(immediate=True) as connection:
            connection.execute('UPDATE application_settings SET value_json=? WHERE key=?', (json.dumps(payload), KEY))
        migrated = store.read()
        assert migrated.collection == state.collection
        assert migrated.decks == state.decks
        assert migrated.active_deck_id == state.active_deck_id
        assert store.read().revision == migrated.revision
    finally:
        db.close()


@pytest.mark.parametrize('finalizer,expected_owner', [(0, CORE), (1, 'oaw.tasks')])
def test_pending_utility_deletion_retains_unknown_finalizers(client, tmp_path, finalizer, expected_owner):
    card = create_node(client, 'oaw.tasks')
    path = tmp_path / 'journal.db'
    db = Database(path)
    with db.transaction(immediate=True) as connection:
        connection.execute('''INSERT INTO pending_node_deletions
            (node_id,batch_id,sequence,plugin_id,plugin_version,plugin_api_version,
             requires_finalize,card_json,edges_json,created_at)
            VALUES (?,?,0,'oaw.tasks','0.2.1','1.23',?,?,'[]','2026-01-01')''',
            (card['id'], 'interrupted-delete', finalizer, json.dumps(card)))
    db.close()
    reopened = Database(path)
    try:
        with reopened.locked() as connection:
            row = connection.execute('SELECT * FROM pending_node_deletions').fetchone()
        assert row['plugin_id'] == expected_owner
        assert row['requires_finalize'] == finalizer
        assert json.loads(row['card_json']) == card
        assert row['plugin_version'] == '0.2.1'
    finally:
        reopened.close()


@pytest.mark.parametrize('old_owner,card_type', [('third.party', 'oaw.tasks'), ('oaw.tasks', 'third.party.card')])
def test_core_migration_does_not_launder_unrelated_template_ownership(old_owner, card_type):
    from backend.legions.models import LegionTemplateNode
    node = LegionTemplateNode(key='task', type=card_type, plugin_id=old_owner, name='Task',
        position={'x': 0, 'y': 0}, size={'width': 360, 'height': 235}, expanded=False,
        status='available', config={})
    assert node.plugin_id == old_owner


@pytest.mark.parametrize('version,accepted', [
    ('==0.2.1', True), ('>=0.3', False),
])
def test_legacy_content_pack_dependencies_remain_bounded(client, tmp_path, version, accepted):
    from backend.packs.content import CreatorRequest, export_archive, prepare_export
    from backend.packs.installation import PackInstallationManager
    from backend.plugins.loader import load_installed_packs
    board = create_node(client, 'oaw.tasks')
    agent = create_node(client, 'agent')
    response = client.post('/api/legions', json={'name': 'Plan', 'node_ids': [board['id'], agent['id']]})
    assert response.status_code == 201, response.text
    saved = response.json()
    services = client.app.state.services
    record = services.legions.get(saved['id'])
    request = CreatorRequest(legion_id=record.id, id='local.old-plan', name='Old plan', creator={})
    result, files = prepare_export(record, request, services.plugins)
    assert result['can_export'], result
    manifest = json.loads(files['manifest.json'])
    manifest['dependencies']['packs'].append({'id': 'oaw.tasks.default', 'version': version})
    files['manifest.json'] = json.dumps(manifest).encode()
    template = json.loads(files['content/legion.json'])
    next(node for node in template['blueprint']['nodes'] if node['type'] == 'oaw.tasks')['plugin_id'] = 'oaw.tasks'
    files['content/legion.json'] = json.dumps(template).encode()
    artifact = export_archive(files)
    registry = create_builtin_registry()
    manager = PackInstallationManager(tmp_path, registry)
    if not accepted:
        with pytest.raises(ValueError, match='incompatible|required|provided'):
            manager.install(artifact)
        assert manager.selected() == {}
        return
    assert manager.plan(artifact)['blockers'] == []
    manager.install(artifact)
    load_installed_packs(registry, tmp_path, strict=True)
    assert registry.has_plugin('local.old-plan')
    assert (manager.version_path('local.old-plan', '0.1.0') / 'manifest.json').read_bytes() == files['manifest.json']


def test_legacy_dependency_cannot_be_claimed_by_another_distribution(tmp_path):
    from backend.packs.archive import inspect_archive
    from backend.packs.installation import PackInstallationManager
    from backend.tests.test_pack_installation import artifact
    from backend.tests.test_pack_unification import unified
    files = dict(inspect_archive(unified()).files)
    files.pop('checksums.json')
    manifest = json.loads(files['manifest.json'])
    manifest['dependencies']['packs'] = [{'id': 'oaw.tasks.default', 'version': '==0.2.1', 'distribution': 'other.publisher'}]
    files['manifest.json'] = json.dumps(manifest).encode()
    data = artifact(files=files)
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    assert manager.plan(data)['dependencies'][0]['state'] == 'incompatible'
    with pytest.raises(ValueError, match='must be provided by'):
        manager.install(data)
    assert manager.selected() == {}
