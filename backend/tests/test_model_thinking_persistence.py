"""Native Run state retains only observed provider text, outside model context."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from backend.agents.model_observation import ModelTrace
from backend.agents.request_recovery import recover_request
from backend.errors import NotFoundError, PermissionDeniedError
from backend.persistence.database import Database
from backend.plugins import create_builtin_registry
from backend.runs.model_checkpoints import RunModelCheckpoints
from backend.runs.reasoning import RunReasoning
from backend.runs.store import RunStore
from backend.state import StateContext, StateStore


@pytest.fixture
def native(tmp_path):
    db = Database(tmp_path / "world.sqlite3")
    state = StateStore(db, create_builtin_registry())
    run = RunStore(db).create(agent_id="test", runtime_provider_id="test", caller_kind="user")
    scope = state.ensure_scope("run", run.run_id)
    checkpoint = RunModelCheckpoints(db, run.run_id, state_store=state)
    yield db, state, run, scope, checkpoint
    db.close()


def partial(text):
    return NS(parts=[NS(text=text, thought=True)])


def test_full_long_thought_persists_after_restart_but_live_preview_stays_bounded(native):
    db, state, run, scope, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    trace.begin("builder", NS(contents=[]))
    text = "观察完整的思考。\n" * 16000
    trace.observe_partial("builder", partial(text[:60000]))
    trace.observe_partial("builder", partial(text[60000:]))
    event = trace._reasoning_event("builder", 1, trace._reasoning["builder"], final=True)
    assert len(event["text"]) == 64000 and event["truncated"]
    db2 = Database(db.path)
    try:
        reopened = RunReasoning(StateStore(db2, create_builtin_registry()), run.run_id)
        row = reopened.get(event["provider_message_id"])
        assert row["text"] == text and not row["truncated"] and not row["streaming"]
        assert row["revision"] == 2  # first live save, then final; not per chunk
        page = reopened.page(0, 50)
        assert len(page["items"][0]["text"]) == 2000 and page["items"][0]["preview_truncated"]
    finally:
        db2.close()
    api = state.api(StateContext((scope,)), permissions={"*"})
    with pytest.raises(PermissionDeniedError):
        api.get(scope, "model_reasoning")
    with pytest.raises(NotFoundError):
        api.resolve("model_reasoning")
    assert "model_reasoning" not in api.snapshot().values
    for method in (api.set, api.patch, api.delete):
        with pytest.raises(PermissionDeniedError):
            if method == api.delete:
                method(scope, "model_reasoning")
            else:
                method(scope, "model_reasoning", {})
    child = state.ensure_scope("run", "child")
    assert "model_reasoning" not in state.snapshot(StateContext((scope, child))).values


def test_redaction_across_chunks_and_final_suffix(native):
    _, _, run, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), api_key="sk-long-secret", checkpoint_store=checkpoints)
    trace.begin("planner", NS(contents=[]))
    trace.observe_partial("planner", partial("safe reasoning with sk-long-"))
    trace.observe_partial("planner", partial("secret in its middle, followed by safe ending"))
    trace._reasoning_event("planner", 1, trace._reasoning["planner"], final=True)
    rows = checkpoints.reasoning.records()
    assert "sk-long-secret" not in json.dumps(rows)
    assert rows["model-reasoning:planner:1"]["text"].endswith("safe ending")


def test_interrupted_retry_and_roles_never_overwrite_completed_requests(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    trace.begin("builder", NS(contents=[]))
    trace.observe_partial("builder", partial("first attempt"))
    trace.interrupted_reasoning("builder")
    trace.request_attempts["builder"] = 2
    trace._reasoning_event("builder", 1, "second attempt", final=True)
    trace.begin("planner", NS(contents=[]))
    trace._reasoning_event("planner", 2, "plan", final=True)
    rows = checkpoints.reasoning.records()
    assert len(rows) == 3
    assert rows["model-reasoning:builder:1"]["interrupted"]
    assert rows["model-reasoning:builder:1:attempt2"]["text"] == "second attempt"
    revision = rows["model-reasoning:builder:1:attempt2"]["revision"]
    trace._reasoning_event("builder", 1, "stale snapshot", final=False)
    checkpoints.reasoning.interrupt()
    assert checkpoints.reasoning.records()["model-reasoning:builder:1:attempt2"]["revision"] == revision
    assert checkpoints.reasoning.records()["model-reasoning:builder:1:attempt2"]["text"] == "second attempt"


@pytest.mark.asyncio
async def test_cancel_persists_latest_received_tail_even_without_event_consumer(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    class Request(NS):
        def model_copy(self, *, update):
            return Request(**(vars(self) | update))
    request = Request(contents=[], config=None, tools_dict={})
    trace.begin("builder", request)
    received = asyncio.Event()
    class Provider:
        async def generate_content_async(self, request, stream=False):
            yield NS(partial=True, content=partial("initial"))
            yield NS(partial=True, content=partial(" received tail"))
            received.set()
            await asyncio.Event().wait()
    async def consume():
        async for _ in recover_request(Provider(), request, trace, "builder"):
            pass
    task = asyncio.create_task(consume())
    await asyncio.wait_for(received.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = next(iter(checkpoints.reasoning.records().values()))
    assert row["text"] == "initial received tail"
    assert row["interrupted"] and not row["streaming"]


def test_restart_closes_streaming_snapshots_and_preserves_completed(native):
    _, _, _, _, checkpoints = native
    trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
    trace.begin("builder", NS(contents=[]))
    trace._reasoning_event("builder", 1, "unfinished", final=False)
    trace.begin("planner", NS(contents=[]))
    trace._reasoning_event("planner", 2, "finished", final=True)
    checkpoints.reasoning.interrupt()
    rows = checkpoints.reasoning.records()
    assert rows["model-reasoning:builder:1"]["interrupted"]
    assert not rows["model-reasoning:planner:2"]["interrupted"]


def test_native_run_reasoning_api_is_paginated_and_run_scoped(client):
    services = client.app.state.services
    runs = RunStore(services.database)
    run = runs.create(agent_id="agent", runtime_provider_id="test", caller_kind="user")
    other = runs.create(agent_id="other", runtime_provider_id="test", caller_kind="user")
    services.state.ensure_scope("run", run.run_id)
    archive = RunReasoning(services.state, run.run_id)
    for i in range(3):
        archive.save({"provider_message_id": f"request:{i}", "text": "x" * 100000,
            "streaming": False, "interrupted": False, "truncated": False})
    page = client.get(f"/api/runs/{run.run_id}/reasoning?limit=2").json()
    assert len(page["items"]) == 2 and page["next_offset"] == 2
    assert len(page["items"][0]["text"]) == 2000
    full = client.get(f"/api/runs/{run.run_id}/reasoning/request%3A0")
    assert full.status_code == 200 and len(full.json()["text"]) == 100000
    assert client.get(f"/api/runs/{other.run_id}/reasoning/request%3A0").status_code == 404
    assert client.get("/api/runs/missing/reasoning").status_code == 404
    assert client.get(f"/api/runs/{run.run_id}/reasoning?limit=1000").status_code == 422
    archive.save({"provider_message_id": "provider/message/1", "text": "slash identity", "streaming": False})
    assert client.get(f"/api/runs/{run.run_id}/reasoning/provider%2Fmessage%2F1").json()["text"] == "slash identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_manager_startup_closes_committed_live_records_even_on_terminal_run(tmp_path, terminal):
    from backend.tests.test_runs import RecordingProvider, _services
    from backend.runs import RunStatus
    services = _services(tmp_path, RecordingProvider())
    try:
        run = services.run_manager.store.create(agent_id="fixture", runtime_provider_id="fixture", caller_kind="user")
        services.state.ensure_scope("run", run.run_id)
        services.run_manager.store.update_status(run.run_id, RunStatus.FAILED if terminal else RunStatus.RUNNING)
        archive = RunReasoning(services.state, run.run_id)
        archive.save({"provider_message_id": "pending", "text": "committed before restart", "streaming": True})
        await services.run_manager.startup()
        row = archive.get("pending")
        assert row["interrupted"] and not row["streaming"] and row["text"] == "committed before restart"
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_run_manager_injects_native_storage_and_drains_on_stop(tmp_path, cancel):
    from backend.agents import AgentEvent, AgentEventType
    from backend.tests.test_runs import RecordingProvider, _services
    from backend.world.models import CardCreate
    class Provider(RecordingProvider):
        def __init__(self):
            super().__init__()
            self.reasoning_store = None
        async def execute(self, config, context, runtime_input):
            checkpoints = RunModelCheckpoints(self.reasoning_store.database, context.run_id,
                                             state_store=self.reasoning_store)
            trace = ModelTrace(asyncio.Queue(), checkpoint_store=checkpoints)
            trace.begin("agent", NS(contents=[]))
            trace.observe_partial("agent", partial("initial thought"))
            trace.observe_partial("agent", partial(" plus last received tail"))
            self.started.set()
            try:
                if cancel:
                    await asyncio.Event().wait()
                await trace.callbacks("agent")["after_model_callback"](None, NS(partial=False,
                    content=partial("initial thought plus last received tail"), usage_metadata=None, finish_reason="STOP"))
                while not trace.queue.empty():
                    _, payload = trace.queue.get_nowait()
                    yield AgentEvent(context.agent_id, context.run_id, AgentEventType.PROGRESS, payload)
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                                 {"text": "Done"}, run_status="succeeded")
            finally:
                trace.interrupted_reasoning("agent")
    provider = Provider()
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Thinking fixture"))
        manager = services.run_manager
        run = await manager.start_run(agent.id, "build")
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        if cancel:
            await manager.cancel_run(run.run_id)
        settled = await manager.wait_execution(run.run_id)
        archive = RunReasoning(services.state, run.run_id)
        row = next(iter(archive.records().values()))
        assert row["text"] == "initial thought plus last received tail"
        assert row["interrupted"] == cancel and not row["streaming"]
        assert settled.status.value == ("cancelled" if cancel else "succeeded")
        assert settled.lifecycle["execution"] == "finished"
        assert "initial thought" not in json.dumps(settled.lifecycle)
        assert manager.final_text(run.run_id) == ("" if cancel else "Done")
        with services.database.locked() as db:
            assert db.execute("SELECT COUNT(*) FROM conversation_messages WHERE run_id=?", (run.run_id,)).fetchone()[0] == 0
    finally:
        services.close()
