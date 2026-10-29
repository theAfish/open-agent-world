"""Recover invalid tool arguments and transient pre-response provider failures."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from pydantic import Field, PrivateAttr
from urllib.parse import urlsplit

from google.adk.models.lite_llm import LiteLlm
from google.genai import types

from .adk_tool_errors import append_argument_feedback, invalid_tool_arguments


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


def _unsupported_tool_choice(error: BaseException) -> bool:
    """A precise parameter rejection, never a generic 400 or transport retry."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        message = str(error).lower()
        message = message.replace("`", "'")
        choice_rejected = re.search(
            r"(?:does not support|not supported|unsupported)\s+(?:this\s+|the\s+)?['\"]?(?:tool_choice|tool choice)\b"
            r"|(?:tool_choice|tool choice)['\"]?(?:(?:\s*=\s*|\s+)['\"]?(?:required|any)['\"]?)?\s+(?:is\s+)?(?:not supported|unsupported)\b",
            message) or re.search(
                r"(?:tool_choice|tool choice)['\"]?\s+(?:must|should)\s+be\s+['\"]?auto\b"
                r"|only\s+(?:supports?|allowed)\s+['\"]?(?:tool_choice|tool choice)['\"]?\s*(?:=|:)\s*['\"]?auto\b", message)
        if getattr(error, "status_code", None) == 400 and choice_rejected:
            return True
        error = error.__cause__ or error.__context__
    return False


def _automatic_tool_choice(request) -> bool:
    config = request.config
    choice = getattr(getattr(config, "tool_config", None), "function_calling_config", None)
    if choice is None or str(choice.mode).rsplit(".", 1)[-1] != "ANY":
        return False
    # ADK's LiteLLM adapter ignores allowed_function_names. Enforce a caller's
    # advertised subset before falling back to automatic selection.
    allowed = set(choice.allowed_function_names or [])
    if allowed:
        tools = []
        for tool in config.tools or []:
            declarations = getattr(tool, "function_declarations", None)
            if declarations:
                kept = [d for d in declarations if d.name in allowed]
                if kept:
                    tools.append(tool.model_copy(update={"function_declarations": kept}))
        if not tools:
            return False
        config.tools = tools
    config.tool_config = config.tool_config.model_copy(update={
        "function_calling_config": choice.model_copy(update={"mode": types.FunctionCallingConfigMode.AUTO})})
    return True


class ResilientLiteLlm(LiteLlm):
    """Correct malformed arguments and retry bounded unpublished provider failures.

    ADK already repairs several complete object-literal formats. Ambiguous
    syntax (especially quotes inside code) must be regenerated, not guessed.
    Recovery contents are request-local and never change stored user messages.
    """

    transport_retry_limit: int = 2
    argument_retry_limit: int = 2
    context_preflight: Any = Field(default=None, exclude=True)
    _auto_tool_choice_models: set[str] = PrivateAttr(default_factory=set)

    def __init__(self, model: str, *, context_preflight=None, transport_retry_limit=2, argument_retry_limit=2, **kwargs):
        # LiteLlm forwards constructor kwargs to the provider. These local
        # control hooks must never enter an HTTP completion payload.
        super().__init__(model, **kwargs)
        self.context_preflight = context_preflight
        self.transport_retry_limit = transport_retry_limit
        self.argument_retry_limit = argument_retry_limit

    @property
    def requires_tool_reasoning(self) -> bool:
        # A configured native route or official endpoint declares its protocol.
        # A DeepSeek model name on an arbitrary intermediary does not.
        return (self.model.startswith("deepseek/") or
                urlsplit(self._additional_args.get("api_base") or "").hostname == "api.deepseek.com")

    async def generate_content_async(self, llm_request, stream=False):
        request = llm_request.model_copy(update={"contents": list(llm_request.contents),
                                                "config": llm_request.config.model_copy(deep=True)})
        model = request.model or self.model
        adapted_choice = model in self._auto_tool_choice_models and _automatic_tool_choice(request)
        parse_retries = 0
        transport_retries = 0
        while True:
            published = False
            published_non_thought = False
            if self.context_preflight is not None:
                await self.context_preflight(request)
            try:
                async for response in super().generate_content_async(request, stream=stream):
                    if (adapted_choice or parse_retries) and hasattr(response, "custom_metadata"):
                        metadata = dict(response.custom_metadata or {})
                        adapter = dict(metadata.get("oaw_adapter") or {})
                        if adapted_choice:
                            adapter.update(tool_choice="auto", requested_tool_choice="required",
                                           reason="provider_rejected_forced_tool_choice")
                        if parse_retries:
                            adapter["tool_argument_corrections"] = parse_retries
                        response = response.model_copy(update={"custom_metadata": {
                            **metadata, "oaw_adapter": adapter}})
                    # ADK publishes reasoning before decoding accumulated tool
                    # arguments. Thoughts alone cannot dispatch a tool; any
                    # other published part makes replay unsafe.
                    content = getattr(response, "content", None)
                    published_non_thought |= not hasattr(response, "content") or any(
                        not part.thought or part.function_call is not None
                        for part in (content.parts or [] if content else []))
                    published = True
                    yield response
                return
            except json.JSONDecodeError as exc:
                # Only ADK's tool-argument decoder is recoverable here. Provider
                # transport JSON errors and already-published actions propagate.
                if not invalid_tool_arguments(exc) or published_non_thought or self.argument_retry_limit == 0:
                    raise
                if parse_retries >= self.argument_retry_limit:
                    raise RuntimeError(
                        f"Tool arguments remained invalid after {parse_retries} correction attempts "
                        f"({exc.msg}, line {exc.lineno}, column {exc.colno}). "
                        "No tools from the rejected responses were executed. "
                        "Try splitting the operation into smaller tool calls."
                    ) from None
                parse_retries += 1
                append_argument_feedback(request, exc)
            except Exception as exc:
                if not published and not adapted_choice and _unsupported_tool_choice(exc) and _automatic_tool_choice(request):
                    # Learn only for this model/adapter instance. Tools and host
                    # authorization remain in place; reasoning stays enabled.
                    adapted_choice = True
                    self._auto_tool_choice_models.add(model)
                    continue
                # Never replay a response once any chunk has escaped to ADK:
                # it could contain a tool call or a partial model response.
                # A bounded retry before that point cannot repeat a tool.
                if published or transport_retries >= self.transport_retry_limit or not _transient_provider_failure(exc):
                    raise
                transport_retries += 1
                await asyncio.sleep(0.5 * (2 ** (transport_retries - 1)))
