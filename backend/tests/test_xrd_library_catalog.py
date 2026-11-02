import json
import sqlite3
import asyncio
import os
import threading

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.hosting.policy import HostedPolicyMiddleware
from backend.main import create_app
from backend.tests.conftest import create_node


@pytest.fixture
def library_path(tmp_path):
    path = tmp_path / 'library.sq'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE id(id INTEGER, chemical_formula TEXT, dvalue TEXT, intensita TEXT)')
        db.execute("INSERT INTO id VALUES(1, 'Si', '3,', '1000,')")
        db.execute('CREATE TABLE infodb(date TEXT)')
    return path


def test_reconfirm_reuses_mount_and_only_changed_files_are_inspected(library_path, monkeypatch):
    import oaw_xrd as xrd
    from open_agent_world.plugin_api import ResourceValidationError
    monkeypatch.delenv('OAW_XRD_LIBRARY_CATALOG', raising=False)
    arguments = {'path': str(library_path), 'slot': 0}
    mounted = xrd.configure_library({}, arguments)
    inspected = []
    real_inspect = xrd.inspect_library

    def inspect(path):
        inspected.append(path)
        return real_inspect(path)

    monkeypatch.setattr(xrd, 'inspect_library', inspect)
    assert xrd.configure_library(mounted, arguments) == mounted
    assert inspected == []
    with pytest.raises(ResourceValidationError, match='其他槽位'):
        xrd.configure_library(mounted, {**arguments, 'slot': 1})
    assert inspected == []
    previous = library_path.stat()
    with sqlite3.connect(library_path) as db:
        db.execute("INSERT INTO id VALUES(2, 'O', '2,', '1000,')")
    os.utime(library_path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 2_000_000_000))
    updated = xrd.configure_library(mounted, arguments)
    assert len(inspected) == 1
    assert updated['count'] == 2 and updated['sha256'] != mounted['sha256']


def test_large_library_preparation_keeps_worker_responsive_and_rejects_stale_result(client, library_path, monkeypatch):
    import oaw_xrd as xrd
    from backend.errors import RevisionConflictError
    from backend.node_documents import DocumentActionRequest, invoke_document_action, read_document
    monkeypatch.delenv('OAW_XRD_LIBRARY_CATALOG', raising=False)
    node = create_node(client, 'xrd.match')
    services = client.app.state.services
    real_inspect = xrd.inspect_library

    async def scenario():
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        loop_thread = threading.get_ident()

        def inspect(path):
            assert threading.get_ident() != loop_thread
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), 'Library inspection did not finish'
            return real_inspect(path)

        monkeypatch.setattr(xrd, 'inspect_library', inspect)
        before = read_document(services, node['id'])
        request = DocumentActionRequest(expected_revision=before['revision'], arguments={
            'path': str(library_path), 'document': {'path': '/unapproved/file.sq'}})
        pending = asyncio.create_task(invoke_document_action(services, node['id'], 'configure', request))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            # A normal document edit must remain available while a large file is read.
            newer = await asyncio.wait_for(invoke_document_action(services, node['id'], 'replace',
                DocumentActionRequest(expected_revision=before['revision'], arguments=before['value'])), 2)
        finally:
            release.set()
        with pytest.raises(RevisionConflictError):
            await pending
        assert read_document(services, node['id']) == newer

    client.portal.call(scenario)


def test_hosted_catalog_mounts_only_approved_libraries_and_persists(tmp_path, monkeypatch):
    entries = {}
    for key in ('cod', 'pdf'):
        path = tmp_path / f'{key}.sq'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE id(id INTEGER, chemical_formula TEXT, dvalue TEXT, intensita TEXT)')
            db.execute("INSERT INTO id VALUES(1, 'Si', '3,', '1000,')")
            db.execute('CREATE TABLE infodb(date TEXT)')
        entries[key] = {'label': key.upper(), 'path': str(path)}
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps(entries), encoding='utf-8')
    monkeypatch.setenv('OAW_XRD_LIBRARY_CATALOG', str(catalog))
    settings = Settings.for_data_root(tmp_path / 'data')
    app = create_app(settings)
    app.add_middleware(HostedPolicyMiddleware)
    with TestClient(app) as client:
        node = create_node(client, 'xrd.match')
        endpoint = f"/api/nodes/{node['id']}"
        original = client.get(endpoint + '/document').json()
        response = client.post(endpoint + '/actions/catalog', json={'arguments': {}})
        assert response.status_code == 200, response.text
        assert [entry['id'] for entry in response.json()['value']['libraries']] == ['cod', 'pdf']
        assert client.get(endpoint + '/document').json() == original

        def mount(arguments, revision):
            return client.post(endpoint + '/actions/configure', json={
                'expected_revision': revision, 'arguments': arguments})

        first = mount({'library_id': 'cod', 'slot': 0, 'document': {'count': 999}}, original['revision'])
        assert first.status_code == 200, first.text
        assert first.json()['value']['count'] == 1
        second = mount({'library_id': 'pdf', 'slot': 1}, first.json()['revision'])
        assert second.status_code == 200, second.text
        stored = second.json()
        assert stored['value']['slots'][0] == first.json()['value']['slots'][0]
        assert stored['value']['slots'][1]['path'] == entries['pdf']['path']
        assert stored['value']['structure'] is None
        assert mount({'library_id': 'pdf', 'slot': 1}, original['revision']).status_code == 409
        for arguments, status in (
            ({'library_id': '../../private', 'slot': 1}, 422),
            ({'library_id': 'cod', 'slot': 2}, 422),
            ({'path': '/etc/passwd', 'slot': 1}, 403),
            ({'path': 'relative.sq', 'library_id': 'pdf', 'slot': 1}, 422),
        ):
            assert mount(arguments, stored['revision']).status_code == status
        assert client.get(endpoint + '/document').json() == stored
    with TestClient(create_app(settings)) as client:
        assert client.get(endpoint + '/document').json() == stored


@pytest.mark.parametrize('content', [None, 'not json', '{}', '{"bad": {"path": "relative.sq", "label": "Bad"}}'])
def test_configured_catalog_never_falls_back_to_browser_paths(tmp_path, monkeypatch, content):
    from oaw_xrd import configure_library
    from open_agent_world.plugin_api import ResourceValidationError
    catalog = tmp_path / 'catalog.json'
    if content is not None:
        catalog.write_text(content, encoding='utf-8')
    monkeypatch.setenv('OAW_XRD_LIBRARY_CATALOG', str(catalog))
    with pytest.raises(ResourceValidationError):
        configure_library({}, {'path': str(tmp_path / 'unapproved.sq')})


def test_unmount_primary_then_last_slot_clears_search_input_and_persists(data_root, library_path):
    from oaw_xrd.library import expand_library_slots
    from backend.main import create_app
    other = library_path.with_name('other.sq')
    other.write_bytes(library_path.read_bytes())
    with TestClient(create_app(Settings.for_data_root(data_root))) as client:
        node = create_node(client, 'xrd.match')
        endpoint = f"/api/nodes/{node['id']}"

        def action(name, arguments, revision):
            return client.post(endpoint + '/actions/' + name, json={
                'arguments': arguments, 'expected_revision': revision})

        doc = client.get(endpoint + '/document').json()
        for slot, path in [(0, library_path), (1, other)]:
            response = action('configure', {'path': str(path), 'slot': slot}, doc['revision'])
            assert response.status_code == 200, response.text
            doc = response.json()
        for slot in [-1, 6, True, '0']:
            assert action('unmount', {'slot': slot}, doc['revision']).status_code == 422
        assert action('unmount', {'slot': 0}, 0).status_code == 409
        primary = action('unmount', {'slot': 0}, doc['revision'])
        assert primary.status_code == 200, primary.text
        doc = primary.json()
        assert doc['value']['slots'][0] is None and doc['value']['path'] == str(other)
        expanded = expand_library_slots([{'node_id': node['id'], 'value': doc['value']}])
        assert len(expanded) == 1 and expanded[0]['value']['path'] == str(other)
        assert library_path.is_file() and other.is_file()
        response = action('unmount', {'slot': 1}, doc['revision'])
        assert response.status_code == 200, response.text
        empty = response.json()
        assert empty['value']['slots'] == [None] * 6
        assert empty['value']['path'] == '' and empty['value']['count'] == 0 and empty['value']['sha256'] == ''
        assert not expand_library_slots([{'node_id': node['id'], 'value': empty['value']}])
    with TestClient(create_app(Settings.for_data_root(data_root))) as reopened:
        assert reopened.get(endpoint + '/document').json() == empty


def test_unmount_preserves_structure_and_supports_legacy_mount(library_path):
    from oaw_xrd import configure_library, unmount_library
    original = configure_library({}, {'path': str(library_path)})
    legacy = {key: value for key, value in original.items() if key != 'slots'}
    cleared = unmount_library(legacy, {'slot': 0})
    assert cleared['path'] == '' and cleared['slots'] == [None] * 6
    # The match action preserves its independent structure document.
    from oaw_xrd import XRDPlugin
    class Registration:
        def __init__(self): self.nodes = {}
        def register_node_type(self, node): self.nodes[node.id] = node
        def __getattr__(self, name): return lambda *args, **kwargs: None
    registration = Registration()
    XRDPlugin().register(registration)
    structure = {'cod_id': '7222155', 'filename': 'candidate.cif'}
    result = registration.nodes['xrd.match'].document.actions['unmount'].handler({**original, 'structure': structure}, {'slot': 0})
    assert result['structure'] == structure and result['path'] == ''


def test_pdf_card_ids_and_source_kind_are_preserved(library_path):
    from oaw_xrd.library import inspect_library, iter_references
    with sqlite3.connect(library_path) as db:
        db.execute('DELETE FROM id')
        db.execute('ALTER TABLE infodb ADD COLUMN type TEXT')
        db.execute('ALTER TABLE infodb ADD COLUMN source TEXT')
        db.execute("INSERT INTO infodb VALUES('test','PDF2','User TXT cards')")
        db.execute('CREATE TABLE card_id_map(engine_id INTEGER PRIMARY KEY,pdf_card_id TEXT)')
        db.execute('CREATE TABLE source_cards(id TEXT PRIMARY KEY,filename TEXT,sha256 TEXT,raw_bytes BLOB,metadata_json TEXT)')
        for engine_id, card_id, kind in [(340688, 'PDF#34-0688', 'card_export'), (40664, 'PDF#04-0664', 'calculated')]:
            db.execute("INSERT INTO id VALUES(?, 'Li7PS6', '3,', '100,')", (engine_id,))
            db.execute('INSERT INTO card_id_map VALUES(?,?)', (engine_id, card_id))
            db.execute('INSERT INTO source_cards VALUES(?,?,?,?,?)',
                (card_id, card_id + '.txt', 'source-hash', b'original', json.dumps({'reference_kind': kind})))
    doc = inspect_library(library_path)
    references = list(iter_references({'node_id': 'lib', 'revision': 1, 'value': doc}))
    by_id = {r['value']['metadata']['engine_reference_id']: r['value'] for r in references}
    assert by_id['340688']['filename'] == 'PDF#34-0688 · Li7PS6'
    assert by_id['340688']['metadata']['reference_kind'] == 'card_export'
    assert by_id['40664']['metadata']['reference_kind'] == 'calculated'
    assert all(r['value']['metadata']['source'] == 'User TXT cards' and 'url' not in r['value']['metadata'] for r in references)
