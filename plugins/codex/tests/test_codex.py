from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from open_agent_world.plugin_api import AgentConfig, AgentConfigurationError, AgentEventType, AgentRuntimeError, ScopedToolDefinition, ToolParameter
from backend.runs import InvocationCaller, InvocationContext, RuntimeInput
from backend.plugins.loader import load_plugin_registry
from oaw_codex.runtime import CodexRuntime


class Capabilities:
    def __init__(self, revoke=False):
        self.revoke = revoke
        self.allowed = True
        self.calls = []

    async def list_tools(self, agent_id):
        tools = [ScopedToolDefinition('text.edit:notes', 'edit_notes', 'Edit notes', (ToolParameter('content'),))]
        if self.revoke:
            self.allowed = False
        return tools

    async def invoke_tool(self, agent_id, capability_id, arguments):
        if not self.allowed:
            raise PermissionError('capability was revoked')
        self.calls.append((agent_id, capability_id, arguments))
        return {'content': arguments['content']}


def config(tmp_path):
    return AgentConfig('agent1', 'Codex', model='default', runtime_provider_id='openai.codex', provider_config={'workspace_path': str(tmp_path)})


def context(run='run1', session='session1'):
    return InvocationContext(run, 'agent1', None, run, InvocationCaller('api'), session, None, 'openai.codex')


def runtime(tmp_path, capabilities=None):
    return CodexRuntime(capabilities or Capabilities(), state_directory=tmp_path / 'state',
                        server_command=[sys.executable, str(Path(__file__).with_name('fake_server.py')), str(tmp_path / 'protocol.jsonl')])


async def collect(provider, cfg, prompt='hello', ctx=None):
    return [event async for event in provider.execute(cfg, ctx or context(), RuntimeInput(prompt))]


def test_packaged_discovery():
    assert any(ep.name == 'openai-codex' for ep in entry_points(group='open_agent_world.plugins'))
    assert load_plugin_registry().has_runtime_provider('openai.codex')


@pytest.mark.asyncio
async def test_stream_and_resume_across_provider_restart(tmp_path):
    cfg = config(tmp_path)
    first = runtime(tmp_path)
    await first.create_agent(cfg)
    events = await collect(first, cfg)
    assert [e.payload['text'] for e in events if e.type == AgentEventType.MESSAGE] == ['Hello ', 'Hello OAW', 'Hello OAW']
    assert events[-1].run_status == 'succeeded'
    assert not first.active
    second = runtime(tmp_path)
    await second.create_agent(cfg)
    await collect(second, cfg, ctx=context('run2'))
    await collect(second, cfg, ctx=context('run3', 'different-session'))
    log = [json.loads(line) for line in (tmp_path / 'protocol.jsonl').read_text().splitlines()]
    threads = [entry for entry in log if entry.get('method') in ('thread/start', 'thread/resume')]
    assert [entry['method'] for entry in threads] == ['thread/start', 'thread/resume', 'thread/start']
    assert threads[0]['params']['sandbox'] == 'workspace-write'
    assert threads[0]['params']['approvalPolicy'] == 'never'
    assert 'model' not in threads[0]['params']
    assert 'dynamicTools' in threads[0]['params']
    await second.delete_agent(cfg.agent_id)
    assert second._session('agent1', 'session1') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('revoke', [False, True])
async def test_graph_tool_invocation_rechecks_authorization(tmp_path, revoke):
    capabilities = Capabilities(revoke)
    provider, cfg = runtime(tmp_path, capabilities), config(tmp_path)
    await provider.create_agent(cfg)
    events = await collect(provider, cfg, 'revoke' if revoke else 'tool')
    calls = [e for e in events if e.type == AgentEventType.TOOL_COMPLETED]
    assert len(calls) == 2
    assert calls[-1].payload['success'] is not revoke
    assert len(capabilities.calls) == (0 if revoke else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('prompt,match', [('fail', 'fixture failure'), ('crash', 'exited'), ('approval', 'interactive')])
async def test_failures_are_not_success(tmp_path, prompt, match):
    provider, cfg = runtime(tmp_path), config(tmp_path)
    await provider.create_agent(cfg)
    with pytest.raises(AgentRuntimeError, match=match):
        await asyncio.wait_for(collect(provider, cfg, prompt), 10)
    assert not provider.active


@pytest.mark.asyncio
async def test_stop_terminates_private_process(tmp_path):
    provider, cfg = runtime(tmp_path), config(tmp_path)
    await provider.create_agent(cfg)
    ready = asyncio.Event()
    async def consume():
        async for event in provider.execute(cfg, context(), RuntimeInput('hang')):
            if event.type == AgentEventType.TOOL_STARTED:
                ready.set()
    task = asyncio.create_task(consume())
    await asyncio.wait_for(ready.wait(), 10)
    server = provider.active['run1'][1]
    task.cancel()
    await provider.stop('run1')
    task.cancel()  # RunManager cancels twice; cleanup must survive this.
    await asyncio.gather(task, return_exceptions=True)
    assert server.process.returncode is not None
    assert not provider.active


@pytest.mark.asyncio
async def test_configuration_is_validated_before_launch(tmp_path):
    provider = runtime(tmp_path)
    with pytest.raises(AgentConfigurationError, match='absolute'):
        await provider.create_agent(replace(config(tmp_path), provider_config={'workspace_path': '.'}))
    with pytest.raises(AgentConfigurationError, match='max_concurrent_runs'):
        await provider.create_agent(replace(config(tmp_path), max_concurrent_runs=2))


@pytest.mark.asyncio
async def test_fresh_sessions_and_reasoning_settings_reach_codex(tmp_path):
    provider = runtime(tmp_path)
    cfg = replace(config(tmp_path), provider_config={
        'workspace_path': str(tmp_path), 'session_mode': 'fresh', 'reasoning_effort': 'high',
    })
    await provider.create_agent(cfg)
    await collect(provider, cfg)
    await collect(provider, cfg, ctx=context('run2'))
    log = [json.loads(line) for line in (tmp_path / 'protocol.jsonl').read_text().splitlines()]
    assert len([entry for entry in log if entry.get('method') == 'thread/start']) == 2
    assert not any(entry.get('method') == 'thread/resume' for entry in log)
    turns = [entry for entry in log if entry.get('method') == 'turn/start']
    assert all(entry['params']['effort'] == 'high' for entry in turns)
