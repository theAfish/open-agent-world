"""Host integration: real permissions, durable ledgers and controlled providers."""
import asyncio

import pytest

from backend.agents import AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.card_state import state_session
from backend.conversations.models import ConversationSessionCreate
from backend.errors import PermissionDeniedError
from backend.node_documents import DocumentActionRequest, invoke_document_action, read_document, write_document
from backend.services import create_services
from plugins.matcreator.tests.test_delegation import research, until, dispatch_args


@pytest.mark.asyncio
async def test_discovery_compact_cursor_and_explicit_report_read(research):
    services, ids, runtime, execute = research
    provider = WorldAgentCapabilityProvider(services)
    snapshot = await execute("collect", detail="summary")
    assert "value" not in snapshot["document"]
    assert snapshot["execution"]["executors"] == [{"library_id": ids["barracks"], "library_name": "Research Executors",
        "agent_id": ids["executor"], "agent_name": "Research Executor"}]
    assert (await execute("collect", detail="summary", since=snapshot["cursor"]))["unchanged"]
    assert services.summoning.snapshot(ids["barracks"])["library_id"] == ids["barracks"]
    found = {}
    async def script(context):
        current = await execute("collect", detail="summary")
        first = await execute("delegate", item_id=current["execution"]["items"][0]["id"],
            expected_revision=current["document"]["revision"], request_id="no-hidden-id", detail="summary")
        found["instance"] = first["attempt"]["instance_id"]
        await execute("wait", instance_ids=[found["instance"]], timeout_seconds=5)
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Discover and dispatch")
    await until(lambda: bool(runtime.children) or runtime.errors)
    assert not runtime.errors
    next(iter(runtime.children.values()))[2].set_result("x" * 12000)
    await services.run_manager.wait_execution(root.run_id)
    assert not runtime.errors
    compact = await execute("collect", detail="summary")
    assert "x" * 100 not in str(compact)
    detail = await execute("inspect", instance_id=found["instance"], detail="summary")
    assert detail["inspected"]["text"] == "x" * 12000
    executor_tools = {d.name for d in await provider.list_tools(next(iter(runtime.children.values()))[0].agent_id)}
    assert "publish_artifact" in executor_tools
    assert not {"start_sandbox", "stop_sandbox", "send_conversation_message"} & executor_tools


@pytest.mark.asyncio
async def test_finished_coordinator_wakes_in_origin_session_and_dispatches_downstream(research):
    services, ids, runtime, execute = research
    origin = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(
        title="Origin", participant_ids=[ids["agent"]]))
    other = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Other"))
    value = read_document(services, ids["tasks"])["value"]
    value["plans"][0]["tasks"] = [
        {"id": "prepare", "title": "Prepare", "acceptance": "Verified input"},
        {"id": "compute", "title": "Compute", "depends_on": ["prepare"], "acceptance": "Verified output"}]
    with state_session(origin.id):
        write_document(services, ids["tasks"], value, 0)
    turns = []
    async def script(context):
        turns.append(context)
        board = await execute("collect")
        plan = board["document"]["value"]["plans"][0]
        for task in plan["tasks"]:
            if task["status"] == "review":
                await invoke_document_action(services, ids["tasks"], "update_task", DocumentActionRequest(
                    expected_revision=board["document"]["revision"], arguments={"plan_id": plan["id"], "task_id": task["id"],
                        "status": "done", "result": "Independently verified output"}))
                board = await execute("collect")
        ready = next((i for i in board["execution"]["items"] if i["ready"]), None)
        if ready:
            await execute("delegate", item_id=ready["id"], request_id=ready["id"], expected_revision=board["document"]["revision"])
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Research", caller_kind="conversation",
        caller_id=ids["conversation"], context_id=origin.id)
    await services.run_manager.wait_execution(root.run_id)
    await until(lambda: len(runtime.children) == 1)
    with state_session(other.id):
        next(iter(runtime.children.values()))[2].set_result("Prepared input")
        await until(lambda: len(runtime.children) == 2 or runtime.errors)
        assert not runtime.errors
        list(runtime.children.values())[1][2].set_result("Computed output")
        await until(lambda: len(turns) == 3 or runtime.errors)
        await services.run_manager.wait_execution(turns[-1].run_id)
        assert read_document(services, ids["tasks"])["value"] == {"plans": []}
    assert not runtime.errors
    assert all(c.context_id == origin.id and c.root_run_id == root.run_id for c in turns)
    assert [c.caller.kind for c in turns] == ["conversation", "delegation", "delegation"]
    with state_session(origin.id):
        assert all(t["status"] == "done" for t in read_document(services, ids["tasks"])["value"]["plans"][0]["tasks"])
    await until(lambda: len(services.conversations.list_messages(ids["conversation"], origin.id)) >= 2)
    assert not services.conversations.list_messages(ids["conversation"], other.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["parent", "board", "permission"])
async def test_pending_notification_respects_stop_and_revocation(research, stop):
    services, ids, runtime, execute = research
    async def script(context):
        if context.caller.kind == "delegation":
            raise AssertionError("Stopped work was restarted")
        await execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "one"))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Start")
    await services.run_manager.wait_execution(root.run_id)
    await until(lambda: bool(runtime.children))
    if stop == "parent":
        await services.run_manager.cancel_run(root.run_id)
    elif stop == "board":
        await services.node_execution.stop(ids["tasks"])
    else:
        edge = next(e for e in services.world.list_edges() if e.source == ids["agent"] and e.target == ids["tasks"])
        await services.delete_edge(edge.id)
        next(iter(runtime.children.values()))[2].set_result("Done")
    await until(lambda: not services.node_execution.continuation_keys)
    assert not runtime.errors
    assert len([c for c in runtime.contexts if c.agent_id == ids["agent"]]) == 1


@pytest.mark.asyncio
async def test_explicit_collect_consumes_notification_but_ui_poll_does_not(research):
    services, ids, runtime, execute = research
    received = asyncio.Event()
    finish = asyncio.Event()
    async def script(context):
        await execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "one"))
        await received.wait()
        await execute("collect")
        finish.set()
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Wait")
    await until(lambda: bool(runtime.children))
    child, (_, _, gate) = next(iter(runtime.children.items()))
    gate.set_result("Result")
    await services.run_manager.wait_execution(child)
    await services.node_execution.delegation_action(ids["tasks"], "collect", {})
    assert not services.node_execution.state(ids["tasks"])["attempts"][0].get("observed_by_run")
    received.set()
    await services.run_manager.wait_execution(root.run_id)
    await until(lambda: not services.node_execution.continuation_keys)
    assert finish.is_set() and len(runtime.contexts) == 2 and not runtime.errors


@pytest.mark.asyncio
async def test_deferred_external_check_survives_restart_once(research):
    services, ids, runtime, execute = research
    async def script(context):
        await execute("defer", request_id="remote-123", delay_seconds=60, reason="Poll remote job 123", external_jobs=["bohr:123"])
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Wait externally")
    await services.run_manager.wait_execution(root.run_id)
    assert not runtime.errors
    await services.shutdown()
    state = services.node_execution.state(ids["tasks"])
    state["wakeups"][0]["due_at"] = 0
    services.node_execution.save(ids["tasks"], state)
    async def resumed(context):
        assert context.context_id == root.context_id and context.root_run_id == root.run_id
    runtime.script = resumed
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await until(lambda: len(runtime.contexts) == 2)
        await restarted.run_manager.wait_execution(runtime.contexts[-1].run_id)
        await until(lambda: not restarted.node_execution.continuation_keys)
        assert not runtime.errors
        await restarted.shutdown()
        again = create_services(services.settings, plugins=services.plugins)
        try:
            await again.startup()
            await until(lambda: not again.node_execution.continuation_keys)
            assert len(runtime.contexts) == 2
        finally:
            await again.shutdown()
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_executor_report_is_scoped_and_schedules_real_job_check(research):
    services, ids, runtime, execute = research
    provider = WorldAgentCapabilityProvider(services)
    base_execute = runtime.execute
    checked = {}
    async def reporting(config, context, runtime_input):
        async for event in base_execute(config, context, runtime_input):
            if context.caller.kind == "summon" and event.type == AgentEventType.MESSAGE:
                tools = await provider.list_tools(context.agent_id)
                assert "report_delegated_task" in {t.name for t in tools}
                checked["report"] = await provider.invoke_tool(context.agent_id, "host:report_delegated_task", {
                    "outcome": "waiting", "summary": "Probe submitted", "outputs": ["probe/input.dat"],
                    "external_jobs": ["bohr:456"], "next_step": "Check probe logs before relaxation", "check_after_seconds": 300})
            yield event
    runtime.execute = reporting
    async def script(context):
        first = await execute("delegate", **dispatch_args(ids, await execute("collect"), 0, "probe"))
        checked["result"] = await execute("wait", instance_ids=[first["attempt"]["instance_id"]], timeout_seconds=5)
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Probe")
    await until(lambda: bool(runtime.children))
    next(iter(runtime.children.values()))[2].set_result("Probe is pending")
    await services.run_manager.wait_execution(root.run_id)
    assert not runtime.errors and checked["report"]["recorded"]
    state = services.node_execution.state(ids["tasks"])
    assert state["attempts"][0]["report"]["outcome"] == "waiting"
    assert len(state["wakeups"]) == 1 and state["wakeups"][0]["external_jobs"] == ["bohr:456"]
    assert checked["result"]["document"]["value"]["plans"][0]["tasks"][0]["status"] == "review"
    with pytest.raises(PermissionDeniedError):
        await provider.invoke_tool(ids["agent"], "host:report_delegated_task", {"outcome": "partial", "summary": "forged", "next_step": "retry"})
    await services.node_execution.stop(ids["tasks"])


@pytest.mark.asyncio
async def test_cancel_defer_and_deduplication(research):
    services, ids, runtime, execute = research
    async def script(context):
        args = dict(request_id="poll", delay_seconds=1, reason="Poll existing job")
        await execute("defer", **args)
        await execute("defer", **args)
        await execute("cancel_defer", request_id="poll")
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Schedule then cancel")
    await services.run_manager.wait_execution(root.run_id)
    await until(lambda: not services.node_execution.continuation_keys)
    assert not runtime.errors and len(runtime.contexts) == 1
    assert len(services.node_execution.state(ids["tasks"])["wakeups"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", ["human", "stop_agent"])
async def test_idle_scheduled_check_can_be_stopped(research, cancel):
    services, ids, runtime, execute = research
    async def script(context):
        await execute("defer", request_id="later", delay_seconds=60, reason="Inspect remote state")
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Wait")
    await services.run_manager.wait_execution(root.run_id)
    if cancel == "human":
        await services.node_execution.delegation_action(ids["tasks"], "cancel_defer", {"request_id": "later"})
    else:
        await services.stop_agent(ids["agent"])
    await until(lambda: not services.node_execution.continuation_keys)
    assert len(runtime.contexts) == 1 and not runtime.errors
    assert not services.node_execution.active(ids["tasks"])


@pytest.mark.asyncio
async def test_simultaneous_results_are_coalesced_and_never_overlap_busy_coordinator(research):
    services, ids, runtime, execute = research
    release = asyncio.Event()
    notifications = []
    async def script(context):
        if context.caller.kind == "delegation":
            notifications.append(context)
            await execute("collect")
            return
        for index in (0, 1):
            await execute("delegate", **dispatch_args(ids, await execute("collect"), index, f"dispatch-{index}"))
        await release.wait()
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Two children")
    await until(lambda: len(runtime.children) == 2)
    for _, _, gate in runtime.children.values():
        gate.set_result("Result")
    for run in runtime.children:
        await services.run_manager.wait_execution(run)
    # Force an event-loop pass while the original coordinator still owns its turn.
    services.node_execution.signal_continuations()
    await asyncio.sleep(.05)
    assert not notifications
    release.set()
    await services.run_manager.wait_execution(root.run_id)
    await until(lambda: len(notifications) == 1 or runtime.errors)
    await services.run_manager.wait_execution(notifications[0].run_id)
    await until(lambda: not services.node_execution.continuation_keys)
    assert len(notifications) == 1 and not runtime.errors


@pytest.mark.asyncio
async def test_unadmitted_claim_is_recovered_without_replaying_an_admitted_turn(research):
    from uuid import uuid4
    services, ids, runtime, execute = research
    turns = []
    async def script(context):
        turns.append(context)
        if len(turns) == 1:
            await execute("defer", request_id="crash-gap", delay_seconds=60, reason="Check existing job")
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Schedule")
    await services.run_manager.wait_execution(root.run_id)
    state = services.node_execution.state(ids["tasks"])
    state["wakeups"][0].update(notification_run_id=str(uuid4()), due_at=0)
    services.node_execution.save(ids["tasks"], state)
    services.node_execution.signal_continuations()
    await until(lambda: len(turns) == 2 or runtime.errors)
    await services.run_manager.wait_execution(turns[-1].run_id)
    await until(lambda: not services.node_execution.continuation_keys)
    services.node_execution.watch_continuations(ids["tasks"])
    await until(lambda: not services.node_execution.continuation_keys)
    assert len(turns) == 2 and not runtime.errors


@pytest.mark.asyncio
async def test_continuation_budget_is_retained_across_followup_turns(research, monkeypatch):
    import backend.node_continuations as continuations
    monkeypatch.setattr(continuations, "MAX_CONTINUATION_TURNS", 1)
    services, ids, runtime, execute = research
    async def script(context):
        await execute("defer", request_id=context.run_id, delay_seconds=1, reason="Check remote job")
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Start bounded work")
    await services.run_manager.wait_execution(root.run_id)
    await until(lambda: len(runtime.contexts) == 2 or runtime.errors)
    await services.run_manager.wait_execution(runtime.contexts[-1].run_id)
    await until(lambda: any(w.get("notification_error") for w in services.node_execution.state(ids["tasks"])["wakeups"]))
    assert len(runtime.contexts) == 2 and not runtime.errors
    assert "limit" in services.node_execution.state(ids["tasks"])["wakeups"][-1]["notification_error"]


@pytest.mark.asyncio
async def test_restart_recovers_continuation_transcript_once_in_origin_session(research):
    services, ids, runtime, _ = research
    origin = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(
        title="Origin", participant_ids=[ids["agent"]]))
    other = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Other"))
    async def script(context):
        pass
    runtime.script = script
    manager = services.run_manager
    root = await manager.start_run(ids["agent"], "Research", caller_kind="conversation",
        caller_id=ids["conversation"], context_id=origin.id)
    await manager.wait_execution(root.run_id)
    # Admission and execution succeeded, but a crash prevented the transcript writer.
    followup = await manager.start_run(ids["agent"], "Inspect completed work", caller_kind="delegation",
        caller_id=ids["tasks"], context_id=origin.id, parent_run_id=root.run_id,
        initial_lifecycle={"work_continuation": True})
    await manager.wait_execution(followup.run_id)
    assert not services.conversations.list_messages(ids["conversation"], origin.id)
    await services.shutdown()
    for _ in range(2):
        restored = create_services(services.settings, plugins=services.plugins)
        try:
            await restored.startup()
            messages = restored.conversations.list_messages(ids["conversation"], origin.id)
            assert [m.run_id for m in messages] == [root.run_id, followup.run_id]
            assert all(m.content == "Coordinator finished" for m in messages)
            assert not restored.conversations.list_messages(ids["conversation"], other.id)
        finally:
            await restored.shutdown()
            restored.close()
