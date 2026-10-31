"""Time Pack integration: real documents, persistence, scoped tools and revocation."""
from unittest.mock import patch
from datetime import datetime, timezone

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.plugins.registry import PluginRegistry
from backend.tests.conftest import create_node
from backend.world.models import CardCreate, EdgeCreate
from oaw_time import create_plugin


def action(client, node, operation, arguments=None, revision=None):
    url = f"/api/nodes/{node['id']}"
    if revision is None:
        revision = client.get(url + '/document').json()['revision']
    return client.post(url + f'/actions/{operation}', json={
        'arguments': arguments or {}, 'expected_revision': revision})


def test_registration_only_preview_and_function_specific_connections():
    registry = PluginRegistry()
    registry.install(create_plugin())
    catalog = registry.catalog()
    assert len(catalog.node_types) == 7
    for card in catalog.node_types:
        assert card.presentation.states == ('preview',)
        assert card.presentation.initial == card.presentation.open == 'preview'
        assert card.frontend == {'preview':'timepiece'}
        assert card.icon_url.startswith('/api/plugins/tools.time/assets/time-')
    assert registry.relationship('tools.time.increment').target_types == frozenset({'tools.time.counter'})
    assert 'tools.time.clock' not in registry.relationship('tools.time.start').target_types
    assert {card.id for card in catalog.node_types if 'ui.ellipse.v1' in card.traits} == {'tools.time.clock','tools.time.pomodoro'}


def test_timer_pause_resume_completion_and_stopwatch_laps(client):
    timer = create_node(client, 'tools.time.countdown')
    stopwatch = create_node(client, 'tools.time.stopwatch')
    with patch('oaw_time.engine.now_ms', return_value=10000):
        assert action(client, timer, 'set_duration', {'seconds':2}).status_code == 200
        assert action(client, timer, 'start').status_code == 200
        assert action(client, stopwatch, 'start').status_code == 200
    with patch('oaw_time.engine.now_ms', return_value=11000):
        assert action(client, timer, 'pause').json()['value']['elapsed_ms'] == 1000
        assert action(client, stopwatch, 'lap').json()['value']['laps'] == [1000]
    with patch('oaw_time.engine.now_ms', return_value=50000):
        assert action(client, timer, 'read').json()['value']['value_ms'] == 1000
        assert action(client, timer, 'start').status_code == 200
        # Repeated start must not restart an active timer.
    with patch('oaw_time.engine.now_ms', return_value=50500):
        assert action(client, timer, 'start').json()['value']['started_at_ms'] == 50000
    with patch('oaw_time.engine.now_ms', return_value=52000):
        current = action(client, timer, 'read').json()['value']
        assert current['finished'] and not current['running'] and current['value_ms'] == 0
        assert action(client, timer, 'start').json()['value']['elapsed_ms'] == 0
        assert action(client, stopwatch, 'read').json()['value']['value_ms'] == 42000
        assert action(client, stopwatch, 'reset').json()['value']['laps'] == []


def test_calendar_clock_counter_validation_and_stale_writes(client):
    calendar = create_node(client, 'tools.time.calendar')
    clock = create_node(client, 'tools.time.digital')
    counter = create_node(client, 'tools.time.counter')
    assert action(client, calendar, 'set_date', {'date':'2024-02-29'}).status_code == 200
    for operation, arguments in [('set_date', {'date':'2025-02-29'}), ('set_zone', {'time_zone':'NoWhere/City'}), ('start', {})]:
        assert action(client, calendar, operation, arguments).status_code == 422
    assert action(client, calendar, 'replace', {'selected_date':'2025-02-29'}).status_code == 422
    with patch('oaw_time.engine.now_ms', return_value=100000):
        assert action(client, clock, 'set_time', {'timestamp_ms':0}).status_code == 200
        read = action(client, clock, 'read').json()['value']
        assert read['timestamp_ms'] == 0 and read['iso_time'].startswith('1970-01-01T08:00:00')
        assert action(client, clock, 'reset').json()['value']['offset_ms'] == 0
    first = action(client, counter, 'increment')
    assert first.json()['value']['count'] == 1
    assert action(client, counter, 'increment', revision=0).status_code == 409
    for args in ({'amount':0}, {'amount':-1}, {'amount':True}, {'amount':1.5}, {'target':'other'}):
        assert action(client, counter, 'increment', args).status_code == 422
    action(client, counter, 'set_count', {'value':999999999})
    assert action(client, counter, 'increment').status_code == 422
    assert action(client, counter, 'read').json()['value']['count'] == 999999999
    assert action(client, counter, 'reset').json()['value']['count'] == 0


def test_restart_restores_active_timestamp_and_count(data_root):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    settings = Settings.for_data_root(data_root)
    with TestClient(create_app(settings)) as client:
        timer = create_node(client, 'tools.time.pomodoro')
        counter = create_node(client, 'tools.time.counter')
        clock = create_node(client, 'tools.time.clock')
        assert action(client, clock, 'set_zone', {'time_zone':'America/New_York'}).status_code == 200
        assert action(client, counter, 'set_count', {'value':42}).status_code == 200
        with patch('oaw_time.engine.now_ms', return_value=10000):
            assert action(client, timer, 'start').status_code == 200
    with TestClient(create_app(settings)) as client:
        with patch('oaw_time.engine.now_ms', return_value=70000):
            assert action(client, timer, 'read').json()['value']['value_ms'] == 1440000
        assert action(client, counter, 'read').json()['value']['count'] == 42
        assert action(client, clock, 'read').json()['value']['time_zone'] == 'America/New_York'


def test_city_zone_rules_follow_the_date_and_legacy_fixed_offsets_remain_readable(client):
    clock = create_node(client, 'tools.time.digital')
    assert action(client, clock, 'set_zone', {'time_zone':'America/New_York'}).status_code == 200
    for stamp, expected in [('2026-01-15T00:30:00+00:00','2026-01-14T19:30:00-05:00'),
                            ('2026-07-15T00:30:00+00:00','2026-07-14T20:30:00-04:00')]:
        with patch('oaw_time.engine.now_ms',return_value=round(datetime.fromisoformat(stamp).timestamp()*1000)):
            assert action(client, clock, 'read').json()['value']['iso_time'] == expected
    # Restoring a document written by the offset-only version preserves its time.
    assert action(client, clock, 'replace', {'offset_minutes':330}).status_code == 200
    with patch('oaw_time.engine.now_ms',return_value=0):
        restored = action(client, clock, 'read').json()['value']
        assert restored['time_zone'] == '' and restored['iso_time'] == '1970-01-01T05:30:00+05:30'
    for name in ('NoWhere/City','../etc/passwd',''):
        assert action(client, clock, 'set_zone', {'time_zone':name}).status_code == 422
    assert action(client, clock, 'replace', {'time_zone':'NoWhere/City'}).status_code == 422


def test_city_local_edits_handle_dst_gaps_repeated_hours_and_invalid_arguments(client):
    clock = create_node(client, 'tools.time.clock')
    action(client, clock, 'set_zone', {'time_zone':'America/New_York'})
    with patch('oaw_time.engine.now_ms',return_value=100000):
        assert action(client, clock, 'set_time', {'local_time':'2026-07-15T09:07:08'}).status_code == 200
        value = action(client, clock, 'read').json()['value']
        assert value['timestamp_ms'] == round(datetime(2026,7,15,13,7,8,tzinfo=timezone.utc).timestamp()*1000)
        before = client.get(f"/api/nodes/{clock['id']}/document").json()
        for arguments in ({'local_time':'2026-03-08T02:30:00'}, {}, {'timestamp_ms':1,'local_time':'2026-01-01T00:00:00'}, {'local_time':'2026-02-30T01:00:00'}):
            assert action(client, clock, 'set_time', arguments).status_code == 422
        assert client.get(f"/api/nodes/{clock['id']}/document").json() == before
        assert action(client, clock, 'set_time', {'local_time':'2026-11-01T01:30:00'}).status_code == 200
        assert action(client, clock, 'read').json()['value']['iso_time'] == '2026-11-01T01:30:00-04:00'


def test_templates_preserve_settings_and_count_but_start_with_idle_timers(client):
    timer = create_node(client, 'tools.time.countdown')
    counter = create_node(client, 'tools.time.counter')
    action(client, timer, 'set_duration', {'seconds':45})
    action(client, timer, 'start')
    action(client, counter, 'set_count', {'value':17})
    saved = client.post('/api/legions', json={'name':'Time kit','node_ids':[timer['id'],counter['id']]})
    assert saved.status_code == 201, saved.text
    restored = client.post(f"/api/legions/{saved.json()['id']}/instances", json={})
    assert restored.status_code == 201, restored.text
    copies = {node['type']:node for node in restored.json()['nodes']}
    state = action(client, copies['tools.time.countdown'], 'read').json()['value']
    assert state['value_ms'] == 45000 and not state['running'] and state['laps'] == []
    assert action(client, copies['tools.time.counter'], 'read').json()['value']['count'] == 17
    assert action(client, timer, 'read').json()['value']['running']


@pytest.mark.asyncio
async def test_graph_scoped_mutations_read_only_access_and_revocation(client):
    services = client.app.state.services
    counter = await services.create_card(CardCreate(type='tools.time.counter'))
    other = await services.create_card(CardCreate(type='tools.time.counter'))
    agent = await services.create_card(CardCreate(type='agent'))
    edge = await services.create_edge(EdgeCreate(source=agent.id,target=counter.id,relationship='tools.time.increment'))
    provider = WorldAgentCapabilityProvider(services)
    result = await provider.invoke_tool(agent.id, f'tools.time.increment:{counter.id}', {})
    assert result['count'] == 1
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, f'tools.time.increment:{other.id}', {})
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, f'tools.time.reset:{counter.id}', {})
    with pytest.raises(ResourceValidationError):
        await provider.invoke_tool(agent.id, f'tools.time.increment:{counter.id}', {'target':other.id})
    await services.delete_edge(edge.id)
    await services.create_edge(EdgeCreate(source=agent.id,target=counter.id,relationship='tools.time.read'))
    assert (await provider.invoke_tool(agent.id, f'tools.time.read:{counter.id}', {}))['count'] == 1
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(agent.id, f'tools.time.increment:{counter.id}', {})
