"""Native wait composition must never replay work or switch operation identity."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agents.tool_execution import collect_operation_result, OperationReceiptMismatch, tool_payload
from backend.sandbox.commands import python_entrypoint
from open_agent_world.context.adk import reset_transient_session


@pytest.mark.asyncio
async def test_collection_awaits_same_operation_and_preserves_errors():
    running = {"result": {"status": "running", "operation_id": "original"}}
    error = {"ok": False, "error": {"code": "permission_denied"}}
    wait = AsyncMock(side_effect=[running, error])
    result = await collect_operation_result(running, wait, asyncio.Event())
    assert result == error and wait.await_count == 2
    assert all(call.args == ("original", 30) for call in wait.await_args_list)
    assert tool_payload({"result": "not a mapping"}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "completed"])
async def test_collection_rejects_crossed_receipts_even_when_completed(status):
    pending = {"status": "running", "operation_id": "original"}
    wait = AsyncMock(return_value={"status": status, "operation_id": "other", "exit_code": 0})
    with pytest.raises(OperationReceiptMismatch) as error:
        await collect_operation_result(pending, wait, asyncio.Event())
    assert error.value.pending == pending and wait.await_count == 1


@pytest.mark.asyncio
async def test_collection_cancellation_does_not_poll_or_resubmit():
    stopped = asyncio.Event(); stopped.set()
    wait = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await collect_operation_result({"status": "running", "operation_id": "original"}, wait, stopped)
    wait.assert_not_called()


@pytest.mark.asyncio
async def test_transient_session_reset_preserves_state_and_empty_session():
    state = {"domain_fact": "preserved"}
    sessions = SimpleNamespace(get_session=AsyncMock(return_value=SimpleNamespace(events=[1], state=state)),
                               delete_session=AsyncMock(), create_session=AsyncMock())
    await reset_transient_session(sessions, app_name="app", user_id="user", session_id="session")
    assert sessions.create_session.call_args.kwargs["state"] == state
    sessions.get_session.return_value.events = []
    await reset_transient_session(sessions, app_name="app", user_id="user", session_id="session")
    assert sessions.create_session.await_count == 1


@pytest.mark.parametrize("argv,expected", [
    (["/env/bin/python3.12", "-X", "dev", "-Wignore", "-u", "script.py", "--n", "3"], ("script", "script.py", 6)),
    (["python3", "-B", "-c", "print(1)"], ("code", "print(1)", 4)),
    (["python.exe", "-m", "package"], ("module", "package", 3)),
    (["python-other", "script.py"], None), (["sh", "-c", "python3 script.py"], None),
])
def test_shared_python_recognizer_does_not_infer_shell_execution(argv, expected):
    assert python_entrypoint(argv) == expected
