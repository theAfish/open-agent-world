import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.tests.conftest import create_node
from oaw_xrd.run_control import stage_config


def test_stage_config_maps_stage_handoff_and_rejects_missing_lineage():
    assert stage_config({'stage': 'search', 'wavelength': 1.54, 'library_top_n': 5}) == {
        'workflow_stage': 'search', 'wavelength': 1.54, 'library_top_n': 5}
    assert stage_config({'stage': 'fit', 'match_run_id': 'm', 'preopt_run_id': 'p', 'selected_candidate_ids': ['a']}) == {
        'workflow_stage': 'fit', 'workflow_match_run_id': 'm', 'selected_candidate_ids': ['a'], 'workflow_preopt_run_id': 'p'}
    for bad in ({'stage': 'preopt'}, {'stage': 'fit', 'match_run_id': 'm', 'selected_candidate_ids': ['a']},
                {'stage': 'search', 'cif': 'x'}, {'stage': 'search', 'wavelength': 99}, {'stage': 'search', 'selected_candidate_ids': ['a'] * 11}):
        with pytest.raises(ResourceValidationError):
            stage_config(bad)


def test_agent_run_control_is_granted_by_relationship_and_revocable(client, tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path / 'runs'))
    owner = create_node(client, 'xrd.match')
    agent = create_node(client, 'agent')
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    assert client.portal.call(provider.list_tools, agent['id']) == []
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': owner['id'], 'relationship': 'xrd.run-control'})
    assert edge.status_code == 201, edge.text
    tools = {tool.name: tool for tool in client.portal.call(provider.list_tools, agent['id'])}
    assert set(tools) == {'xrd_run_stage', 'xrd_run_stop', 'xrd_run_status', 'xrd_read_results'}

    def call(name, arguments):
        return client.portal.call(provider.invoke_tool, agent['id'], tools[name].capability_id, {'target': owner['id'], **arguments})

    assert call('xrd_run_status', {})['latest_run'] is None
    with pytest.raises(ResourceValidationError):
        call('xrd_run_stage', {'stage': 'fit'})
    started = call('xrd_run_stage', {'stage': 'search', 'wavelength': 1.7})
    assert started['run_id']
    config = client.get(f"/api/nodes/{owner['id']}").json()['config']
    assert config['workflow_stage'] == 'search' and config['wavelength'] == 1.7
    assert call('xrd_run_stop', {})['agent_id'] == owner['id']
    client.delete(f"/api/edges/{edge.json()['id']}")
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        call('xrd_run_status', {})
