import json
from types import SimpleNamespace

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.tests.conftest import create_node
from oaw_xrd.results_context import read_results_context


def write_run(root, run_id, *, owner='owner', stage='search', source='', order=1, status='completed', result=None, state=None):
    directory = root / ('oaw-' + run_id)
    directory.mkdir()
    manifest = {'run_id': run_id, 'agent_id': owner, 'workflow_stage': stage,
                'workflow_match_run_id': source, 'created_at_ns': order, 'status': status}
    if stage == 'multiphase':
        manifest.update(agent_id='optimizer', source_owner_node_id=owner)
    (directory / 'oaw.json').write_text(json.dumps(manifest))
    if result is not None:
        (directory / 'result.json').write_text(json.dumps(result))
    if state is not None:
        (directory / 'multiphase-state.json').write_text(json.dumps(state))
    return directory


def multi(run_id, source, **overrides):
    return {'run_id': run_id, 'source_match_run_id': source, 'status': 'completed',
            'options': {'owner_node_id': 'owner'}, 'trials': [], **overrides}


def test_results_only_include_the_current_owners_source_and_compact_data(tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path))
    write_run(tmp_path, 'old', result={'mode': 'match'}, order=1)
    write_run(tmp_path, 'current', result={'mode': 'match', 'candidates': [{'node_id': 'a', 'score': .8, 'peaks': [1]*3000}]}, order=2)
    write_run(tmp_path, 'foreign', owner='other', result={'mode': 'match'}, order=50)
    write_run(tmp_path, 'stale-multi', stage='multiphase', source='old', order=100,
              state=multi('stale-multi', 'old', incumbent={'score': 99}))
    write_run(tmp_path, 'fresh-multi', stage='multiphase', source='current', order=3,
              state=multi('fresh-multi', 'current', optimizer_label='Jev 1.13.0', protocol_version='jev_autoregressive_v2',
                          cloud_request_count=12, cloud_request_limit=18, incumbent={'score': 62, 'plot': [1]*3000,
                          'phase_contributions': [{'profile_area_fraction': .6, 'fraction_kind': 'not_mass_fraction'}]}))
    result = read_results_context(tmp_path, 'owner')
    assert result['source_match_run_id'] == 'current'
    assert result['multiphase']['run_id'] == 'fresh-multi'
    assert result['multiphase']['incumbent']['score'] == 62
    assert result['multiphase']['optimizer_label'] == 'Jev 1.13.0'
    assert result['multiphase']['protocol_version'] == 'jev_autoregressive_v2'
    assert result['multiphase']['cloud_request_count'] == 12
    assert 'plot' not in result['multiphase']['incumbent']
    assert 'peaks' not in result['search']['candidates'][0]
    assert result['multiphase']['incumbent']['phase_contributions'][0]['fraction_kind'] == 'not_mass_fraction'


def test_failed_latest_search_does_not_claim_previous_fit_is_current(tmp_path):
    write_run(tmp_path, 'old', result={'mode': 'match'}, order=1)
    write_run(tmp_path, 'new', status='failed', order=2)
    result = read_results_context(tmp_path, 'owner')
    assert result['status'] == 'failed'
    assert result['source_match_run_id'] == 'new'
    assert result['search'] is None and result['multiphase'] is None


def test_rejects_multiphase_state_with_mismatched_lineage(tmp_path):
    write_run(tmp_path, 'current', result={'mode': 'match'})
    write_run(tmp_path, 'multi', stage='multiphase', source='current', order=2,
              state=multi('multi', 'other', incumbent={'score': 100}))
    assert read_results_context(tmp_path, 'owner')['multiphase'] == {
        'status': 'unavailable', 'error': '多相运行的来源校验未通过。'}


def test_old_preoptimization_fit_is_marked_historical_without_metrics(tmp_path):
    write_run(tmp_path, 'current', result={'mode': 'match'})
    write_run(tmp_path, 'preopt-new', stage='preopt', source='current', order=4,
              result={'mode': 'pipeline', 'match_run_id': 'current', 'candidates': []})
    write_run(tmp_path, 'fit-old', stage='fit', source='current', order=3,
              result={'mode': 'pipeline', 'match_run_id': 'current', 'preopt_run_id': 'preopt-old',
                      'candidates': [{'metrics': {'rwp_percent': 1}}]})
    result = read_results_context(tmp_path, 'owner')
    assert result['single_phase']['fit']['is_current_source'] is False
    assert 'candidates' not in result['single_phase']['fit']


def test_result_analyst_capability_is_read_only_scoped_and_revocable(client, tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path / 'runs'))
    owner = create_node(client, 'xrd.match')
    unrelated = create_node(client, 'xrd.match')
    analyst = create_node(client, 'agent')
    conversation = create_node(client, 'conversation')
    context = client.post('/api/edges', json={'source': conversation['id'], 'target': owner['id'], 'relationship': 'xrd.results-context'})
    assert context.status_code == 201, context.text
    edge = client.post('/api/edges', json={'source': analyst['id'], 'target': owner['id'], 'relationship': 'xrd.results-read'})
    assert edge.status_code == 201, edge.text
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = client.portal.call(provider.list_tools, analyst['id'])
    assert [tool.name for tool in tools] == ['xrd_read_results']
    reader = tools[0]
    result = client.portal.call(provider.invoke_tool, analyst['id'], reader.capability_id, {'target': owner['id']})
    assert result['owner_node_id'] == owner['id'] and result['read_only'] is True
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        client.portal.call(provider.invoke_tool, analyst['id'], reader.capability_id, {'target': unrelated['id']})
    client.delete(f"/api/edges/{edge.json()['id']}")
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        client.portal.call(provider.invoke_tool, analyst['id'], reader.capability_id, {'target': owner['id']})
