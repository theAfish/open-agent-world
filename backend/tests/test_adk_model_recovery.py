"""Dependency-light coverage of ordinary ADK Agent stream recovery."""

import asyncio
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agents.google_adk import GoogleAdkAgentRuntime
from backend.agents.model_observation import (
    ModelStreamStalled,
    ModelTrace,
    model_stream_disconnected,
    multiplex_adk_events,
)
from backend.agents.models import AgentConfig, AgentEventType, ScopedToolDefinition
from backend.persistence.database import Database
from backend.runs.store import RunStore
from backend.runs.tool_receipts import RunToolReceipts


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class AdkModelRecoveryTests(unittest.TestCase):
    def test_retryable_provider_status_is_not_an_authentication_error(self) -> None:
        self.assertTrue(model_stream_disconnected(_StatusError(503)))
        self.assertFalse(model_stream_disconnected(_StatusError(401)))

    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.run = RunStore(self.database).create(agent_id="agent", runtime_provider_id="google.adk",
                                                   caller_kind="user")

    def tearDown(self) -> None:
        self.database.close()

    def _exercise(self, *, write_before_disconnect: bool = False,
                  read_tool_before_disconnect: bool = False, journal_read_tool: bool = True,
                  capture_error: bool = False, request_exhausted: bool = False):
        attempts = []
        read_invocations = []
        sessions = SimpleNamespace(
            get_session=AsyncMock(return_value=SimpleNamespace(events=[], state={})),
            delete_session=AsyncMock(), create_session=AsyncMock(),
        )

        class Provider:
            async def list_tools(self, _agent_id):
                return [ScopedToolDefinition("text.read:notes", "read_notes", "Read notes")]

            async def invoke_tool(self, _agent_id, _capability_id, _arguments):
                read_invocations.append(1)
                if journal_read_tool:
                    journal = RunToolReceipts(database)
                    receipt = journal.begin(run_id=run_id, agent_id="agent",
                                            capability_kind="text.read", target_id="notes",
                                            arguments={}, read_only=True)
                    journal.finish(receipt, {"revision": 1})
                return {"revision": 1}

        database = self.database
        run_id = self.run.run_id

        class Runner:
            def __init__(self, app, session_service):
                self.callbacks = app.root_agent

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def run_async(self, **_kwargs):
                async def stream():
                    attempts.append(1)
                    request = SimpleNamespace(contents=[SimpleNamespace(model_dump=lambda **_opts: {
                        "role": "user", "parts": [{"text": "Inspect"}],
                    })], config=SimpleNamespace(max_output_tokens=None))
                    await self.callbacks.before_model_callback(None, request)
                    if len(attempts) == 1:
                        if read_tool_before_disconnect:
                            await self.callbacks.tools[0]()
                        if write_before_disconnect:
                            journal = RunToolReceipts(database)
                            receipt = journal.begin(run_id=run_id, agent_id="agent",
                                                    capability_kind="text.edit", target_id="notes",
                                                    arguments={"text": "written"}, read_only=False)
                            journal.finish(receipt, {"revision": 2})
                        yield SimpleNamespace(partial=True, content=SimpleNamespace(parts=[
                            SimpleNamespace(thought=True, text="considering evidence"),
                        ]))
                        if request_exhausted:
                            from backend.agents.request_recovery import RequestRecoveryExhausted
                            raise RequestRecoveryExhausted("request retries exhausted")
                        raise ConnectionResetError("model stream lost")
                    answer = SimpleNamespace(parts=[SimpleNamespace(thought=False, text="Done")])
                    await self.callbacks.after_model_callback(None, SimpleNamespace(
                        partial=False, content=answer, usage_metadata=None,
                        finish_reason=None,
                    ))
                    yield SimpleNamespace(partial=False, content=answer,
                                          is_final_response=lambda: True)
                return stream()

        bindings = SimpleNamespace(
            InMemorySessionService=lambda: sessions, Runner=Runner,
            Agent=lambda **values: SimpleNamespace(**values),
            App=lambda **values: SimpleNamespace(**values),
            types=SimpleNamespace(Content=lambda **values: SimpleNamespace(**values),
                                  Part=SimpleNamespace(from_text=lambda text: SimpleNamespace(text=text))),
        )
        runtime = GoogleAdkAgentRuntime(Provider(), adk_bindings=bindings,
                                        context_store=SimpleNamespace(database=self.database))
        config = AgentConfig(agent_id="agent", name="Agent")

        class Managed:
            async def before_model(self, _context, _request):
                return None

            async def after_model(self, _context, _response):
                return None

            def observe(self, _event):
                return None

        model_module = SimpleNamespace(LLMRegistry=SimpleNamespace(new_llm=lambda value: value))
        class TypeSafeModel:
            pass

        async def exercise():
            with patch.dict(sys.modules, {"google": SimpleNamespace(),
                                          "google.adk": SimpleNamespace(),
                                          "google.adk.agents": SimpleNamespace(),
                                          "google.adk.agents.run_config": SimpleNamespace(
                                              RunConfig=lambda **values: SimpleNamespace(**values),
                                              StreamingMode=SimpleNamespace(SSE="SSE")),
                                          "google.adk.models": model_module,
                                          "backend.agents.typesafe": SimpleNamespace(
                                              TypeSafeModel=TypeSafeModel, execute_typesafe=None)}), \
                 patch("backend.agents.context.ManagedContext", return_value=Managed()), \
                 patch.object(runtime, "_adk_model", return_value=SimpleNamespace(
                     model="openai/test", _additional_args={})), \
                 patch.object(runtime, "_context_session", new=AsyncMock(return_value="session")), \
                 patch.object(runtime, "_context_budget", return_value=object()), \
                 patch.object(runtime, "_record", new=AsyncMock(return_value=SimpleNamespace(
                     config=config, lock=asyncio.Lock(), last_error=None))):
                events = []
                try:
                    async for event in runtime._execute(
                        config, SimpleNamespace(agent_id="agent", context_id="session", run_id=run_id),
                        SimpleNamespace(prompt="Inspect"),
                    ):
                        events.append(event)
                except RuntimeError as exc:
                    if capture_error:
                        return events, exc
                    raise
                return events

        return runtime, sessions, attempts, read_invocations, exercise

    def test_read_only_disconnect_retries_and_exposes_incomplete_thought(self) -> None:
        _runtime, sessions, attempts, _read_invocations, exercise = self._exercise()
        events = asyncio.run(exercise())
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sessions.delete_session.await_count, 1)
        kinds = [item.payload.get("kind") for item in events]
        self.assertIn("model_stream_retry", kinds)
        thought = next(item for item in events if item.payload.get("kind") == "model_reasoning")
        self.assertTrue(thought.payload["interrupted"])
        self.assertEqual(events[-1].type, AgentEventType.COMPLETED)

    def test_completed_write_is_not_replayed(self) -> None:
        _runtime, _sessions, attempts, _read_invocations, exercise = self._exercise(
            write_before_disconnect=True, capture_error=True,
        )
        events, error = asyncio.run(exercise())
        self.assertEqual(len(attempts), 1)
        self.assertIn("disconnected", str(error))
        interrupted = next(item for item in events if item.payload.get("kind") == "model_stream_interrupted")
        self.assertEqual(interrupted.payload["recovery_classification"], "reconcile_required")

    def test_request_exhaustion_retains_review_without_another_agent_retry(self) -> None:
        _runtime, sessions, attempts, _reads, exercise = self._exercise(
            capture_error=True, request_exhausted=True,
        )
        events, error = asyncio.run(exercise())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sessions.delete_session.await_count, 0)
        self.assertIn("request retries exhausted", str(error))
        self.assertIn("model_stream_interrupted", [e.payload.get("kind") for e in events])
        self.assertNotIn("model_stream_retry", [e.payload.get("kind") for e in events])

    def test_read_only_tool_receipt_allows_one_safe_retry(self) -> None:
        _runtime, _sessions, attempts, read_invocations, exercise = self._exercise(
            read_tool_before_disconnect=True,
        )
        events = asyncio.run(exercise())
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(read_invocations), 1)
        self.assertIn("model_stream_retry", [item.payload.get("kind") for item in events])

    def test_unjournaled_tool_is_not_assumed_read_only(self) -> None:
        _runtime, _sessions, attempts, read_invocations, exercise = self._exercise(
            read_tool_before_disconnect=True, journal_read_tool=False, capture_error=True,
        )
        events, _error = asyncio.run(exercise())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(read_invocations), 1)
        interrupted = next(item for item in events if item.payload.get("kind") == "model_stream_interrupted")
        self.assertEqual(interrupted.payload["recovery_classification"], "unknown")


class StreamStallDetectionTests(unittest.TestCase):
    def test_empty_partials_do_not_postpone_the_watchdog(self) -> None:
        async def start_request():
            trace = ModelTrace(asyncio.Queue())
            await trace.callbacks("agent")["before_model_callback"](None, SimpleNamespace(contents=[]))
            return trace

        trace = asyncio.run(start_request())
        request_id, started, phase = trace.pending["agent"]
        trace.pending["agent"] = (request_id, started - 400, phase)
        trace.note_activity("agent", None)
        trace.note_activity("agent", SimpleNamespace(parts=[]))
        trace.note_activity("agent", SimpleNamespace(parts=[SimpleNamespace(text="")]))
        self.assertEqual(trace.stalled_stream()["stage"], "first_byte")
        trace.note_activity("agent", SimpleNamespace(parts=[SimpleNamespace(text="answer")]))
        self.assertIsNone(trace.stalled_stream())
        self.assertIn("agent", trace._last_activity)
        trace._last_activity["agent"] -= 200
        trace.note_activity("agent", SimpleNamespace(parts=[
            SimpleNamespace(function_call=SimpleNamespace(name="tool")),
        ]))
        self.assertIsNone(trace.stalled_stream())

    def test_stall_requires_a_started_stream(self) -> None:
        async def start_request():
            trace = ModelTrace(asyncio.Queue())
            await trace.callbacks("agent")["before_model_callback"](None, SimpleNamespace(contents=[]))
            return trace

        trace = asyncio.run(start_request())
        # A fresh request that has not delivered anything yet is neither stall.
        self.assertIsNone(trace.stalled_stream())
        trace.note_activity("agent", SimpleNamespace(parts=[SimpleNamespace(text="chunk")]))
        trace._last_activity["agent"] -= 200
        stalled = trace.stalled_stream()
        self.assertEqual(stalled["role"], "agent")
        self.assertEqual(stalled["model_request"], 1)
        self.assertEqual(stalled["stage"], "mid_stream")
        self.assertGreaterEqual(stalled["idle_seconds"], 90)
        # A new request resets the activity baseline.
        asyncio.run(trace.callbacks("agent")["before_model_callback"](None, SimpleNamespace(contents=[])))
        self.assertIsNone(trace.stalled_stream())

    def test_first_byte_stall_bounds_a_never_started_stream(self) -> None:
        async def start_request():
            trace = ModelTrace(asyncio.Queue())
            await trace.callbacks("agent")["before_model_callback"](None, SimpleNamespace(contents=[]))
            return trace

        trace = asyncio.run(start_request())
        # Within the cold-start bound: silence is legitimate queueing.
        self.assertIsNone(trace.stalled_stream())
        # Outliving the first-byte bound is a stall even though nothing was
        # ever delivered: the gateway accepted the request and never answered.
        request_id, started, phase = trace.pending["agent"]
        trace.pending["agent"] = (request_id, started - 400, phase)
        stalled = trace.stalled_stream()
        self.assertEqual(stalled["stage"], "first_byte")
        self.assertEqual(stalled["model_request"], 1)
        self.assertGreaterEqual(stalled["idle_seconds"], 300)
        error = ModelStreamStalled(stalled)
        self.assertIn("no content at all", str(error))
        self.assertTrue(model_stream_disconnected(error))

    def test_synthesized_stall_is_classified_as_disconnected(self) -> None:
        error = ModelStreamStalled({"role": "agent", "model_request": 3, "idle_seconds": 120.0})
        self.assertTrue(model_stream_disconnected(error))
        self.assertIn("stopped delivering", str(error))

    def test_multiplexer_synthesizes_a_stall_for_silent_streams(self) -> None:
        async def exercise():
            trace = ModelTrace(asyncio.Queue())
            await trace.callbacks("agent")["before_model_callback"](None, SimpleNamespace(contents=[]))
            trace.note_activity("agent", SimpleNamespace(parts=[SimpleNamespace(text="chunk")]))
            trace._last_activity["agent"] -= 200

            async def silent_stream():
                await asyncio.sleep(30)
                yield "never"

            async def instant_wait(_tasks, timeout=None, **_kwargs):
                await asyncio.sleep(0)
                return set(), set()

            with patch.object(asyncio, "wait", instant_wait):
                try:
                    async for _ in multiplex_adk_events(silent_stream(), asyncio.Queue(), trace):
                        pass
                except ModelStreamStalled as stalled:
                    return stalled
            raise AssertionError("the silent stream was not synthesized as a stall")

        stalled = asyncio.run(exercise())
        self.assertEqual(stalled.stalled["role"], "agent")
        self.assertEqual(stalled.stalled["model_request"], 1)


if __name__ == "__main__":
    unittest.main()
