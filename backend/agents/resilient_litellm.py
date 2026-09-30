"""Recover invalid tool arguments and transient pre-response provider failures."""

from __future__ import annotations

import asyncio
import json

from google.adk.models.lite_llm import LiteLlm
from google.genai import types


def _transient_provider_failure(error: BaseException) -> bool:
    """Only retry failures for which a fresh, unpublished request is useful."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 409, 429}:
            return False
        if isinstance(current, (ConnectionError, TimeoutError)) or type(current).__name__ in {
            "APIConnectionError", "APITimeoutError", "Timeout", "ReadTimeout",
            "ConnectTimeout", "ReadError", "ConnectError", "RemoteProtocolError",
            "TransferEncodingError", "MidStreamFallbackError",
        }:
            return True
        if isinstance(status, int) and (status in {408, 409, 429} or 500 <= status < 600):
            return True
        current = current.__cause__ or current.__context__
    return False


class ResilientLiteLlm(LiteLlm):
    """Correct malformed arguments and retry bounded unpublished provider failures.

    ADK already repairs several complete object-literal formats. Ambiguous
    syntax (especially quotes inside code) must be regenerated, not guessed.
    Recovery contents are request-local and never change stored user messages.
    """

    transport_retry_limit: int = 2

    async def generate_content_async(self, llm_request, stream=False):
        request = llm_request.model_copy(update={"contents": list(llm_request.contents)})
        parse_retries = 0
        transport_retries = 0
        while True:
            published = False
            try:
                async for response in super().generate_content_async(request, stream=stream):
                    published = True
                    yield response
                return
            except json.JSONDecodeError as exc:
                # Only ADK's tool-argument decoder is recoverable here. Provider
                # transport JSON errors and already-published streams propagate.
                traceback = exc.__traceback__
                tool_arguments = False
                while traceback is not None:
                    frame = traceback.tb_frame
                    if (frame.f_globals.get("__name__") == "google.adk.models.lite_llm"
                            and frame.f_code.co_name == "_parse_tool_call_arguments"):
                        tool_arguments = True
                    traceback = traceback.tb_next
                if not tool_arguments or published:
                    raise
                if parse_retries == 2:
                    raise RuntimeError(
                        "Tool arguments remained invalid after 2 correction attempts "
                        f"({exc.msg}, line {exc.lineno}, column {exc.colno}). "
                        "No tools from the rejected responses were executed. "
                        "Try splitting the operation into smaller tool calls."
                    ) from None
                parse_retries += 1
                # Bound context growth and never quote the raw payload in errors
                # sent to the UI/logs. It goes only to the same configured model.
                excerpt = exc.doc[:12000]
                request.contents.extend([
                    types.Content(role="model", parts=[types.Part.from_text(
                        text="Rejected tool argument payload (possibly truncated):\n" + excerpt
                    )]),
                    types.Content(role="user", parts=[types.Part.from_text(text=(
                        "Runtime feedback: your last tool-call response could not be parsed: "
                        f"{exc.msg}, line {exc.lineno}, column {exc.colno}. "
                        "No tool in that response was executed. Regenerate the required tool "
                        "calls using valid JSON objects matching their schemas. Escape quotes, "
                        "backslashes and newlines inside strings. Do not repeat tools from "
                        "earlier successful responses. Split large operations if needed."
                    ))]),
                ])
            except Exception as exc:
                # Never replay a response once any chunk has escaped to ADK:
                # it could contain a tool call or a partial model response.
                # A bounded retry before that point cannot repeat a tool.
                if published or transport_retries >= self.transport_retry_limit or not _transient_provider_failure(exc):
                    raise
                transport_retries += 1
                await asyncio.sleep(0.5 * (2 ** (transport_retries - 1)))
