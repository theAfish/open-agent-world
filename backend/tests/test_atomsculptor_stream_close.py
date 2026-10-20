"""Closing AtomSculptor's public stream must drain its ADK model pump."""
import asyncio

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.config import Settings
from backend.runs import InvocationCaller, InvocationContext, RuntimeInput, RunStore
from backend.services import create_services
from backend.world.models import CardCreate
from oaw_atomsculptor.runtime import AtomSculptorRuntime
from open_agent_world.plugin_api import AgentConfig, RuntimeModelConnection


@pytest.mark.asyncio
async def test_closing_atomsculptor_stream_drains_model_and_releases_scope(data_root, monkeypatch):
    services = create_services(Settings.for_data_root(data_root))
    entered, closed = asyncio.Event(), asyncio.Event()

    class HangingModel(BaseLlm):
        model: str = "openai/atomsculptor-close-test"

        async def generate_content_async(self, request, stream=False):
            entered.set()
            try:
                yield LlmResponse(partial=True, content=types.Content(role="model", parts=[
                    types.Part(text="Working on the structure", thought=True)]))
                await asyncio.Event().wait()
            finally:
                closed.set()

    class Capabilities:
        async def list_tools(self, agent_id):
            return []

    class Resolver:
        def resolve_runtime(self, reference):
            return RuntimeModelConnection("openai", "atomsculptor-close-test", "https://model.invalid", None)

        def context_limits(self, reference):
            return 8192, 1024

    try:
        agent = services.world.create_card(CardCreate(type="agent", name="Atom"))
        run = RunStore(services.database).create(agent_id=agent.id,
            runtime_provider_id="atomsculptor", caller_kind="test", context_id="test-session")
        context = InvocationContext(run_id=run.run_id, root_run_id=run.run_id, agent_id=agent.id,
            parent_run_id=None, caller=InvocationCaller("test"), context_id="test-session",
            task_id=None, runtime_provider_id="atomsculptor")
        runtime = AtomSculptorRuntime(Capabilities(), model_connection_resolver=Resolver(), context_store=services.contexts)
        monkeypatch.setattr(runtime, "_model", lambda *_args: HangingModel())
        config = AgentConfig(agent.id, "Atom", model="oaw:model:scripted")
        await runtime.create_agent(config)
        stream = runtime.execute(config, context, RuntimeInput("Inspect this structure"))
        try:
            await asyncio.wait_for(anext(stream), timeout=5)
            await asyncio.wait_for(entered.wait(), timeout=5)
        finally:
            await stream.aclose()
        assert closed.is_set()
        assert run.run_id not in runtime.active
        assert not services.contexts.lock(agent.id, "test-session").locked()
        assert not any(task.get_name() == "oaw-adk-events" for task in asyncio.all_tasks() if not task.done())
    finally:
        services.close()
