"""Execution ownership, recoverable feedback, and host waits through live tools."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.agents.tools import build_scoped_tool_callables
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.errors import PermissionDeniedError
from backend.sandbox import history, python_runtime
from backend.sandbox.models import CommandResult, SandboxBusyError, SandboxPreparationError, SandboxSecurityError
from backend.sandbox.wsl import _error
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def toolset(client, agent):
    provider = WorldAgentCapabilityProvider(client.app.state.services)
    definitions = client.portal.call(provider.list_tools, agent['id'])
    return {tool.__name__: tool for tool in build_scoped_tool_callables(provider, agent['id'], definitions)}


def test_real_lock_busy_preserves_type_across_prepare_and_transport(tmp_path, monkeypatch):
    runtime = python_runtime.SharedPythonRuntime(tmp_path)
    with python_runtime.mutation_lock(runtime.root):
        ticks = iter([0, 61])
        monkeypatch.setattr(python_runtime, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
        with pytest.raises(SandboxBusyError):
            asyncio.run(runtime.prepare())
    for kind in (SandboxBusyError, SandboxPreparationError, SandboxSecurityError):
        assert isinstance(_error({'type': kind.__name__, 'message': 'example'}), kind)


def test_parallel_install_and_busy_command_return_feedback_then_continue(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    tools = toolset(client, agent)
    assert 'wait_sandbox_operation' in tools
    installed = asyncio.Event()
    async def install(*args, **kwargs):
        await installed.wait()
        return {'requirements': ['example'], 'kind': 'python'}
    async def execute(sandbox_id, argv, **kwargs):
        if not installed.is_set():
            raise SandboxBusyError('Shared Python installation in progress; command not started')
        return CommandResult(sandbox_id, tuple(argv), 0, 'done', '', .01)
    monkeypatch.setattr(type(services), 'install_python_packages', install)
    monkeypatch.setattr(backend, 'execute', execute)
    async def scenario():
        pending, busy = await asyncio.gather(
            tools['install_python_packages'](sandbox=sandbox['id'], requirements=['example'], wait_seconds=0),
            tools['execute_command'](sandbox=sandbox['id'], argv=['check']))
        assert pending['status'] == 'running'
        assert busy['error']['code'] == 'resource_busy' and busy['error']['retryable']
        inspected = await tools['inspect_sandbox'](sandbox=sandbox['id'])
        assert any(r['id'] == pending['operation_id'] and r['operation_kind'] == 'python_install'
                   for r in inspected['active_commands'])
        installed.set()
        result = await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=pending['operation_id'], wait_seconds=1)
        assert result['requirements'] == ['example']
        assert (await tools['execute_command'](sandbox=sandbox['id'], argv=['check']))['stdout'] == 'done'
        # Final installation result remains queryable after its worker is gone.
        assert await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=pending['operation_id'], wait_seconds=0) == result
        assert not services.sandbox_operations.tasks
    client.portal.call(scenario)


@pytest.mark.parametrize('outcome', ['exit', 'timeout', 'preparation', 'security'])
def test_delayed_results_and_failures_remain_observable(runtime_client, monkeypatch, outcome):
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    tools = toolset(client, agent)
    release = asyncio.Event()
    async def execute(sandbox_id, argv, **kwargs):
        await release.wait()
        if outcome == 'preparation':
            raise SandboxPreparationError('installer failed')
        if outcome == 'security':
            raise SandboxSecurityError('isolation unavailable')
        return CommandResult(sandbox_id, tuple(argv), 7, '', 'script error', .01, timed_out=outcome == 'timeout')
    monkeypatch.setattr(backend, 'execute', execute)
    async def scenario():
        pending = await tools['execute_command'](sandbox=sandbox['id'], argv=['work'], wait_seconds=0)
        release.set()
        if outcome == 'security':
            with pytest.raises(SandboxSecurityError):
                await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=pending['operation_id'], wait_seconds=1)
            with pytest.raises(SandboxSecurityError):
                await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=pending['operation_id'], wait_seconds=0)
        else:
            result = await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=pending['operation_id'], wait_seconds=1)
            assert result.get('exit_code') == 7 if outcome != 'preparation' else result['error']['code'] == 'environment_preparation_failed'
            assert await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=pending['operation_id'], wait_seconds=0) == result
    client.portal.call(scenario)


def test_cancelling_wait_preserves_workers_and_targeted_cancel_stops_only_one(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    tools = toolset(client, agent)
    release = asyncio.Event()
    stopped = []
    async def execute(sandbox_id, argv, **kwargs):
        try:
            await release.wait()
            return CommandResult(sandbox_id, tuple(argv), 0, argv[0], '', .01)
        except asyncio.CancelledError:
            stopped.append(argv[0])
            raise
    monkeypatch.setattr(backend, 'execute', execute)
    async def scenario():
        a = await tools['execute_command'](sandbox=sandbox['id'], argv=['a'], wait_seconds=0)
        b = await tools['execute_command'](sandbox=sandbox['id'], argv=['b'], wait_seconds=0)
        waiter = asyncio.create_task(tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=a['operation_id'], wait_seconds=60))
        await asyncio.sleep(.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not stopped
        assert (await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=b['operation_id'], wait_seconds=.01))['status'] == 'running'
        await tools['cancel_command'](sandbox=sandbox['id'], command_id=a['command_id'])
        assert stopped == ['a']
        release.set()
        assert (await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id=b['operation_id'], wait_seconds=1))['stdout'] == 'b'
    client.portal.call(scenario)


def test_wait_rechecks_live_authority_after_sleep(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, _, _, edges = setup_skill(client)
    services = client.app.state.services
    async def scenario():
        waiting = asyncio.create_task(services.sandbox_operations.wait(agent['id'], sandbox['id'], wait_seconds=.05))
        await asyncio.sleep(.01)
        await services.delete_edge(edges[0]['id'])
        with pytest.raises(PermissionDeniedError):
            await waiting
    client.portal.call(scenario)


def test_host_timer_and_restart_receipts_never_launch_work(runtime_client, monkeypatch):
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    tools = toolset(client, agent)
    async def forbidden(*args, **kwargs):
        pytest.fail('Host wait must not launch a process or prepare Python')
    monkeypatch.setattr(backend, 'execute', forbidden)
    async def scenario():
        result = await tools['wait_sandbox_operation'](sandbox=sandbox['id'], wait_seconds=.01)
        assert result['status'] == 'waited'
        history.save(services, sandbox['id'], {'id': 'before-restart', 'state': 'running', 'argv': [],
            'sandbox_id': sandbox['id'], 'caller': agent['id'], 'started_at': '2026-09-18T00:00:00+00:00'})
        result = await tools['wait_sandbox_operation'](sandbox=sandbox['id'], operation_id='before-restart', wait_seconds=0)
        assert result['status'] == 'interrupted'
        assert 'not resubmitted' in result['error']['message']
    client.portal.call(scenario)


@pytest.mark.parametrize('wait_seconds', [None, 60])
def test_cancelled_submission_waits_for_worker_cleanup(runtime_client, wait_seconds):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services

    async def scenario():
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()

        async def execute(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        submission = asyncio.create_task(services.sandbox_operations.submit(
            agent['id'], sandbox['id'], 'test', execute, wait_seconds=wait_seconds))
        try:
            await asyncio.wait_for(started.wait(), 1)
            submission.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            assert not submission.done()
            submission.cancel()
            await asyncio.sleep(0)
            assert not submission.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(submission, 1)
            assert cleaned.is_set()
            assert not services.sandbox_operations.tasks
            assert not services._sandbox_tasks
            assert not services._sandbox_commands
            assert history.read(services, sandbox['id'])[0]['state'] == 'cancelled'
        finally:
            release.set()
            await asyncio.gather(submission, return_exceptions=True)

    client.portal.call(scenario)


def test_cancel_before_admission_closes_receipt(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    async def scenario():
        async def never(_):
            pytest.fail('Cancelled operation must not start')
        pending = await services.sandbox_operations.submit(agent['id'], sandbox['id'], 'test', never, wait_seconds=0)
        task = services.sandbox_operations.tasks[pending['operation_id']]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert not services.sandbox_operations.tasks
        assert not services._sandbox_commands
        assert (await services.sandbox_operations.wait(agent['id'], sandbox['id'], pending['operation_id'], 0))['status'] == 'cancelled'
    client.portal.call(scenario)


def test_real_adk_parallel_tool_batch_survives_busy_and_waits(runtime_client, monkeypatch):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from backend.agents import AgentConfig, AgentEventType, GoogleAdkAgentRuntime
    from backend.runs import InvocationCaller, InvocationContext, RuntimeInput, RunStatus

    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    release = asyncio.Event()
    async def install(*args, **kwargs):
        await release.wait()
        return {'requirements': ['example']}
    async def execute(sandbox_id, argv, **kwargs):
        if not release.is_set():
            raise SandboxBusyError('Environment busy; command not started')
        return CommandResult(sandbox_id, tuple(argv), 0, 'recovered', '', .01)
    monkeypatch.setattr(type(services), 'install_python_packages', install)
    monkeypatch.setattr(backend, 'execute', execute)
    observed = []

    class ScriptedModel(BaseLlm):
        model: str = 'deterministic-contract-test'
        step: int = 0

        async def generate_content_async(self, llm_request, stream=False):
            responses = {part.function_response.name: part.function_response.response
                for content in llm_request.contents for part in (content.parts or []) if part.function_response}
            observed.append(responses)
            def call(name, **args):
                return types.Part(function_call=types.FunctionCall(name=name, args={'sandbox': sandbox['id'], **args}))
            if self.step == 0:
                parts = [call('install_python_packages', requirements=['example'], wait_seconds=0),
                         call('execute_command', argv=['check'])]
            elif self.step == 1:
                assert responses['execute_command']['error']['code'] == 'resource_busy'
                pending = responses['install_python_packages']
                assert pending['status'] == 'running'
                release.set()
                parts = [call('wait_sandbox_operation', operation_id=pending['operation_id'], wait_seconds=1)]
            elif self.step == 2:
                assert responses['wait_sandbox_operation']['requirements'] == ['example']
                parts = [call('execute_command', argv=['check'])]
            else:
                assert responses['execute_command']['stdout'] == 'recovered'
                parts = [types.Part(text='Completed after waiting for the installation.')]
            self.step += 1
            yield LlmResponse(content=types.Content(role='model', parts=parts))

    runtime = GoogleAdkAgentRuntime(WorldAgentCapabilityProvider(services))
    model = ScriptedModel()
    monkeypatch.setattr(runtime, '_adk_model', lambda _: model)
    async def scenario():
        config = AgentConfig(agent_id=agent['id'], name='Recovery test')
        await runtime.create_agent(config)
        context = InvocationContext(run_id='test-recovery', agent_id=agent['id'], parent_run_id=None,
            root_run_id='test-recovery', caller=InvocationCaller('test'), context_id=None,
            task_id=None, runtime_provider_id='google.adk')
        events = [event async for event in runtime.execute(config, context, RuntimeInput(prompt='Run the check after installation'))]
        assert events[-1].run_status == RunStatus.SUCCEEDED
        assert sum(event.type == AgentEventType.TOOL_COMPLETED for event in events) == 4
        assert model.step == 4
        await runtime.delete_agent(agent['id'])
    client.portal.call(scenario)


@pytest.mark.parametrize('finish_turn', [False, True])
def test_run_stop_cleans_up_yielded_work_even_after_turn_completion(runtime_client, monkeypatch, finish_turn):
    from backend.tests.test_runs import RecordingProvider
    from backend.agents import AgentEvent, AgentEventType
    from backend.runs import RunStatus
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    started, stopped, yielded = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(sandbox_id, argv, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(backend, 'execute', execute)
    class Provider(RecordingProvider):
        async def execute(self, config, context, runtime_input):
            result = await WorldAgentCapabilityProvider(services).invoke_tool(agent['id'], 'operation:execute_command',
                {'sandbox': sandbox['id'], 'argv': ['long-work'], 'wait_seconds': 0})
            assert result['status'] == 'running'
            yielded.set()
            if not finish_turn:
                await asyncio.Event().wait()
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                {'text': 'Background work submitted'}, run_status=RunStatus.SUCCEEDED)
    async def scenario():
        services.install_runtime_provider('core.mock', Provider(), default=True)
        run = await services.run_manager.start_run(agent['id'], 'Start work')
        await asyncio.wait_for(yielded.wait(), 1)
        await asyncio.wait_for(started.wait(), 1)
        if finish_turn:
            await services.run_manager.wait_execution(run.run_id)
        await services.stop_agent(agent['id'])
        await asyncio.wait_for(stopped.wait(), 1)
        assert not services.sandbox_operations.tasks
        assert not services._sandbox_commands
    client.portal.call(scenario)


def test_shared_python_progress_is_bounded_and_tolerates_incomplete_log(tmp_path):
    import json
    runtime = python_runtime.SharedPythonRuntime(tmp_path)
    runtime.root.mkdir(parents=True)
    (runtime.root / 'install.log').write_text(json.dumps({'state': 'running', 'time': 123}) + '\n{"unfinished":', encoding='utf-8')
    (runtime.root / 'install-output.log').write_text('x' * 10000 + 'Downloading wheel', encoding='utf-8')
    result = runtime.snapshot()
    assert result['last_install_state'] == 'running'
    assert len(result['output_tail']) == 8192
    assert result['output_tail'].endswith('Downloading wheel')


def test_uncollected_result_survives_more_than_twenty_newer_operations(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    async def scenario():
        async def result(operation_id):
            return {'value': operation_id}
        first = await services.sandbox_operations.submit(agent['id'], sandbox['id'], 'test', result, wait_seconds=0)
        await asyncio.sleep(.01)
        for _ in range(25):
            await services.sandbox_operations.submit(agent['id'], sandbox['id'], 'test', result)
        found = await services.sandbox_operations.wait(agent['id'], sandbox['id'], first['operation_id'], 0)
        assert found == {'value': first['operation_id']}
        assert len(history.read(services, sandbox['id'])) == 20
    client.portal.call(scenario)
