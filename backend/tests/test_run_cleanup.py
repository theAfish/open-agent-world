import asyncio
import pytest
from backend.tests.test_runs import RecordingProvider, _services, HangingStopProvider
from backend.world.models import CardCreate
from backend.runs import RunStatus
from backend.errors import RuntimeUnavailableError


@pytest.mark.asyncio
async def test_quiet_registered_tool_outlives_chat_inactivity(tmp_path):
    provider = RecordingProvider(mode='tool')
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.inactivity_timeout_seconds = .02
        agent = await services.create_card(CardCreate(type='agent'))
        run = await manager.start_run(agent.id, 'quiet tool')
        await provider.tool_started.wait()
        await asyncio.sleep(.08)
        assert manager.get_run(run.run_id).status == RunStatus.RUNNING
        assert manager.get_run(run.run_id).lifecycle['active_tools'] == 1
        provider.continue_tool.set()
        await manager.wait_execution(run.run_id)
        assert manager.get_run(run.run_id).status == RunStatus.SUCCEEDED
    finally:
        services.close()


@pytest.mark.asyncio
async def test_cleanup_pending_is_durable_and_retry_joins_same_stop(tmp_path):
    provider = HangingStopProvider()
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.cleanup_timeout_seconds = .02
        agent = await services.create_card(CardCreate(type='agent'))
        run = await manager.start_run(agent.id, 'blocked')
        await provider.started.wait()
        with pytest.raises(RuntimeUnavailableError, match='pending'):
            await manager.cancel_run(run.run_id)
        record = manager.get_run(run.run_id)
        assert record.status == RunStatus.CANCELLED
        assert record.lifecycle['cleanup'] == 'pending'
        provider.release_stop.set()
        record = await manager.cancel_run(run.run_id)
        assert record.lifecycle['cleanup'] == 'complete'
        assert provider.stopped_run_ids == [run.run_id]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_dependent_cancellation_preserves_detached_attempt(tmp_path):
    provider = RecordingProvider(mode='block')
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type='agent', config={'max_concurrent_runs': 3}))
        manager = services.run_manager
        parent = await manager.start_run(agent.id, 'parent')
        child = await manager.start_run(agent.id, 'child', parent_run_id=parent.run_id)
        detached = await manager.start_run(agent.id, 'independent', detached=True)
        await manager.cancel_run(parent.run_id)
        assert manager.get_run(child.run_id).status == RunStatus.CANCELLED
        assert manager.get_run(detached.run_id).status == RunStatus.RUNNING
        assert manager.get_run(detached.run_id).lifecycle['owner_id'] == agent.id
        await manager.cancel_run(detached.run_id)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_quiet_tool_still_has_execution_deadline(tmp_path):
    provider = RecordingProvider(mode='tool')
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.execution_deadline_seconds = .05
        manager.inactivity_timeout_seconds = .01
        agent = await services.create_card(CardCreate(type='agent'))
        record = await manager.start_run(agent.id, 'tool exceeding deadline')
        await asyncio.wait_for(manager.wait_execution(record.run_id), 1)
        assert manager.get_run(record.run_id).status == RunStatus.FAILED
        assert 'deadline' in manager.get_run(record.run_id).error
    finally:
        services.close()

