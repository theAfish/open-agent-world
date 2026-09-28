from backend.tests.test_literature_service import configure
from backend.tests.test_literature_frontiers_service import action, route


def test_reviewed_continuation_preserves_old_route_and_connects_new_card(client):
    scope = configure(client)
    old = route(client, scope)
    before = client.get(f"/api/literature/scopes/{scope['id']}").json()
    revision = client.post(f"/api/nodes/{scope['id']}/actions/revise",json={'expected_revision':before['revision'],
        'arguments':{'question':'Reviewed scope','budget':{'max_searches':10,'max_papers':30,'max_duration_seconds':900}}})
    assert revision.status_code == 200, revision.text
    result = action(client, scope, 'frontier',continue_from=old['id'],query=old['query'],
        missing_evidence=old['missing_evidence'],rationale='Confirmed still relevant after reviewing scope')
    assert result.status_code == 200, result.text
    value = result.json()['value']
    new = value['frontiers'][-1]
    assert new['id'] != old['id'] and new['continued_from'] == old['id']
    assert new['scope_revision'] == 2 and value['frontiers'][0]['scope_revision'] == 1
    assert value['search_runs'] == before['value']['search_runs']
    assert any(link['source'] == 'trail:'+old['id'] and link['target'] == 'trail:'+new['id'] for link in value['exploration_links'])
    assert action(client,scope,'frontier',continue_from='unknown',query='No',missing_evidence=['No'],rationale='No').status_code == 422
