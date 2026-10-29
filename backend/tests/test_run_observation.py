"""Backend subscriptions use exact Runs, live grants and durable snapshots."""
import asyncio
import pytest

from backend.errors import PermissionDeniedError
from backend.events.models import EventType
from backend.runs.models import RunStatus
from backend.tests.conftest import create_node
from backend.tests.test_execution_graph import connect
from open_agent_world.plugin_api import ObservedRun


def prepare(client):
    services = client.app.state.services
    viewer = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    edge = connect(client, viewer, agent)
    run = services.run_manager.store.create(agent_id=agent['id'],
        runtime_provider_id='core.mock', caller_kind='user')
    return services, viewer, agent, edge, run


def test_exact_read_and_permission_checks(client):
    services, viewer, agent, edge, run = prepare(client)
    async def check():
        sdk = services._node_lifecycle_context().run_observations
        value = await sdk.read(viewer['id'], agent['id'], run.run_id)
        assert isinstance(value, ObservedRun)
        assert value.status == 'created'
        other = services.run_manager.store.create(agent_id=viewer['id'],
            runtime_provider_id='core.mock', caller_kind='user')
        with pytest.raises(PermissionDeniedError):
            await sdk.read(viewer['id'], agent['id'], other.run_id)
        services.world.delete_edge(edge['id'])
        with pytest.raises(PermissionDeniedError):
            await sdk.read(viewer['id'], agent['id'], run.run_id)
    asyncio.run(check())


@pytest.mark.parametrize('status', [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED])
def test_initial_terminal_snapshot_and_cleanup(client, status):
    services, viewer, agent, edge, run = prepare(client)
    services.run_manager.store.update_status(run.run_id, status)
    async def check():
        values = []
        async def callback(value):
            values.append(value)
        sdk = services.run_observations
        subscribers_before = len(services.events._subscribers)
        handle = await sdk.watch(viewer['id'], agent['id'], run.run_id, callback)
        assert [v.status for v in values] == [status.value]
        await handle.close()
        assert not sdk.tasks
        assert len(services.events._subscribers) == subscribers_before
        await sdk.shutdown()
        with pytest.raises(RuntimeError):
            await sdk.watch(viewer['id'], agent['id'], run.run_id, callback)
    asyncio.run(check())


def test_event_reconciliation_retry_and_revocation(client):
    services, viewer, agent, edge, run = prepare(client)
    async def check():
        sdk = services.run_observations
        sdk.reconcile_seconds = .01
        values = []
        completed = asyncio.Event()
        fail_once = True
        async def callback(value):
            nonlocal fail_once
            if value.status == 'failed' and fail_once:
                fail_once = False
                raise ValueError('transient callback failure')
            values.append(value.status)
            if value.status == 'failed':
                completed.set()
        handle = await sdk.watch(viewer['id'], agent['id'], run.run_id, callback)
        # Even if the terminal event was lost, timeout reads authoritative state.
        services.run_manager.store.update_status(run.run_id, RunStatus.FAILED)
        await asyncio.wait_for(completed.wait(), 2)
        assert values == ['created', 'failed']
        services.world.delete_edge(edge['id'])
        await services.events.publish(EventType.EDGE_DELETED)
        await asyncio.sleep(.04)
        assert not handle.task.done()
        assert values == ['created', 'failed']
        await sdk.shutdown()
    asyncio.run(check())


def test_event_wakes_watch_without_waiting_for_timeout(client):
    services, viewer, agent, edge, run = prepare(client)
    async def check():
        sdk = services.run_observations
        done = asyncio.Event()
        async def callback(value):
            if value.status == 'cancelled':
                done.set()
        handle = await sdk.watch(viewer['id'], agent['id'], run.run_id, callback)
        services.run_manager.store.update_status(run.run_id, RunStatus.CANCELLED)
        await services.events.publish(EventType.RUN_CANCELLED, run_id=run.run_id)
        await asyncio.wait_for(done.wait(), 2)
        await sdk.shutdown()
        assert handle.task.done()
    asyncio.run(check())


def test_deleted_agent_ends_paused_subscription(client):
    services, viewer, agent, edge, run = prepare(client)
    async def check():
        async def callback(value):
            pass
        handle = await services.run_observations.watch(viewer['id'], agent['id'], run.run_id, callback)
        await services.delete_card(agent['id'])
        await services.events.publish(EventType.CARD_DELETED)
        await asyncio.wait_for(asyncio.shield(handle.task), 2)
        assert handle.task.done()
        await services.run_observations.shutdown()
    asyncio.run(check())


@pytest.mark.parametrize('error,expected', [
    ('BadRequestError: Invalid model name passed in model=private-model', '模型名称无效'),
    ('AuthenticationError: invalid api key SECRET', '认证失败'),
    ('TimeoutError: request timed out SECRET', '超时'),
    ('RateLimitError: 429 SECRET', '频率'),
    ('unknown error SECRET ' + 'x' * 5000, '运行发生错误'),
])
def test_failure_summary_is_bounded_and_does_not_export_raw_errors(client, error, expected):
    services, viewer, agent, edge, run = prepare(client)
    services.run_manager.store.update_status(run.run_id, RunStatus.FAILED, error=error)
    async def check():
        sdk = services.run_observations
        result = await sdk.read(viewer['id'], agent['id'], run.run_id)
        assert expected in result.error
        assert len(result.error) <= 1200
        assert 'SECRET' not in result.error and 'private-model' not in result.error
        values = []
        async def callback(value):
            values.append(value)
        handle = await sdk.watch(viewer['id'], agent['id'], run.run_id, callback)
        assert values[0].error == result.error
        await handle.close()
        await sdk.shutdown()
    asyncio.run(check())
