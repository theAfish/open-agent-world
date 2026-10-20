"""Run preparation must release active ownership on all exits."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from backend.agents.models import AgentConfig
from oaw_atomsculptor.runtime import AtomSculptorRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['capabilities', 'selection'])
@pytest.mark.parametrize('cancel', [False, True])
async def test_preparation_failure_releases_agent_for_update(monkeypatch, phase, cancel):
    entered = asyncio.Event()
    async def fail(*args):
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError('preparation failed')
    capabilities = NS(list_tools=AsyncMock(return_value=[]))
    runtime = AtomSculptorRuntime(capabilities, model_connection_resolver=NS(
        resolve_runtime=lambda _: NS(supports_images=True)))
    config = AgentConfig('agent', 'Atom')
    monkeypatch.setattr(runtime, '_validate', lambda _: None)
    await runtime.create_agent(config)
    monkeypatch.setattr(runtime, '_bindings', lambda: NS())
    monkeypatch.setattr(runtime, '_model', lambda *args: NS())
    monkeypatch.setattr(runtime, '_session_service', lambda _: NS())
    monkeypatch.setattr(runtime, '_session', AsyncMock(return_value='session'))
    monkeypatch.setattr(runtime, '_selection_context', fail if phase == 'selection' else AsyncMock(return_value={}))
    if phase == 'capabilities':
        capabilities.list_tools = fail
    async def consume():
        async for _ in runtime.execute(config, NS(run_id='run', context_id='session'), NS(prompt='Build structure')):
            pass
    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 2)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await task
    assert runtime.active == {}
    assert (await runtime.update_agent(config)).config == config
