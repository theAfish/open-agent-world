"""ADK dispatch failures occur before the scoped callable's tool boundary."""
from __future__ import annotations

import asyncio
import json

from .tool_execution import execute_tool


def invalid_tool_arguments(error: BaseException) -> bool:
    """Only ADK's argument decoder, never provider/transport JSON failures."""
    if not isinstance(error, json.JSONDecodeError):
        return False
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if (frame.f_globals.get("__name__") == "google.adk.models.lite_llm"
                and frame.f_code.co_name == "_parse_tool_call_arguments"):
            return True
        traceback = traceback.tb_next
    return False


def append_argument_feedback(request, error: json.JSONDecodeError) -> None:
    """Call only when no tool from the rejected response could be dispatched."""
    from google.genai import types

    # Bound context growth. Raw arguments go only to the same configured model,
    # never to public diagnostics or exception messages.
    request.contents.append(types.Content(role="user", parts=[
        types.Part.from_text(text="Rejected tool argument payload (possibly truncated):\n" + error.doc[:12000]),
        types.Part.from_text(text=(
            "Runtime feedback: your last tool-call response could not be parsed: "
            f"{error.msg}, line {error.lineno}, column {error.colno}. "
            "No tool in that response was executed. Regenerate the required tool "
            "calls using valid JSON objects matching their schemas. Escape quotes, "
            "backslashes and newlines inside strings. Do not repeat tools from "
            "earlier successful responses. Split large operations if needed."
        )),
    ]))


async def on_tool_error(*, tool, args, tool_context, error):
    # ADK supplies this placeholder when lookup fails, before calling any tool.
    from google.adk.tools.base_tool import BaseTool

    if asyncio.current_task().cancelling():
        raise asyncio.CancelledError()
    if type(tool) is BaseTool and tool.description == "Tool not found":
        return {"ok": False, "error": {
            "code": "unknown_tool", "side_effects": "none",
            "message": "This tool is not registered. Use only the function names and argument schemas supplied in this request.",
        }}

    async def failed():
        raise error

    # Includes ADK argument conversion failures outside our scoped callable.
    # The common boundary sanitizes details and never retries the invocation.
    return (await execute_tool(failed)).response
