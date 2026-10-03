"""The error boundary is a shared contract, not a provider-specific catch list."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
from types import SimpleNamespace

import pytest

from backend.agents.media import VisualToolResult, adk_tool_result, codex_tool_content
from backend.agents.tool_execution import execute_tool
from backend.errors import PermissionDeniedError, ResourceValidationError


async def returning(value):
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, False, 0, "text", [1, 2], {"value": 3}])
async def test_success_keeps_payload_shape(value):
    outcome = await execute_tool(lambda: returning(value))
    assert outcome.ok
    assert outcome.status == "succeeded"
    assert outcome.response is value


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    TypeError("secret implementation detail"),
    ValueError("secret implementation detail"),
    OSError("secret implementation detail"),
    PermissionError("secret implementation detail"),
    RuntimeError("secret implementation detail"),
    ExceptionGroup("secret implementation detail", [TypeError("nested secret")]),
])
async def test_unexpected_failures_are_sanitized_logged_and_not_retried(failure, caplog):
    calls = 0

    async def broken():
        nonlocal calls
        calls += 1
        raise failure

    outcome = await execute_tool(broken)
    assert not outcome.ok
    assert outcome.status == "failed"
    error = outcome.response["error"]
    assert error["code"] == "tool_execution_error"
    assert "secret" not in json.dumps(outcome.response)
    assert error["error_id"] in caplog.text
    assert any(record.exc_info for record in caplog.records)
    assert calls == 1
    assert (await execute_tool(lambda: returning("next action"))).ok


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [PermissionDeniedError("revoked"), ResourceValidationError("invalid input")])
async def test_domain_error_feedback_remains_actionable(failure):
    async def denied():
        raise failure

    outcome = await execute_tool(denied)
    assert outcome.response == {"ok": False, "error": {
        "code": failure.code, "type": type(failure).__name__, "message": failure.message,
    }}


@pytest.mark.asyncio
async def test_returned_failure_is_not_reported_as_success():
    feedback = {"ok": False, "error": {"code": "sandbox_failed", "message": "command failed"}}
    outcome = await execute_tool(lambda: returning(feedback), serialize=codex_tool_content)
    assert not outcome.ok
    assert json.loads(outcome.response[0]["text"]) == feedback


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [object(), {"set": {1, 2}}, {"value": float("nan")}])
async def test_adk_serialization_failure_is_tool_feedback(value):
    outcome = await execute_tool(lambda: returning(value), serialize=adk_tool_result)
    assert not outcome.ok
    assert outcome.response["error"]["code"] == "tool_execution_error"
    json.dumps(outcome.response, allow_nan=False)


@pytest.mark.asyncio
async def test_circular_result_does_not_escape_boundary():
    value = {}
    value["self"] = value
    outcome = await execute_tool(lambda: returning(value), serialize=adk_tool_result)
    assert not outcome.ok
    assert outcome.response["ok"] is False


@pytest.mark.asyncio
async def test_codex_serialization_failure_returns_error_content():
    outcome = await execute_tool(lambda: returning(object()), serialize=codex_tool_content)
    assert not outcome.ok
    assert json.loads(outcome.response[0]["text"])["ok"] is False


@pytest.mark.asyncio
async def test_multimodal_codex_payload_survives_boundary():
    # Encoding fixture: byte validation belongs to ToolImage's separate tests.
    image = SimpleNamespace(data=b"image bytes", media_type="image/png", data_url="data:image/png;base64,aW1hZ2U=")
    value = VisualToolResult({"filename": "figure.png"}, (image,))
    outcome = await execute_tool(lambda: returning(value), serialize=codex_tool_content)
    assert outcome.ok
    assert outcome.response[1] == {"type": "inputImage", "imageUrl": image.data_url}
    assert json.loads(outcome.response[0]["text"])["filename"] == "figure.png"


@pytest.mark.asyncio
async def test_tool_task_inherits_context_without_mutating_caller():
    scope = ContextVar("test-tool-scope", default="outside")
    token = scope.set("run-1")
    owner = asyncio.current_task()

    async def operation():
        assert asyncio.current_task() is not owner
        assert scope.get() == "run-1"
        scope.set("tool-local")
        return "done"

    try:
        assert (await execute_tool(operation)).ok
        assert scope.get() == "run-1"
    finally:
        scope.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_self", [False, True])
async def test_tool_only_cancellation_is_feedback(cancel_self):
    async def operation():
        if cancel_self:
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
        raise asyncio.CancelledError()

    outcome = await execute_tool(operation)
    assert outcome.status == "cancelled"
    assert outcome.response["error"]["code"] == "tool_cancelled"
    assert asyncio.current_task().cancelling() == 0
    assert (await execute_tool(lambda: returning("continue"))).ok


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["normal", "suppress", "error"])
async def test_run_cancellation_still_cancels_and_joins_tool(cleanup):
    entered = asyncio.Event()
    cleaned = asyncio.Event()
    worker = None

    async def operation():
        nonlocal worker
        worker = asyncio.current_task()
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if cleanup == "suppress":
                return "must not swallow Run cancellation"
            if cleanup == "error":
                raise TypeError("cleanup failed")
            raise
        finally:
            cleaned.set()

    caller = asyncio.create_task(execute_tool(operation))
    await asyncio.wait_for(entered.wait(), timeout=1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cleaned.is_set()
    assert worker.done()
    assert caller.cancelled()


@pytest.mark.asyncio
async def test_tool_timeout_is_not_a_run_timeout():
    async def operation():
        async with asyncio.timeout(0):
            await asyncio.Event().wait()

    outcome = await execute_tool(operation)
    assert outcome.status == "timed_out"
    assert outcome.response["error"]["code"] == "tool_timeout"
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.asyncio
async def test_caller_deadline_remains_control_flow():
    cleaned = asyncio.Event()

    async def operation():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    # Wait until the child really starts before expiring the outer deadline.
    started = asyncio.Event()

    async def tracked():
        started.set()
        return await operation()

    caller = asyncio.create_task(execute_tool(tracked))
    await started.wait()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0):
            await caller
    assert cleaned.is_set()
    assert caller.cancelled()


@pytest.mark.asyncio
async def test_parallel_failure_does_not_cancel_successful_sibling():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def good():
        entered.set()
        await release.wait()
        return "success"

    async def bad():
        await entered.wait()
        release.set()
        raise TypeError("one tool failed")

    async with asyncio.TaskGroup() as group:
        failed = group.create_task(execute_tool(bad))
        succeeded = group.create_task(execute_tool(good))
    assert not failed.result().ok
    assert succeeded.result().response == "success"


@pytest.mark.asyncio
async def test_failure_to_encode_safe_envelope_remains_adapter_fault():
    def broken_serializer(value):
        raise RuntimeError("adapter cannot encode any response")

    with pytest.raises(RuntimeError, match="adapter cannot encode"):
        await execute_tool(lambda: returning("data"), serialize=broken_serializer)


@pytest.mark.asyncio
async def test_process_control_base_exception_is_not_tool_feedback():
    class FatalControl(BaseException):
        pass

    async def operation():
        raise FatalControl()

    with pytest.raises(FatalControl):
        await execute_tool(operation)
