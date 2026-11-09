import base64

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.tests.conftest import create_node

POINTS = '\n'.join(f'{20 + index * .1:.2f},{100 + (index % 7) * 10}' for index in range(60))


def test_agent_imports_pattern_through_grant_and_ui_import_is_unchanged(client):
    canvas = create_node(client, 'xrd.spectrum-canvas')
    agent = create_node(client, 'agent')
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    assert client.portal.call(provider.list_tools, agent['id']) == []
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': canvas['id'], 'relationship': 'xrd.pattern-import'})
    assert edge.status_code == 201, edge.text
    [tool] = client.portal.call(provider.list_tools, agent['id'])
    assert tool.name == 'xrd_import_pattern'

    def call(arguments):
        return client.portal.call(provider.invoke_tool, agent['id'], tool.capability_id, {'target': canvas['id'], **arguments})

    result = call({'filename': 'converted.csv', 'text': POINTS})
    assert result['points'] == 60 and result['two_theta_range'] == [20.0, 25.9]
    document = client.get(f"/api/nodes/{canvas['id']}/document").json()
    assert document['value']['sha256'] == result['sha256'] and len(document['value']['points']) == 60
    for bad in ('10,5\n9,5\n' * 20, 'a b c', ''):
        with pytest.raises(ResourceValidationError):
            call({'filename': 'bad.txt', 'text': bad})
    assert len(client.get(f"/api/nodes/{canvas['id']}/document").json()['value']['points']) == 60
    ui_text = '\n'.join(f'{30 + index * .1:.2f} {50 + index}' for index in range(30)).encode()
    ui = client.post(f"/api/nodes/{canvas['id']}/actions/import", json={'expected_revision': document['revision'], 'arguments': {
        'filename': 'ui.txt', 'source_base64': base64.b64encode(ui_text).decode()}})
    assert ui.status_code == 200, ui.text
    client.delete(f"/api/edges/{edge.json()['id']}")
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        call({'filename': 'late.csv', 'text': POINTS})
