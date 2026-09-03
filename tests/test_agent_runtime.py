from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from backend.agents import (
    AgentConfig,
    AgentConfigurationError,
    AgentEventType,
    AgentStatus,
    GoogleAdkAgentRuntime,
    MockAgentRuntime,
    ScopedToolDefinition,
    ToolParameter,
)
from backend.agents.google_adk import _AdkBindings, _runtime_error_message
from backend.agents.tools import build_scoped_tool_callables, build_scoped_tool_schemas
from backend.runs import InvocationCaller, InvocationContext, RuntimeInput
from backend.plugins import create_builtin_registry


class CapabilityProvider:
    def __init__(self) -> None:
        self.allowed = True
        self.definitions = [
            ScopedToolDefinition(
                capability_id="resource:notes:read",
                name="read_notes",
                description="Read the connected notes resource.",
                parameters=(
                    ToolParameter("line", int, "First line to read.", False, 1),
                ),
            )
        ]
        self.invocations = []
        self.list_count = 0

    async def list_tools(self, agent_id):
        self.list_count += 1
        return tuple(self.definitions)

    async def invoke_tool(self, agent_id, capability_id, arguments):
        if not self.allowed:
            raise PermissionError("capability was revoked")
        self.invocations.append((agent_id, capability_id, dict(arguments)))
        return {"text": "notes"}


class ScopedToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_signature_and_live_reauthorization(self) -> None:
        provider = CapabilityProvider()
        tool = build_scoped_tool_callables(
            provider, "agent-a", provider.definitions
        )[0]
        self.assertEqual(tool.__name__, "read_notes")
        self.assertEqual(list(inspect.signature(tool).parameters), ["line"])
        self.assertEqual(await tool(line=2), {"text": "notes"})
        provider.allowed = False
        with self.assertRaises(PermissionError):
            await tool(line=3)

    async def test_invalid_or_duplicate_tools_fail_explicitly(self) -> None:
        provider = CapabilityProvider()
        duplicate = [provider.definitions[0], provider.definitions[0]]
        with self.assertRaises(AgentConfigurationError):
            build_scoped_tool_callables(provider, "agent-a", duplicate)

    async def test_tool_schema_preserves_scoped_tool_shape(self) -> None:
        provider = CapabilityProvider()
        schema = build_scoped_tool_schemas(provider.definitions)[0]
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "read_notes")
        self.assertEqual(schema["function"]["parameters"]["properties"]["line"]["type"], "integer")
        self.assertEqual(schema["function"]["parameters"]["required"], [])


class MockAgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.provider = CapabilityProvider()
        self.runtime = MockAgentRuntime(self.provider)
        self.config = AgentConfig("agent-a", "Researcher")

    async def test_default_model_and_lifecycle_events(self) -> None:
        created = await self.runtime.create_agent(self.config)
        self.assertEqual(created.config.model, "gemini-3.7-flash")
        context = self._context("run-1")
        events = [
            event async for event in self.runtime.execute(
                self.config, context, RuntimeInput("hello")
            )
        ]
        self.assertEqual(events[0].type, AgentEventType.MESSAGE)
        self.assertIn(AgentEventType.COMPLETED, [event.type for event in events])
        message = next(event for event in events if event.type == AgentEventType.MESSAGE)
        self.assertEqual(message.payload["available_tools"], ["read_notes"])
        self.assertEqual((await self.runtime.get_agent("agent-a")).status, AgentStatus.IDLE)

    async def test_tools_are_resolved_again_for_every_run(self) -> None:
        await self.runtime.create_agent(self.config)
        _ = [event async for event in self.runtime.execute(
            self.config, self._context("run-1"), RuntimeInput("one")
        )]
        self.provider.definitions = []
        events = [event async for event in self.runtime.execute(
            self.config, self._context("run-2"), RuntimeInput("two")
        )]
        message = next(event for event in events if event.type == AgentEventType.MESSAGE)
        self.assertEqual(message.payload["available_tools"], [])
        self.assertEqual(self.provider.list_count, 2)

    async def test_registry_requires_explicit_provider_selection(self) -> None:
        registry = create_builtin_registry()
        self.assertIsInstance(
            registry.create_runtime_provider("core.mock", self.provider),
            MockAgentRuntime,
        )
        for unsupported_runtime in ("automatic", "litellm"):
            with self.assertRaises(ValueError):
                registry.create_runtime_provider(unsupported_runtime, self.provider)
    @staticmethod
    def _context(run_id: str) -> InvocationContext:
        return InvocationContext(
            run_id=run_id,
            agent_id="agent-a",
            parent_run_id=None,
            root_run_id=run_id,
            caller=InvocationCaller("test"),
            context_id=None,
            task_id=None,
            runtime_provider_id="core.mock",
        )


class FakeSessionService:
    def __init__(self) -> None:
        self.created = []
        self.deleted = []

    async def create_session(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=kwargs["session_id"])

    async def delete_session(self, **kwargs):
        self.deleted.append(kwargs)

    async def flush(self):
        pass


class FakePart:
    def __init__(self, *, text=None, function_call=None, function_response=None, thought=False):
        self.text = text
        self.function_call = function_call
        self.function_response = function_response
        self.thought = thought

    @classmethod
    def from_text(cls, *, text):
        return cls(text=text)


class FakeContent:
    def __init__(self, *, role=None, parts=None):
        self.role = role
        self.parts = parts or []


class FakeEvent:
    def __init__(self, parts, final=False):
        self.content = FakeContent(parts=parts)
        self._final = final

    def is_final_response(self):
        return self._final


class FakeAgent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeApp:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeRunner:
    instances = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.session_service.flush()

    async def run_async(self, **kwargs):
        self.run_kwargs = kwargs
        yield FakeEvent(
            [
                FakePart(
                    function_call=SimpleNamespace(
                        name="read_notes", id="call-1", args={"line": 1}
                    )
                )
            ]
        )
        yield FakeEvent(
            [
                FakePart(
                    function_response=SimpleNamespace(
                        name="read_notes", id="call-1", response={"text": "notes"}
                    )
                ),
                FakePart(text="private reasoning", thought=True),
            ]
        )
        yield FakeEvent([FakePart(text="Answer")], final=True)


class GoogleAdkBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_provider_failure_is_unwrapped_for_the_ui(self) -> None:
        provider_error = RuntimeError("Missing credentials. Set OPENAI_API_KEY.")
        wrapper_error = RuntimeError("Dynamic node agent failed")
        wrapper_error.error = provider_error

        self.assertEqual(
            _runtime_error_message(wrapper_error),
            "Missing credentials. Set OPENAI_API_KEY.",
        )

    async def test_adk_2_style_app_runner_and_operational_translation(self) -> None:
        provider = CapabilityProvider()
        bindings = _AdkBindings(
            Agent=FakeAgent,
            App=FakeApp,
            Runner=FakeRunner,
            InMemorySessionService=FakeSessionService,
            types=SimpleNamespace(Content=FakeContent, Part=FakePart),
        )
        runtime = GoogleAdkAgentRuntime(provider, adk_bindings=bindings)
        await runtime.create_agent(AgentConfig("agent-a", "Agent"))
        context = InvocationContext(
            run_id="run-adk", agent_id="agent-a", parent_run_id=None,
            root_run_id="run-adk", caller=InvocationCaller("test"),
            context_id=None, task_id=None, runtime_provider_id="google.adk",
        )
        events = [event async for event in runtime.execute(
            AgentConfig("agent-a", "Agent"), context, RuntimeInput("do it")
        )]
        types = [event.type for event in events]
        self.assertIn(AgentEventType.TOOL_STARTED, types)
        self.assertIn(AgentEventType.TOOL_COMPLETED, types)
        texts = [
            event.payload["text"]
            for event in events
            if event.type == AgentEventType.MESSAGE
        ]
        self.assertEqual(texts, ["Answer"])
        runner = FakeRunner.instances[-1]
        self.assertIsInstance(runner.app, FakeApp)
        self.assertEqual(runner.app.root_agent.model, "gemini-3.7-flash")
        self.assertEqual(len(runner.app.root_agent.tools), 1)


if __name__ == "__main__":
    unittest.main()
