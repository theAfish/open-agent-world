"""Offline, deterministic coverage of the request-level stream boundary."""
import asyncio
import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from backend.agents.context import Checkpoint, ContextBudget, ManagedContext, text_content
from backend.agents.model_observation import ModelTrace, model_stream_disconnected
from backend.agents.request_recovery import RequestRecoveryExhausted, recover_request


class Request(NS):
    def model_copy(self, *, update):
        return Request(**(vars(self) | update))


def response(text="", *, partial=False, tool=None):
    return NS(partial=partial, content=NS(parts=[NS(text=text, thought=partial, function_call=tool)]))


class RequestRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setup_request(self):
        request = Request(contents=[{"role": "user", "parts": [{"text": "build"}]}],
                          config=NS(max_output_tokens=None), tools_dict={"save": object()})
        trace = ModelTrace(asyncio.Queue())
        trace.begin("structure_builder", request)
        return request, trace

    async def test_partial_disconnect_retries_exact_request_without_tool_replay(self):
        request, trace = self.setup_request()
        requests, closed = [], []

        class Provider:
            async def generate_content_async(self, req, stream=False):
                requests.append(copy.deepcopy(req.contents))
                self.assert_tools = req.tools_dict is request.tools_dict
                attempt = len(requests)
                try:
                    yield response("thinking", partial=True, tool={"name": "save"})
                    if attempt == 1:
                        req.contents.append({"text": "provider mutated its input"})
                        raise ConnectionResetError("secret request data")
                    yield response(tool={"name": "save"})
                finally:
                    closed.append(attempt)

        provider = Provider()
        results = [item async for item in recover_request(provider, request, trace, "structure_builder", retry_delay=0)]
        self.assertEqual(len(results), 1)  # Only the confirmed tool escapes to ADK.
        self.assertEqual(requests, [request.contents, request.contents])
        self.assertTrue(provider.assert_tools)
        self.assertEqual(closed, [1, 2])
        events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
        recovery = next(e for e in events if e.get("phase") == "model_recovery")
        self.assertEqual(recovery["model_request"], 1)
        self.assertEqual(recovery["error_type"], "ConnectionResetError")
        self.assertNotIn("secret request data", str(events))
        previews = [e for e in events if e.get("kind") == "model_reasoning"]
        self.assertTrue(any(e["interrupted"] for e in previews))
        self.assertEqual(len({e["provider_message_id"] for e in previews}), 2)
        self.assertEqual(trace.waiting()["model_attempt"], 2)

    async def test_error_after_final_before_eof_does_not_publish_first_tool(self):
        request, trace = self.setup_request()
        calls = []

        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                yield response(tool={"name": "save", "attempt": len(calls)})
                if len(calls) == 1:
                    raise ConnectionResetError()

        results = [item async for item in recover_request(Provider(), request, trace, "structure_builder", retry_delay=0)]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].content.parts[0].function_call["attempt"], 2)

    async def test_exhaustion_prevents_outer_agent_restart(self):
        request, trace = self.setup_request()
        class Provider:
            async def generate_content_async(self, req, stream=False):
                yield response("partial", partial=True)
                raise ConnectionResetError()
        with self.assertRaises(RequestRecoveryExhausted) as caught:
            _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder", retry_delay=0)]
        self.assertFalse(model_stream_disconnected(caught.exception))
        self.assertEqual(trace.request_attempts["structure_builder"], 2)

    async def test_local_watchdog_cancels_read_before_retry(self):
        request, trace = self.setup_request()
        closed, calls = [], []
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                try:
                    if len(calls) == 1:
                        yield response("partial", partial=True)
                        await asyncio.Event().wait()
                    yield response("done")
                finally:
                    closed.append(len(calls))
        with patch("backend.agents.model_observation.STREAM_STALL_SECONDS", 0.01):
            results = [r async for r in recover_request(Provider(), request, trace, "structure_builder", retry_delay=0)]
        self.assertEqual(closed, [1, 2])
        self.assertEqual(len(results), 1)
        events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
        error = next(e for e in events if e.get("phase") == "model_recovery")
        self.assertEqual(error["error_origin"], "local_idle_watchdog")
        self.assertEqual(error["stage"], "mid_stream")

    async def test_first_byte_timeout_and_empty_keepalives(self):
        request, trace = self.setup_request()
        class Provider:
            async def generate_content_async(self, req, stream=False):
                while True:
                    await asyncio.sleep(0.001)
                    yield response(partial=True)
        with patch("backend.agents.model_observation.STREAM_FIRST_BYTE_SECONDS", 0.02):
            with self.assertRaises(RequestRecoveryExhausted):
                _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder", retries=0)]
        events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
        error = next(e for e in events if e.get("phase") == "model_recovery")
        self.assertEqual(error["stage"], "first_byte")
        self.assertEqual(error["received_content_chunks"], 0)

    async def test_cancellation_never_retries(self):
        request, trace = self.setup_request()
        ready, closed = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                try:
                    ready.set()
                    await asyncio.Event().wait()
                    yield response("unreachable")
                finally:
                    closed.set()
        async def run():
            return [r async for r in recover_request(Provider(), request, trace, "structure_builder")]
        task = asyncio.create_task(run())
        await ready.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertEqual(len(calls), 1)

    async def test_auth_error_is_not_retried(self):
        request, trace = self.setup_request()
        calls = []
        class AuthError(Exception):
            status_code = 401
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                raise AuthError()
                yield
        with self.assertRaises(AuthError):
            _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder")]
        self.assertEqual(len(calls), 1)

    async def test_clean_eof_without_final_is_not_success(self):
        request, trace = self.setup_request()
        class Provider:
            async def generate_content_async(self, req, stream=False):
                yield response("incomplete", partial=True)
        with self.assertRaises(RequestRecoveryExhausted):
            _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder", retries=0)]


class ContextRetryTests(unittest.TestCase):
    def test_same_run_keeps_one_input_new_run_keeps_repeated_user_task(self):
        checkpoint = Checkpoint()
        store = NS(load=lambda *_: checkpoint, session=lambda *_: None)
        args = (store, "agent", "conversation#atomsculptor:atom_sculptor")
        model = NS(model="test")
        first = ManagedContext(*args, "run-1", model, "build", budget=ContextBudget(8192, 1024))
        first.checkpoint.contents.append({"role": "model", "parts": [{"text": "confirmed tool result"}]})
        ManagedContext(*args, "run-1", model, "build", budget=ContextBudget(8192, 1024))
        self.assertEqual(checkpoint.contents.count(text_content("build")), 1)
        ManagedContext(*args, "run-2", model, "build", budget=ContextBudget(8192, 1024))
        self.assertEqual(checkpoint.contents.count(text_content("build")), 2)
