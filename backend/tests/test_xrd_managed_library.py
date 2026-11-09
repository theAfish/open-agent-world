import sqlite3

import pytest

from backend.tests.conftest import create_node
from oaw_xrd.library import expand_library_slots, iter_references, validate_library
from oaw_xrd.managed_library import stored_structure

LICL = 'PDF#mp-licl: Materials Project calculated\nLithium Chloride\nLiCl\nRadiation=CuKa1 Lambda=1.5406\n2-Theta d I (h k l)\n30.0 2.98 100.0 (2 0 0)\n43.0 2.10 50.0 (2 2 0)\n'
CIF = 'data_licl\n_cell_length_a 5.14\n_cell_length_b 5.14\n_cell_length_c 5.14\nloop_\n_atom_site_label\nLi1\nCl1\n'
SIO2 = 'PDF#46-1045: QM=Star(*)\nQuartz\nSiO2\nRadiation=CuKa1 Lambda=1.5406\n2-Theta d I (h k l)\n26.64 3.34 100.0 (1 0 1)\n20.86 4.26 22.0 (1 0 0)\n'


def post(client, node, action, arguments):
    revision = client.get(f"/api/nodes/{node['id']}/document").json()['revision']
    return client.post(f"/api/nodes/{node['id']}/actions/{action}", json={'expected_revision': revision, 'arguments': arguments})


def managed_document(client, node):
    return client.get(f"/api/nodes/{node['id']}/document").json()['value']


def test_managed_library_adds_searches_replaces_and_removes_cards(client):
    node = create_node(client, 'xrd.match')
    response = post(client, node, 'add_cards', {'cards': [
        {'filename': 'licl.txt', 'text': LICL}, {'filename': 'quartz.txt', 'text': SIO2}, {'filename': 'bad.txt', 'text': 'not a card'}]})
    assert response.status_code == 200, response.text
    document = managed_document(client, node)
    assert document['managed']['count'] == 2
    assert [item['filename'] for item in document['managed']['metadata']['last_import']['rejected']] == ['bad.txt']

    [item] = expand_library_slots([{'node_id': 'lib', 'revision': 1, 'value': document}])
    assert item['node_id'] == 'lib:managed'
    validate_library(document)
    references = {ref['value']['metadata']['reference_code']: ref['value']['metadata'] for ref in iter_references(item)}
    assert references['mp-licl']['reference_kind'] == 'calculated' and references['46-1045']['reference_kind'] == 'card_export'
    assert [ref for ref in iter_references(item, {'Li', 'Cl'}) if ref.get('excluded')]

    assert post(client, node, 'add_cards', {'cards': [{'filename': 'again.txt', 'text': LICL}]}).status_code == 200
    again = managed_document(client, node)['managed']
    assert again['count'] == 2 and again['metadata']['last_import']['replaced'] == 1
    validate_library(managed_document(client, node))

    listed = post(client, node, 'entries', {'query': 'quartz'})
    assert listed.status_code == 200 and listed.json()['value']['total'] == 1
    assert post(client, node, 'remove_entries', {'ids': ['mp-licl']}).status_code == 200
    assert managed_document(client, node)['managed']['count'] == 1


def test_managed_library_rejects_batches_without_a_valid_card(client):
    node = create_node(client, 'xrd.match')
    for arguments in ({'cards': []}, {'cards': [{'filename': 'x.txt', 'text': 'junk'}]}, {'cards': [{'filename': 'x.txt'}]}):
        assert post(client, node, 'add_cards', arguments).status_code == 422
    assert not managed_document(client, node).get('managed')


def test_mounting_and_unmounting_external_libraries_keep_the_managed_library(client, tmp_path):
    node = create_node(client, 'xrd.match')
    assert post(client, node, 'add_cards', {'cards': [{'filename': 'licl.txt', 'text': LICL}]}).status_code == 200
    path = tmp_path / 'external.sq'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE id(id INTEGER, chemical_formula TEXT, dvalue TEXT, intensita TEXT)')
        db.execute("INSERT INTO id VALUES(1, 'Si', '3,', '1000,')")
        db.execute('CREATE TABLE infodb(date TEXT)')
    assert post(client, node, 'configure', {'path': str(path), 'slot': 1}).status_code == 200
    assert managed_document(client, node)['managed']['count'] == 1
    assert post(client, node, 'unmount', {'slot': 1}).status_code == 200
    assert managed_document(client, node)['managed']['count'] == 1


def test_managed_library_attaches_cifs_and_serves_them_to_candidates(client):
    node = create_node(client, 'xrd.match')
    assert post(client, node, 'add_cards', {'cards': [{'filename': 'licl.txt', 'text': LICL}, {'filename': 'quartz.txt', 'text': SIO2}]}).status_code == 200
    assert post(client, node, 'add_structures', {'cifs': [{'filename': 'x.cif', 'text': CIF}]}).status_code == 422
    response = post(client, node, 'add_structures', {'cifs': [
        {'filename': 'mp-licl_LiCl.cif', 'text': CIF}, {'filename': 'orphan.cif', 'text': CIF}, {'filename': 'mp-licl_bad.cif', 'text': 'not a cif'}]})
    assert response.status_code == 200, response.text
    document = managed_document(client, node)
    report = document['managed']['metadata']['last_import']
    assert report['attached'] == 1 and len(report['rejected']) == 2
    listed = post(client, node, 'entries', {}).json()['value']['entries']
    assert {entry['id']: entry['structure'] for entry in listed} == {'46-1045': False, 'mp-licl': True}

    snapshot = [{'node_id': 'lib', 'revision': 1, 'value': document}]
    [item] = expand_library_slots(snapshot)
    metadata = {ref['value']['metadata']['reference_code']: ref['value']['metadata'] for ref in iter_references(item)}
    assert metadata['mp-licl']['structure_available'] == 'true' and metadata['46-1045']['structure_available'] == 'false'
    candidate = {'node_id': 'lib:managed:mp-licl', 'metadata': metadata['mp-licl']}
    assert stored_structure(snapshot, candidate)['text'] == CIF
    assert stored_structure(snapshot, {'node_id': 'lib:managed:46-1045', 'metadata': metadata['46-1045']}) is None
    assert stored_structure(snapshot, {**candidate, 'node_id': 'other:mp-licl'}) is None

    from oaw_xrd.library import inspect_library
    mounted = [{'node_id': 'lib', 'revision': 1, 'value': {'kind': 'library', 'slots': [inspect_library(document['managed']['path'])]}}]
    [slot_item] = expand_library_slots(mounted)
    slot_metadata = {ref['value']['metadata']['reference_code']: ref['value']['metadata'] for ref in iter_references(slot_item)}
    assert stored_structure(mounted, {'node_id': 'lib:slot1:mp-licl', 'metadata': slot_metadata['mp-licl']})['text'] == CIF

    assert post(client, node, 'remove_entries', {'ids': ['mp-licl']}).status_code == 200
    assert not [e for e in post(client, node, 'entries', {}).json()['value']['entries'] if e['structure']]
