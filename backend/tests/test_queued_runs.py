from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from backend.card_state import active_state_session, state_session
from backend.conversations import ConversationSessionCreate
from backend.errors import RuntimeUnavailableError
from backend.events.models import EventType
from backend.request_context import (
    ActorRef,
    bind_request_context,
    create_local_request_context,
    current_request_context,
)
from backend.runs import RunStatus
from backend.tests.test_runs import RecordingProvider, _services
from backend.world.models import CardCreate


class SubmissionProvider(RecordingProvider):
    def __init__(self, *, mode="success"):
        super().__init__(mode=mode)
        self.observed = []
        self.stopped = []

    async def execute(self, config, context, runtime_input):
        self.observed.append((runtime_input.prompt, current_request_context(), active_state_session.get()))
        async for event in super().execute(config, context, runtime_input):
            yield event

    async def stop(self, run_id):
        self.stopped.append(run_id)
        await super().stop(run_id)


@asynccontextmanager
async def queue_services(tmp_path, provider):
    services = _services(tmp_path, provider)
    try:
        yield services
    finally:
        await services.shutdown()
        services.close()


async def submit(manager, agent_id, prompt="queued work", **kwargs):
    with bind_request_context(create_local_request_context("queue-test")):
        return await manager.submit_run(agent_id, prompt, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("graceful", [False, True])
async def test_pending_submission_and_input_survive_profile_restart(tmp_path, graceful):
    provider = SubmissionProvider()
    services = _services(tmp_path, provider)
    prompt = "Exact queued input\n" + "持久化正文" * 1500
    try:
        manager = services._require_run_manager()
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        original = await submit(manager, agent.id, prompt, idempotency_key="survive-restart")
        assert original.status is RunStatus.CREATED
        assert manager.store.is_pending(original.run_id)
        if graceful:
            # Includes AgentNodeBehavior.on_shutdown -> cancel_agent_runs.
            await services.shutdown()
            assert manager.store.is_pending(original.run_id)
        # Without graceful shutdown, closing the database models process loss.
        assert provider.observed == []
    finally:
        services.close()

    restarted_provider = SubmissionProvider()
    async with queue_services(tmp_path, restarted_provider) as reopened:
        await reopened.startup()
        manager = reopened._require_run_manager()
        finished = await asyncio.wait_for(manager.wait_settled(original.run_id), timeout=2)
        assert finished.status is RunStatus.SUCCEEDED
        assert [item[0] for item in restarted_provider.observed] == [prompt]
        replay = await submit(manager, agent.id, prompt, idempotency_key="survive-restart")
        assert replay.run_id == original.run_id
        assert len(manager.list_runs()) == 1


@pytest.mark.asyncio
async def test_claimed_submission_is_interrupted_and_never_replayed_after_restart(tmp_path):
    services = _services(tmp_path, SubmissionProvider())
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        original = await submit(manager, agent.id, idempotency_key="claimed-before-crash")
        assert manager.store.claim_submission(original.run_id)
        assert not manager.store.claim_submission(original.run_id)
    finally:
        services.close()

    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as reopened:
        await reopened.startup()
        manager = reopened._require_run_manager()
        await manager.dispatch_pending()
        replay = await submit(manager, agent.id, idempotency_key="claimed-before-crash")
        assert replay.run_id == original.run_id
        assert replay.status is RunStatus.INTERRUPTED
        assert not manager.store.is_pending(original.run_id)
        assert not manager.execution_in_progress(original.run_id)
        assert provider.observed == []


@pytest.mark.asyncio
async def test_queue_write_failure_rolls_back_run_and_idempotency_receipt(tmp_path):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        with services.database.transaction() as connection:
            connection.execute("""CREATE TRIGGER fail_submission BEFORE INSERT ON run_submissions
                BEGIN SELECT RAISE(ABORT, 'queue write failed'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="queue write failed"):
            await submit(manager, agent.id, idempotency_key="atomic-queue")
        assert manager.list_runs() == []
        assert provider.observed == []
        with services.database.transaction() as connection:
            connection.execute("DROP TRIGGER fail_submission")
        accepted = await submit(manager, agent.id, idempotency_key="atomic-queue")
        assert manager.store.is_pending(accepted.run_id)
        await manager.dispatch_pending()
        await asyncio.wait_for(manager.wait_settled(accepted.run_id), timeout=2)
        assert len(provider.observed) == 1


@pytest.mark.asyncio
async def test_dispatch_waits_for_capacity_without_claiming_later_submission(tmp_path):
    provider = SubmissionProvider(mode="block")
    async with queue_services(tmp_path, provider) as services:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        first = await submit(manager, agent.id, "first")
        second = await submit(manager, agent.id, "second")
        await manager.dispatch_pending()
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        assert [item[0] for item in provider.observed] == ["first"]
        assert manager.store.is_pending(second.run_id)
        assert not manager.store.is_pending(first.run_id)
        await manager.cancel_run(first.run_id)
        provider.started.clear()
        await manager.dispatch_pending()
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        assert [item[0] for item in provider.observed] == ["first", "second"]
        assert not manager.store.is_pending(second.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_agent", [False, True])
async def test_pending_stop_and_agent_deletion_do_not_contact_provider(tmp_path, delete_agent):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        queued = await submit(manager, agent.id)
        if delete_agent:
            await services.delete_card(agent.id)
        else:
            await manager.cancel_run(queued.run_id)
        await manager.dispatch_pending()
        cancelled = manager.get_run(queued.run_id)
        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.lifecycle["cleanup"] in {"none", "complete"}
        assert not manager.store.is_pending(queued.run_id)
        assert provider.stopped == []
        assert provider.observed == []


@pytest.mark.asyncio
async def test_dispatcher_restores_each_submission_context_without_ambient_leakage(tmp_path):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        manager = services._require_run_manager()
        conversation = await services.create_card(CardCreate(type="conversation", name="Sessions"))
        expected = {}
        runs = []
        for name in ("alice", "bob"):
            agent = await services.create_card(CardCreate(type="agent", name=name))
            context = replace(create_local_request_context(f"request-{name}"), actor=ActorRef("user", name))
            namespace = services.conversations.create_session(
                conversation.id, ConversationSessionCreate(title=name),
            ).id
            expected[name] = (context, namespace)
            with bind_request_context(context), state_session(namespace):
                runs.append(await manager.submit_run(agent.id, name, state_session_id=namespace))
        ambient = replace(create_local_request_context("unrelated-request"), actor=ActorRef("user", "unrelated"))
        with bind_request_context(ambient), state_session("unrelated-state"):
            manager.start_dispatcher()
            await asyncio.wait_for(asyncio.gather(*(manager.wait_settled(run.run_id) for run in runs)), timeout=2)
            assert current_request_context() == ambient
            assert active_state_session.get() == "unrelated-state"
        assert {prompt: (context, namespace) for prompt, context, namespace in provider.observed} == expected
        assert all(context.parent_run_id is None for context in provider.contexts)


@pytest.mark.asyncio
async def test_unavailable_runtime_ends_pending_submission_without_execution(tmp_path):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        queued = await submit(manager, agent.id)
        services.plugins.mark_unavailable("test.runtime-plugin", "test", ValueError("runtime removed"), source="test")
        await manager.dispatch_pending()
        assert manager.get_run(queued.run_id).status in {RunStatus.CANCELLED, RunStatus.FAILED}
        assert not manager.store.is_pending(queued.run_id)
        assert provider.observed == []


@pytest.mark.asyncio
async def test_cancel_during_dispatch_publication_cannot_launch_or_revive_agent(tmp_path, monkeypatch):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        queued = await submit(manager, agent.id)
        publishing = asyncio.Event()
        release = asyncio.Event()
        original_publish = type(manager)._publish_run

        async def pause_started(self, record, event_type):
            if record.run_id == queued.run_id and event_type is EventType.RUN_STARTED:
                publishing.set()
                await release.wait()
            await original_publish(self, record, event_type)

        monkeypatch.setattr(type(manager), "_publish_run", pause_started)
        dispatch = asyncio.create_task(manager.dispatch_pending())
        try:
            await asyncio.wait_for(publishing.wait(), timeout=2)
            cancelled = await asyncio.wait_for(manager.cancel_run(queued.run_id), timeout=2)
            assert cancelled.status is RunStatus.CANCELLED
        finally:
            release.set()
            await asyncio.wait_for(dispatch, timeout=2)
        assert not manager.execution_in_progress(queued.run_id)
        assert provider.observed == []
        assert services.world.get_card(agent.id).status == "idle"


@pytest.mark.asyncio
async def test_queue_limit_allows_replay_and_rejected_key_can_be_retried(tmp_path):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        manager = services._require_run_manager()
        manager.run_queue_limit = 1
        first_agent = await services.create_card(CardCreate(type="agent", name="First"))
        second_agent = await services.create_card(CardCreate(type="agent", name="Second"))
        first = await submit(manager, first_agent.id, idempotency_key="first")
        replay = await submit(manager, first_agent.id, idempotency_key="first")
        assert replay.run_id == first.run_id

        with pytest.raises(RuntimeUnavailableError, match="Run queue is full"):
            await submit(manager, second_agent.id, idempotency_key="second")
        assert [run.run_id for run in manager.list_runs()] == [first.run_id]

        await manager.cancel_run(first.run_id)
        second = await submit(manager, second_agent.id, idempotency_key="second")
        assert manager.store.is_pending(second.run_id)
        assert second.run_id != first.run_id


@pytest.mark.asyncio
async def test_dispatcher_retries_a_transient_pending_read_failure(tmp_path, monkeypatch, caplog):
    provider = SubmissionProvider()
    async with queue_services(tmp_path, provider) as services:
        manager = services._require_run_manager()
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        queued = await submit(manager, agent.id)
        calls = 0
        read_pending = type(manager.store).pending_submissions

        def transient_pending_read(store):
            nonlocal calls
            calls += 1
            if calls == 2:  # Allow startup's pending check; fail the running dispatcher's first read.
                raise sqlite3.OperationalError("temporary read failure")
            return read_pending(store)

        monkeypatch.setattr(type(manager.store), "pending_submissions", transient_pending_read)
        manager.start_dispatcher()
        completed = await asyncio.wait_for(manager.wait_settled(queued.run_id), timeout=3)
        assert completed.status is RunStatus.SUCCEEDED
        assert [item[0] for item in provider.observed] == ["queued work"]
        assert "Could not read pending Run submissions" in caplog.text
