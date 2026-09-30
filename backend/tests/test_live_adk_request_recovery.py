"""Real ADK Runner integration, scripted models only; no API key or network."""
import asyncio
import importlib.util
import unittest


def has_adk():
    try:
        return importlib.util.find_spec("google.adk") is not None
    except (ImportError, ValueError):
        return False


@unittest.skipUnless(has_adk(), "Install backend ADK dependencies for Runner integration")
class LiveAdkRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prior_write_executes_once_and_same_request_recovers(self):
        from google.adk.agents import Agent
        from google.adk.agents.run_config import RunConfig, StreamingMode
        from google.adk.apps import App
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
        from backend.agents.model_observation import ModelTrace
        from backend.agents.request_recovery import recoverable_model

        writes, inputs = [], []

        def save_result(value: str) -> dict:
            """Persist one result and return its revision."""
            writes.append(value)
            return {"revision": len(writes)}

        class ScriptedModel(BaseLlm):
            model: str = "scripted-recovery"

            async def generate_content_async(self, request, stream=False):
                inputs.append([c.model_dump(mode="json", exclude_none=True) for c in request.contents])
                if len(inputs) == 1:
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                        function_call=types.FunctionCall(name="save_result", id="save-1", args={"value": "saved"}))]))
                elif len(inputs) == 2:
                    yield LlmResponse(partial=True, content=types.Content(role="model", parts=[
                        types.Part(text="checking the saved result", thought=True)]))
                    raise ConnectionResetError("scripted mid-stream interruption")
                else:
                    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Done")]))

        trace = ModelTrace(asyncio.Queue())
        agent = Agent(name="agent", model=recoverable_model(ScriptedModel(), trace, "agent"),
                      tools=[save_result], **trace.callbacks("agent"))
        sessions = InMemorySessionService()
        await sessions.create_session(app_name="recovery_test", user_id="user", session_id="session")
        async with Runner(app=App(name="recovery_test", root_agent=agent), session_service=sessions) as runner:
            events = [event async for event in runner.run_async(
                user_id="user", session_id="session", new_message=types.Content(
                    role="user", parts=[types.Part(text="Save then report")]),
                run_config=RunConfig(streaming_mode=StreamingMode.SSE))]
        self.assertEqual(writes, ["saved"])
        self.assertEqual(len(inputs), 3)
        self.assertEqual(inputs[1], inputs[2])
        self.assertEqual(trace.sequence, 2)  # Retry did not start a new Agent request.
        self.assertEqual(trace.request_attempts["agent"], 2)
        self.assertTrue(any(e.is_final_response() and any(
            p.text == "Done" for p in (e.content.parts or [])) for e in events if e.content))
        self.assertFalse(any(e.partial for e in events))
