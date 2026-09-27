import asyncio
import json
from types import SimpleNamespace

import pytest

from oaw_xrd import multiphase_object as harness


class Registration:
    def __init__(self):
        self.nodes, self.relationships, self.capabilities = [], [], {}
    def register_node_type(self, node):
        self.nodes.append(node)
    def register_relationship(self, relation):
        self.relationships.append(relation)
    def register_capability(self, definition, handler):
        self.capabilities[definition.kind] = handler


def test_registered_harness_is_object_with_native_agent_tools():
    registry = Registration()
    harness.register_harness(registry)
    assert [node.id for node in registry.nodes] == ['xrd.multiphase-harness']
    assert 'core.agent' not in registry.nodes[0].traits
    assert registry.nodes[0].document.actions['read'].project
    assert len(registry.capabilities) == 7
    assert {'xrd.multiphase.decision', 'xrd.multiphase.stop'} <= registry.capabilities.keys()
    tools = next(r for r in registry.relationships if r.id == 'xrd.multiphase-tools')
    assert tools.source_traits == frozenset({'core.agent'})
    assert len(tools.capabilities) == 7


def test_tool_handler_fetches_revision_without_llm_managing_it():
    registry = Registration()
    harness.register_harness(registry)
    calls = []
    class Context:
        async def node_document_action(self, capability, action, args, expected_revision=None):
            calls.append((action, args, expected_revision))
            return {'revision': 7, 'value': {'state': {'status': 'idle'}}}
    asyncio.run(registry.capabilities['xrd.multiphase.start'](Context(), SimpleNamespace(agent_id='native-agent'), {}))
    assert calls == [('inspect_start', {}, None), ('start', {'_agent_node_id': 'native-agent'}, 7)]


def state_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path))
    directory = tmp_path / 'oaw-test'
    directory.mkdir()
    (directory / 'oaw.json').write_text(json.dumps({'run_id': 'test', 'status': 'running'}))
    state = {'run_id': 'test', 'status': 'running', 'options': {'budget': 6, 'max_phases': 3, 'evaluate_baseline': True},
             'search_space_size': 7, 'pool': [{'candidate_id': x, 'label': x} for x in ('a', 'b', 'c')],
             'trials': [], 'baseline': {'status': 'pending', 'trials': []}, 'progress': {'completed': 0}}
    (directory / 'multiphase-state.json').write_text(json.dumps(state))
    monkeypatch.setitem(harness._processes, 'test', (SimpleNamespace(returncode=0), None))
    return {'options': state['options'], 'run_id': 'test', 'state': state}, directory


def test_baseline_cannot_leak_results_before_agent_budget(tmp_path, monkeypatch):
    value, directory = state_fixture(tmp_path, monkeypatch)
    with pytest.raises(Exception, match='公平'):
        asyncio.run(harness._operate(value, {}, 'baseline'))
    with pytest.raises(Exception, match='不能提前'):
        asyncio.run(harness._operate(value, {}, 'finish'))
    assert json.loads((directory / 'multiphase-state.json').read_text(encoding='utf-8'))['status'] == 'running'


def test_evaluation_rejects_unknown_or_duplicate_without_worker(tmp_path, monkeypatch):
    value, _ = state_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='supplied'):
        asyncio.run(harness._operate(value, {'candidate_ids': ['foreign'], 'reason': 'test'}, 'evaluate'))
    with pytest.raises(ValueError, match='duplicate'):
        asyncio.run(harness._operate(value, {'candidate_ids': ['a', 'a'], 'reason': 'test'}, 'evaluate'))


def test_failed_start_is_durable_document_state(tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path))
    async def fail(root, options, progress):
        raise ValueError('Immutable source unavailable')
    monkeypatch.setattr(harness, 'build_multiphase_payload', fail)
    value = {'options': {'owner_node_id': 'owner', 'source_match_run_id': 'search', 'candidate_ids': ['a', 'b']}, 'run_id': '', 'state': {'status': 'idle'}}
    prepared = asyncio.run(harness.prepare_start(value, {'_agent_node_id': 'native-agent'}))['prepared']
    assert prepared['state']['status'] == 'failed'
    assert prepared['state']['error'] == 'Immutable source unavailable'
    assert (tmp_path / ('oaw-' + prepared['run_id']) / 'multiphase-state.json').is_file()
    assert ('owner', 'search') not in harness._initializing


def test_agent_tool_summary_never_contains_plots_or_cif():
    value = {'value': {'state': {'status': 'running', 'pool': [], 'trials': [
        {'candidate_ids': ['a'], 'score': 80, 'plot': {'observed': [[1, 2]]}, 'source_cif': 'secret cif content'}]}}}
    compact = json.dumps(harness.tool_summary(value))
    assert 'observed' not in compact
    assert 'source_cif' not in compact
    assert '80' in compact


def test_live_pywpem_state_is_projected_only_during_review(tmp_path, monkeypatch):
    value, directory = state_fixture(tmp_path, monkeypatch)
    (directory / 'pywpem-review').mkdir()
    (directory / 'pywpem-review/live.json').write_text(json.dumps({'iteration': 3, 'candidate_ids': ['a']}))
    state = value['state']
    state['progress']['stage'] = 'PyWPEM 多相联合复核与逐相移除检验'
    harness._persist(directory, state)
    assert harness._read(value)['state']['pywpem_review']['live']['iteration'] == 3
    state['progress']['stage'] = '等待 Agent 选择下一组候选'
    harness._persist(directory, state)
    assert 'pywpem_review' not in harness._read(value)['state']


def test_finish_defers_review_without_changing_search_scores(tmp_path, monkeypatch):
    value, directory = state_fixture(tmp_path, monkeypatch)
    state = value['state']
    state['options']['pywpem_review'] = True
    incumbent = {'candidate_ids': ['a', 'b'], 'score': 62.5}
    state.update(trials=[incumbent]*6, incumbent=incumbent)
    state['baseline'].update(status='completed', trials=[incumbent]*6, incumbent=incumbent)
    harness._persist(directory, state)
    calls = []
    async def rpc(run_id, command, **arguments):
        calls.append((command, arguments))
        return {'status': 'failed', 'error': 'scientific failure retained'}
    async def kill(run_id):
        pass
    monkeypatch.setattr(harness, '_rpc', rpc)
    monkeypatch.setattr(harness, '_kill', kill)
    result = asyncio.run(harness._operate(value, {}, 'finish'))['prepared']['state']
    assert calls == []
    assert result['incumbent']['score'] == 62.5
    assert len(result['trials']) == 6
    assert result['pywpem_review']['status'] == 'pending'
    assert result['comparison']['equal_budget'] is True


def test_native_agent_capabilities_are_object_scoped_and_revocable(client):
    from backend.tests.conftest import create_node
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.errors import PermissionDeniedError, ResourceValidationError
    agent = create_node(client, 'agent')
    obj = create_node(client, 'xrd.multiphase-harness')
    unrelated = create_node(client, 'xrd.multiphase-harness')
    edge = client.post('/api/edges', json={'source': agent['id'], 'target': obj['id'], 'relationship': 'xrd.multiphase-tools'})
    assert edge.status_code == 201, edge.text
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    tools = client.portal.call(provider.list_tools, agent['id'])
    reader = next(t for t in tools if t.name == 'xrd_multiphase_read')
    value = client.portal.call(provider.invoke_tool, agent['id'], reader.capability_id, {'target': obj['id']})
    assert value['status'] == 'idle'
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        client.portal.call(provider.invoke_tool, agent['id'], reader.capability_id, {'target': unrelated['id']})
    client.delete(f"/api/edges/{edge.json()['id']}")
    with pytest.raises((PermissionDeniedError, ResourceValidationError)):
        client.portal.call(provider.invoke_tool, agent['id'], reader.capability_id, {'target': obj['id']})


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
def test_manual_review_reuses_cancelled_search_without_rerunning_it(tmp_path, monkeypatch, outcome):
    value, directory = state_fixture(tmp_path, monkeypatch)
    state = value['state']
    incumbent = {'candidate_ids': ['a', 'b'], 'score': 62.5, 'status': 'completed'}
    state.update(status='cancelled', incumbent=incumbent, trials=[incumbent], llm_status='completed')
    state['baseline'].update(status='completed', incumbent=incumbent)
    harness._persist(directory, state)
    (directory / 'multiphase-input.json').write_text('{}')
    calls = []
    rpc_started = asyncio.Event()
    async def spawn(*args, **kwargs):
        return SimpleNamespace(returncode=0)
    async def rpc(run_id, command, **arguments):
        calls.append(command)
        rpc_started.set()
        if outcome == 'cancelled':
            await asyncio.Event().wait()
        assert arguments['combinations'] == [['a', 'b']]
        assert arguments['drop_one'] is False
        return {'status': outcome, 'reviews': [{'full': {'candidate_ids': ['a', 'b'], 'status': outcome}, 'removals': []}]}
    async def kill(run_id):
        record = harness._processes.pop(run_id, None)
        if record and record[1]:
            record[1].close()
    monkeypatch.setattr(harness.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(harness, '_rpc', rpc)
    monkeypatch.setattr(harness, '_kill', kill)
    async def scenario():
        prepared = await harness.prepare_review(value, {'run_id': 'test', 'combinations': [['a', 'b']]})
        task = harness._review_tasks['test']
        assert prepared['prepared']['state']['status'] == 'running'
        with pytest.raises(Exception, match='结束或停止'):
            await harness.prepare_review(value, {'run_id': 'test', 'combinations': [['a', 'b']]})
        await asyncio.wait_for(rpc_started.wait(), timeout=5)
        if outcome == 'cancelled':
            await harness._operate(value, {}, 'stop')
        else:
            await task
    asyncio.run(scenario())
    final = json.loads((directory / 'multiphase-state.json').read_text(encoding='utf-8'))
    assert calls == ['pywpem_review']
    assert final['pywpem_review']['status'] == outcome
    assert final['incumbent'] == incumbent
    assert final['trials'] == [incumbent]
    assert 'test' not in harness._review_tasks


@pytest.mark.parametrize('selection', [[], [['unknown']], [['a'], ['a']]])
def test_review_rejects_unmeasured_or_duplicate_selections(tmp_path, monkeypatch, selection):
    value, directory = state_fixture(tmp_path, monkeypatch)
    t = {'candidate_ids': ['a'], 'status': 'completed', 'score': 80}
    value['state'].update(status='completed', incumbent=t, trials=[t])
    harness._persist(directory, value['state'])
    with pytest.raises(Exception, match='成功实测组合'):
        asyncio.run(harness.prepare_review(value, {'run_id': 'test', 'combinations': selection}))
    assert 'test' not in harness._review_tasks


def test_review_preserves_completed_combination_when_later_fit_times_out(tmp_path, monkeypatch):
    value, directory = state_fixture(tmp_path, monkeypatch)
    trials = [{'candidate_ids': [x], 'status': 'completed', 'score': 80} for x in ['a','b']]
    value['state'].update(status='completed', incumbent=trials[0], trials=trials)
    harness._persist(directory, value['state'])
    (directory / 'multiphase-input.json').write_text('{}')
    calls = []
    async def spawn(*a, **k):
        return SimpleNamespace(returncode=0)
    async def rpc(run_id, command, **args):
        calls.append(args['combinations'])
        if len(calls) == 2:
            raise asyncio.TimeoutError()
        return {'status':'completed', 'reviews':[{'full':trials[0], 'removals':[]}]}
    async def kill(run_id):
        record = harness._processes.pop(run_id, None)
        if record: record[1].close()
    monkeypatch.setattr(harness.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(harness, '_rpc', rpc)
    monkeypatch.setattr(harness, '_kill', kill)
    async def run():
        await harness.prepare_review(value, {'run_id':'test', 'combinations':[['a'],['b']]})
        await harness._review_tasks['test']
    asyncio.run(run())
    state = json.loads((directory/'multiphase-state.json').read_text(encoding='utf-8'))
    assert calls == [[['a']],[['b']]]
    assert state['pywpem_review']['status'] == 'failed'
    assert state['pywpem_review']['error']
    assert len(state['pywpem_review']['reviews']) == 1


def test_save_next_options_preserves_active_run_and_history(tmp_path, monkeypatch):
    value, directory = state_fixture(tmp_path, monkeypatch)
    before = json.loads(json.dumps(value))
    updated = harness.save_options(value, {'budget':55,'max_phases':3,'evaluate_baseline':False})
    assert updated['next_options']['budget'] == 55
    assert updated['next_options']['evaluate_baseline'] is False
    assert updated['state'] == before['state']
    assert updated['run_id'] == before['run_id']
    assert updated['options'] == before['options']
    assert harness.HarnessDocument.model_validate(updated).model_dump()['next_options']['budget'] == 55
    with pytest.raises(ValueError):
        harness.save_options(value, {'budget':101})


def test_review_progress_is_current_and_does_not_require_a_live_frame(tmp_path, monkeypatch):
    run = tmp_path / 'oaw-test'
    (run / 'pywpem-review').mkdir(parents=True)
    state = {'status': 'running', 'progress': {'stage': 'PyWPEM 联合复核 1 / 3'},
             'review_started_at_ns': 0, 'pywpem_review': {'status': 'running'}}
    (run / 'multiphase-state.json').write_text(json.dumps(state))
    detail = run / 'pywpem-review/progress.json'
    detail.write_text(json.dumps({'stage': 'EM 更新', 'iteration': 2}))
    monkeypatch.setattr(harness, '_root', lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(harness, 'run_directory', lambda *args: run)
    monkeypatch.setitem(harness._review_tasks, 'test', object())
    assert harness._read({'run_id': 'test'})['state']['pywpem_review']['progress']['stage'] == 'EM 更新'
    state['review_started_at_ns'] = detail.stat().st_mtime_ns + 1
    (run / 'multiphase-state.json').write_text(json.dumps(state))
    assert 'progress' not in harness._read({'run_id': 'test'})['state']['pywpem_review']


def test_ui_overview_omits_heavy_payload_and_frame_preserves_selected_evidence(monkeypatch):
    value = {'state': {'run_id': 'r', 'status': 'completed', 'trials': [
        {'trial_id': 'a', 'score': 12, 'plot': {'observed': [[1, 2]]}, 'structures': [{'cif':'a'}]},
        {'trial_id': 'b', 'score': 13, 'plot': {'observed': [[3, 4]]}, 'structures': [{'cif':'b'}]}]}}
    monkeypatch.setattr(harness, '_read', lambda v:v)
    summary = harness._ui_overview(value, {})
    assert summary['state']['trials'][0] == {'trial_id':'a','score':12}
    frame = harness._ui_frame(value, {'run_id':'r','lane':'agent','trial_id':'b'})
    assert frame['plot']['observed'] == [[3,4]]
    assert value['state']['trials'][0]['plot']['observed'] == [[1,2]]
    with pytest.raises(Exception, match='运行已切换'):
        harness._ui_frame(value, {'run_id':'old','lane':'agent','trial_id':'b'})
