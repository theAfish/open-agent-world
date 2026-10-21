"""Real host persistence/permissions with controlled compute and model providers."""
import asyncio
import sqlite3
from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.agents import AgentEvent, AgentEventType
from backend.conversations.models import ConversationSessionCreate, ConversationPost
from backend.errors import ConflictError, PermissionDeniedError
from backend.plugins.background_jobs import BackgroundJobProvider, JobObservation
from backend.services import create_services
from backend.api.card_library import plugin_usage
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_node_delegation import research, until
from backend.tests.conversation_helpers import wait_for_conversation_delivery


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "md"


class Compute:
    status = "running"
    submissions = 0
    probes = 0
    fail_submit = False
    fail_probe = False

    async def submit(self, context, spec, request_id):
        self.submissions += 1
        await asyncio.sleep(0)
        if self.fail_submit:
            raise TimeoutError("response lost after remote admission")
        return "remote-123"

    async def inspect(self, context, external_id):
        self.probes += 1
        if self.fail_probe:
            raise ConnectionError("offline")
        return JobObservation(status=self.status)

    async def cancel(self, context, external_id):
        pass  # Request acceptance is deliberately not termination evidence.


def setup(research):
    services, ids, runtime, _ = research
    compute = Compute()
    install_test_plugin(services.plugins, "test.jobs", lambda registration:
        registration.register_background_job_provider("test.compute", BackgroundJobProvider(
            specification=Spec, submit=compute.submit, inspect=compute.inspect, cancel=compute.cancel, poll_interval_seconds=1)))
    services.node_execution.continuation_closed = True
    async def invoke(name, **args):
        return await WorldAgentCapabilityProvider(services).invoke_tool(ids["agent"], "host:" + name, args)
    request = dict(provider="test.compute", sandbox_id=ids["sandbox"], request_key="plan:md", specification={})
    return services, ids, runtime, compute, invoke, request


@pytest.mark.asyncio
async def test_unsafe_probe_stops_monitoring_without_rewriting_durable_job(research, monkeypatch):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def submit(context):
        captured.update(await invoke('submit_background_job', **request))
    runtime.script = submit
    run = await services.run_manager.start_run(ids['agent'], 'submit')
    await services.run_manager.wait_execution(run.run_id)
    before = services.background_jobs.get(captured['id'])
    async def corrupt(*args):
        raise sqlite3.DatabaseError('fault injected in provider observation')
    definition = services.plugins.background_job_provider('test.compute')
    monkeypatch.setattr(services.plugins, 'background_job_provider', lambda _: replace(definition, inspect=corrupt))
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values(), return_exceptions=True)
    with pytest.raises(sqlite3.DatabaseError):
        await services.background_jobs.tick()
    assert services.background_jobs.get(captured['id']) == before
    services.node_execution.continuation_closed = False
    with pytest.raises(sqlite3.DatabaseError):
        await services.node_execution._continue_work()
    assert services.node_execution.continuation_closed
    with pytest.raises(sqlite3.DatabaseError):
        await services.background_jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('narrate', [False, True])
async def test_wait_has_durable_system_notice_and_resumes_once(research, monkeypatch, narrate):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def execute(config, context, runtime_input):
        runtime.contexts.append(context)
        if context.extensions.get('background_job_notification'):
            assert context.extensions['background_job_notification'] == captured['id']
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE, {'text': 'Collected the result.'})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                {'text': 'Collected the result.'}, run_status='succeeded')
            return
        if narrate:
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                {'text': 'Re-planning the remaining steps.', 'final': False})
        captured.update(await invoke('submit_background_job', **request))
        await invoke('wait_background_jobs', job_ids=[captured['id']])
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
            {'text': ''}, run_status='succeeded')
    monkeypatch.setattr(runtime, 'execute', execute)
    session = await services.create_conversation_session(ids['conversation'],
        ConversationSessionCreate(participant_ids=[ids['agent']]))
    await services.post_conversation_message(ids['conversation'], session.id,
        ConversationPost(content='Compute', mention_agent_ids=[ids['agent']]))
    await wait_for_conversation_delivery(services, ids['conversation'], session.id)
    page = services.conversations.page_messages(ids['conversation'], session.id)
    notice = page.items[-1]
    assert notice.sender_kind == 'system' and notice.kind == 'run_outcome'
    assert '1 background job' in notice.content and 'automatic monitoring' in notice.content
    assert 'conversation resumes' in notice.content
    assert request['request_key'] in notice.content and request['provider'] in notice.content
    assert captured['id'] in notice.content and 'remote-123' in notice.content
    assert 'Find these jobs by name or ID in Jobs.' in notice.content
    assert 'without producing' not in notice.content
    assert len(page.items) == (3 if narrate else 2)
    root = services.run_manager.get_run(notice.run_id)
    assert root.status == 'succeeded'  # Only this bounded turn has finished.
    assert services.background_jobs.get(captured['id'])['status'] == 'queued'
    assert root.lifecycle['awaiting_jobs'] == [captured['id']]
    assert page.run_summaries[root.run_id].final_message_id is None
    assert services.run_manager.final_text(root.run_id) == ''
    assert not page.active_runs and not services.run_manager.holds_agent_slot(root.run_id)
    await services._persist_conversation_run(ids['agent'], root.run_id, ids['conversation'], session.id)
    assert [m.id for m in services.conversations.page_messages(ids['conversation'], session.id).items] == [m.id for m in page.items]
    assert services.run_manager.get_run(root.run_id).updated_at == root.updated_at

    compute.status = 'succeeded'
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    await until(lambda: len(runtime.contexts) == 2)
    child = runtime.contexts[-1]
    await services.run_manager.wait_execution(child.run_id)
    await until(lambda: services.run_manager.get_run(child.run_id).lifecycle.get('final_message_id'))
    await services.background_jobs.tick()
    result = services.conversations.page_messages(ids['conversation'], session.id)
    assert result.items[-1].content == 'Collected the result.'
    assert result.run_summaries[child.run_id].final_message_id == result.items[-1].id
    assert len(runtime.contexts) == 2 and compute.submissions == 1
    assert child.parent_run_id == root.run_id and child.context_id == session.id


@pytest.mark.asyncio
async def test_startup_repairs_legacy_wait_final_reply_once(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    session = await services.create_conversation_session(ids['conversation'],
        ConversationSessionCreate(participant_ids=[ids['agent']]))
    async def script(context):
        job = await invoke('submit_background_job', **request)
        await invoke('wait_background_jobs', job_ids=[job['id']])
    runtime.script = script
    run = await services.run_manager.start_run(ids['agent'], 'Compute', caller_kind='conversation',
        caller_id=ids['conversation'], context_id=session.id)
    await services.run_manager.wait_execution(run.run_id)
    progress = services._conversation_final_message(ids['conversation'], session.id,
        sender_id=ids['agent'], sender_name='Researcher', content='Coordinator finished', run_id=run.run_id)
    assert services.run_manager.get_run(run.run_id).lifecycle['final_message_id'] == progress.id
    await services.shutdown()

    for _ in range(2):
        restored = create_services(services.settings, plugins=services.plugins)
        try:
            await restored.startup()
            page = restored.conversations.page_messages(ids['conversation'], session.id)
            assert len(page.items) == 2
            assert page.items[0].id == progress.id
            assert page.items[1].sender_kind == 'system' and 'automatic monitoring' in page.items[1].content
            assert page.run_summaries[run.run_id].final_message_id is None
            assert compute.submissions == 1 and len(runtime.contexts) == 1
        finally:
            await restored.shutdown()
            restored.close()


@pytest.mark.asyncio
async def test_submission_dedup_and_unknown_outcome_never_replay(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    compute.fail_submit = True
    captured = []
    async def script(context):
        captured.append(await invoke("submit_background_job", **request))
        captured.append(await invoke("submit_background_job", **{**request, "specification": {"name": "md"}}))
        with pytest.raises(ConflictError):
            await invoke("submit_background_job", **{**request, "specification": {"name": "other"}})
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors
    assert compute.submissions == 1 and captured[0]["status"] == "unknown"
    assert captured[0]["id"] == captured[1]["id"]
    job = services.background_jobs.get(captured[0]["id"])
    assert "specification" not in job  # Credential-bearing specs are never journaled.


@pytest.mark.asyncio
async def test_concurrent_submission_reserves_one_identity_before_side_effect(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    async def script(context):
        first, second = await asyncio.gather(invoke("submit_background_job", **request), invoke("submit_background_job", **request))
        assert first["id"] == second["id"]
        assert services.background_jobs.get(first["id"])["external_id"] == "remote-123"
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Submit concurrently")
    await services.run_manager.wait_execution(root.run_id)
    assert not runtime.errors and compute.submissions == 1


@pytest.mark.asyncio
async def test_monitor_is_model_free_and_terminal_notification_admitted_once(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
            await invoke("wait_background_jobs", job_ids=[captured["id"]])
        else:
            assert (await invoke("inspect_background_jobs", job_ids=[captured["id"]]))["jobs"][0]["status"] == "succeeded"
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(root.run_id)
    for _ in range(70):
        services.background_jobs.update(captured["id"], next_check_at=0)
        await services.background_jobs.tick()
        await asyncio.gather(*services.background_jobs.probes.values())
    assert len(runtime.contexts) == 1 and compute.probes == 70 and compute.submissions == 1
    compute.status = "succeeded"
    services.background_jobs.update(captured["id"], next_check_at=0)
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    await until(lambda: len(runtime.contexts) == 2)
    await services.run_manager.wait_execution(runtime.contexts[-1].run_id)
    await services.background_jobs.tick()
    assert len(runtime.contexts) == 2 and not runtime.errors
    assert runtime.contexts[-1].root_run_id == root.run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["run", "permission"])
async def test_stop_and_revocation_pause_automation_without_remote_cancellation(research, stop):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        captured.update(await invoke("submit_background_job", **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(root.run_id)
    if stop == "run":
        await services.run_manager.cancel_run(root.run_id)
    else:
        edge = next(e for e in services.world.list_edges() if e.source == ids["agent"] and e.target == ids["sandbox"])
        await services.delete_edge(edge.id)
    await services.background_jobs.tick()
    job = services.background_jobs.get(captured["id"])
    assert job["paused"] and job["status"] == "queued" and compute.probes == 0
    assert len(runtime.contexts) == 1


@pytest.mark.asyncio
async def test_job_reads_reauthorize_session_and_transient_probe_is_not_failure(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    origin = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Origin", participant_ids=[ids["agent"]]))
    other = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Other", participant_ids=[ids["agent"]]))
    captured = {}
    async def script(context):
        if context.context_id == origin.id:
            captured.update(await invoke("submit_background_job", **request))
        else:
            with pytest.raises(PermissionDeniedError):
                await invoke("inspect_background_jobs", job_ids=[captured["id"]])
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit", caller_kind="conversation", caller_id=ids["conversation"], context_id=origin.id)
    await services.run_manager.wait_execution(root.run_id)
    compute.fail_probe = True
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    job = services.background_jobs.get(captured["id"])
    assert job["status"] == "queued" and job["failures"] == 1 and job["next_check_at"] > job["updated_at"]
    second = await services.run_manager.start_run(ids["agent"], "other", caller_kind="conversation", caller_id=ids["conversation"], context_id=other.id)
    await services.run_manager.wait_execution(second.run_id)
    assert not runtime.errors


@pytest.mark.asyncio
async def test_restart_recovers_pending_job_and_unadmitted_claim_only(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(root.run_id)
    services.background_jobs.update(captured["id"], notification_run_id="unadmitted-reservation")
    await services.shutdown()
    compute.status = "succeeded"
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await until(lambda: len(runtime.contexts) == 2)
        await restarted.run_manager.wait_execution(runtime.contexts[-1].run_id)
        assert compute.submissions == 1 and not runtime.errors
        await restarted.shutdown()
        again = create_services(services.settings, plugins=services.plugins)
        try:
            await again.startup()
            await again.background_jobs.tick()
            assert len(runtime.contexts) == 2
        finally:
            await again.shutdown()
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_uncertain_submission_can_attach_but_cancel_requires_terminal_observation(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    compute.fail_submit = True
    captured = {}
    async def script(context):
        captured.update(await invoke("submit_background_job", **request))
        attached = await invoke("attach_background_job", provider=request["provider"], sandbox_id=ids["sandbox"],
            request_key=request["request_key"], external_id="remote-123", job_id=captured["id"])
        assert attached["id"] == captured["id"] and attached["status"] == "running"
        pending = await invoke("control_background_job", job_id=captured["id"], action="cancel")
        assert pending["status"] == "running" and pending["paused"]
        compute.status = "cancelled"
        confirmed = await invoke("control_background_job", job_id=captured["id"], action="cancel")
        assert confirmed["status"] == "cancelled"
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "Reconcile")
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors and compute.submissions == 1


@pytest.mark.asyncio
async def test_active_job_blocks_owner_deletion_and_provider_unload_until_explicit_detach(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if not captured:
            captured.update(await invoke("submit_background_job", **request))
        else:
            await invoke("control_background_job", job_id=captured["id"], action="detach")
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "Submit")
    await services.run_manager.wait_execution(run.run_id)
    with pytest.raises(ConflictError, match="Background work"):
        await services.delete_cards([ids["sandbox"]])
    assert "tracked background work" in plugin_usage(services, "test.jobs")
    run = await services.run_manager.start_run(ids["agent"], "Relinquish monitoring")
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors
    assert "tracked background work" not in plugin_usage(services, "test.jobs")
    await services.delete_cards([ids["sandbox"]])
    assert services.background_jobs.get(captured["id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_host_shutdown_during_active_turn_preserves_external_wakeup(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
            await asyncio.Event().wait()
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "Submit")
    await until(lambda: bool(captured))
    await services.shutdown()
    assert services.run_manager.get_run(run.run_id).lifecycle["cancellation_origin"] == "shutdown"
    compute.status = "succeeded"
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await until(lambda: len(runtime.contexts) == 2)
        await restarted.run_manager.wait_execution(runtime.contexts[-1].run_id)
        assert runtime.contexts[-1].parent_run_id == run.run_id and not runtime.errors
        assert compute.submissions == 1
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_interrupted_admitted_notification_requires_reconciliation_not_replay(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
        else:
            await asyncio.Event().wait()
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Submit")
    await services.run_manager.wait_execution(root.run_id)
    compute.status = "succeeded"
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    await until(lambda: len(runtime.contexts) == 2)
    await services.shutdown()
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await restarted.background_jobs.tick()
        job = restarted.background_jobs.get(captured["id"])
        assert job["paused"] and job["notification_run_id"] == runtime.contexts[-1].run_id
        assert len(runtime.contexts) == 2 and compute.submissions == 1
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_revocation_during_probe_cannot_publish_completion(research, monkeypatch):
    services, ids, runtime, compute, invoke, request = setup(research)
    started, release = asyncio.Event(), asyncio.Event()
    definition = services.plugins.background_job_provider("test.compute")
    async def inspect(context, external_id):
        started.set()
        await release.wait()
        return JobObservation(status="succeeded")
    monkeypatch.setitem(services.plugins._background_job_providers, "test.compute", replace(definition, inspect=inspect))
    captured = {}
    async def script(context):
        captured.update(await invoke("submit_background_job", **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Submit")
    await services.run_manager.wait_execution(root.run_id)
    await services.background_jobs.tick()
    await started.wait()
    edge = next(e for e in services.world.list_edges() if e.source == ids["agent"] and e.target == ids["sandbox"])
    await services.delete_edge(edge.id)
    release.set()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    job = services.background_jobs.get(captured["id"])
    assert job["status"] == "queued" and job["paused"] and len(runtime.contexts) == 1
    async def inspect_remaining(context):
        assert (await invoke("inspect_background_jobs"))["jobs"] == []
        with pytest.raises(PermissionDeniedError):
            await invoke("inspect_background_jobs", job_ids=[captured["id"]])
    runtime.script = inspect_remaining
    followup = await services.run_manager.start_run(ids["agent"], "Inspect authorized work")
    await services.run_manager.wait_execution(followup.run_id)
    assert not runtime.errors


@pytest.mark.asyncio
@pytest.mark.parametrize('manage,ready', [(True, False), (False, False), (False, True)])
async def test_monitor_restores_sandbox_only_with_live_start_permission(research, monkeypatch, tmp_path, manage, ready):
    from backend.tests.test_sandbox_manager import RecordingBackend
    from backend.sandbox.models import SandboxState
    from backend.world.models import EdgeCreate

    services, ids, runtime, compute, invoke, request = setup(research)
    sandbox = RecordingBackend(tmp_path / 'monitor-sandbox')
    await sandbox.create(ids['sandbox'])
    monkeypatch.setattr(services, 'sandbox_backend', sandbox)
    if ready:
        await services.start_sandbox(ids['sandbox'])
    if not manage:
        edge = next(e for e in services.world.list_edges() if e.source == ids['agent'] and e.target == ids['sandbox'])
        await services.delete_edge(edge.id)
        await services.create_edge(EdgeCreate(source=ids['agent'], target=ids['sandbox'], relationship='execute'))

    definition = services.plugins.background_job_provider('test.compute')
    async def inspect(context, external_id):
        result = await context.execute(['inspect-existing-job', external_id])
        assert result['exit_code'] == 0
        return JobObservation(status='succeeded')
    monkeypatch.setitem(services.plugins._background_job_providers, 'test.compute', replace(definition, inspect=inspect))
    captured = {}
    async def script(context):
        if context.caller.kind != 'delegation':
            captured.update(await invoke('submit_background_job', **request))
            await invoke('wait_background_jobs', job_ids=[captured['id']])
    runtime.script = script
    root = await services.run_manager.start_run(ids['agent'], 'Submit')
    await services.run_manager.wait_execution(root.run_id)
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    job = services.background_jobs.get(captured['id'])
    assert compute.submissions == 1 and not runtime.errors
    if manage or ready:
        assert job['status'] == 'succeeded' and job['observation_error'] is None
        assert (await sandbox.get(ids['sandbox'])).state == SandboxState.READY
        assert sandbox.commands == [['inspect-existing-job', 'remote-123']]
        await services.background_jobs.tick()
        await until(lambda: len(runtime.contexts) == 2)
        await services.run_manager.wait_execution(runtime.contexts[-1].run_id)
        await services.background_jobs.tick()
        assert len(runtime.contexts) == 2 and compute.submissions == 1
    else:
        assert job['status'] == 'queued' and job['failures'] == 1
        assert 'Start permission' in job['observation_error']
        assert (await sandbox.get(ids['sandbox'])).state == SandboxState.STOPPED
        assert not sandbox.commands and len(runtime.contexts) == 1


@pytest.mark.asyncio
async def test_monitor_reauthorizes_after_start_before_inspection(research, monkeypatch, tmp_path):
    from backend.tests.test_sandbox_manager import RecordingBackend

    services, ids, runtime, compute, invoke, request = setup(research)
    sandbox = RecordingBackend(tmp_path / 'monitor-sandbox')
    await sandbox.create(ids['sandbox'])
    monkeypatch.setattr(services, 'sandbox_backend', sandbox)
    start = services.start_sandbox
    async def start_then_revoke(self, sandbox_id, *, agent_id):
        result = await start(sandbox_id, agent_id=agent_id)
        edge = next(e for e in services.world.list_edges() if e.source == agent_id and e.target == sandbox_id)
        await services.delete_edge(edge.id)
        return result
    monkeypatch.setattr(type(services), 'start_sandbox', start_then_revoke)
    definition = services.plugins.background_job_provider('test.compute')
    async def inspect(context, external_id):
        await context.execute(['inspect-existing-job', external_id])
        return JobObservation(status='succeeded')
    monkeypatch.setitem(services.plugins._background_job_providers, 'test.compute', replace(definition, inspect=inspect))
    captured = {}
    async def script(context):
        captured.update(await invoke('submit_background_job', **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids['agent'], 'Submit')
    await services.run_manager.wait_execution(root.run_id)
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    job = services.background_jobs.get(captured['id'])
    assert not sandbox.commands and job['paused'] and job['status'] == 'queued'
    assert len(runtime.contexts) == 1 and compute.submissions == 1


@pytest.mark.asyncio
async def test_startup_rechecks_active_jobs_without_losing_user_pause(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = []
    async def script(context):
        for key in ('active', 'paused'):
            captured.append(await invoke('submit_background_job', **{**request, 'request_key': key}))
    runtime.script = script
    root = await services.run_manager.start_run(ids['agent'], 'Submit')
    await services.run_manager.wait_execution(root.run_id)
    for index, job in enumerate(captured):
        services.background_jobs.update(job['id'], paused=bool(index), next_check_at=10**15, failures=18)
    await services.background_jobs.startup()
    active, paused = [services.background_jobs.get(job['id']) for job in captured]
    assert active['next_check_at'] == 0 and active['failures'] == 18
    assert paused['paused'] and paused['next_check_at'] == 10**15
    assert compute.submissions == 2 and not runtime.errors


@pytest.mark.asyncio
@pytest.mark.parametrize('revocation,phase', [(None, 'queue'), ('pack', 'queue'), ('profile', 'queue'),
    ('permission', 'queue'), ('pack', 'bootstrap'), ('profile', 'bootstrap'), ('permission', 'bootstrap')])
async def test_bohrium_profile_survives_run_and_rechecks_after_wait(research, monkeypatch, tmp_path, revocation, phase):
    import json
    from backend.background_jobs import JobContext
    from backend.plugins.background_jobs import JobControl
    from backend.sandbox.models import CommandResult, SandboxValidationError
    from backend.tests.test_sandbox_manager import make_manager

    services, ids, runtime, _, invoke, _ = setup(research)
    manager, backend = make_manager(tmp_path / 'sandbox')
    await manager.create(ids['sandbox'])
    monkeypatch.setattr(services, 'sandbox_backend', manager)
    commands, recipes = [], []
    cancelled = False
    async def execute(sandbox_id, argv, **options):
        nonlocal cancelled
        commands.append(argv)
        recipe = json.loads(dict(options['runtime_mount'].bundle.files)['_oaw_profile/recipe.json'])
        recipes.append(recipe)
        logical = argv[5:]
        assert logical[:2] == ['python3', '-c']
        assert 'NODE_PATH' in logical[2] and 'batchjob' in logical[3:]
        if 'kill' in logical[3:]:
            cancelled = True
            data = {'confirmed': True}
        elif 'describe' in logical[3:]:
            data = {'status_name': 'cancelled' if cancelled else 'running'}
        else:
            data = {'jobId': 'known-external-job'}
        return CommandResult(sandbox_id, tuple(argv), 0, json.dumps({'ok': True, 'data': data}), '', .01)
    monkeypatch.setattr(backend, 'execute', execute)
    request = dict(provider='matcreator.bohrium-batch', sandbox_id=ids['sandbox'], request_key='stable-job',
        specification=dict(name='fixture', image='selected-image', command='run', project_id='project',
                           machine_type='machine', max_run_time='1h'))
    captured = {}
    async def script(context):
        captured.update(await invoke('submit_background_job', **request))
        assert (await invoke('submit_background_job', **request))['id'] == captured['id']
    runtime.script = script
    run = await services.run_manager.start_run(ids['agent'], 'Submit once')
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors and captured['external_id'] == 'known-external-job'
    assert len(commands) == 2  # Preflight and one submission, despite the repeated key.
    assert services.run_manager.current_context is None
    job = services.background_jobs.get(captured['id'])
    definition = services.plugins.background_job_provider(job['provider'])
    context = JobContext(services.background_jobs, job)
    if revocation:
        async def revoke_after_wait(*args, **kwargs):
            if revocation == 'pack':
                services.plugins.enrolled_pack_owners.discard('matcreator')
            elif revocation == 'profile':
                services.plugins._background_job_providers[job['provider']] = replace(definition, sandbox_environments=())
            else:
                edge = next(e for e in services.world.list_edges() if e.source == ids['agent'] and e.target == ids['sandbox'])
                services.world.delete_edge(edge.id)
        if phase == 'queue':
            monkeypatch.setattr(manager, 'wait_for_python_preparation', revoke_after_wait)
        else:
            monkeypatch.setattr(backend, 'prepare_python', revoke_after_wait, raising=False)
        with pytest.raises((SandboxValidationError, PermissionDeniedError)):
            await definition.inspect(context, job['external_id'])
        assert len(commands) == 2
        retained = services.background_jobs.get(job['id'])
        assert retained['external_id'] == 'known-external-job' and retained['request_key'] == 'stable-job'
    else:
        assert (await definition.inspect(context, job['external_id'])).status == 'running'
        result = await services.background_jobs.control_record(job, JobControl(job_id=job['id'], action='cancel'))
        assert result['status'] == 'cancelled' and len(commands) == 5
        assert all(recipe == recipes[0] for recipe in recipes)
        assert recipes[0]['node'] == ['@dptech-corp/bohr-cli'] and recipes[0]['python'] == []
        assert recipes[0]['sources'][0]['owner'] == 'matcreator'


def test_background_job_profile_names_are_bounded_and_validated():
    async def inspect(context, external_id):
        return JobObservation(status='running')
    for names in [('bohr', 'bohr'), ('../outside',), ('',), ('x',) * 21, (['invalid'],)]:
        with pytest.raises(ValueError, match='unique identifiers'):
            BackgroundJobProvider(specification=Spec, inspect=inspect, sandbox_environments=names)
