"""LiteLLM adapter for OpenAI-compatible and multi-provider chat models."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ._state import AgentRecord, validate_agent_config
from .base import AgentCapabilityProvider, AgentRuntime
from .models import (
    AgentConfig,
    AgentDependencyError,
    AgentEvent,
    AgentEventType,
    AgentInfo,
    AgentNotFoundError,
    AgentRuntimeError,
    AgentStateError,
    AgentStatus,
    ScopedToolDefinition,
)
from .tools import build_scoped_tool_schemas


CompletionFunction = Callable[..., Awaitable[Any]]


def _load_completion() -> CompletionFunction:
    try:
        module = importlib.import_module("litellm")
        completion = module.acompletion
    except (ImportError, AttributeError) as exc:
        raise AgentDependencyError(
            "LiteLLM runtime selected but litellm is not installed; "
            "run scripts/setup.ps1"
        ) from exc
    return completion


@dataclass(frozen=True, slots=True)
class _ToolCall:
    call_id: str
    name: str
    arguments: str


class LiteLLMAgentRuntime(AgentRuntime):
    """Run one independent Chat Completions conversation per Agent Card.

    ``model`` is passed through unchanged to LiteLLM. Examples include
    ``openai/gpt-4o-mini``, ``anthropic/claude-3-5-sonnet`` and
    ``openai/local-model`` with ``OPENAI_API_BASE`` configured.
    """

    def __init__(
        self,
        capability_provider: AgentCapabilityProvider,
        *,
        max_tool_rounds: int = 8,
        completion: CompletionFunction | None = None,
        completion_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        if max_tool_rounds <= 0 or max_tool_rounds > 32:
            raise AgentStateError("max_tool_rounds must be between 1 and 32")
        self._provider = capability_provider
        self._completion = completion or _load_completion()
        self._completion_kwargs = dict(completion_kwargs or {})
        self._max_tool_rounds = max_tool_rounds
        self._records: dict[str, AgentRecord] = {}
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._records_lock = asyncio.Lock()

    def configure_connection(
        self, *, api_base: str | None = None, api_key: str | None = None
    ) -> None:
        """Update connection overrides without persisting secrets.

        LiteLLM still falls back to its normal environment-variable discovery
        when an override is cleared. The values live only in this runtime
        instance and are snapshotted at the beginning of each run.
        """

        if api_base:
            self._completion_kwargs["api_base"] = api_base
        else:
            self._completion_kwargs.pop("api_base", None)
        if api_key:
            self._completion_kwargs["api_key"] = api_key
        else:
            self._completion_kwargs.pop("api_key", None)

    async def create_agent(self, config: AgentConfig) -> AgentInfo:
        validate_agent_config(config)
        async with self._records_lock:
            if config.agent_id in self._records:
                raise AgentStateError(f"agent already exists: {config.agent_id}")
            record = AgentRecord(
                config=config,
                session_id=self._session_id(config.agent_id),
            )
            self._records[config.agent_id] = record
            self._histories[config.agent_id] = []
            return record.info()

    async def update_agent(self, config: AgentConfig) -> AgentInfo:
        validate_agent_config(config)
        record = await self._record(config.agent_id)
        async with record.lock:
            if record.status == AgentStatus.RUNNING:
                raise AgentStateError("cannot update a running agent")
            record.config = config
            record.last_error = None
            return record.info()

    async def delete_agent(self, agent_id: str) -> None:
        record = await self._record(agent_id)
        await self.stop(agent_id)
        async with self._records_lock:
            if self._records.get(agent_id) is record:
                del self._records[agent_id]
                self._histories.pop(agent_id, None)

    async def run(self, agent_id: str, prompt: str) -> AsyncIterator[AgentEvent]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentStateError("prompt must not be empty")
        record = await self._record(agent_id)
        task = asyncio.current_task()
        if task is None:  # pragma: no cover
            raise AgentStateError("agent run requires an asyncio task")

        async with record.lock:
            if record.status == AgentStatus.RUNNING:
                raise AgentStateError(f"agent is already running: {agent_id}")
            record.run_counter += 1
            run_id = f"litellm-{agent_id}-{record.run_counter}"
            record.status = AgentStatus.RUNNING
            record.active_run_id = run_id
            record.active_task = task
            record.last_error = None

        yield AgentEvent(agent_id, run_id, AgentEventType.STARTED, {"model": record.config.model})
        yield self._status_event(record, run_id)
        final_text = ""
        try:
            definitions = tuple(await self._provider.list_tools(agent_id))
            tools = build_scoped_tool_schemas(definitions)
            by_name = {definition.name: definition for definition in definitions}
            messages = self._messages(record, prompt)
            connection_kwargs = dict(self._completion_kwargs)
            for _ in range(self._max_tool_rounds):
                response = await self._request(
                    record.config.model, messages, tools, connection_kwargs
                )
                message = _response_message(response)
                content = _message_value(message, "content")
                if content:
                    final_text = str(content)
                    yield AgentEvent(
                        agent_id,
                        run_id,
                        AgentEventType.MESSAGE,
                        {"text": final_text, "final": not _tool_calls(message)},
                    )
                tool_calls = _tool_calls(message)
                if not tool_calls:
                    messages.append(_assistant_message(message))
                    self._save_history(agent_id, messages)
                    break

                messages.append(_assistant_message(message))
                record.status = AgentStatus.WAITING
                yield self._status_event(record, run_id)
                for call in tool_calls:
                    definition = by_name.get(call.name)
                    if definition is None:
                        raise AgentRuntimeError(f"model requested unknown tool: {call.name}")
                    arguments = _parse_arguments(call.arguments, call.name)
                    yield AgentEvent(
                        agent_id,
                        run_id,
                        AgentEventType.TOOL_STARTED,
                        {
                            "name": call.name,
                            "call_id": call.call_id,
                            "arguments": _json_safe(arguments),
                        },
                    )
                    result = await self._provider.invoke_tool(
                        agent_id, definition.capability_id, arguments
                    )
                    yield AgentEvent(
                        agent_id,
                        run_id,
                        AgentEventType.TOOL_COMPLETED,
                        {
                            "name": call.name,
                            "call_id": call.call_id,
                            "response": _json_safe(result),
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id,
                            "content": _tool_content(result),
                        }
                    )
                record.status = AgentStatus.RUNNING
                yield self._status_event(record, run_id)
            else:
                raise AgentRuntimeError(
                    f"maximum tool-call rounds exceeded ({self._max_tool_rounds})"
                )

            async with record.lock:
                record.status = AgentStatus.IDLE
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(agent_id, run_id, AgentEventType.COMPLETED, {"text": final_text})
            yield self._status_event(record, run_id)
        except asyncio.CancelledError:
            async with record.lock:
                record.status = AgentStatus.IDLE
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(agent_id, run_id, AgentEventType.STOPPED, {})
            yield self._status_event(record, run_id)
        except Exception as exc:
            async with record.lock:
                record.status = AgentStatus.ERROR
                record.last_error = str(exc)
                record.active_run_id = None
                record.active_task = None
            yield AgentEvent(agent_id, run_id, AgentEventType.ERROR, {"error": str(exc)})
            yield self._status_event(record, run_id)
            raise

    async def _request(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        connection_kwargs: Mapping[str, Any],
    ) -> Any:
        kwargs: dict[str, Any] = {
            **connection_kwargs,
            "model": model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs.setdefault("tool_choice", "auto")
        return await self._completion(**kwargs)

    def _messages(self, record: AgentRecord, prompt: str) -> list[dict[str, Any]]:
        history = self._histories.get(record.config.agent_id, [])
        if not history:
            history = [{"role": "system", "content": record.config.system_instruction}]
        else:
            # Agent edits take effect on the next run without discarding the
            # rest of the conversation history.
            history = [
                {"role": "system", "content": record.config.system_instruction},
                *history[1:],
            ]
        return [*history, {"role": "user", "content": prompt.strip()}]

    def _save_history(self, agent_id: str, messages: list[dict[str, Any]]) -> None:
        # Keep the session bounded while preserving the system message.
        self._histories[agent_id] = [messages[0], *messages[1:][-40:]]

    async def stop(self, agent_id: str) -> None:
        record = await self._record(agent_id)
        async with record.lock:
            task = record.active_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.sleep(0)

    async def get_agent(self, agent_id: str) -> AgentInfo:
        return (await self._record(agent_id)).info()

    async def _record(self, agent_id: str) -> AgentRecord:
        async with self._records_lock:
            try:
                return self._records[agent_id]
            except KeyError as exc:
                raise AgentNotFoundError(f"agent not found: {agent_id}") from exc

    @staticmethod
    def _status_event(record: AgentRecord, run_id: str) -> AgentEvent:
        return AgentEvent(
            record.config.agent_id,
            run_id,
            AgentEventType.STATUS_CHANGED,
            {"status": record.status.value},
        )

    @staticmethod
    def _session_id(agent_id: str) -> str:
        digest = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:24]
        return f"agent-session-{digest}"


def _response_message(response: Any) -> Any:
    choices = response.get("choices") if isinstance(response, Mapping) else getattr(response, "choices", None)
    if not choices:
        raise AgentRuntimeError("LiteLLM returned no choices")
    choice = choices[0]
    return choice.get("message") if isinstance(choice, Mapping) else getattr(choice, "message", None)


def _message_value(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(key, default)
    return getattr(message, key, default)


def _tool_calls(message: Any) -> list[_ToolCall]:
    raw_calls = _message_value(message, "tool_calls", []) or []
    calls: list[_ToolCall] = []
    for raw in raw_calls:
        function = _message_value(raw, "function", {}) or {}
        raw_arguments = _message_value(function, "arguments", "{}")
        if isinstance(raw_arguments, Mapping):
            raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)
        calls.append(
            _ToolCall(
                call_id=str(_message_value(raw, "id", "")),
                name=str(_message_value(function, "name", "")),
                arguments=str(raw_arguments or "{}"),
            )
        )
    return calls


def _assistant_message(message: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"role": "assistant", "content": _message_value(message, "content")}
    calls = _tool_calls(message)
    if calls:
        value["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ]
    return value


def _parse_arguments(raw: str, name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise AgentRuntimeError(f"invalid arguments for tool {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise AgentRuntimeError(f"arguments for tool {name} must be a JSON object")
    return value


def _tool_content(value: Any) -> str:
    try:
        return json.dumps(_json_safe(value), ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)[:8192]


def _json_safe(value: Any, *, max_length: int = 8192) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_length]
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _json_safe(item, max_length=max_length)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, max_length=max_length) for item in value[:100]]
    return repr(value)[:max_length]
