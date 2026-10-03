"""Provider-neutral tool failure boundary, separate from Run control flow.

This is a cooperative task boundary, not a security/process sandbox. The caller
owns and awaits the task: cancelling a Run still cancels and joins its tool.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from backend.errors import DomainError

logger = logging.getLogger(__name__)
ToolStatus = Literal["succeeded", "failed", "cancelled", "timed_out"]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """One invocation result; never an instruction to transition its Run."""

    status: ToolStatus
    response: Any

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


def _identity(value: Any) -> Any:
    return value


def _error(code: str, kind: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "type": kind, "message": message, **details}}


async def execute_tool(
    operation: Callable[[], Awaitable[Any]],
    *,
    serialize: Callable[[Any], Any] = _identity,
) -> ToolOutcome:
    """Contain invocation AND result conversion failures for every adapter.

    Successful payloads (including multimodal content) keep their existing shape.
    DomainError messages are public by contract; unexpected exception details
    stay in operator logs, correlated by error_id. Never retry here: a failed or
    timed-out operation may already have produced external side effects.

    A child-only CancelledError becomes tool feedback. Cancellation of this
    caller remains control flow, including when a plugin suppresses cancellation
    or throws during cleanup. No shield, detached worker, or uncancel is used.
    Runtime/provider errors outside this function deliberately still propagate.
    """
    owner = asyncio.current_task()
    if owner is None:
        raise RuntimeError("Tool execution requires an asyncio task")
    if owner.cancelling():
        raise asyncio.CancelledError()

    async def invoke() -> ToolOutcome:
        try:
            value = await operation()
            status: ToolStatus = (
                "failed" if isinstance(value, Mapping) and value.get("ok") is False
                else "succeeded"
            )
            return ToolOutcome(status, serialize(value))
        except DomainError as exc:
            status = "failed"
            feedback = _error(exc.code, type(exc).__name__, exc.message)
        except TimeoutError:
            status = "timed_out"
            feedback = _error(
                "tool_timeout", "TimeoutError",
                "Tool execution timed out. Inspect the operation state before retrying; "
                "external work may have partially completed or may still be running.",
            )
        except Exception:
            status = "failed"
            error_id = uuid4().hex
            logger.exception("Tool invocation failed (error_id=%s)", error_id)
            feedback = _error(
                "tool_execution_error", "ToolExecutionError",
                "Tool execution failed. Inspect the operation state before retrying; "
                "it may have partially completed.",
                error_id=error_id,
            )
        # Failure to encode even our safe envelope is an adapter/protocol fault,
        # not another plugin result. Do not recursively swallow that failure.
        return ToolOutcome(status, serialize(feedback))

    task = asyncio.create_task(invoke(), name="oaw-tool-invocation")
    try:
        outcome = await task
    except asyncio.CancelledError:
        if owner.cancelling():
            raise
        return ToolOutcome("cancelled", serialize(_error(
            "tool_cancelled", "CancelledError",
            "This tool invocation was cancelled. Inspect its state before retrying.",
        )))
    except Exception:
        if owner.cancelling():
            raise asyncio.CancelledError() from None
        raise
    # A plugin can suppress CancelledError in its cleanup. That must not swallow
    # the user's Stop or turn the cancelled Run into a successful invocation.
    if owner.cancelling():
        raise asyncio.CancelledError()
    return outcome
