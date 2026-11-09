"""Offline, deterministic coverage of the request-level stream boundary."""
import asyncio
import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from backend.agents.context import Checkpoint, ContextBudget, ManagedContext, text_content
from backend.agents.model_observation import ModelTrace, content_kinds, model_stream_disconnected
from backend.agents.request_recovery import RequestRecoveryExhausted, RequestRetryPolicy, recover_request, _retry_delay


class Request(NS):
    def model_copy(self, *, update):
        return Request(**(vars(self) | update))


def response(text="", *, partial=False, tool=None):
    return NS(partial=partial, content=NS(parts=[NS(text=text, thought=partial, function_call=tool)]))


class RequestRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_content_kinds_include_non_text_activity_without_exposing_payloads(self):
        content = NS(parts=[NS(text="thought", thought=True), NS(text="answer"),
                            NS(function_call={"name": "save"}), NS(function_response={"result": "private"}),
                            NS(inline_data=b"private"), NS(file_data="private"),
                            NS(executable_code="private"), NS(code_execution_result="private")])
        self.assertEqual(content_kinds(content), {"reasoning", "text", "tool_call", "tool_result", "media"})
        self.assertEqual(content_kinds(NS(parts=[NS(text="")])), set())

    def setup_request(self):
        request = Request(contents=[{"role": "user", "parts": [{"text": "build"}]}],
                          config=NS(max_output_tokens=None), tools_dict={"save": object()})
        trace = ModelTrace(asyncio.Queue())
        trace.begin("structure_builder", request)
        return request, trace

    async def test_adapter_heartbeats_are_diagnosed_separately_from_content(self):
        request, trace = self.setup_request()
        class Provider:
            async def generate_content_async(self, req, stream=False):
                yield response(partial=True)
                yield response("a", partial=True)
                yield response(partial=True)
                raise ConnectionResetError("private upstream details") from TimeoutError("private secret")
        with self.assertRaises(RequestRecoveryExhausted):
            _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder", retries=0)]
        events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
        row = next(e for e in events if e.get("phase") == "model_recovery")
        self.assertEqual(row["observed_response_chunks"], 3)
        self.assertEqual(row["received_content_chunks"], 1)
        self.assertEqual(row["empty_response_chunks"], 2)
        self.assertEqual(row["watchdog_scope"], "adapter_content")
        self.assertEqual(row["error_chain_types"], ["ConnectionResetError", "TimeoutError"])
        self.assertIsNotNone(row["first_response_seconds"])
        self.assertIsNotNone(row["last_response_idle_seconds"])
        self.assertNotIn("private", str(events))
        metrics = row["stream_metrics"]
        self.assertEqual(metrics["content_chunks_by_kind"], {"reasoning": 1})
        self.assertEqual(metrics["observed_activity"], "content_received")
        self.assertEqual(metrics["observation_scope"], "adapter_responses")
        self.assertFalse(metrics["transport_observation_available"])
        self.assertFalse(metrics["generation_resume_used"])
        self.assertEqual(metrics["request_mode"], "initial_request")
        self.assertGreaterEqual(metrics["longest_content_gap_seconds"], 0)

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
        self.assertEqual(trace.request_stream_metrics["structure_builder"]["observed_response_chunks"], 2)
        self.assertFalse(trace.request_stream_metrics["structure_builder"]["stream_requested"])
        self.assertEqual(trace.request_stream_metrics["structure_builder"]["request_mode"], "request_replay")
        self.assertEqual(trace.request_stream_metrics["structure_builder"]["content_chunks_by_kind"],
                         {"reasoning": 1, "tool_call": 2})
        events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
        recovery = next(e for e in events if e.get("phase") == "model_recovery")
        self.assertEqual(recovery["model_request"], 1)
        self.assertEqual(recovery["error_type"], "ConnectionResetError")
        self.assertEqual(recovery["recovery_mode"], "request_replay")
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
        policy = RequestRetryPolicy(initial_delay_seconds=0.0)
        class Provider:
            async def generate_content_async(self, req, stream=False):
                yield response("partial", partial=True)
                raise ConnectionResetError()
        with self.assertRaises(RequestRecoveryExhausted) as caught:
            _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder", policy=policy)]
        self.assertFalse(model_stream_disconnected(caught.exception))
        self.assertEqual(trace.request_attempts["structure_builder"], policy.max_attempts)

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
        self.assertEqual(error["stream_metrics"]["observed_activity"], "empty_responses_only")
        self.assertIsNone(error["stream_metrics"]["first_content_seconds"])

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

    async def test_default_budget_recovers_on_last_attempt_with_identical_inputs(self):
        request, trace = self.setup_request()
        policy = RequestRetryPolicy(initial_delay_seconds=0.0)
        calls = []
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(copy.deepcopy(req.contents))
                yield response("partial", partial=True, tool={"name": "save"})
                if len(calls) < policy.max_attempts:
                    raise ConnectionResetError()
                yield response("done")
        result = [r async for r in recover_request(Provider(), request, trace, "structure_builder", policy=policy)]
        self.assertEqual(len(calls), policy.max_attempts)
        self.assertEqual(calls, [request.contents] * policy.max_attempts)
        self.assertEqual(len(result), 1)
        self.assertEqual(trace.sequence, 1)
        events = [trace.queue.get_nowait()[1] for _ in range(trace.queue.qsize())]
        errors = [e for e in events if e.get("phase") == "model_recovery"]
        self.assertEqual([e["next_attempt"] for e in errors], list(range(2, policy.max_attempts + 1)))
        self.assertTrue(all(e["retrying"] and e["max_attempts"] == policy.max_attempts for e in errors))

    async def test_cancel_during_backoff_returns_promptly_without_another_request(self):
        request, trace = self.setup_request()
        calls = []
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                raise ConnectionResetError()
                yield
        async def consume():
            return [r async for r in recover_request(Provider(), request, trace, "structure_builder",
                policy=RequestRetryPolicy(initial_delay_seconds=60.0, max_delay_seconds=60.0, jitter_ratio=0.0))]
        task = asyncio.create_task(consume())
        while True:
            _, event = await asyncio.wait_for(trace.queue.get(), 1)
            if event.get("phase") == "model_retry_wait":
                break
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertEqual(calls, [1])

    async def test_backoff_does_not_consume_active_model_deadline(self):
        request, trace = self.setup_request()
        calls = []
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                await asyncio.sleep(0.01)
                if len(calls) < 3:
                    raise ConnectionResetError()
                yield response("done")
        deadline = asyncio.timeout(0.04)
        async with deadline:
            trace.recovery_deadlines["structure_builder"] = (deadline, 0.04)
            result = [r async for r in recover_request(Provider(), request, trace, "structure_builder",
                policy=RequestRetryPolicy(max_attempts=3, initial_delay_seconds=0.05, max_delay_seconds=0.05, jitter_ratio=0.0))]
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result), 1)
        self.assertFalse(deadline.expired())

    async def test_wrapped_authentication_error_is_not_treated_as_gateway_retry(self):
        request, trace = self.setup_request()
        calls = []
        class AuthError(Exception):
            status_code = 401
        class MidStreamFallbackError(Exception):
            status_code = 500
        class Provider:
            async def generate_content_async(self, req, stream=False):
                calls.append(1)
                raise MidStreamFallbackError() from AuthError()
                yield
        with self.assertRaises(MidStreamFallbackError):
            _ = [r async for r in recover_request(Provider(), request, trace, "structure_builder", retry_delay=0)]
        self.assertEqual(calls, [1])

    def test_backoff_is_bounded_and_honors_retry_after(self):
        policy = RequestRetryPolicy(jitter_ratio=0.0)
        self.assertEqual([_retry_delay(ConnectionResetError(), i, policy) for i in range(1, 7)], [2, 4, 8, 16, 30, 30])
        self.assertEqual(_retry_delay(NS(response=NS(headers={"Retry-After": "10"})), 1, policy), 10)
        self.assertEqual(_retry_delay(NS(response=NS(headers={"retry-after": "999"})), 1, policy), 30)
        self.assertEqual(_retry_delay(NS(response=NS(headers={"retry-after": "bad"})), 1, policy), 2)

    def test_unreachable_network_retries_but_filesystem_and_bad_configuration_do_not(self):
        import errno
        self.assertTrue(model_stream_disconnected(OSError(errno.ENETUNREACH, "unreachable")))
        self.assertFalse(model_stream_disconnected(PermissionError(errno.EACCES, "file denied")))
        self.assertFalse(model_stream_disconnected(ValueError("bad model")))

    def test_retry_policy_rejects_unbounded_or_invalid_configuration(self):
        from pydantic import ValidationError
        for config in [{"max_attempts": 0}, {"max_attempts": True}, {"max_attempts": 100},
                       {"initial_delay_seconds": float("nan")}, {"initial_delay_seconds": 10.0, "max_delay_seconds": 2.0}]:
            with self.assertRaises(ValidationError):
                RequestRetryPolicy.model_validate(config)


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


class NetworkClassificationTests(unittest.TestCase):
    def test_temporary_dns_error_retries_but_certificate_verification_does_not(self):
        import socket
        import ssl
        self.assertTrue(model_stream_disconnected(socket.gaierror(socket.EAI_AGAIN, "temporary failure")))
        self.assertFalse(model_stream_disconnected(socket.gaierror(socket.EAI_NONAME, "unknown host")))
        wrapper = ConnectionError("connect failed")
        wrapper.__cause__ = ssl.SSLCertVerificationError("invalid certificate")
        self.assertFalse(model_stream_disconnected(wrapper))

    def test_retry_after_http_date_is_bounded(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        later = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
        delay = _retry_delay(NS(response=NS(headers={"Retry-After": later})), 1, RequestRetryPolicy(jitter_ratio=0.0))
        self.assertGreater(delay, 8)
        self.assertLessEqual(delay, 10)

    def test_rate_limit_and_temporary_server_statuses_only(self):
        for status in [408, 429, 500, 502, 503, 504]:
            self.assertTrue(model_stream_disconnected(NS(status_code=status, __cause__=None, __context__=None)))
        for status in [400, 401, 403, 404, 422]:
            self.assertFalse(model_stream_disconnected(NS(status_code=status, __cause__=None, __context__=None)))
