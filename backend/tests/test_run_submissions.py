from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager

import httpx
import pytest

from backend.idempotency import IdempotencyStore
from backend.events.models import EventType
from backend.main import create_app
from backend.request_context import bind_request_context, create_local_request_context
from backend.runs import RunStatus
from backend.tests.test_runs import RecordingProvider, _services
from backend.world.models import CardCreate


@asynccontextmanager
async def api(tmp_path, provider):
    services = _services(tmp_path, provider)
    app = create_app(services.settings, services=services)
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                yield services, client
    finally:
        services.close()


@pytest.mark.asyncio
async def test_competing_submissions_replay_before_capacity_and_check_all_input(tmp_path):
    provider = RecordingProvider(mode="block")
    async with api(tmp_path, provider) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        headers = {"Idempotency-Key": "submit-1"}
        body = {"prompt": "work", "fresh_context": True}
        responses = await asyncio.gather(*[
            client.post(url, headers=headers, json=body) for _ in range(8)
        ])
        assert all(response.status_code == 202 for response in responses)
        assert all(response.json() == responses[0].json() for response in responses)
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        assert len(provider.contexts) == 1
        assert len(services._require_run_manager().list_runs()) == 1
        for change in (
            {"prompt": "different"}, {"fresh_context": False}, {"detached": True},
            {"task_id": "another-task"}, {"parent_run_id": "another-parent"},
        ):
            conflict = await client.post(url, headers=headers, json={**body, **change})
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["error"]["code"] == "idempotency_conflict"
        invalid = await client.post(url, headers={"Idempotency-Key": "with space"}, json=body)
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_idempotency_key"
        # Omitting the key keeps normal admission, even for the same prompt.
        assert (await client.post(url, json=body)).status_code == 503


@pytest.mark.asyncio
async def test_finished_submission_replays_and_new_key_starts_fresh_context(tmp_path):
    provider = RecordingProvider()
    async with api(tmp_path, provider) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        body = {"prompt": "work", "fresh_context": True}
        first = await client.post(url, headers={"Idempotency-Key": "first"}, json=body)
        manager = services._require_run_manager()
        finished = await manager.wait_settled(first.json()["run_id"])
        assert finished.status is RunStatus.SUCCEEDED
        replay = await client.post(url, headers={"Idempotency-Key": "first"}, json=body)
        assert replay.status_code == 202 and replay.json() == first.json()
        second = await client.post(url, headers={"Idempotency-Key": "second"}, json=body)
        another = await manager.wait_settled(second.json()["run_id"])
        assert another.run_id != finished.run_id
        assert another.context_id != finished.context_id
        assert all(run.context_id.startswith("isolated:") for run in (finished, another))
        assert len(provider.contexts) == 2


@pytest.mark.asyncio
async def test_rejected_admission_does_not_consume_submission_key(tmp_path):
    provider = RecordingProvider(mode="block")
    async with api(tmp_path, provider) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        first = await client.post(url, json={"prompt": "busy"})
        headers = {"Idempotency-Key": "retry-when-free"}
        rejected = await client.post(url, headers=headers, json={"prompt": "next"})
        assert rejected.status_code == 503
        manager = services._require_run_manager()
        await manager.cancel_run(first.json()["run_id"])
        accepted = await client.post(url, headers=headers, json={"prompt": "next"})
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["run_id"] != first.json()["run_id"]


@pytest.mark.asyncio
async def test_receipt_write_failure_rolls_back_run_and_allows_retry(tmp_path):
    provider = RecordingProvider()
    async with api(tmp_path, provider) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        IdempotencyStore(services.database)
        with services.database.transaction() as connection:
            connection.execute("""CREATE TRIGGER fail_receipt BEFORE INSERT ON idempotency_results
                BEGIN SELECT RAISE(ABORT, 'receipt write failed'); END""")
        url = f"/api/agents/{agent.id}/run"
        headers = {"Idempotency-Key": "atomic-submit"}
        with pytest.raises(sqlite3.IntegrityError, match="receipt write failed"):
            await client.post(url, headers=headers, json={"prompt": "work"})
        manager = services._require_run_manager()
        assert manager.list_runs() == []
        assert provider.contexts == []
        with services.database.transaction() as connection:
            connection.execute("DROP TRIGGER fail_receipt")
        accepted = await client.post(url, headers=headers, json={"prompt": "work"})
        assert accepted.status_code == 202
        await manager.wait_settled(accepted.json()["run_id"])
        assert len(provider.contexts) == 1


@pytest.mark.asyncio
async def test_post_commit_failure_survives_restart_without_relaunch(tmp_path, monkeypatch):
    provider = RecordingProvider()
    headers = {"Idempotency-Key": "lost-response"}
    body = {"prompt": "work", "fresh_context": True}
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        manager = services._require_run_manager()

        async def fail_publish(*args, **kwargs):
            raise RuntimeError("interrupted after commit")

        with monkeypatch.context() as patch, bind_request_context(create_local_request_context()):
            patch.setattr(type(manager), "_publish_run", fail_publish)
            with pytest.raises(RuntimeError, match="interrupted after commit"):
                await services.run_agent(agent.id, "work", fresh_context=True,
                                         idempotency_key=headers["Idempotency-Key"])
        [original] = manager.list_runs()
        assert provider.contexts == []
    finally:
        # Model process loss: close storage without graceful Run cancellation.
        services.close()
    restarted_provider = RecordingProvider()
    async with api(tmp_path, restarted_provider) as (services, client):
        replay = await client.post(url, headers=headers, json=body)
        assert replay.status_code == 202
        assert replay.json()["run_id"] == original.run_id
        manager = services._require_run_manager()
        assert manager.get_run(original.run_id).status is RunStatus.INTERRUPTED
        assert not manager.execution_in_progress(original.run_id)
        assert restarted_provider.contexts == []
        assert len(manager.list_runs()) == 1


@pytest.mark.asyncio
async def test_replay_rechecks_agent_availability(tmp_path):
    async with api(tmp_path, RecordingProvider()) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        headers = {"Idempotency-Key": "removed-agent"}
        first = await client.post(url, headers=headers, json={"prompt": "work"})
        await services._require_run_manager().wait_settled(first.json()["run_id"])
        await services.delete_card(agent.id)
        replay = await client.post(url, headers=headers, json={"prompt": "work"})
        assert replay.status_code == 404


@pytest.mark.asyncio
async def test_queued_http_submission_waits_for_capacity_and_can_be_cancelled(tmp_path):
    provider = RecordingProvider(mode="block")
    async with api(tmp_path, provider) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        first = await client.post(url, json={"prompt": "occupy capacity"})
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        headers = {"Idempotency-Key": "queued-work"}
        body = {"prompt": "work later", "enqueue": True, "fresh_context": True}
        queued = await client.post(url, headers=headers, json=body)
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["run_id"]
        status = (await client.get(f"/api/runs/{run_id}")).json()
        assert status["status"] == "created"
        assert status["lifecycle"]["execution"] == "queued"
        assert not status["lifecycle"]["holds_capacity"]
        replay = await client.post(url, headers=headers, json=body)
        assert replay.json() == queued.json()
        changed = await client.post(url, headers=headers, json={**body, "prompt": "changed"})
        assert changed.status_code == 409
        immediate = await client.post(url, headers=headers, json={**body, "enqueue": False})
        assert immediate.status_code == 409
        assert len(provider.contexts) == 1
        cancelled = await client.post(f"/api/runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        await services._require_run_manager().cancel_run(first.json()["run_id"])
        await services._require_run_manager().dispatch_pending()
        assert len(provider.contexts) == 1


@pytest.mark.asyncio
async def test_queued_http_input_survives_application_shutdown_and_reopen(tmp_path):
    body = {"prompt": "accepted input survives restart", "enqueue": True}
    headers = {"Idempotency-Key": "restart-queued"}
    async with api(tmp_path, RecordingProvider(mode="block")) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        url = f"/api/agents/{agent.id}/run"
        active = await client.post(url, json={"prompt": "occupy capacity"})
        queued = await client.post(url, headers=headers, json=body)
        assert queued.status_code == 202
        queued_id = queued.json()["run_id"]
    provider = RecordingProvider()
    async with api(tmp_path, provider) as (services, client):
        manager = services._require_run_manager()
        finished = await asyncio.wait_for(manager.wait_terminal(queued_id), timeout=2)
        await manager.wait_execution(queued_id)
        assert finished.status is RunStatus.SUCCEEDED
        assert manager.final_text(queued_id) == body["prompt"]
        assert [context.run_id for context in provider.contexts] == [queued_id]
        assert manager.get_run(active.json()["run_id"]).status is RunStatus.CANCELLED
        replay = await client.post(url, headers=headers, json=body)
        assert replay.json() == queued.json()


@pytest.mark.asyncio
async def test_queued_http_response_failure_does_not_lose_or_duplicate_execution(tmp_path, monkeypatch):
    provider = RecordingProvider()
    async with api(tmp_path, provider) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        manager = services._require_run_manager()
        publish = type(manager)._publish_run

        async def fail_receipt_notification(self, record, event_type):
            if event_type is EventType.RUN_CREATED:
                raise RuntimeError("response lost after queue commit")
            await publish(self, record, event_type)

        url = f"/api/agents/{agent.id}/run"
        body = {"prompt": "saved before acknowledgement", "enqueue": True}
        headers = {"Idempotency-Key": "lost-queued-response"}
        with monkeypatch.context() as patch:
            patch.setattr(type(manager), "_publish_run", fail_receipt_notification)
            with pytest.raises(RuntimeError, match="response lost after queue commit"):
                await client.post(url, headers=headers, json=body)
        [original] = manager.list_runs()
        replay = await client.post(url, headers=headers, json=body)
        assert replay.status_code == 202, replay.text
        assert replay.json()["run_id"] == original.run_id
        finished = await asyncio.wait_for(manager.wait_settled(original.run_id), timeout=2)
        assert finished.status is RunStatus.SUCCEEDED
        assert len(provider.contexts) == 1


@pytest.mark.asyncio
async def test_http_cancel_during_drain_does_not_acknowledge_preserved_work(tmp_path):
    async with api(tmp_path, RecordingProvider(mode="block")) as (services, client):
        agent = await services.create_card(CardCreate(type="agent", name="Atlas"))
        await client.post(f"/api/agents/{agent.id}/run", json={"prompt": "occupy capacity"})
        queued = await client.post(f"/api/agents/{agent.id}/run", json={"prompt": "later", "enqueue": True})
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        manager = services._require_run_manager()
        await manager.stop_dispatcher()
        assert (await client.post(f"/api/runs/{run_id}/cancel")).status_code == 503
        assert (await client.post(f"/api/agents/{agent.id}/stop")).status_code == 503
        assert manager.store.is_pending(run_id)
